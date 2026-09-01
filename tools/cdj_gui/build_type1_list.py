"""Build the internal type 1 payload — the GUI's browse/library list.

Decompiled from the handler ``FUN_00b7d61c`` (Ghidra + the Blackfin processor
module).  Type 1 is **not** a record of
fixed title/artist/album fields — that earlier conclusion was an artefact of
bisecting with a fixed-length identity ramp.  It is a 9-word header followed by
up to ten variable-length UTF-16 entries::

    word 1..2  CONCAT22(w1, w2) -> 0x4b53d8   total item count (w1 = HIGH half)
    word 3..4  CONCAT22(w3, w4) -> 0x4b53dc   cursor / first visible index
    word 5                      -> 0x4b53d4
    word 6                      -> 0x4b53e0   highlighted row, clamped to <= 5
    word 7     bits 0..6  -> 0x4b53d0    bit 7      -> 0x4b53e3
               bits 8..11 -> 0x4b53d2    bits 12..15-> 0x4b7c5f (clamped to 8)
    word 8                                    number of entries that follow
    word 9+                                   the entries

Each entry is ``[attr, attr2, length_in_chars, utf16...]`` and the next one
starts at ``offset + 3 + length``; an entry whose length exceeds 0x100 is
dropped by the handler.

**What the entries actually drive** (verified on screen 2026-08-04, widget trace
plus an A/B frame — the earlier "entry 0 is the title slot" wording was a guess):

* entry **0** -> ``0x4b4fc4``, the text of the **source header** widget
  ``0x59ecf8`` across the top of the browse area.  This is where ``USB`` goes.
* entries **1..6** -> ``0x4b53f2`` stride ``0x206``, the six rows of the **left
  browse column** (widgets ``0x59e604``..``0x59eb90``).  The reference photo's
  ``【ARTIST】【TRACK】【ALBUM】【PLAYLIST】【HISTORY】【FOLDER】`` are ordinary
  MAIN-supplied text here, not firmware strings picked by id -- although the
  firmware does carry the same set as string ids 115..139, which is where the
  bracket glyphs come from.  Those brackets are **U+FFFA / U+FFFB**, not
  U+3010/U+3011; the font maps them to 【 】.  Copy them out of the string table
  rather than typing them.
* the right pane (widgets ``0x59d85c``..``0x59dc98``) reads a *second* buffer at
  ``0x4b6628``, which this record does not fill directly.

How many rows appear is ``MIN(total - cursor, 6)`` -- every filler
(``FUN_00bac24a``, ``FUN_00bab78e``, ``FUN_00ba5df0``, ``FUN_00bb29f6``) computes
it the same way from ``0x4b53d8`` and ``0x4b53dc``, and gives every row past that
count string id 181 (empty).  So ``total`` must exceed ``cursor`` or the list
renders blank however many entries are transmitted -- that, and not a layout
fault, is why the rows were empty for so long.

The initial word offset is **9**.  That constant is invisible in the decompiler
output because the loop packs the entry index into the low half and the word
offset into the high half of a single 32-bit register; it is read from the
disassembly instead (``[FP+0x10] = 9`` at ``0xb7d748``).
"""

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import argparse
from pathlib import Path

from .main_packet import build_command_payload

TYPE1_COMMAND = 0x11
FIRST_ENTRY_WORD = 9
MAX_ENTRIES = 10
MAX_ENTRY_CHARS = 0x100
MAX_HIGHLIGHT = 5

# The UI font draws U+FFFA/U+FFFB as 【 】.  Read straight out of the firmware
# string table (ids 115..139, e.g. 116 = FFFA 'ARTIST' FFFB), because the
# lookalike CJK brackets U+3010/U+3011 render as nothing.
BRACKET_OPEN = "￺"
BRACKET_CLOSE = "￻"

# The browse root as the reference photo shows it.
BROWSE_ROOT = ("ARTIST", "TRACK", "ALBUM", "PLAYLIST", "HISTORY", "FOLDER")


def category(name: str) -> str:
    """Return *name* wrapped in the brackets the browse list uses."""

    return f"{BRACKET_OPEN}{name}{BRACKET_CLOSE}"


def build_type1_list(
    rows: list[str],
    *,
    total: int | None = None,
    cursor: int = 0,
    highlight: int = 0,
    attrs: list[tuple[int, int]] | None = None,
    word_count: int = 256,
) -> bytes:
    """Return a complete command-0x11 payload listing *rows*.

    *total* defaults to ``len(rows)``; it is the library's full item count and
    may legitimately exceed the number of entries actually transmitted, which is
    how the GUI knows the list scrolls.  Note the fillers show
    ``MIN(total - cursor, 6)`` rows, so ``total`` must exceed ``cursor``.

    *attrs* gives the two per-entry attribute words ``(attr, attr2)`` that
    precede each length; the default ``(0x0100, 0)`` is what the browse list was
    first made to render with.  ``attr2`` is the value the narrow numeric column
    prints (``FUN_00bab78e`` reads it at ``0x4b53ee`` with the same 0x206
    stride), so it is not free-form padding.
    """

    if len(rows) > MAX_ENTRIES:
        raise ValueError(f"the handler reads at most {MAX_ENTRIES} entries")
    for text in rows:
        if len(text) > MAX_ENTRY_CHARS:
            raise ValueError("an entry longer than 0x100 chars is dropped")
    if not 0 <= highlight <= MAX_HIGHLIGHT:
        raise ValueError(f"highlight is clamped to 0..{MAX_HIGHLIGHT}")
    if total is None:
        total = len(rows)
    if attrs is None:
        attrs = [(0x0100, 0)] * len(rows)
    if len(attrs) != len(rows):
        raise ValueError("attrs needs one (attr, attr2) pair per row")

    words = {
        1: (total >> 16) & 0xFFFF,
        2: total & 0xFFFF,
        3: (cursor >> 16) & 0xFFFF,
        4: cursor & 0xFFFF,
        5: 1,
        6: highlight,
        7: 0x0101,
        8: len(rows),
    }

    offset = FIRST_ENTRY_WORD
    for text, (attr, attr2) in zip(rows, attrs):
        words[offset] = attr & 0xFFFF
        words[offset + 1] = attr2 & 0xFFFF
        words[offset + 2] = len(text)
        for index, char in enumerate(text):
            words[offset + 3 + index] = ord(char)
        offset += 3 + len(text)

    # Leave room for the trailing CRC word build_command_payload appends.
    if offset >= word_count - 1:
        raise ValueError(
            f"entries need {offset + 1} words; raise word_count above {word_count}"
        )
    return build_command_payload(
        TYPE1_COMMAND, word_count=word_count, payload_words=words
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("rows", nargs="+", help="list entries, entry 0 is the title")
    parser.add_argument(
        "--total",
        type=lambda value: int(value, 0),
        default=None,
        help="full library item count (default: the number of rows given)",
    )
    parser.add_argument("--cursor", type=lambda value: int(value, 0), default=0)
    parser.add_argument(
        "--highlight",
        type=lambda value: int(value, 0),
        default=0,
        help=f"highlighted row, 0..{MAX_HIGHLIGHT}",
    )
    parser.add_argument("--word-count", type=lambda value: int(value, 0), default=256)
    args = parser.parse_args()

    payload = build_type1_list(
        args.rows,
        total=args.total,
        cursor=args.cursor,
        highlight=args.highlight,
        word_count=args.word_count,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    print(f"wrote {args.output}; {len(args.rows)} entries, {len(payload)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
