"""Build the internal type-5 payload: the marker table drawn over the waveform.

Reverse engineered from the handler at 0x00b7cd12 (2026-07-15).  Like every
other payload it lands at receive-buffer offset 0x40, so payload word N sits at
0xf00040 + 2N.  The handler reads:

    count = payload[1]                  ; clamped to 220, stored at 0x4b4400
    cursor = 2
    for i in range(count):
        kind = payload[cursor]          ; cursor += 1
        if kind == 0xff:                ; escape record, two more words
            [0x4b4c9c] = payload[cursor]        ; cursor += 1
            [0x4b4c9e] = payload[cursor] >> 8
            [0x4b4ca0] = payload[cursor] & 0xff ; cursor += 1
        else:                           ; normal entry, two more words
            entry = 0x4b4404 + e * 10
            W[entry + 0x8] = kind
            W[entry + 0x2] = payload[cursor]        ; cursor += 1
            W[entry + 0x4] = payload[cursor] >> 8
            W[entry + 0x6] = payload[cursor] & 0xff ; cursor += 1
            e += 1

The 220 bound is self-confirming: 0x4b4404 + 220 * 10 == 0x4b4c9c, exactly the
escape record's first field, and the 400-entry overview waveform buffer follows
at 0x4b4ca2.  So this table is the marker overlay for that waveform.

The handler decrements the stored count once on the way out (0x00b7ce4a), which
is why the value at 0x4b4400 settles one below the count sent here.
"""

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from .main_packet import firmware_crc

TYPE5_COMMAND = 0x15
TYPE5_MAX_ENTRIES = 220
TYPE5_ESCAPE_KIND = 0xFF


def build_type5_markers(
    entries: list[tuple[int, int, int, int]],
    *,
    escape: tuple[int, int, int] | None = None,
) -> bytes:
    """Build a type-5 payload from ``(kind, value, hi, lo)`` *entries*.

    ``escape`` is an optional ``(value, hi, lo)`` record emitted with kind 0xff;
    it feeds the three fields at 0x4b4c9c/0x4b4c9e/0x4b4ca0 rather than the
    entry table.
    """

    records: list[list[int]] = []
    for kind, value, hi, lo in entries:
        if kind == TYPE5_ESCAPE_KIND:
            raise ValueError("kind 0xff is reserved for the escape record")
        if not 0 <= kind <= 0xFFFF:
            raise ValueError("kind must fit in 16 bits")
        if not 0 <= value <= 0xFFFF:
            raise ValueError("value must fit in 16 bits")
        if not 0 <= hi <= 0xFF or not 0 <= lo <= 0xFF:
            raise ValueError("hi and lo must each fit in one byte")
        records.append([kind, value, (hi << 8) | lo])
    if escape is not None:
        value, hi, lo = escape
        records.append([TYPE5_ESCAPE_KIND, value, (hi << 8) | lo])

    count = len(records)
    if count > TYPE5_MAX_ENTRIES:
        raise ValueError(f"at most {TYPE5_MAX_ENTRIES} records fit in the table")

    words = [TYPE5_COMMAND, count]
    for record in records:
        words.extend(record)
    words.append(0)  # CRC

    payload = bytearray(struct.pack(f"<{len(words)}H", *words))
    struct.pack_into("<H", payload, len(payload) - 2, firmware_crc(payload[:-2]))
    return bytes(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--entry",
        action="append",
        default=[],
        metavar="KIND,VALUE,HI,LO",
        help="one marker entry; may be supplied repeatedly",
    )
    parser.add_argument(
        "--escape",
        metavar="VALUE,HI,LO",
        help="optional kind-0xff record feeding 0x4b4c9c/0x4b4c9e/0x4b4ca0",
    )
    parser.add_argument(
        "--sweep",
        type=int,
        default=0,
        help="emit N entries with kind=1 spread across the 400 waveform columns",
    )
    args = parser.parse_args()

    entries: list[tuple[int, int, int, int]] = []
    for text in args.entry:
        parts = [int(field, 0) for field in text.split(",")]
        if len(parts) != 4:
            parser.error(f"--entry {text!r} needs KIND,VALUE,HI,LO")
        entries.append((parts[0], parts[1], parts[2], parts[3]))

    for index in range(args.sweep):
        column = round(index * 399 / max(args.sweep - 1, 1))
        entries.append((1, column, 0, index & 0xFF))

    escape = None
    if args.escape:
        parts = [int(field, 0) for field in args.escape.split(",")]
        if len(parts) != 3:
            parser.error("--escape needs VALUE,HI,LO")
        escape = (parts[0], parts[1], parts[2])

    payload = build_type5_markers(entries, escape=escape)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    print(f"wrote {args.output}: {len(payload)} bytes, {len(entries)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
