"""Build the 64-byte MAIN status record from named fields.

The status record is MAIN's heartbeat: the GUI reads one on every poll and it
carries the whole machine state.  Decompiled from the parser ``FUN_00b7dec8``
(called from ``0xb7f63a``) with Ghidra plus a Blackfin objdump index.

The record lands at **``0xf00000``** — payloads land at ``0xf00040``, which is a
recurring source of off-by-0x20 mistakes — so word N is at ``0xf00000 + 2N``.
Parsed fields land in the machine-state block based at ``0x4b4398``, which is
what practically every widget reads.

Why this module exists: the simulator's MAIN peer harvests a status template out
of the captured stream and then repeats it forever.  Decoded, that template says
the selected media source is **LINK** and that *no* data request is enabled, so
the GUI is starved of everything the capture did not happen to contain.  A
standalone player that has not been given a medium needs a different record, and
inventing one by hand is what this builds.

Word 13's low byte selects a protocol mode: values 2..5 take an entirely
different branch in the parser (it only fills ``0x4b94e8..0x4b94eb`` and
``0x4b95dc..0x4b95e0``).  ``MODE_NORMAL`` is the branch documented below.
"""

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from .main_packet import firmware_crc

RECORD_WORDS = 32
RECORD_BYTES = RECORD_WORDS * 2

MODE_NORMAL = 0

# Word 18 bits 2:0 -> 0x4b43d0, the selected media source.
SOURCE_NONE = 0
SOURCE_DISC = 1
SOURCE_SD = 2
SOURCE_USB = 3
SOURCE_LINK = 4
SOURCE_NAMES = {
    SOURCE_NONE: "none",
    SOURCE_DISC: "DISC",
    SOURCE_SD: "SD",
    SOURCE_USB: "USB",
    SOURCE_LINK: "LINK",
}

# Word 26, four 3-bit fields -> 0x4b439c..0x4b439f, one per source.  The values
# the screen router FUN_00b9b706 distinguishes:
#
#   0 -> screen 0                       (nothing usable mounted)
#   1 -> screen 5, library type 0
#   2 -> screen 5, library type 2
#
# Values above 2 are handled by no branch at all; the capture carries 4 for
# DISC, which is one reason it parks.  Only 0..2 are understood, so only those
# are named here.
STATE_ABSENT = 0
STATE_MOUNTED = 1
STATE_MOUNTED_ALT = 2

# Word 18 bits 6..15: each bit sets a flag that makes the GUI request a data
# type.  Enumerated from the bit tests at 0xb7e1c0..0xb7e330; bits 0..5 are
# never tested and cannot gate anything.
REQUEST_BIT_6 = 1 << 6
REQUEST_BIT_7 = 1 << 7
REQUEST_STRING_TABLE = 1 << 10  # 0x4b43d1, drives internal type 8
REQUEST_WAVEFORM = 1 << 11  # 0x4b43c9, the only gate on internal type 4
REQUEST_BIT_12 = 1 << 12
REQUEST_BIT_13 = 1 << 13
REQUEST_BIT_14 = 1 << 14
REQUEST_BIT_15 = 1 << 15

# Word 11 (-> 0x4b43c2) is the player row's element show/hide bitfield.  The
# dispatcher FUN_00b659d8 tests one bit at a time and calls a per-element
# ``show(1)`` / ``hide(0)`` function; each of those passes a fixed resource id
# to FUN_00b877ec, so the bit-to-label mapping is readable statically and was
# then confirmed on screen by A/B diff.  The captured idle value lights only
# three of them, which is why the strip looked half-finished.
STRIP_MEMORY = 1 << 7  # resource 108 "MEMORY" at x=1
STRIP_CUE = 1 << 8  # resource 102 "CUE" at x=1
STRIP_A_CUE = 1 << 12  # resource 101 "A.CUE" at x=93
STRIP_MT = 1 << 14  # resource 106 "MT" at x=368
STRIP_TRACK = 1 << 6  # resource 115 "TRACK" at x=47
STRIP_REMAIN = 1 << 5  # resource 113 "REMAIN" at x=93
STRIP_TEMPO = 1 << 13  # resource 116 "TEMPO" at x=325
STRIP_BPM = 1 << 9  # the BPM box at x=414 (resource chosen from word 12)
# Setting this bit draws the *red* (active) QUANTIZE, resource 119.  The grey
# (inactive) one the reference photo shows is resource 120 and comes from the
# hide path instead -- see WORD25_QUANTIZE.
STRIP_QUANTIZE_ACTIVE = 1 << 10

