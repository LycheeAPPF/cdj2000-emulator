"""Rewrite the mode/player-mask word of the steady-state MAIN status records.

The captured stream feeds the GUI one mode-2 bootstrap and then hundreds of
status records whose word 13 is zero, i.e. mode 0 with no players advertised.
``main_packet.build_bootstrap_packet`` documents modes 2..5 as the recognized
status paths, so mode 0 leaves the GUI with nothing to show.  This utility
rewrites word 13 on the plain status records only, leaving the bootstrap, the
payload announcements (word 29 == 1) and every payload untouched.
"""

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from .main_packet import firmware_crc
from .build_command11_probe import read_records, write_records


def retune_status(
    records: list[bytes], *, mode: int, player_mask: int
) -> int:
    """Set word 13 on plain status records; return how many were changed."""

    if not 0 <= mode <= 0xFF:
        raise ValueError("mode must fit in one byte")
    if not 0 <= player_mask <= 0xF:
        raise ValueError("player_mask must fit in four bits")

    changed = 0
    # Record zero is the bootstrap and must keep its own mode word.
    for index in range(1, len(records)):
        record = records[index]
        if len(record) != 64:
            continue
        words = struct.unpack("<32H", record)
        # Word 29 marks a record that announces a following payload; its word
        # 30 carries that payload's length, so leave those records alone.
        if words[29] == 1:
            continue
        patched = bytearray(record)
        struct.pack_into("<H", patched, 26, mode | (player_mask << 8))
        struct.pack_into("<H", patched, 62, firmware_crc(patched[:62]))
        records[index] = bytes(patched)
        changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--mode", type=lambda value: int(value, 0), default=2)
    parser.add_argument(
        "--player-mask", type=lambda value: int(value, 0), default=0xF
    )
    args = parser.parse_args()

    records = read_records(args.input.read_bytes())
    changed = retune_status(
        records, mode=args.mode, player_mask=args.player_mask
    )
    if changed == 0:
        parser.error("no plain status records found")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(write_records(records))
    print(
        f"wrote {args.output}; retuned {changed} status records to "
        f"mode={args.mode} player_mask=0x{args.player_mask:x}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
