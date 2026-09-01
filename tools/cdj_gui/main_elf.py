"""Package the unpacked CDJ-2000 MAIN image as a minimal SH ELF file.

The local SH simulator consumes ELF files.  Binutils' binary backend on the
Windows host truncates this particular 3.75 MiB image, so emitting the small
ELF container directly is both simpler and reproducible.
"""

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import argparse
import struct
from pathlib import Path


ELF_HEADER_SIZE = 52
PROGRAM_HEADER_SIZE = 32
SECTION_HEADER_SIZE = 40
PAYLOAD_OFFSET = 0x100
EM_SH = 42


def add_builder_shim(
    image: bytes,
    *,
    base: int,
    builder: int,
    output_buffer: int = 0x05000000,
    stack: int = 0x05FFFFF0,
) -> tuple[bytes, int]:
    """Append an SH shim that calls *builder* with a valid stack and r4."""

    entry = base + len(image)
    # With the lab's 27-bit memory map, returning to 0x08000000 produces a
    # clean simulated bus stop after the builder has restored its stack.
    return_address = 0x08000000
    # mov.l output,r4; mov.l stack,r15; mov.l return,r0; lds r0,pr;
    # mov.l builder,r0; jmp @r0; nop; nop.  Four literals follow.  The MAIN
    # CPU stores both instructions and data little-endian.
    shim = bytearray(struct.pack("<8H", 0xD403, 0xDF04, 0xD004, 0x402A,
                                 0xD004, 0x402B, 0x0009, 0x0009))
    shim.extend(struct.pack("<IIII", output_buffer, stack, return_address, builder))
    return image + shim, entry


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & -alignment


def make_sh_elf(image: bytes, *, base: int, entry: int) -> bytes:
    """Return a little-endian ELF32/SH executable containing *image*."""

    names = b"\0.text\0.shstrtab\0"
    names_offset = PAYLOAD_OFFSET + len(image)
    section_offset = _align(names_offset + len(names), 4)

    ident = b"\x7fELF" + bytes((1, 1, 1, 0)) + bytes(8)
    header = ident + struct.pack(
        "<HHIIIIIHHHHHH",
        2,  # ET_EXEC
        EM_SH,
        1,
        entry,
        ELF_HEADER_SIZE,
        section_offset,
        0,
        ELF_HEADER_SIZE,
        PROGRAM_HEADER_SIZE,
        1,
        SECTION_HEADER_SIZE,
        3,
        2,
    )
    program = struct.pack(
        "<IIIIIIII",
        1,  # PT_LOAD
        PAYLOAD_OFFSET,
        base,
        base,
        len(image),
        len(image),
        5,  # PF_R | PF_X
        0x100,
    )
    text_section = struct.pack(
        "<IIIIIIIIII",
        1,  # ".text"
        1,  # SHT_PROGBITS
        6,  # SHF_ALLOC | SHF_EXECINSTR
        base,
        PAYLOAD_OFFSET,
        len(image),
        0,
        0,
        2,
        0,
    )
    names_section = struct.pack(
        "<IIIIIIIIII",
        7,  # ".shstrtab"
        3,  # SHT_STRTAB
        0,
        0,
        names_offset,
        len(names),
        0,
        0,
        1,
        0,
    )

    out = bytearray(header + program)
    out.extend(bytes(PAYLOAD_OFFSET - len(out)))
    out.extend(image)
    out.extend(names)
    out.extend(bytes(section_offset - len(out)))
    out.extend(bytes(SECTION_HEADER_SIZE))
    out.extend(text_section)
    out.extend(names_section)
    return bytes(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--base", type=lambda value: int(value, 0), default=0x04000000)
    entry_group = parser.add_mutually_exclusive_group(required=True)
    entry_group.add_argument("--entry", type=lambda value: int(value, 0))
    entry_group.add_argument("--builder", type=lambda value: int(value, 0))
    parser.add_argument(
        "--output-buffer", type=lambda value: int(value, 0), default=0x05000000
    )
    parser.add_argument("--stack", type=lambda value: int(value, 0), default=0x05FFFFF0)
    args = parser.parse_args()

    image = args.input.read_bytes()
    entry = args.entry
    if args.builder is not None:
        image, entry = add_builder_shim(
            image,
            base=args.base,
            builder=args.builder,
            output_buffer=args.output_buffer,
            stack=args.stack,
        )
    args.output.write_bytes(
        make_sh_elf(image, base=args.base, entry=entry)
    )


if __name__ == "__main__":
    main()
