"""Did that input change the display?  The masked answer, as one command.

    python -m tools.cdj_main.frame_delta pair before.ppm after.ppm
    python -m tools.cdj_main.frame_delta mask control-frames/ mask.bin --from 60
    python -m tools.cdj_main.frame_delta windows run-frames/ 150:18.1 175:18.2

A raw byte compare of two frames proves nothing here.  The screen animates by a
median of **808 bytes per second** whatever the input, so any two frames differ
and "the frame changed" is not evidence of anything.  `INPUT_MANIFEST.md` says
how the numbers in it were actually made, and this file is that method:

1. A **control run of the same length and settings** gives a per-pixel animation
   mask: every byte that differs between any two of its steady-state frames is
   excluded.  For r048 that left 349 835 of 367 200 bytes -- 95.3 % of the frame
   -- where nothing moves on its own.
2. A **tight window**: the last frame before the press against the first at
   least six seconds after it.  Pairs 19-28 seconds apart are reported as
   unattributed rather than counted, however large their delta.

**And the window is counted in content changes, not file timestamps.**  The
sampler writes a file only when the bytes changed, so a file's name says when a
frame appeared and nothing at all about how long it stood.  In r096 that cost
three rows: 18.3 showed 498 bytes in the player row and was reported over a
19.3-second window, because the previous *file* was 19.3 seconds old -- while
`index.tsv` recorded eleven `same` ticks in between, each one a sighting of
that same frame still on the screen.  The change was therefore at t234.8, 7.8
seconds after the press, and attributable.  `held_until` is that reading; it
can only shrink a window, never widen one, so the calibration below is
untouched by it.

What it deliberately does *not* do is make the rule looser.  r096's 18.4 and
18.7 share one change at t317.8, 65.8 and 15.8 seconds after their presses:
both stay `WINDOW TOO WIDE`, and a row that cannot be scored now also makes the
command exit non-zero, the same as `NOT MEASURED`.

Two things this file refuses to let you skip.

**The mask must come from the steady phase, not the whole run.**  The first mask
built here was made from an entire control run and was useless: it folded the
boot transitions in with the animation, marked half the frame as animated, and
wiped out exactly the fields that display state.  `--from` is therefore
required, and the report says what fraction was excluded so a repeat of that
mistake is visible in the first line of output rather than in a conclusion.

**A number is not a look.**  `GOAL.md`: "Counting is no substitute for
looking: enlarge the region before you judge it."  A count says something
moved; which
screen it became is only visible in the picture.  `--look` writes the changed
region out magnified, next to a full frame with the region ringed.

## Mask format

One byte per channel byte of the capture, `1` where the pixel animates and `0`
where it is evidence.  Byte-compatible with `runs/anim-mask.bin`, which this
file reproduces to the byte on all six rows of the manifest's table
(`tests/test_frame_delta.py`).
"""
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

import numpy as np
from PIL import Image

# The capture, not the panel: the PPI emits 255 lines and the frame sampler
# stores all of them, so a mask made here lines up with runs/anim-mask.bin.
# The trailing 21 lines are blanking and are black in every frame, so they never
# contribute to a delta -- but they do belong to the coordinates.
CAPTURE_WIDTH = 480
CAPTURE_HEIGHT = 255
PANEL_HEIGHT = 234          # what is actually the LCD; see GOAL.md
CAPTURE_BYTES = CAPTURE_WIDTH * CAPTURE_HEIGHT * 3

# The frame sampler names files by elapsed wall clock: "t%06.1f.ppm".
FRAME_NAME = re.compile(r"^t(\d+(?:\.\d+)?)\.(?:ppm|png)$")

# Only pairs closer together than this are attributable.  The four rows in
# INPUT_MANIFEST.md with 19-28 second gaps are honest failures of the sampling,
# not of the input, and they are reported rather than counted.
MAX_WINDOW_SECONDS = 10.0
DEFAULT_SETTLE_SECONDS = 6.0

# How many content changes in the ten seconds *before* an input make the screen
# "busy" rather than still.
#
# One can be the previous input's answer arriving late, or the tail of a
# redraw.  Two or more is a screen repainting under its own steam, and over such
# a stretch a masked-off byte compare answers a different question than the one
# asked: not "did this input move the display" but "what did the display do
# next".  r113 is the case that made this a rule -- see `baseline_delta`.
BUSY_CHANGES = 2

# How many input-free windows the self-baseline is taken over, by default.
BASELINE_SAMPLES = 4

# A press that reached the GUI's key dispatcher, as BFIN_STEP_TRACE prints it:
#
#   step-trace: key dispatcher 0xb9b98c R2=8 rets=0x00ba4ff6 (#1) t448.3 | input
#   census -- parses 508, source writes 508, key dispatches 1
#
# The `rets=` field is not always there (r133's line has none), so it is not
# part of the match.
TRACE_KEY = re.compile(
    r"key dispatcher\s+(\S+)\s+R2=(\d+).*?\bt(\d+(?:\.\d+)?)")

# The trace clock is the Blackfin simulator's and the frame names are the
# sampler's; in r133 and r134 they differed by 2.9 s and 3.6 s.  The tolerance
# for pairing a dispatch with an input is therefore the attribution limit
# itself, which absorbs that skew and is a number this file already lives by.
# --trace-shift is there for a run where the skew is larger and known.
TRACE_TOLERANCE = MAX_WINDOW_SECONDS


# ------------------------------------------------------------------ frames --
def load(path: Path) -> np.ndarray:
    """One frame as a flat array of channel bytes."""
    with Image.open(path) as image:
        data = np.frombuffer(image.convert("RGB").tobytes(), dtype=np.uint8)
    if data.size != CAPTURE_BYTES:
        raise ValueError(f"{path.name} is {data.size} bytes, expected "
                         f"{CAPTURE_BYTES} ({CAPTURE_WIDTH}x{CAPTURE_HEIGHT} RGB)")
    return data


def frames_in(directory: Path) -> list[tuple[float, Path]]:
    """Every sampled frame, as (elapsed seconds, path), earliest first.

    The sampler only writes a file when the frame *changed*, so the times are
    uneven and a quiet stretch leaves a hole.  That is exactly why a window has
    to be checked for width rather than assumed to be tight.
    """
    found: list[tuple[float, Path]] = []
    for path in sorted(directory.iterdir()):
        match = FRAME_NAME.match(path.name)
        if match:
            found.append((float(match.group(1)), path))
    found.sort()
    return found


# -------------------------------------------------------------------- mask --
def build_mask(frames: list[Path]) -> np.ndarray:
    """1 where a byte ever moves across these frames, 0 where it never does."""
    if len(frames) < 2:
        raise ValueError("a mask needs at least two frames")
    first = load(frames[0])
    lowest = first.copy()
    highest = first.copy()
    for path in frames[1:]:
        data = load(path)
        np.minimum(lowest, data, out=lowest)
        np.maximum(highest, data, out=highest)
    return (highest != lowest).astype(np.uint8)


def stable_delta(before: np.ndarray, after: np.ndarray,
                 mask: np.ndarray | None) -> np.ndarray:
    """Bytes that differ and are not masked away, as a boolean array."""
    changed = before != after
    if mask is not None:
        changed &= mask == 0
    return changed


def residual_floor(mask: np.ndarray, frames: list[Path]) -> int:
    """The worst stable delta between any two of these frames.

    Run over the frames the mask was *fitted* on it is 0 by construction, which
    makes it a self-check: anything else means the mask and the frames do not
    line up.  Run over held-out frames from the same control run it is the real
    noise floor -- what the mask failed to predict.
    """
    worst = 0
    loaded = [load(path) for path in frames]
    for index in range(len(loaded) - 1):
        for other in loaded[index + 1:]:
            worst = max(worst, int(stable_delta(loaded[index], other,
                                                mask).sum()))
    return worst


def sidecar_path(mask_path: Path) -> Path:
    return mask_path.with_suffix(mask_path.suffix + ".json")


def read_mask(path: Path) -> tuple[np.ndarray, dict]:
    data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    if data.size != CAPTURE_BYTES:
        raise ValueError(f"{path} is {data.size} bytes, expected {CAPTURE_BYTES}")
    if data.max(initial=0) > 1:
        raise ValueError(f"{path} is not a 0/1 mask")
    notes: dict = {}
    sidecar = sidecar_path(path)
    if sidecar.exists():
        notes = json.loads(sidecar.read_text(encoding="utf-8"))
    return data, notes


