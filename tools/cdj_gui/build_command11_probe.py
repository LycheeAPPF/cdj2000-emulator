"""Normalize runtime state records in a proven GUI boot stream.

The first command 0x11 record is the fixed eight-word mode-2 boot header.
Later command 0x11 records are variable MAIN-to-GUI player-state payloads and
command 0x10 carries the 400-column overview waveform.  Short probe versions
of either command make the firmware read stale receive-buffer words.  This
utility preserves every record and delay while replacing those runtime
payloads with fully initialized idle packets.
"""

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from .main_packet import (
    build_command_payload,
    build_neutral_overview_waveform,
    build_neutral_player_state,
    firmware_crc,
)


def read_records(data: bytes) -> list[bytes]:
    records: list[bytes] = []
    offset = 0
    while offset < len(data):
        if offset + 4 > len(data):
            raise ValueError("truncated record length")
        length = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        end = offset + length
        if end > len(data):
            raise ValueError("truncated record payload")
        records.append(data[offset:end])
        offset = end
    return records


def write_records(records: list[bytes]) -> bytes:
    return b"".join(struct.pack("<I", len(record)) + record for record in records)


def expand_runtime_command11(records: list[bytes], word_count: int) -> int:
    replacement = (
        build_neutral_player_state()
        if word_count == 128
        else build_command_payload(0x11, word_count=word_count)
    )
    replaced = 0

    # Record zero is the bootstrap and record one is the fixed boot header.
    for index in range(2, len(records)):
        record = records[index]
        if len(record) != 16 or struct.unpack_from("<H", record)[0] != 0x11:
            continue
        if len(records[index - 1]) != 64:
            raise ValueError(f"command 0x11 record {index} lacks an announcement")

        announcement = bytearray(records[index - 1])
        struct.pack_into("<H", announcement, 58, 1)
        struct.pack_into("<H", announcement, 60, word_count)
        struct.pack_into("<H", announcement, 62, firmware_crc(announcement[:62]))
        records[index - 1] = bytes(announcement)
        records[index] = replacement
        replaced += 1

    return replaced


def normalize_runtime_commands(records: list[bytes], word_count: int = 128) -> tuple[int, int]:
    """Replace short runtime 0x10/0x11 records and repair announcements."""

    replacements = {
        0x10: build_neutral_overview_waveform(),
        0x11: (
            build_neutral_player_state()
            if word_count == 128
            else build_command_payload(0x11, word_count=word_count)
        ),
    }
    counts = {0x10: 0, 0x11: 0}

    # Record zero is the bootstrap and record one is the fixed boot header.
    for index in range(2, len(records)):
        record = records[index]
        if len(record) < 2:
            continue
        command = struct.unpack_from("<H", record)[0]
        replacement = replacements.get(command)
        if replacement is None:
            continue
        if len(records[index - 1]) != 64:
            raise ValueError(
                f"command 0x{command:02x} record {index} lacks an announcement"
            )

        announcement = bytearray(records[index - 1])
        struct.pack_into("<H", announcement, 58, 1)
        struct.pack_into("<H", announcement, 60, len(replacement) // 2)
        struct.pack_into("<H", announcement, 62, firmware_crc(announcement[:62]))
        records[index - 1] = bytes(announcement)
        records[index] = replacement
        counts[command] += 1

    return counts[0x10], counts[0x11]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--word-count", type=int, default=128)
    args = parser.parse_args()

    records = read_records(args.input.read_bytes())
    replaced10, replaced11 = normalize_runtime_commands(records, args.word_count)
    if replaced10 + replaced11 == 0:
        parser.error("no runtime command 0x10/0x11 records found")

    output = write_records(records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)
    print(
        f"wrote {len(output)} bytes ({len(records)} records) to {args.output}; "
        f"normalized {replaced10} command 0x10 and {replaced11} command 0x11 payloads"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
