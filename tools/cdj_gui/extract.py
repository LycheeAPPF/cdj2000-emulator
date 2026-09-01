# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import argparse
from pathlib import Path

from .firmware import parse_gui_update, write_outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract a CDJ-2000 BF531 GUI update")
    parser.add_argument("update", type=Path, help="path to C2KGUI.UPD")
    parser.add_argument("output", type=Path, help="output directory")
    args = parser.parse_args()

    update = parse_gui_update(args.update)
    if not update.crc_valid:
        parser.error(
            f"CRC mismatch: calculated 0x{update.crc_calculated:04x}, "
            f"stored 0x{update.crc_stored:04x}"
        )
    write_outputs(update, args.output)
    print(f"{update.version_text}")
    print(f"blocks: {len(update.blocks)}")
    print(f"entry: 0x{update.entry_point:08X}")
    print(f"boot stream: 0x{update.boot_stream_end:x} bytes")
    print(f"resource tail: 0x{len(update.resource_tail):x} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