def describe(mask: np.ndarray, notes: dict) -> list[str]:
    """What the mask itself says about how much a delta is worth."""
    animated = int(mask.sum())
    surface = mask.size - animated
    lines = [
        "evidence surface  %d of %d bytes (%.1f %% of the frame; %.2f %% animates)"
        % (surface, mask.size, 100.0 * surface / mask.size,
           100.0 * animated / mask.size),
    ]
    if animated > mask.size // 5:
        lines.append(
            "  WARNING: over a fifth of the frame is masked away.  A mask built "
            "from a whole run rather than its steady phase folds the boot "
            "transitions in with the animation and wipes out exactly the fields "
            "that display state.  Check --from.")
    if "noise_floor" in notes:
        lines.append(
            "noise floor       %d bytes, from %d held-out frames of %s"
            % (notes["noise_floor"], notes.get("holdout_frames", 0),
               notes.get("source", "the control run")))
        lines.append(
            "  a delta at or below that is noise; above it, something moved")
    else:
        lines.append("noise floor       not recorded beside this mask "
                     "(no .json sidecar); it is 0 by construction over the "
                     "frames the mask was fitted on")
    return lines


# ------------------------------------------------------------------- looks --
def bounding_box(changed: np.ndarray) -> tuple[int, int, int, int] | None:
    """The rectangle the changed bytes live in, in capture pixels."""
    if not changed.any():
        return None
    pixels = changed.reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 3).any(axis=2)
    rows = np.flatnonzero(pixels.any(axis=1))
    columns = np.flatnonzero(pixels.any(axis=0))
    return (int(columns[0]), int(rows[0]), int(columns[-1]) + 1,
            int(rows[-1]) + 1)


def write_look(directory: Path, name: str, before: Path, after: Path,
               changed: np.ndarray, zoom: int) -> list[Path]:
    """Write the changed region magnified, and a frame with it ringed.

    Counting called every broken label in this project "clean" once; cropping
    the rectangle and magnifying it is what settled them.  So the tool that
    produces the number also produces the picture, rather than leaving that as
    an optional afterwards nobody does.
    """
    from PIL import ImageDraw

    box = bounding_box(changed)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with Image.open(before) as image:
        first = image.convert("RGB")
    with Image.open(after) as image:
        second = image.convert("RGB")

    if box is not None:
        left, top, right, bottom = box
        # A margin, so the change is seen in its surroundings rather than alone.
        pad = 6
        wide = (max(0, left - pad), max(0, top - pad),
                min(CAPTURE_WIDTH, right + pad), min(CAPTURE_HEIGHT, bottom + pad))
        crops = [first.crop(wide), second.crop(wide)]
        width = crops[0].width
        height = crops[0].height
        sheet = Image.new("RGB", (width, height * 2 + 2), (255, 0, 0))
        sheet.paste(crops[0], (0, 0))
        sheet.paste(crops[1], (0, height + 2))
        sheet = sheet.resize((sheet.width * zoom, sheet.height * zoom),
                             Image.Resampling.NEAREST)
        target = directory / f"{name}-crop.png"
        sheet.save(target)
        written.append(target)

    ringed = second.crop((0, 0, CAPTURE_WIDTH, PANEL_HEIGHT)).copy()
    if box is not None:
        draw = ImageDraw.Draw(ringed)
        draw.rectangle((box[0] - 1, box[1] - 1, box[2], box[3]),
                       outline=(255, 0, 0))
    target = directory / f"{name}-where.png"
    ringed.save(target)
    written.append(target)
    return written


# ----------------------------------------------------------------- windows --
def parse_windows(entries: list[str]) -> list[tuple[float, str]]:
    """`150:18.1` -> (150.0, '18.1'), sorted.  Same spelling as a session."""
    windows: list[tuple[float, str]] = []
    for entry in entries:
        head, separator, name = entry.partition(":")
        if not separator or not name.strip():
            raise ValueError(f"expected SECONDS:NAME, got {entry!r}")
        windows.append((float(head), name.strip()))
    return sorted(windows, key=lambda item: item[0])


# ------------------------------------------------------------ the index ----
#
# The sampler writes a file only when the bytes changed, which is right for disk
# and wrong for evidence: a window over a stretch where the screen stood still
# contains no file at all, and "no frame on one side" is then reported for two
# opposite findings -- *this input changed nothing* and *this input was never
# measured*.
#
# GOAL.md allows an expected no-op but requires it to be **proven**, and a no-op
# can only be proven if a gap can be told from it.  boot_vm now writes
# `index.tsv`, one row per tick with a status, and that is the distinction.
INDEX_NAME = "index.tsv"

# A tick that produced a file, or one that deliberately produced none because
# nothing had changed.  Either way the screen was observed at that moment.
OBSERVED = ("new", "same")