# Word 25 bit 6 -> 0x4b43fd.  FUN_00b85ab8 consults it whenever QUANTIZE is not
# being force-shown, and it is what actually makes the label appear at all: with
# the bit clear the element is hidden no matter what word 11 says.
WORD25_QUANTIZE = 1 << 6

# Words whose meaning is still undecoded but whose captured idle values are
# known to boot cleanly.  Overriding them is a deliberate experiment, so they
# are defaults rather than constants baked into the packer.
CAPTURED_IDLE = {5: 0xFFFF, 10: 0xFFFF, 11: 0x2240, 12: 0x8080, 24: 0x01FF, 25: 0x4000, 28: 0x001E}

# Word 29 == 1 announces a payload and word 30 carries its length; a plain
# status record must leave both clear or the GUI will try to read a payload that
# is not coming.
ANNOUNCE_WORD = 29
ANNOUNCE_LENGTH_WORD = 30
CRC_WORD = 31


def build_status_record(
    *,
    source: int = SOURCE_DISC,
    states: tuple[int, int, int, int] = (STATE_ABSENT,) * 4,
    colours: tuple[int, int, int, int] = (0, 0, 0, 0),
    requests: int = 0,
    mode: int = MODE_NORMAL,
    word19: int = 0,
    overrides: dict[int, int] | None = None,
) -> bytes:
    """Return one 64-byte status record.

    *states* and *colours* are ``(disc, sd, usb, link)``.  *states* are the
    3-bit per-source states from word 26; *colours* are the word-3 nibbles,
    which select the source's palette (``FUN_00b9b554`` packs entry N of the
    chosen palette to RGB555) — they are **not** message indices.

    *requests* is OR-ed into word 18 above bit 5 and must not disturb the source
    field.
    """

    if not 0 <= source <= 7:
        raise ValueError("source must fit in word 18 bits 2:0")
    if requests & 0x3F:
        raise ValueError("requests must not touch word 18 bits 0..5")
    for value in states:
        if not 0 <= value <= 7:
            raise ValueError("a source state must fit in 3 bits")
    for value in colours:
        if not 0 <= value <= 0xF:
            raise ValueError("a source colour must fit in a nibble")

    words = dict(CAPTURED_IDLE)
    disc, sd, usb, link = states
    words[3] = (colours[0] << 12) | (colours[1] << 8) | (colours[2] << 4) | colours[3]
    words[13] = mode & 0xFF
    words[18] = (requests & 0xFFC0) | (source & 7)
    words[19] = word19 & 0xFFFF
    words[26] = (disc << 9) | (sd << 6) | (usb << 3) | link
    if overrides:
        for index, value in overrides.items():
            if not 0 <= index < CRC_WORD:
                raise ValueError("override index must leave the CRC word alone")
            words[index] = value & 0xFFFF

    if words.get(ANNOUNCE_WORD):
        raise ValueError("word 29 must stay clear; a plain record announces nothing")

    record = bytearray(RECORD_BYTES)
    for index, value in words.items():
        struct.pack_into("<H", record, index * 2, value & 0xFFFF)
    struct.pack_into("<H", record, CRC_WORD * 2, firmware_crc(record[: CRC_WORD * 2]))
    return bytes(record)


