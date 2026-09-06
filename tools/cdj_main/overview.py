"""Tile the frames of a two-board run nearest the given seconds into one PNG.

    python -m tools.cdj_main.overview RUN_DIR OUT.png T1 T2 [T3 ...]

RUN_DIR is a directory `twoboard.py` wrote (its `frames/` holds `tNNNN.N.ppm`
files, one every `--frame-every` seconds).  For each T the frame nearest that
guest second is picked, and the sheet shows them two to a row with the run's
name, the time and the file name above each.  This is the picture a run's
result is judged by -- a time display, a waveform, a marker -- and it is what
the notes under runs/ refer to as `result-overview.png`.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

LABEL_HEIGHT = 18
COLUMNS = 2


def nearest_frames(run: Path, seconds: list[float]) -> list[tuple[float, Path]]:
    """The (time, path) of the frame nearest each of SECONDS, in that order."""
    frames = sorted((float(p.stem[1:]), p) for p in (run / "frames").glob("t*.ppm"))
    if not frames:
        raise SystemExit(f"{run}: no frames/t*.ppm")
    return [min(frames, key=lambda item: abs(item[0] - t)) for t in seconds]


def build_sheet(run: Path, picked: list[tuple[float, Path]]) -> Image.Image:
    images = [Image.open(p).convert("RGB") for _, p in picked]
    w, h = images[0].size
    rows = (len(images) + COLUMNS - 1) // COLUMNS
    sheet = Image.new("RGB", (COLUMNS * w, rows * (h + LABEL_HEIGHT)), "black")
    draw = ImageDraw.Draw(sheet)
    for i, ((t, p), img) in enumerate(zip(picked, images)):
        x, y = (i % COLUMNS) * w, (i // COLUMNS) * (h + LABEL_HEIGHT)
        sheet.paste(img, (x, y + LABEL_HEIGHT))
        draw.text((x + 4, y + 2), f"{run.name}  t={t:.1f}s  ({p.name})", fill="white")
    return sheet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run", type=Path, help="a twoboard run directory")
    parser.add_argument("out", type=Path, help="the PNG to write")
    parser.add_argument("seconds", type=float, nargs="+", help="guest seconds to show")
    args = parser.parse_args(argv)
    picked = nearest_frames(args.run, args.seconds)
    sheet = build_sheet(args.run, picked)
    sheet.save(args.out)
    print(args.out, sheet.size, [t for t, _ in picked])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