def read_index(directory: Path) -> list[tuple[float, str]] | None:
    """(elapsed, status) per sampler tick, or None when the run predates it."""
    path = directory / INDEX_NAME
    if not path.exists():
        return None
    ticks: list[tuple[float, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            ticks.append((float(parts[0]), parts[1]))
        except ValueError:
            continue
    ticks.sort()
    return ticks


def held_until(ticks: list[tuple[float, str]], since: float) -> float:
    """The last moment the frame written at `since` was *seen* still on screen.

    A file's timestamp says when a frame appeared, not how long it stood, and
    the sampler writes nothing while it stands.  So two files thirty seconds
    apart look like a thirty-second window even when the screen changed once,
    in the last second of it.

    The index says which.  A `same` tick is not the absence of news: the
    sampler read the live capture, compared it against exactly the bytes of the
    last written file, and found them equal.  It is a positive sighting of that
    frame at that moment.  A run of them therefore moves the earliest possible
    time of the next change forward, from the file's timestamp to the last
    sighting before the next `new`.

    An `error` or `empty` tick is not a sighting and does not extend the hold,
    but it does not end it either -- a later `same` supersedes it, because it
    sees the same bytes again.  The hold ends at the first `new`, which is the
    change itself.
    """
    held = since
    for when, status in ticks:
        if when <= since:
            continue
        if status == "new":
            break
        if status in OBSERVED:
            held = when
    return held


def attribution_span(ticks: list[tuple[float, str]] | None, before_at: float,
                     after_at: float, at: float) -> tuple[float, float]:
    """(start, held): the stretch a delta between two frames arose in.

    File timestamps alone put it at `(before_at, after_at]`, and everything the
    input could be blamed for has to fit inside that.  With an index the start
    moves to the last sighting of the earlier frame, which can be long after
    the file that carries it.

    The input at `at` still has to be inside or ahead of the stretch, so what
    has to be tight is `after_at - start` with `start = min(held, at)`.  When
    the screen was last seen unchanged *after* the input, that is exactly "how
    long after the press did the display move"; when it was last seen before
    the input, the input sits inside the stretch and the old file-timestamp
    arithmetic comes back out.  Either way the span can only shrink, never
    grow, so nothing that was attributable before stops being so.
    """
    if ticks is None:
        return min(before_at, at), before_at
    held = min(held_until(ticks, before_at), after_at)
    return min(held, at), held


def passed_over(ticks: list[tuple[float, str]] | None, at: float,
                settle: float, after_at: float) -> float | None:
    """The answer `--settle` stepped past, if it stepped past one.

    `--settle` says how long after the input the second frame must be taken,
    and it exists so a frame caught mid-redraw is not mistaken for the settled
    result.  Its failure mode is silent and severe: the sampler writes nothing
    while the screen stands, so if the display answers *before* the settle
    expires, the answering frame is the only one there is -- and skipping it
    takes the next input's repaint instead.  The row then carries a real number
    over a window that spans two inputs.

    Measured on a synthetic run: a 5-second answer read with `--settle 6`
    reports 24 bytes over 30 s where the truth is 12 bytes over 5 s.

    Only the harmful shape is reported.  A change inside the settle is normal
    while the screen is busy -- r096's first three windows each have one, and
    each still has a later frame inside the attribution limit, so their pairs
    are tight and their numbers stand.  What is fatal is a change skipped
    inside the limit when the frame that was taken instead lies **outside** it:
    an answer existed, and the settle stepped over it into the next input.
    """
    if ticks is None or after_at <= at + MAX_WINDOW_SECONDS:
        return None
    inside = [when for when, status in ticks
              if status == "new" and at < when < at + settle]
    return inside[-1] if inside else None


def observed_between(ticks: list[tuple[float, str]], start: float,
                     end: float) -> tuple[bool, str]:
    """Was the screen actually watched across this window?

    Returns (observed, why-not).  A stretch is only evidence if every tick in it
    reports `new` or `same`: an `error:` tick is a failed read and an absent
    tick is a stretch nobody looked at, and neither can support "nothing
    changed here".
    """
    inside = [(when, status) for when, status in ticks if start <= when <= end]
    if not inside:
        return False, "no sampler tick in the window"
    broken = [status for _, status in inside if status not in OBSERVED]
    if broken:
        return False, "sampler reported %s" % ", ".join(sorted(set(broken)))
    return True, ""


def read_key_dispatches(path: Path) -> list[tuple[float, int]]:
    """(trace seconds, R2) for every press that reached the key dispatcher.

    **The picture cannot answer this and no guard built on it ever could.**
    r134's `19.3-a` carried 172 807 bytes over a 3.1-second window, with a
    self-baseline of 4 583, no `SCREEN BUSY` and no `CHANGE NOT PROVEN` -- it
    passed every check this file had.  It was not the press.  The boot
    sequence's own word-13 change (`0xb899fc(screen=0)`) is 1.4 s in front of
    it, and a press landing at the end of the boot animation **inherits that
    repaint**.  The whole run contains exactly one dispatch, at t448.3, and
    `19.3-a` is at t61.9.

    So this is `index.tsv`'s distinction one level up.  There it was "the screen
    did not change" against "nobody was looking"; here it is "the key did
    nothing" against "the key never arrived", and only the trace tells them
    apart.  A window without a dispatch is a gap, not a result -- and that
    applies to a proven no-op just as much as to a delta, because a no-op is
    only a statement about a key if the key reached the dispatcher.
    """
    found: list[tuple[float, int]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = TRACE_KEY.search(line)
        if match:
            found.append((float(match.group(3)), int(match.group(2))))
    found.sort()
    return found


def nearest_dispatch(dispatches: list[tuple[float, int]], at: float,
                     tolerance: float = TRACE_TOLERANCE
                     ) -> tuple[float, int] | None:
    """The dispatch closest to this input, if one is close enough to be it."""
    candidates = [(abs(when - at), when, code) for when, code in dispatches
                  if abs(when - at) <= tolerance]
    if not candidates:
        return None
    _, when, code = min(candidates)
    return when, code


# ------------------------------------------- the analogue arrival proof ----
#
# **A rotary cannot have a key dispatcher line, by construction.**  The GUI's
# dispatcher at 0xb9b98c is reached from the button bitmap; an analogue field
# never touches it.  Until 2026-08-07 this file set `traced` once for the whole
# report, so every analogue window was refused with `NO KEY DISPATCHED` -- a
# sentence that is not merely useless there but false.  r160, the only real
# coverage run, refused all nine of its analogue windows that way.
#
# For analogue windows only, the dispatcher line is replaced
# with a stricter proof rather than a weaker one: the field's destination word
# in MAIN's status block must have moved by **exactly the amount the transcript
# sent** inside the window.  It carries the value, not just the fact of a
# delivery, and it is the reason this is not a loosening of `NO KEY DISPATCHED`.
#
# The three things that keep it from becoming one:
#
# 1. **The verb decides, and it comes from the transcript**, not from a naming
#    convention on the window label.  `press`/`down`/`up` still need a dispatch;
#    `rotary`/`analog` need the counter.  A window whose verb cannot be read
#    falls back to the *press* rule, so nothing is ever rescued by failing to
#    classify.
# 2. **No counter stream is not an excuse.**  An analogue window without one is
#    refused as NOT MEASURED, the same way an undispatched press is -- "the
#    input did nothing" and "nobody measured whether it arrived" stay apart.
# 3. **The amount has to match.**  A counter that drifted, or that moved by
#    someone else's step, is a mismatch and not an arrival.
#
# What the guard exists against is r134: a press at the end of the boot
# animation inherited its repaint, 172 807 bytes, past all three pixel guards.
# An analogue window on the same frames fails here for the same reason it failed
# there -- nothing it sent reached the machine in that window.

# `CDJ_WATCH=<addr>` overlays four bytes and prints every write.  From
# cdj2000_main.c:
#
#   cdj2000-watch: 0x5a1b15d 1-byte write 0x41: 0000000000 -> 0x00004100  pc 0x0414def0
#
# `before` and `after` are always the whole 32-bit word, whatever the access
# width, so a byte write to base+1 still reports the word either side of it.
WATCH_WRITE = re.compile(
    r"cdj2000-watch:\s+(0x[0-9a-fA-F]+)\s+\d+-byte write\s+\S+:\s+"
    r"(\S+)\s*->\s*(\S+)")

# **The watch line carries no time of its own** -- checked against every
# archived stream on disk (r011, r028, r031, r032, r067, r068, r073, r075,
# r077, r078, r083..r086, r141, r151, r156): not one `cdj2000-watch:` line has
# a timestamp.  So the clock is carried from whatever timestamped line the same
# stream last printed, and both producers spell it the same way:
#
#   cdj2000: panel key byte 19 mask 0x2 at 40.00 s          (CDJ_PANEL_KEYS)
#   cdj2000-input: byte 19 mask 0x2 down at 300.123 s ...   (the control channel)
#   cdj2000-input: analogue field 7 = 12 at 1425.310 s ...  (the ramp, per step)
#
# The third line is what makes an analogue window datable at all, and it was
# added to cdj2000_input.c for that: in `plan coverage` the last press is at
# t1275 and the first analogue window at t1250, so a clock carried from presses
# alone would be stale by up to three minutes exactly where it is needed.
WATCH_CLOCK = re.compile(r"\bat (\d+(?:\.\d+)?) s\b")

# Verbs, as `panel_control.resolve` understands them.  Anything else falls back
# to the press rule; see point 1 above.
PRESS_VERBS = ("press", "down", "up")
ANALOG_VERBS = ("rotary", "analog")

# How far a window time may sit from the transcript entry that is its input.
# The plan's spacing is 25 s and a session sends within milliseconds of its
# scheduled time (r160: every entry landed on `1250.000`, `1275.000`, ...), so
# this only has to absorb a send that blocked, never a neighbouring window.
WINDOW_MATCH_SECONDS = 5.0


def signed32(value: int) -> int:
    """The watched word as a signed 32-bit number.

    **It was written on a reading r174 contradicts.**  The note here used to say
    "byte 14 is sign-extended by `mov.b`", i.e. that a rotary walked below zero
    would leave `0xfffffff4` and subtract to -12 on its own.  r174's stream
    holds `0x000000f4` in all 29 writes to 0x04fe2a44: the byte is
    zero-extended, and the sign is recovered by `wrapped_by` below rather than
    here.  This stays for a word that is genuinely negative, which none of the
    eight analogue destinations has ever been.
    """
    return value - (1 << 32) if value & (1 << 31) else value


def analog_modulus(field: int) -> int | None:
    """How wide this field's destination value is, from panel_control."""
    from tools.cdj_main import panel_control

    return panel_control.analog_modulus(field)


def wrapped_by(net: int, expected: int, modulus: int | None) -> int | None:
    """How many wraps separate a measured move from the one that was sent.

    `None` means they are not the same movement at all.  `0` means they agree
    outright; anything else means they agree **modulo the destination's own
    width**, which is the only sense in which a byte-wide field can carry a
    negative amount at all.

    r174: `rotary 7 -24` walked 0x0c to 0xf4, so the words subtract to +232 and
    the transcript sent -24.  Those are one movement of one byte, not two
    findings, and reading them as two refuses every negative rotary window ever
    planned -- `plan coverage`'s own included.

    **It is not a loosening, because the reduction is what the hardware does.**
    A payload byte cannot hold -24; the only thing that ever reaches MAIN is the
    byte, and 232 and -24 are the same byte.  What would be a loosening is
    letting `modulus` swallow a *whole* wrap, and `judge_analog` refuses that
    case by name instead of scoring it.
    """
    if modulus is None or modulus <= 0:
        return None if net != expected else 0
    difference = net - expected
    if difference % modulus:
        return None
    return difference // modulus


class Movement(NamedTuple):
    at: float | None
    address: int
    before: int
    after: int


def read_counter_writes(path: Path) -> dict[int, list[Movement]]:
    """{aligned word address: the writes that CHANGED it}, in stream order.

    Writes that leave the word alone are dropped: the panel handler rewrites
    every field on every accepted frame, so the overwhelming majority of the
    stream is the same value being stored again, and counting those as movement
    would make "the counter moved" true in every window of every run.
    """
    found: dict[int, list[Movement]] = {}
    clock: float | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stamp = WATCH_CLOCK.search(line)
        if stamp:
            clock = float(stamp.group(1))
        match = WATCH_WRITE.search(line)
        if not match:
            continue
        try:
            address = int(match.group(1), 16)
            before = int(match.group(2), 16)
            after = int(match.group(3), 16)
        except ValueError:
            continue
        if before == after:
            continue
        found.setdefault(address & ~3, []).append(
            Movement(at=clock, address=address, before=before, after=after))
    return found


def counter_movement(movements: list[Movement], start: float, end: float
                     ) -> tuple[int, int, float | None, float | None]:
    """(net signed change, writes, first, last) inside [start, end].

    The net is taken across the bracket rather than summed step by step, so a
    ramp that passes through a value and comes back reads as what it did, not
    as how far it travelled.
    """
    inside = [item for item in movements
              if item.at is not None and start <= item.at <= end]
    if not inside:
        return 0, 0, None, None
    return (signed32(inside[-1].after) - signed32(inside[0].before),
            len(inside), inside[0].at, inside[-1].at)


def analog_destination(field: int) -> int | None:
    """Where this field lands in MAIN's status block, from panel_control."""
    from tools.cdj_main import panel_control

    return panel_control.ANALOG_DESTINATION.get(field)


def read_session(transcript: Path) -> list[tuple[float, float, str, str]]:
    from tools.cdj_main import panel_control

    _epoch, entries = panel_control.read_transcript(str(transcript))
    return entries


def analog_expectations(entries: list[tuple[float, float, str, str]]
                        ) -> dict[float, tuple[str, int, int]]:
    """{elapsed: (verb, field, expected change)} for every analogue command.

    `rotary F D` asks the destination for exactly `D`, whatever it holds now.
    `analog F V` asks for `V - <what this session last set F to>`, so the model
    walks the whole transcript rather than only the windowed entries -- an
    un-scored command still moves the field, and a window after it would
    otherwise expect the wrong amount.
    """
    value: dict[int, int] = {}
    out: dict[float, tuple[str, int, int]] = {}
    for _epoch, elapsed, command, _reply in entries:
        parts = command.split()
        if not parts:
            continue
        if parts[0] == "clear":
            value = {}
            continue
        if parts[0] not in ANALOG_VERBS or len(parts) < 3:
            continue
        try:
            field, amount = int(parts[1], 0), int(parts[2], 0)
        except ValueError:
            continue
        current = value.get(field, 0)
        if parts[0] == "rotary":
            out[elapsed] = ("rotary", field, amount)
            value[field] = current + amount
        else:
            out[elapsed] = ("analog", field, amount - current)
            value[field] = amount
    return out


def window_command(entries: list[tuple[float, float, str, str]], at: float,
                   tolerance: float = WINDOW_MATCH_SECONDS
                   ) -> tuple[float, str] | None:
    """(elapsed, command) for the transcript entry this window is about."""
    best: tuple[float, float, str] | None = None
    for _epoch, elapsed, command, _reply in entries:
        gap = abs(elapsed - at)
        if gap <= tolerance and (best is None or gap < best[0]):
            best = (gap, elapsed, command)
    return None if best is None else (best[1], best[2])


class Arrival(NamedTuple):
    """Whether this window's input reached the machine, and how that was told.

    `verdict` is empty when it did.  `note` is the refusal as it goes on the
    row; `line` is the confirming evidence printed under a row that stands.
    """
    kind: str            # "press" | "analog"
    verdict: str
    note: str
    line: str


def judge_press(dispatch: tuple[float, int] | None, at: float) -> Arrival:
    if dispatch is None:
        return Arrival("press", "undispatched",
                       "NO KEY DISPATCHED -- the trace has no key dispatcher "
                       "line within %g s of this input, so whatever moved "
                       "here, the key did not cause it" % TRACE_TOLERANCE, "")
    return Arrival("press", "", "",
                   "key dispatcher R2=%d at t%.1f (%+.1f s from the input on "
                   "the trace's own clock)"
                   % (dispatch[1], dispatch[0], dispatch[0] - at))


def judge_analog(counters: dict[int, list[Movement]] | None, field: int,
                 expected: int, at: float, settle_end: float) -> Arrival:
    """The counter's own answer for one analogue window.

    Three refusals, each a different statement, because collapsing them is the
    mistake `NO KEY DISPATCHED` made on this row in the first place: *nobody
    measured it*, *it never arrived*, and *something else arrived* are not the
    same finding and do not call for the same next run.
    """
    address = analog_destination(field)
    if address is None:
        return Arrival("analog", "analog-unmeasured",
                       "ANALOG NOT MEASURED -- field %d has no known "
                       "destination in MAIN's status block" % field, "")
    modulus = analog_modulus(field)
    # **The one way a modular comparison could become a loophole, refused by
    # name.**  If the transcript sent a whole multiple of the destination's
    # width, the destination ends where it started, and "it arrived" and "it
    # never arrived" produce the same reading.  That is not an arrival proof,
    # so it is not scored -- it is a plan that has to be rewritten with an
    # amount the destination can show.
    if modulus and expected and expected % modulus == 0:
        return Arrival("analog", "analog-unmeasurable",
                       "ANALOG AMOUNT UNOBSERVABLE -- the transcript sent %+d "
                       "and field %d's destination %#010x wraps every %d, so "
                       "an arrival would leave it exactly where a non-arrival "
                       "does.  Send an amount the destination can show"
                       % (expected, field, address, modulus), "")
    if counters is None:
        return Arrival("analog", "analog-unmeasured",
                       "ANALOG NOT MEASURED -- no counter stream given "
                       "(--counter).  A rotary cannot reach the key "
                       "dispatcher, so without CDJ_WATCH=%#010x nothing here "
                       "can tell an input that arrived from one that did not"
                       % address, "")
    movements = counters.get(address, [])
    undated = sum(1 for item in movements if item.at is None)
    if movements and undated == len(movements):
        return Arrival("analog", "analog-unmeasured",
                       "ANALOG NOT MEASURED -- %d write(s) to %#010x and not "
                       "one is datable: the stream carries no `at <n> s` line "
                       "to carry a clock from, so no write can be placed in "
                       "this window" % (len(movements), address), "")
    net, writes, first, last = counter_movement(
        movements, at - TRACE_TOLERANCE, settle_end)
    if not writes:
        return Arrival("analog", "analog-absent",
                       "NO ANALOG ARRIVAL -- the panel counter %#010x never "
                       "moved between t%.1f and t%.1f, so whatever moved here, "
                       "the %+d this window sent did not cause it"
                       % (address, at - TRACE_TOLERANCE, settle_end, expected),
                       "")
    wraps = wrapped_by(net, expected, modulus)
    if wraps is None:
        return Arrival("analog", "analog-amount",
                       "ANALOG AMOUNT MISMATCH -- the panel counter %#010x "
                       "moved %+d over %d write(s), where the transcript sent "
                       "%+d%s.  An arrival has to carry the value, not just the "
                       "fact of one"
                       % (address, net, writes, expected,
                          "" if modulus is None
                          else " (and %+d is not %+d modulo the destination's "
                               "%d)" % (net, expected, modulus)), "")
    # The raw number stays on the line whichever way it read, so a row rescued
    # by the wrap never looks like one that never needed it.
    return Arrival("analog", "", "",
                   "panel counter %#010x moved %+d at t%.1f..t%.1f over %d "
                   "write(s), %s"
                   % (address, net, first, last, writes,
                      "exactly what the transcript sent" if wraps == 0
                      else "which is %+d modulo the destination's %d -- exactly "
                           "what the transcript sent" % (expected, modulus)))


def self_motion_before(ticks: list[tuple[float, str]], at: float,
                       span: float = MAX_WINDOW_SECONDS) -> int:
    """How many times the screen changed on its own just before an input.

    An input cannot have caused what happened before it, so this stretch is a
    control the run carries with it.  If the display was repainting here it is
    repainting through the window too, and a byte compare across that window
    measures the repainting, not the key -- unless a mask covers it, and a mask
    can only cover it if some control run was ever in this state.

    That is not a hypothetical.  `r113` attributed three changes with no mask,
    justified by a control run (`r112`) whose own steady phase began at t121.3.
    Two of the three sat at t151 and t176, inside a stretch where `r113`'s index
    records a change on five ticks out of every six, and their deltas -- 759 and
    796 bytes -- are *smaller* than what input-free windows in the same stretch
    of the same run produce (797 to 1148 bytes, median 866).  The control run
    could not have said so: it was never in that state.
    """
    return sum(1 for when, status in ticks
               if status == "new" and at - span <= when < at)


def gap_midpoints(inputs: list[float], at: float,
                  clearance: float = MAX_WINDOW_SECONDS) -> list[float]:
    """Times where nothing was pressed, nearest `at` first.

    The midpoint of the gap between two consecutive inputs is as far from both
    as the plan allows.  Anything closer than the attribution limit to any input
    is dropped, so a "control" window can never contain an answer.
    """
    ordered = sorted(inputs)
    candidates = [(first + second) / 2.0
                  for first, second in zip(ordered, ordered[1:])]
    free = [when for when in candidates
            if all(abs(when - other) >= clearance for other in ordered)]
    return sorted(free, key=lambda when: abs(when - at))


def baseline_delta(frames: list[tuple[float, Path]],
                   ticks: list[tuple[float, str]] | None,
                   mask: np.ndarray | None, at: float, inputs: list[float],
                   settle: float, count: int,
                   cache: dict[Path, np.ndarray]
                   ) -> tuple[int, int, list[tuple[float, int]]]:
    """The largest delta a window of this shape gets where nothing was pressed.

    The mask says which pixels move by themselves **in some other run**.  This
    says what they do in *this* one, and it needs no second machine: the plan
    leaves 25-second gaps between inputs, so the midpoints of those gaps are
    windows of the same width, on the same screen, in the same minute, with no
    input in them.  A row that does not beat the worst of them is not evidence
    of anything, whatever the mask said.

    Returns (worst delta, samples used, the samples).  Zero samples means the
    run cannot control itself -- one window, or every gap too small -- and the
    caller must not read the 0 as a floor.
    """
    samples: list[tuple[float, int]] = []
    for when in gap_midpoints(inputs, at):
        if len(samples) >= count:
            break
        if ticks is not None:
            observed, _ = observed_between(ticks, when,
                                           when + MAX_WINDOW_SECONDS)
            if not observed:
                continue
            if not any(status == "new" and when < moment
                       <= when + MAX_WINDOW_SECONDS
                       for moment, status in ticks):
                # The screen was watched and never moved: a proven zero, the
                # same reading a scored row gets.
                samples.append((when, 0))
                continue
        pair = pick_pair(frames, when, settle)
        if pair is None:
            continue
        (before_at, before), (after_at, after) = pair
        span_start, _ = attribution_span(ticks, before_at, after_at, when)
        if after_at - span_start > MAX_WINDOW_SECONDS:
            # Too wide to be a comparable window; it would understate nothing
            # and overstate everything.
            continue
        for path in (before, after):
            if path not in cache:
                cache[path] = load(path)
        samples.append((when, int(stable_delta(cache[before], cache[after],
                                               mask).sum())))
    worst = max((delta for _, delta in samples), default=0)
    return worst, len(samples), samples


def sampler_epoch(frames: list[tuple[float, Path]]) -> tuple[float, float]:
    """When the frame sampler started, in wall-clock epoch, and its spread.

    Frames are named by seconds elapsed since the sampler began and are written
    as they are taken, so `mtime - label` is the sampler's start epoch measured
    once per frame.  The spread over all of them is the honesty check: a wide
    one means the files have been copied without preserving times and the
    alignment below cannot be trusted.
    """
    if not frames:
        raise ValueError("no frames to take a start time from")
    estimates = sorted(path.stat().st_mtime - when for when, path in frames)
    middle = estimates[len(estimates) // 2]
    return middle, estimates[-1] - estimates[0]


def alignment_shift(frames: list[tuple[float, Path]],
                    transcript: Path) -> tuple[float, float]:
    """Seconds to add to a session time to get a frame time, and the spread.

    A session's clock starts when the control channel opens; the frame
    sampler's starts when boot_vm does.  In r091 those differed by about 45
    seconds, and at 25-second spacing an error that size slides every window
    nearly two positions -- attributing each key to its neighbour, consistently,
    which is the kind of wrong that looks right.

    Both clocks are anchored to the wall clock: the transcript records the
    epoch at which the channel opened, and the frames carry theirs in their
    modification times.  So the offset is a measurement of two artefacts and
    needs no number typed in by anybody.
    """
    from tools.cdj_main import panel_control

    connect_epoch, _ = panel_control.read_transcript(str(transcript))
    started, spread = sampler_epoch(frames)
    return connect_epoch - started, spread


def pick_pair(frames: list[tuple[float, Path]], at: float,
              settle: float) -> tuple[tuple[float, Path], tuple[float, Path]] | None:
    """The last frame at or before `at`, and the first at or after `at + settle`."""
    before = None
    for when, path in frames:
        if when <= at:
            before = (when, path)
        else:
            break
    after = next(((when, path) for when, path in frames if when >= at + settle),
                 None)
    if before is None or after is None:
        return None
    return before, after


# -------------------------------------------------------------------- CLI ---
def command_mask(args: argparse.Namespace) -> int:
    frames = frames_in(args.frames)
    steady = [(when, path) for when, path in frames
              if when >= args.start and (args.end is None or when <= args.end)]
    if len(steady) < 2:
        print(f"frame_delta: {len(steady)} frames in the steady phase "
              f"[{args.start:g}, {args.end if args.end else 'end'}]; need at "
              f"least two", file=sys.stderr)
        return 1

    # Every third frame is held out, so the floor is measured on frames the mask
    # has not seen.  Fitting and testing on the same frames gives 0 and proves
    # only that the arithmetic is consistent.
    holdout = [path for index, (_, path) in enumerate(steady)
               if args.holdout and index % 3 == 2]
    fitted = [path for index, (_, path) in enumerate(steady)
              if not (args.holdout and index % 3 == 2)]
    if len(fitted) < 2:
        fitted, holdout = [path for _, path in steady], []

    mask = build_mask(fitted)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(mask.tobytes())

    notes = {
        "source": str(args.frames),
        "steady_from": args.start,
        "steady_to": args.end,
        "fitted_frames": len(fitted),
        "holdout_frames": len(holdout),
        "evidence_bytes": int(mask.size - mask.sum()),
        "self_check": residual_floor(mask, fitted[:6]),
    }
    if len(holdout) >= 2:
        notes["noise_floor"] = residual_floor(mask, holdout)
    sidecar_path(args.output).write_text(json.dumps(notes, indent=2),
                                         encoding="utf-8")

    print(f"# mask from {len(fitted)} frames of {args.frames} "
          f"(t >= {args.start:g})  ->  {args.output}")
    for line in describe(mask, notes):
        print("  " + line)
    print("  self check        %d bytes over the fitted frames (must be 0)"
          % notes["self_check"])
    return 0 if notes["self_check"] == 0 else 1


def report_pair(before: Path, after: Path, mask: np.ndarray | None,
                name: str, look: Path | None, zoom: int,
                gap: float | None = None, raw_gap: float | None = None,
                held: float | None = None,
                change_at: float | None = None,
                at: float | None = None,
                skipped: float | None = None,
                settle: float | None = None,
                busy: int = 0,
                baseline: tuple[int, int] | None = None,
                arrival: Arrival | None = None) -> tuple[int, str]:
    """Print one row, and say whether it is attributable.

    `gap` is the span the delta could have arisen in, `raw_gap` the same span
    measured from file timestamps alone.  When they differ the index narrowed
    it, and the row says so -- a number that only became attributable because
    of the index must not look like one that always was.

    `skipped` is the moment a change was passed over because `--settle` was
    longer than the display took to answer.  That is the one failure here that
    produces a plausible wrong number rather than a refusal, so it is named on
    the line and the row is not counted.

    `busy` and `baseline` are the two readings that come from the run itself
    rather than from a control run, and they exist because r113 produced three
    attributions that a control run could not contradict -- it had never been
    in the state they were measured in.  Returns the delta and a verdict:
    `""` for a row that counts, otherwise why it does not.
    """
    changed = stable_delta(load(before), load(after), mask)
    delta = int(changed.sum())
    box = bounding_box(changed)
    narrowed = (gap is not None and raw_gap is not None
                and raw_gap - gap > 0.05)
    note = ""
    verdict = ""
    too_wide = gap is not None and gap > MAX_WINDOW_SECONDS
    below = (baseline is not None and baseline[1] > 0 and delta <= baseline[0])
    # The index brackets the change in (held, change_at].  When that bracket
    # starts before the input, part of it is a stretch in which the change
    # could have happened *before* the thing it is being blamed on.
    #
    # **How much of it decides, not whether any of it does.**  The first version
    # of this refused whenever `held < at`, and that was too blunt by exactly
    # the sampler's own tick: the display answers in 0.7-0.8 s (r133, r134) and
    # a tick is 1.05 s, so a genuine fast answer *always* has its bracket open
    # a fraction of a second before the input.  It threw out r134's 83 671-byte
    # row, which the trace shows was dispatched.
    #
    # So the test is which side the bracket mostly lies on.  r113's 18.4:
    # 1.0 s before the input against 0.0 s after -- refused, and rightly.
    # r134's 19.3-i: 0.2 s before against 0.8 s after -- kept.
    ahead = (at - held) if (held is not None and at is not None) else 0.0
    behind = (change_at - at) if (change_at is not None
                                  and at is not None) else 0.0
    precedes = (held is not None and at is not None and change_at is not None
                and ahead > 0 and ahead > behind)
    # Arrival outranks every guard built on the picture, because it answers a
    # question the picture cannot be asked: did this input reach the machine at
    # all?  r134's 172 807-byte row passed all three pixel guards and was the
    # boot animation's repaint, inherited by a press that landed at its end.
    #
    # **Which arrival is demanded is decided per window, not per report.**  A
    # press has to reach the key dispatcher; a rotary cannot, and has to have
    # moved its own destination word by the amount the transcript sent.
    if arrival is not None and arrival.verdict:
        verdict = arrival.verdict
        note = "  " + arrival.note
    elif precedes:
        verdict = "precedes"
        note = ("  CHANGE NOT PROVEN AFTER THE INPUT -- the change is bracketed "
                "in (t%.1f, t%.1f] and %.1f s of that lies before the input "
                "against %.1f s after it" % (held, change_at, ahead, behind))
    elif skipped is not None:
        verdict = "settle"
        note = ("  SETTLE SKIPPED the answer at t%.1f -- --settle %g s is "
                "longer than the display took; lower it" % (skipped, settle))
    elif too_wide:
        verdict = "wide"
        note = "  WINDOW TOO WIDE (%.1f s%s) -- report as unattributed" % (
            gap, ", narrowed from %.1f s by the index" % raw_gap
            if narrowed else "")
    elif busy >= BUSY_CHANGES:
        verdict = "busy"
        note = ("  SCREEN BUSY -- %d changes in the %g s before the input, so "
                "this is what the display did next, not what the input did"
                % (busy, MAX_WINDOW_SECONDS))
    elif below:
        verdict = "baseline"
        note = ("  BELOW SELF-BASELINE -- %d B over %d input-free window(s) of "
                "this run" % (baseline[0], baseline[1]))
    elif narrowed:
        note = "  window %.1f s, narrowed from %.1f s by the index" % (gap,
                                                                       raw_gap)
    print("%-10s %7d  %s -> %s%s"
          % (name, delta, before.name, after.name, note))
    # With an index the moment of the change is known, so it is printed on
    # every compared row rather than only on the narrowed ones.  How long the
    # display took to answer is a measurement the next plan needs, and r096
    # left exactly one sample of it because nothing was reporting it.
    if held is not None and change_at is not None and at is not None:
        still = ("the earlier frame was still on screen at t%.1f, so " % held
                 if narrowed else "")
        print("           %14s %sthe change is at t%.1f, %.1f s after the input"
              % ("", still, change_at, change_at - at))
    if box:
        print("           %14s box %d,%d..%d,%d (%dx%d)"
              % ("", box[0], box[1], box[2], box[3],
                 box[2] - box[0], box[3] - box[1]))
    if baseline is not None and baseline[1] > 0:
        print("           %14s self-baseline %d B over %d input-free window(s) "
              "of this run" % ("", baseline[0], baseline[1]))
    if arrival is not None and arrival.line:
        # The offset between the two clocks is printed rather than assumed
        # away: if every dispatched row shows the same sign and size, that is
        # the skew, and --trace-shift is the place to put it.
        print("           %14s %s" % ("", arrival.line))
    if look is not None:
        for path in write_look(look, name, before, after, changed, zoom):
            print("           %14s %s" % ("", path))
    return delta, verdict


def command_pair(args: argparse.Namespace) -> int:
    mask = None
    if args.mask:
        mask, notes = read_mask(args.mask)
        for line in describe(mask, notes):
            print("# " + line)
    report_pair(args.before, args.after, mask, args.name or "pair", args.look,
                args.zoom)
    return 0


def command_windows(args: argparse.Namespace) -> int:
    try:
        windows = parse_windows(args.window)
    except ValueError as error:
        print(f"frame_delta: {error}", file=sys.stderr)
        return 1
    frames = frames_in(args.frames)
    if not frames:
        print(f"frame_delta: no t*.ppm frames in {args.frames}",
              file=sys.stderr)
        return 1
    mask = None
    if args.mask:
        mask, notes = read_mask(args.mask)
        for line in describe(mask, notes):
            print("# " + line)

    shift = args.shift
    if args.align:
        try:
            shift, spread = alignment_shift(frames, args.align)
        except (OSError, ValueError) as error:
            print(f"frame_delta: cannot align against {args.align}: {error}",
                  file=sys.stderr)
            return 1
        print("# alignment       session clock is %+.1f s from the sampler's, "
              "measured from %s" % (shift, args.align.name))
        if spread > 5.0:
            print("#   WARNING: the frames' start times disagree by %.1f s. "
                  "Copied files lose their modification times; align against "
                  "the sampler's own directory, or pass --shift." % spread)
    elif shift:
        print("# alignment       %+.1f s, given rather than measured" % shift)
    else:
        print("# alignment       none.  The session's clock starts when the "
              "control channel opens and the sampler's when boot_vm does; in "
              "r091 they differed by 45 s, which at 25 s spacing moves every "
              "window nearly two places.  Pass --align <transcript>.")

    dispatches: list[tuple[float, int]] | None = None
    if args.trace:
        try:
            dispatches = [(when + args.trace_shift, code)
                          for when, code in read_key_dispatches(args.trace)]
        except OSError as error:
            print(f"frame_delta: cannot read {args.trace}: {error}",
                  file=sys.stderr)
            return 1
        if dispatches:
            print("# key dispatches   %d in %s, at %s"
                  % (len(dispatches), args.trace.name,
                     ", ".join("t%.1f" % when for when, _ in dispatches[:12])))
        else:
            print("# key dispatches   **none** in %s.  No press reached the "
                  "GUI's key dispatcher in this run, so no window in it can be "
                  "an attribution and no zero in it is a statement about a key."
                  % args.trace.name)
    else:
        print("# key dispatches   no trace given.  A press that never reached "
              "the dispatcher cannot be told from one that did nothing, and a "
              "press landing at the end of the boot animation inherits its "
              "repaint -- r134's 172 807-byte row passed every pixel guard "
              "here and was exactly that.  Pass --trace <step-trace.txt>.")

    # **The transcript decides what each window has to prove.**  It is the same
    # file --align measures the clock from, and it already carries the verb per
    # entry, so no naming convention on the window label is invented here.
    # Without it every window is judged as a press, which is what this file did
    # before and is the strict side of the choice.
    session: list[tuple[float, float, str, str]] | None = None
    expectations: dict[float, tuple[str, int, int]] = {}
    if args.align:
        try:
            session = read_session(args.align)
            expectations = analog_expectations(session)
        except (OSError, ValueError) as error:
            print("# window verbs     NOT READ from %s: %s.  Every window will "
                  "be judged as a press." % (args.align.name, error))
            session = None

    counters: dict[int, list[Movement]] | None = None
    if args.counter:
        try:
            raw = read_counter_writes(args.counter)
        except OSError as error:
            # **A missing stream must not kill the report.**  This is the
            # `filter-trace` lesson one file over: an evaluation that dies on
            # its own input writes one line into windows.txt and exits 1, and
            # every row of the run -- including the forty press rows that had
            # nothing to do with the counter -- is lost with no indication why.
            # Rehearsed on r150's artefacts, that cost the whole point-3 stage.
            # So the analogue rows say NOT MEASURED, which is true, and every
            # other row of the run is still scored.
            print("# panel counters   NOT MEASURED -- cannot read %s: %s.  "
                  "Every analogue window below is refused for that, and no "
                  "press row is affected." % (args.counter, error))
            raw = None
        if raw is not None:
            counters = {address: [item._replace(
                                      at=None if item.at is None
                                      else item.at + args.counter_shift)
                                  for item in items]
                        for address, items in raw.items()}
            if counters:
                print("# panel counters   %s in %s"
                      % (", ".join("%#010x %d write(s)" % (address, len(items))
                                   for address, items
                                   in sorted(counters.items())),
                         args.counter.name))
            else:
                print("# panel counters   **none** in %s.  No watched word "
                      "ever changed, so no analogue input in this run reached "
                      "MAIN and no zero in one is a statement about a field."
                      % args.counter.name)
    elif expectations:
        print("# panel counters   no counter stream given, and the transcript "
              "contains %d analogue command(s).  A rotary cannot reach the key "
              "dispatcher, so those windows are NOT MEASURED rather than "
              "refused as undispatched.  Pass --counter <the run's stderr, with "
              "CDJ_WATCH set>." % len(expectations))

    ticks = read_index(args.frames)
    if ticks is None:
        print("# index            none.  The sampler writes a file only when "
              "the frame changed, so a window with no file is either a proven "
              "no-op or a stretch nobody recorded -- and without index.tsv "
              "those cannot be told apart.  Runs before fbde2eb have none.")
    else:
        counts: dict[str, int] = {}
        for _, status in ticks:
            counts[status.split(":")[0]] = counts.get(status.split(":")[0], 0) + 1
        print("# index            %d ticks, t%.1f..t%.1f (%s)"
              % (len(ticks), ticks[0][0], ticks[-1][0],
                 ", ".join("%s %d" % item for item in sorted(counts.items()))))

    print(f"# {len(frames)} frames, t{frames[0][0]:g}..t{frames[-1][0]:g}; "
          f"settle {args.settle:g} s")
    print("%-10s %7s  %s" % ("input", "delta", "pair"))
    unmeasured = 0
    unattributed = 0
    mistimed = 0
    busy_rows = 0
    unproven = 0
    undispatched = 0
    # The analogue refusals are counted apart from the press one, and apart
    # from each other, because they are three different next actions: measure
    # it, send it again, or find out what moved the counter instead.
    unarrived: dict[str, int] = {}
    # Every scored input, on the frame sampler's clock.  The gaps between them
    # are where the run controls itself; see baseline_delta.
    inputs = [when + shift for when, _ in windows]
    cache: dict[Path, np.ndarray] = {}
    for session_at, name in windows:
        at = session_at + shift
        # Ten seconds is the attribution limit the manifest's method uses, so
        # it is also the stretch across which "nothing moved" has to hold for a
        # no-op to be proven.
        window_end = at + MAX_WINDOW_SECONDS
        observed, why = (observed_between(ticks, at, window_end)
                         if ticks is not None else (None, ""))
        changed = (ticks is not None
                   and any(status == "new" and at < when <= window_end
                           for when, status in ticks))

        # Which arrival proof this window owes.  The verb is the transcript's,
        # read at the window's own time on the session's own clock; a window
        # with no entry, or one whose verb this file does not know, gets the
        # press rule, which is the one that cannot be talked into a pass.
        entry = (window_command(session, session_at)
                 if session is not None else None)
        kind, field, expected = "press", None, 0
        if entry is not None:
            verb = entry[1].split()[0] if entry[1].split() else ""
            if verb in ANALOG_VERBS and entry[0] in expectations:
                kind, field, expected = "analog", *expectations[entry[0]][1:]

        if kind == "analog":
            arrival = judge_analog(counters, field, expected, at, window_end)
        elif dispatches is None:
            arrival = None                  # nothing can judge it; as before
        else:
            arrival = judge_press(nearest_dispatch(dispatches, at), at)

        if observed and not changed:
            # The screen was watched across the whole window and never once
            # changed.  That is stronger evidence than any byte compare, and it
            # is what GOAL.md means by a no-op that is proven rather than
            # assumed -- *provided the key arrived*.  Without a dispatch the
            # zero is a fact about the event path, not about the key, and
            # recording it as "this key does nothing" is the same conflation
            # index.tsv was built to end, one level up.
            watched = sum(1 for when, _ in ticks if at <= when <= window_end)
            if arrival is not None and arrival.verdict:
                print("%-10s %7s  %s at t%.1f -- %d ticks and the frame never "
                      "changed, but %s, so this is not a no-op of the %s"
                      % (name, "-", arrival.note.split(" --")[0], at, watched,
                         "the key never reached the dispatcher"
                         if arrival.kind == "press"
                         else "the field never reached MAIN",
                         "key" if arrival.kind == "press" else "field"))
                if arrival.kind == "press":
                    undispatched += 1
                else:
                    unarrived[arrival.verdict] = unarrived.get(
                        arrival.verdict, 0) + 1
                continue
            print("%-10s %7d  no-op, proven: %d ticks over t%.1f..t%.1f, the "
                  "frame never changed" % (name, 0, watched, at, window_end))
            if arrival is not None and arrival.line:
                print("           %14s %s" % ("", arrival.line))
            continue

        pair = pick_pair(frames, at, args.settle)
        if pair is None:
            if ticks is not None and not observed:
                print("%-10s %7s  NOT MEASURED at t%.1f -- %s"
                      % (name, "-", at, why))
            elif ticks is None:
                print("%-10s %7s  no frame on one side of t%.1f (no index: a "
                      "no-op and a hole look alike)" % (name, "-", at))
            else:
                print("%-10s %7s  NOT MEASURED at t%.1f -- the frame changed in "
                      "the window but no sample survives it" % (name, "-", at))
            unmeasured += 1
            continue
        (before_at, before), (after_at, after) = pair
        # The window is counted in content changes, not file timestamps: a
        # stretch of `same` ticks proves the earlier frame was still standing,
        # so the change is pinned to the `new` that ends it.
        span_start, held = attribution_span(ticks, before_at, after_at, at)
        # `--settle` is a floor on how late the second frame may be taken, and
        # the sampler writes nothing while the screen stands.  So a settle
        # longer than the display's answer skips the answering frame entirely
        # and hands the row the *next* input's repaint: a plausible number over
        # an impossible window.  Measured, not feared -- see
        # tests/test_frame_delta.py.
        skipped = passed_over(ticks, at, args.settle, after_at)
        busy = self_motion_before(ticks, at) if ticks is not None else 0
        baseline = None
        if args.baseline and len(inputs) > 1:
            worst, used, _ = baseline_delta(frames, ticks, mask, at, inputs,
                                            args.settle, args.baseline, cache)
            baseline = (worst, used)
        _, verdict = report_pair(
            before, after, mask, name, args.look, args.zoom,
            gap=after_at - span_start, raw_gap=after_at - before_at,
            held=held if ticks is not None else None,
            change_at=after_at, at=at, skipped=skipped, settle=args.settle,
            busy=busy, baseline=baseline, arrival=arrival)
        if verdict.startswith("analog-"):
            unarrived[verdict] = unarrived.get(verdict, 0) + 1
        elif verdict == "undispatched":
            undispatched += 1
        elif verdict == "precedes":
            unattributed += 1
        elif verdict == "settle":
            mistimed += 1
        elif verdict == "wide":
            unattributed += 1
        elif verdict == "busy":
            busy_rows += 1
        elif verdict == "baseline":
            unproven += 1
    if unmeasured:
        print("# %d window(s) NOT MEASURED -- those rows are not results and "
              "must not be reported as no-ops" % unmeasured)
    if unattributed:
        print("# %d window(s) WINDOW TOO WIDE -- a delta that cannot be tied "
              "to its input is not a result either" % unattributed)
    if mistimed:
        print("# %d window(s) SETTLE SKIPPED -- the whole run is suspect, not "
              "just these rows: --settle %g s is longer than this display "
              "takes to answer, so every row was read past its own evidence"
              % (mistimed, args.settle))
    if busy_rows:
        print("# %d window(s) SCREEN BUSY -- the display was already repainting "
              "on its own before those inputs, so a byte compare there measures "
              "the repainting.  Move the plan past the busy stretch (the index "
              "says where it ends) or mask it with a control run that reached "
              "the same state" % busy_rows)
    if unproven:
        print("# %d window(s) BELOW SELF-BASELINE -- input-free windows of this "
              "same run reach that delta, so those rows are not evidence "
              "whatever the mask says" % unproven)
    if undispatched:
        print("# %d window(s) NO KEY DISPATCHED -- the trace says the key never "
              "reached the GUI's dispatcher there.  A delta is then something "
              "else's repaint (r134: a press at the end of the boot animation "
              "inherited it, 172 807 bytes, past all three pixel guards) and a "
              "zero is a fact about the event path, not about the key"
              % undispatched)
    for verdict, count in sorted(unarrived.items()):
        print("# %d window(s) %s" % (count, ANALOG_SUMMARY[verdict]))
    return 1 if (unmeasured or unattributed or mistimed or busy_rows
                 or unproven or undispatched or unarrived) else 0


# What each analogue refusal means for the next run, spelled out where the
# tallies are.  `NO KEY DISPATCHED` gets one of these lines and it took eleven
# runs to learn to read it; three refusals that all printed the same sentence
# would cost the same again.
ANALOG_SUMMARY = {
    "analog-unmeasured":
        "ANALOG NOT MEASURED -- an analogue field cannot reach the GUI's key "
        "dispatcher, so its arrival is only visible in MAIN's own status "
        "block.  Re-run with CDJ_WATCH on the field's destination and pass "
        "--counter; until then those rows are neither results nor no-ops",
    "analog-absent":
        "NO ANALOG ARRIVAL -- the destination word never moved in those "
        "windows.  A delta there is something else's repaint, exactly as an "
        "undispatched press's is, and a zero says nothing about the field",
    "analog-amount":
        "ANALOG AMOUNT MISMATCH -- the destination moved by an amount the "
        "transcript did not send, and not by that amount modulo the "
        "destination's own width either.  That is not a weaker arrival, it is a "
        "different one: something else wrote the field, or the window is "
        "sitting over two commands",
    "analog-unmeasurable":
        "ANALOG AMOUNT UNOBSERVABLE -- the transcript sent a whole multiple of "
        "the destination's width, so it ends where it started and an arrival "
        "cannot be told from none.  This is a fault in the plan, not in the "
        "run: change the amount",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("mask", help="animation mask from a control run")
    build.add_argument("frames", type=Path)
    build.add_argument("output", type=Path)
    build.add_argument("--from", dest="start", type=float, required=True,
                       metavar="SECONDS",
                       help="start of the steady phase.  Required, and not a "
                            "formality: a mask built from a whole run marks "
                            "half the frame as animated and erases the fields "
                            "that display state")
    build.add_argument("--to", dest="end", type=float, default=None,
                       metavar="SECONDS")
    build.add_argument("--no-holdout", dest="holdout", action="store_false",
                       help="fit on every steady frame, leaving no noise floor")
    build.set_defaults(func=command_mask)

    pair = sub.add_parser("pair", help="one before/after pair")
    pair.add_argument("before", type=Path)
    pair.add_argument("after", type=Path)
    pair.add_argument("--mask", type=Path, default=None)
    pair.add_argument("--name", default=None)
    pair.set_defaults(func=command_pair)

    windows = sub.add_parser(
        "windows", help="one window per input, e.g. 150:18.1 175:18.2")
    windows.add_argument("frames", type=Path)
    windows.add_argument("window", nargs="+", metavar="SECONDS:NAME")
    windows.add_argument("--mask", type=Path, default=None)
    windows.add_argument("--settle", type=float,
                         default=DEFAULT_SETTLE_SECONDS,
                         help="how long after the input the second frame must "
                              "be taken (default 6 s)")
    windows.add_argument("--align", type=Path, default=None,
                         metavar="TRANSCRIPT",
                         help="measure the offset between the session's clock "
                              "and the frame sampler's from a "
                              "panel_control session --transcript file, "
                              "instead of assuming there is none.  In r091 "
                              "they differed by 45 s")
    windows.add_argument("--trace", type=Path, default=None,
                         metavar="STEP_TRACE",
                         help="BFIN_STEP_TRACE output.  A row whose input has "
                              "no `key dispatcher` line within %g s is refused: "
                              "a delta there belongs to something else's "
                              "repaint and a zero says nothing about the key.  "
                              "The picture cannot answer this -- r134's "
                              "172 807-byte row passed every pixel guard and "
                              "was the boot animation" % TRACE_TOLERANCE)
    windows.add_argument("--trace-shift", type=float, default=0.0,
                         metavar="SECONDS",
                         help="add this to every trace timestamp.  The trace "
                              "clock is the simulator's and the frame names are "
                              "the sampler's; r133 and r134 differed by 2.9 s "
                              "and 3.6 s, which the tolerance absorbs.  Every "
                              "dispatched row prints its own offset, so a "
                              "larger skew is visible rather than silent")
    windows.add_argument("--counter", type=Path, default=None,
                         metavar="WATCH_STREAM",
                         help="the run's stderr with CDJ_WATCH set on the "
                              "analogue destinations (0x04fe2a20..0x04fe2a44; "
                              "one page, so watching all eight costs what "
                              "watching one costs).  This is the arrival proof "
                              "for a rotary, which cannot reach the key "
                              "dispatcher and was therefore refused as "
                              "`NO KEY DISPATCHED` until 2026-08-07 -- all nine "
                              "analogue windows of r160.  A window is scored "
                              "only if its destination moved by exactly the "
                              "amount the transcript sent")
    windows.add_argument("--counter-shift", type=float, default=0.0,
                         metavar="SECONDS",
                         help="add this to every counter timestamp.  The watch "
                              "stream's clock is QEMU's virtual one and starts "
                              "with the machine, the same moment the frame "
                              "sampler does, so it needs no shift in the normal "
                              "case -- this is the escape hatch for when it does")
    windows.add_argument("--baseline", type=int, default=BASELINE_SAMPLES,
                         metavar="N",
                         help="take a control window in each of the N gaps "
                              "between inputs nearest each row, and refuse a "
                              "row that does not beat the worst of them.  0 "
                              "turns it off.  This is the control the run "
                              "carries with it; r113 needed it because its "
                              "control run had never been in the state its "
                              "rows were measured in")
    windows.add_argument("--shift", type=float, default=0.0, metavar="SECONDS",
                         help="add this to every window time.  The manual "
                              "fallback for when --align cannot be used, e.g. "
                              "frames copied without their modification times")
    windows.set_defaults(func=command_windows)

    for which in (pair, windows):
        which.add_argument("--look", type=Path, default=None, metavar="DIR",
                           help="write the changed region out magnified, and a "
                                "frame with it ringed.  A count says something "
                                "moved; only the picture says what")
        which.add_argument("--zoom", type=int, default=4)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError) as error:
        print(f"frame_delta: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