def build_standalone_stream(record: bytes, *, count: int = 4) -> bytes:
    """Frame *record* into a stream the simulator's MAIN peer can harvest.

    ``bfin_sport_peer_scan`` takes the **first non-announcing 64-byte record at a
    non-zero offset** as its template, so the stream needs a leading record it
    will skip.  Nothing else is included on purpose: an 872-byte record would be
    harvested as an overview waveform and a 256-byte one as a player state, and
    a player with no medium has neither.
    """

    if len(record) != RECORD_BYTES:
        raise ValueError("a status record is 64 bytes")
    records = [record] * max(2, count)
    return b"".join(struct.pack("<I", len(item)) + item for item in records)


def describe(record: bytes) -> str:
    words = struct.unpack("<32H", record)
    source = words[18] & 7
    state = words[26]
    colour = words[3]
    return "\n".join(
        [
            f"  word 13 mode      = {words[13] & 0xFF}",
            f"  word 18           = 0x{words[18]:04x}"
            f"  source={source} ({SOURCE_NAMES.get(source, '?')})"
            f"  requests=0x{words[18] & 0xFFC0:04x}",
            f"  word 26 states    = DISC={(state >> 9) & 7} SD={(state >> 6) & 7}"
            f" USB={(state >> 3) & 7} LINK={state & 7}",
            f"  word  3 colours   = DISC={(colour >> 12) & 0xF} SD={(colour >> 8) & 0xF}"
            f" USB={(colour >> 4) & 0xF} LINK={colour & 0xF}",
            f"  word 19           = 0x{words[19]:04x}",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--source",
        type=lambda value: int(value, 0),
        default=SOURCE_DISC,
        help="selected media source: 1=DISC 2=SD 3=USB 4=LINK (default: DISC)",
    )
    parser.add_argument(
        "--states",
        default="0,0,0,0",
        help="per-source state disc,sd,usb,link (default: all 0 = nothing mounted)",
    )
    parser.add_argument(
        "--colours", default="0,0,0,0", help="per-source palette nibble disc,sd,usb,link"
    )
    parser.add_argument(
        "--requests",
        type=lambda value: int(value, 0),
        default=0,
        help="request bits OR-ed into word 18 above bit 5",
    )
    parser.add_argument("--mode", type=lambda value: int(value, 0), default=MODE_NORMAL)
    parser.add_argument("--word19", type=lambda value: int(value, 0), default=0)
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="WORD=VALUE",
        help="override a raw record word; may be repeated",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="emit bare 64-byte records for BFIN_MAIN_PEER_STATUS instead of the "
             "length-prefixed stream --packet/BFIN_SPORT_RX_INPUT expects",
    )
    parser.add_argument("--count", type=int, default=4)
    args = parser.parse_args()

    def triple(text: str) -> tuple[int, int, int, int]:
        parts = [int(part, 0) for part in text.split(",")]
        if len(parts) != 4:
            parser.error("expected four comma-separated values (disc,sd,usb,link)")
        return tuple(parts)  # type: ignore[return-value]

    overrides = {}
    for assignment in args.set:
        name, _, value = assignment.partition("=")
        overrides[int(name, 0)] = int(value, 0)

    record = build_status_record(
        source=args.source,
        states=triple(args.states),
        colours=triple(args.colours),
        requests=args.requests,
        mode=args.mode,
        word19=args.word19,
        overrides=overrides,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Two consumers, two formats, and mixing them is silent.  ``--packet`` /
    # BFIN_SPORT_RX_INPUT wants each record behind a 4-byte length;
    # BFIN_MAIN_PEER_STATUS wants bare 64-byte records.  Feeding the
    # length-prefixed form to the peer shifts every field two words late, so the
    # GUI reads word 18 out of word 20 -- it sees zero and nothing complains.
    # That went unnoticed through a whole series of sweeps.
    if args.raw:
        args.output.write_bytes(record * max(1, args.count))
    else:
        args.output.write_bytes(build_standalone_stream(record, count=args.count))
    print(f"wrote {args.output} ({args.output.stat().st_size} bytes)")
    print(describe(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
