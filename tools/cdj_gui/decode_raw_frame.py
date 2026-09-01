"""Render a raw 16-bit CDJ framebuffer using candidate pixel layouts."""

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from PIL import Image, ImageDraw


def expand5(value: int) -> int:
    return (value << 3) | (value >> 2)


def expand6(value: int) -> int:
    return (value << 2) | (value >> 4)


def decode(word: int, mode: str) -> tuple[int, int, int]:
    if mode.endswith("be"):
        word = ((word & 0xFF) << 8) | (word >> 8)
    bgr = mode.startswith("bgr")
    if "565" in mode:
        hi = expand5((word >> 11) & 0x1F)
        green = expand6((word >> 5) & 0x3F)
        lo = expand5(word & 0x1F)
    else:
        hi = expand5((word >> 10) & 0x1F)
        green = expand5((word >> 5) & 0x1F)
        lo = expand5(word & 0x1F)
    return (lo, green, hi) if bgr else (hi, green, lo)


def render(data: bytes, width: int, height: int, mode: str) -> Image.Image:
    words = struct.unpack(f"<{width * height}H", data)
    image = Image.new("RGB", (width, height))
    image.putdata([decode(word, mode) for word in words])
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=480)
    parser.add_argument("--height", type=int, default=255)
    args = parser.parse_args()

    data = args.input.read_bytes()
    expected = args.width * args.height * 2
    if len(data) != expected:
        parser.error(f"expected {expected} bytes, got {len(data)}")

    modes = ("rgb555le", "rgb555be", "rgb565le", "rgb565be",
             "bgr555le", "bgr555be", "bgr565le", "bgr565be")
    label_height = 24
    montage = Image.new("RGB", (args.width * 2, (args.height + label_height) * 4))
    draw = ImageDraw.Draw(montage)
    for index, mode in enumerate(modes):
        x = (index % 2) * args.width
        y = (index // 2) * (args.height + label_height)
        draw.text((x + 6, y + 5), mode, fill=(255, 255, 255))
        montage.paste(render(data, args.width, args.height, mode), (x, y + label_height))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    montage.save(args.output)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
