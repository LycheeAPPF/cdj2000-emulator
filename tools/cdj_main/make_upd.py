"""Build a `C2KMAIN.UPD` from a MAIN flash image.

    python -m tools.cdj_main.make_upd IMAGE OUT.UPD [--version 4.34] [--end 0x287d60]

The updater in MAIN 4.33 (task `UpDtae_TASK`, state machine 0x2d68b2) takes
the file from the root of a USB stick, so this is the inverse of
`tools.cdj_gui.main_unpack`'s S-record decoder: what the updater checks, this
writes.

The file is a 32-byte header, Motorola S-records, and a CRC.

* Header: `CDJ-2000 MAIN   Ver4.33\\0` then spaces and a flag byte at 0x1f
  (`'0'`; `'1'` selects the updater's second programming mode).  The updater
  parses the version digits at 0x13, 0x15 and 0x16 (`4`, `3`, `3`) and only
  accepts a file whose number is **greater** than the running firmware's
  (0x2d58e4); a file of the installed version is skipped with "nothing to do".
  Bytes 0x17/0x18 must not be `+`.
* Records: one `S0` ("romobj  mot"), then `S2` records of 32 data bytes from
  address 0 up to `--end` -- leaving out records that are all zero, as the
  original does -- then `S7` with entry 0xa0000000, CRLF line ends.
  The updater erases the application area (0x40000 upwards) in 64 KiB sectors
  and programs what the records carry there; the boot ROM and loader below
  0x40000 are carried but left alone.
* Trailer: a 16-bit CRC over everything before it, little-endian.  It is the
  CRC-16 with polynomial 0x1021, zero initial value and two zero bytes fed
  after the data (loader 0x210ca, app 0x2d6290) -- which is CRC-16/XMODEM.

`--end` defaults to the original file's 0x287d60, the packed application's end
rounded up to a record; the image's remaining bytes up to 4 MiB are settings and
erased space the updater never touches.
"""
from __future__ import annotations

import argparse
from pathlib import Path

HEADER_SIZE = 0x20
RECORD_BYTES = 32
DEFAULT_END = 0x287D60
S0_RECORD = b"S00E0000726F6D6F626A20206D6F74D8"
S7_RECORD = b"S705A00000005A"
CRC_POLY = 0x1021


def crc16(data: bytes) -> int:
    """The updater's CRC: 0x1021, init 0, two zero bytes fed after the data."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def header(version: str, flag: str = "0") -> bytes:
    if len(version) != 4 or version[1] != "." or not (version[0] + version[2:]).isdigit():
        raise ValueError("version must look like 4.34")
    text = b"CDJ-2000 MAIN   Ver" + version.encode("ascii")
    return (text + b"\0").ljust(HEADER_SIZE - 1, b" ") + flag.encode("ascii")


def srecord(address: int, payload: bytes) -> bytes:
    body = (len(payload) + 4).to_bytes(1, "big") + address.to_bytes(3, "big") + payload
    checksum = 0xFF - (sum(body) & 0xFF)
    return b"S2" + (body + bytes([checksum])).hex().upper().encode("ascii")


def build(image: bytes, version: str, end: int = DEFAULT_END, flag: str = "0") -> bytes:
    if len(image) < end:
        image = image + b"\xff" * (end - len(image))
    lines = [S0_RECORD]
    # Records that are all zero are left out, as in the original (0x60..0xff
    # of the boot ROM); all-0xff records are carried.  That is what Pioneer's
    # converter did, and it is what makes a rebuilt 4.33 byte-identical.
    lines += [srecord(a, image[a : a + RECORD_BYTES]) for a in range(0, end, RECORD_BYTES)
              if any(image[a : a + RECORD_BYTES])]
    lines.append(S7_RECORD)
    body = header(version, flag) + b"\r\n".join(lines) + b"\r\n"
    return body + crc16(body).to_bytes(2, "little")


def check(upd: bytes) -> tuple[str, bool]:
    """(version, crc ok) of an existing file, the way the updater sees it."""
    version = upd[0x13:0x17].decode("ascii", "replace")
    return version, crc16(upd[:-2]) == int.from_bytes(upd[-2:], "little")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("image", type=Path, help="address-zero flash image")
    parser.add_argument("out", type=Path, help="the .UPD to write")
    parser.add_argument("--version", default="4.34", help="header version, e.g. 4.34")
    parser.add_argument("--end", type=lambda s: int(s, 0), default=DEFAULT_END,
                        help="first address not carried (default 0x287d60)")
    parser.add_argument("--flag", default="0", help="header byte 0x1f, '0' or '1'")
    args = parser.parse_args(argv)
    data = build(args.image.read_bytes(), args.version, args.end, args.flag)
    args.out.write_bytes(data)
    print(args.out, len(data), "bytes, version", args.version, "crc",
          data[-2:][::-1].hex())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
