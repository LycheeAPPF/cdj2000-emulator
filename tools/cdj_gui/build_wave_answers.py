"""Answer the GUI's repeated type-4 (waveform) requests.

With bit 11 set in status word 18 the GUI stops asking for type 1 and instead
asks for type 4 (the overview waveform) ~700 times per minute -- see the SPORT
TX dump, whose 48-byte request packets carry ``word1 = 0x8000 | type``.  MAIN
answers such a request by sending a status record that announces a payload
(word 29 == 1, word 30 == length in words) followed by the payload itself.

The captured stream contains exactly one command-0x10 announcement/payload pair,
so all but one request goes unanswered.  This rewrites each plain status record
into an announce+payload pair carrying the 400-column overview waveform, so the
GUI's requests are actually serviced.
"""

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from .main_packet import build_neutral_overview_waveform, firmware_crc
from .build_command11_probe import read_records, write_records
from .build_status_bits import STATUS_REQUEST_WORD, WAVEFORM_REQUEST_BIT


def make_announcement(status: bytes, *, words: int, keep_request: bool) -> bytes:
    """Turn a plain status record into one announcing a *words*-word payload."""

    packet = bytearray(status)
    struct.pack_into("<H", packet, 58, 1)
    struct.pack_into("<H", packet, 60, words)
    if keep_request:
        value = struct.unpack_from("<H", packet, STATUS_REQUEST_WORD * 2)[0]
        value |= 1 << WAVEFORM_REQUEST_BIT
        struct.pack_into("<H", packet, STATUS_REQUEST_WORD * 2, value)
    struct.pack_into("<H", packet, 62, firmware_crc(packet[:62]))
    return bytes(packet)


def add_wave_answers(
    records: list[bytes], *, keep_request: bool, every: int = 1
) -> tuple[list[bytes], int]:
    """Answer every *every*-th plain status record with a waveform payload.

    Answering every single request floods the link and the GUI reports E-8709,
    so the density is tunable.
    """

    waveform = build_neutral_overview_waveform()
    words = len(waveform) // 2
    out = records[:2]
    added = 0
    seen = 0
    index = 2
    while index < len(records):
        record = records[index]
        if len(record) != 64:
            out.append(record)
            index += 1
            continue
        announced = struct.unpack("<32H", record)[29] == 1
        if announced:
            # Preserve existing announce/payload pairs verbatim.
            out.append(record)
            if index + 1 < len(records):
                out.append(records[index + 1])
            index += 2
            continue
        seen += 1
        if seen % every == 0:
            out.append(
                make_announcement(record, words=words, keep_request=keep_request)
            )
            out.append(waveform)
            added += 1
        else:
            out.append(record)
        index += 1
    return out, added


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--every", type=int, default=1)
    parser.add_argument(
        "--drop-request",
        action="store_true",
        help="do not keep bit 11 set on the rewritten announcements",
    )
    args = parser.parse_args()

    records = read_records(args.input.read_bytes())
    out, added = add_wave_answers(records, keep_request=not args.drop_request, every=args.every)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(write_records(out))
    print(
        f"wrote {args.output}; {len(records)} -> {len(out)} records, "
        f"{added} waveform answers added"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
