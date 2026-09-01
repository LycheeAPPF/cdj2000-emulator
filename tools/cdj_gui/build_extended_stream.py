"""Repeat a MAIN record stream's body so the GUI is not starved.

The captured stream is a 232-record replay that simply runs out.  The GUI is an
interactive peer: it keeps requesting state, and once the stream is exhausted no
further messages arrive, so anything posted at the tail (for example a type-4
waveform request) is never serviced.  Repeating the body keeps the conversation
going long enough to observe steady-state behaviour.

Record 0 is the bootstrap and record 1 is the fixed mode-2 boot header; both are
sent once.  Everything after them is repeated verbatim, which preserves each
announcement/payload pair and its ordering.
"""

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import argparse
from pathlib import Path

from .build_command11_probe import read_records, write_records

BODY_START = 2


def extend(records: list[bytes], *, repeat: int) -> list[bytes]:
    if repeat < 1:
        raise ValueError("repeat must be positive")
    if len(records) <= BODY_START:
        raise ValueError("stream has no body records to repeat")
    head = records[:BODY_START]
    body = records[BODY_START:]
    return head + body * repeat


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repeat", type=int, default=10)
    args = parser.parse_args()

    records = read_records(args.input.read_bytes())
    extended = extend(records, repeat=args.repeat)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(write_records(extended))
    print(
        f"wrote {args.output}; {len(records)} records -> {len(extended)} "
        f"(body x{args.repeat})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
