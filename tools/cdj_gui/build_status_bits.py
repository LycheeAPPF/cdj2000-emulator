"""Set request bits in the MAIN status records' word 18.

The GUI samples the 64-byte status record at receive-buffer offset 0x24
(word 18) and, at ``0x00b7e214``, tests bit 11:

    R1 = W[0xf00024]
    CC = !BITTST (R1, 0xb)
    IF CC JUMP <skip>
    B[0x4b43c9] = 1          ; waveform-request flag

That flag is the sole gate on internal message type 4, which is the only type
whose jump-table entry reaches the command-0x10 waveform handler 0x00b7ea4e.
The captured stream carries word 18 = 0x4, so bit 11 is clear and the waveform
is never requested or drawn.
"""

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from .main_packet import firmware_crc
from .build_command11_probe import read_records, write_records

STATUS_REQUEST_WORD = 18
WAVEFORM_REQUEST_BIT = 11


def set_status_bits(records: list[bytes], *, bits: int, word: int = STATUS_REQUEST_WORD) -> int:
    """OR *bits* into *word* of every plain status record.

    *word* defaults to 18, the request bitfield.  Word 19 is a second flag word
    read at ``0xb7e1de`` (bit 14 -> 0x4b43ca, bits 13..10 -> 0x4b43d6..0x4b43d9);
    note its bit 14 *clears* the waveform-request flag 0x4b43c9, so it conflicts
    with word 18 bit 11.
    """

    if not 0 <= word < 31:
        raise ValueError("word must index a 64-byte record and leave the CRC alone")
    changed = 0
    # Record zero is the bootstrap; records announcing a payload (word 29 == 1)
    # carry that payload's length and must not be disturbed.
    for index in range(1, len(records)):
        record = records[index]
        if len(record) != 64:
            continue
        words = struct.unpack("<32H", record)
        if words[29] == 1:
            continue
        patched = bytearray(record)
        value = words[word] | bits
        struct.pack_into("<H", patched, word * 2, value)
        struct.pack_into("<H", patched, 62, firmware_crc(patched[:62]))
        records[index] = bytes(patched)
        changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--bits",
        type=lambda value: int(value, 0),
        default=1 << WAVEFORM_REQUEST_BIT,
        help="bit mask to OR into word 18 (default: bit 11, the waveform request)",
    )
    parser.add_argument(
        "--word",
        type=lambda value: int(value, 0),
        default=STATUS_REQUEST_WORD,
        help="status record word to OR the bits into (default: 18)",
    )
    args = parser.parse_args()

    records = read_records(args.input.read_bytes())
    changed = set_status_bits(records, bits=args.bits, word=args.word)
    if changed == 0:
        parser.error("no plain status records found")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(write_records(records))
    print(
        f"wrote {args.output}; set bits 0x{args.bits:04x} in word "
        f"{args.word} of {changed} status records"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
