"""Replace runtime command 0x11 payloads with an index-marker packet.

Every payload word carries its own word index, so each value the handler
stores into GUI RAM names the packet word it was copied from.  That turns one
boot into a complete packet-word -> field map without hand-tracing the
handler's unrolled index arithmetic.
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
    firmware_crc,
)
from .build_command11_probe import read_records, write_records


def build_marker_player_state(word_count: int = 128) -> bytes:
    """Build a 0x11 packet whose word *i* contains the value *i*."""

    # Word 0 is the command and the final word is the CRC, so only the words
    # between them can carry markers.
    return build_command_payload(
        0x11,
        word_count=word_count,
        payload_words={index: index for index in range(1, word_count - 1)},
    )


def apply_markers(records: list[bytes], word_count: int = 128) -> tuple[int, int]:
    replacements = {
        0x10: build_neutral_overview_waveform(),
        0x11: build_marker_player_state(word_count),
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
    replaced10, replaced11 = apply_markers(records, args.word_count)
    if replaced11 == 0:
        parser.error("no runtime command 0x11 records found")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(write_records(records))
    print(
        f"wrote {args.output}; {replaced10} command 0x10 and "
        f"{replaced11} marker command 0x11 payloads"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
