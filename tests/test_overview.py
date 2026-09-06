from pathlib import Path

from PIL import Image

from tools.cdj_main.overview import build_sheet, main, nearest_frames


def _run_dir(tmp_path: Path) -> Path:
    frames = tmp_path / "run-1" / "frames"
    frames.mkdir(parents=True)
    for t, colour in ((10.0, (255, 0, 0)), (12.2, (0, 255, 0)), (14.1, (0, 0, 255))):
        Image.new("RGB", (48, 24), colour).save(frames / f"t{t:06.1f}.ppm")
    return tmp_path / "run-1"


def test_nearest_frames_pick_by_time(tmp_path):
    run = _run_dir(tmp_path)
    picked = nearest_frames(run, [9.0, 12.0, 13.9])
    assert [t for t, _ in picked] == [10.0, 12.2, 14.1]


def test_sheet_tiles_two_to_a_row_with_labels(tmp_path):
    run = _run_dir(tmp_path)
    sheet = build_sheet(run, nearest_frames(run, [10, 12, 14]))
    assert sheet.size == (96, 2 * (24 + 18))
    assert sheet.getpixel((10, 18 + 5)) == (255, 0, 0)      # first frame under its label
    assert sheet.getpixel((48 + 10, 18 + 5)) == (0, 255, 0)
    assert sheet.getpixel((10, 42 + 18 + 5)) == (0, 0, 255)


def test_main_writes_the_png(tmp_path, capsys):
    run = _run_dir(tmp_path)
    out = tmp_path / "sheet.png"
    assert main([str(run), str(out), "10", "14"]) == 0
    assert Image.open(out).size == (96, 42)
    assert "[10.0, 14.1]" in capsys.readouterr().out
