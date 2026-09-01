# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from pathlib import Path


PACKED_REGIONS = (0x10000, 0x40000)
LZSS_WINDOW_SIZE = 4096
LZSS_LOOKAHEAD = 18


@dataclass(frozen=True)
class PackedRegion:
    address: int
    packed: bytes
    checksum_stored: int
    checksum_calculated: int
    unpacked: bytes

    @property
    def checksum_valid(self) -> bool:
        return self.checksum_stored == self.checksum_calculated


def decode_srecords(data: bytes) -> bytes:
    """Reconstruct the address-zero image carried by a MAIN updater."""
    records: list[tuple[int, bytes]] = []
    for raw_line in data.splitlines():
        line = raw_line.strip()
        if len(line) < 4 or line[:1] != b"S" or line[1:2] not in (b"1", b"2", b"3"):
            continue
        record_type = chr(line[1])
        address_bytes = {"1": 2, "2": 3, "3": 4}[record_type]
        count = int(line[2:4], 16)
        decoded = bytes.fromhex(line[4:].decode("ascii"))
        if len(decoded) != count:
            raise ValueError("invalid S-record byte count")
        if (count + sum(decoded)) & 0xFF != 0xFF:
            raise ValueError("invalid S-record checksum")
        address = int.from_bytes(decoded[:address_bytes], "big")
        payload = decoded[address_bytes:-1]
        records.append((address, payload))

    if not records:
        raise ValueError("no S1/S2/S3 records found")
    end = max(address + len(payload) for address, payload in records)
    image = bytearray(end)
    for address, payload in records:
        image[address : address + len(payload)] = payload
    return bytes(image)


def decompress_lzss(source: bytes) -> bytes:
    """Decode the 4 KiB-window LZSS format used by the MAIN bootloader."""
    window = bytearray(b" " * LZSS_WINDOW_SIZE)
    write_position = LZSS_WINDOW_SIZE - LZSS_LOOKAHEAD
    flags = 0
    source_position = 0
    output = bytearray()

    while source_position < len(source):
        flags >>= 1
        if not flags & 0x100:
            flags = source[source_position] | 0xFF00
            source_position += 1

        if flags & 1:
            if source_position >= len(source):
                break
            value = source[source_position]
            source_position += 1
            output.append(value)
            window[write_position] = value
            write_position = (write_position + 1) & (LZSS_WINDOW_SIZE - 1)
            continue

        if source_position + 1 >= len(source):
            break
        first = source[source_position]
        second = source[source_position + 1]
        source_position += 2
        read_position = first | ((second & 0xF0) << 4)
        count = (second & 0x0F) + 3
        for offset in range(count):
            value = window[(read_position + offset) & (LZSS_WINDOW_SIZE - 1)]
            output.append(value)
            window[write_position] = value
            write_position = (write_position + 1) & (LZSS_WINDOW_SIZE - 1)

    return bytes(output)


def unpack_region(image: bytes, address: int) -> PackedRegion:
    if address + 4 > len(image):
        raise ValueError(f"packed region 0x{address:x} lies outside the image")
    packed_size = int.from_bytes(image[address : address + 4], "little")
    packed_start = address + 4
    packed_end = packed_start + packed_size
    if packed_end + 2 > len(image):
        raise ValueError(f"packed region 0x{address:x} is truncated")
    packed = image[packed_start:packed_end]
    checksum_stored = int.from_bytes(image[packed_end : packed_end + 2], "little")
    checksum_calculated = sum(image[address:packed_end]) & 0xFFFF
    return PackedRegion(
        address=address,
        packed=packed,
        checksum_stored=checksum_stored,
        checksum_calculated=checksum_calculated,
        unpacked=decompress_lzss(packed),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Unpack a CDJ MAIN S-record updater")
    parser.add_argument("update", type=Path, help="path to C2KMAIN.UPD or an S-record file")
    parser.add_argument("output", type=Path, help="output directory")
    args = parser.parse_args()

    image = decode_srecords(args.update.read_bytes())
    args.output.mkdir(parents=True, exist_ok=True)

    # The decoded S-records are the MAIN flash image byte for byte, which is
    # what the QEMU board loads with -bios.  Writing it here rather than in a
    # tool of its own keeps one decode: the unpacked regions below are cut out
    # of this same image, so they cannot disagree with it.
    flash = args.output / "main-firmware.bin"
    flash.write_bytes(image)
    print(f"0x000000: flash image 0x{len(image):x}, "
          f"sha256 {hashlib.sha256(image).hexdigest()}")
    print(flash)

    # The application region is what every host tool means by "the MAIN
    # image": caution.py reads its caution tables out of it and
    # tests/test_service_mode_key_names.py reads the key-name table.
    names = ("main-loader-unpacked.bin", "main-unpacked.bin")
    for address, name in zip(PACKED_REGIONS, names):
        region = unpack_region(image, address)
        if not region.checksum_valid:
            parser.error(
                f"region 0x{address:x} checksum mismatch: "
                f"calculated 0x{region.checksum_calculated:04x}, "
                f"stored 0x{region.checksum_stored:04x}"
            )
        destination = args.output / name
        destination.write_bytes(region.unpacked)
        digest = hashlib.sha256(region.unpacked).hexdigest()
        print(
            f"0x{address:06x}: packed 0x{len(region.packed):x}, "
            f"unpacked 0x{len(region.unpacked):x}, sha256 {digest}"
        )
        print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
