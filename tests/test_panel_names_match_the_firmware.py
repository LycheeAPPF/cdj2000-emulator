"""Every host-side name for a panel bit, checked against MAIN's own name table.

This file exists because of one bug that cost this project weeks, and it is the
shape of the bug rather than the bug itself that matters.  The four SOURCE keys
were labelled backwards -- `press sd` sent payload 19.1, which is the **USB**
key -- and the mistake was invisible from inside the host tools, because every
one of them agreed with every other one:

    boot_vm.SOURCE_KEYS        {"disc": 0x01, "sd": 0x02, "usb": 0x04, ...}
    view_vm.SOURCE_KEYS        the same four, copied
    panel_control.BUTTON_NAMES the same four again
    INPUT_MANIFEST.md          "19.1 SOURCE SD", in two tables
    the r026/r096/r113 rows    written from those tables

Five statements, one source, and no way for any test between them to notice.
So the check here does not compare the copies with each other -- it compares
each of them with **the firmware**, which is the only party that gets a vote.

Two layers, on purpose:

* `test_*_agree_with_the_firmware` needs `firmware/main-unpacked.bin` and
  skips without it.  That is the real check.
* `test_every_source_table_in_the_tree_says_the_same_thing` needs nothing but
  the checkout.  It walks the AST of every Python file under `tools/` and
  `tests/`, finds every dict literal keyed by the four source names -- however
  it is spelled, whatever it is called, wherever somebody puts the next copy --
  and requires them all to match.  A fifth copy added tomorrow is caught by a
  test nobody has to remember to update.

The chain from a payload bit to a printed name, all static, is in
`tests/test_service_mode_key_names.py`; this file imports its reader rather
than restating any of it.
"""

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.cdj_gui import view_ui                       # noqa: E402
from tools.cdj_main import boot_vm, panel_control, view_vm  # noqa: E402

import test_service_mode_key_names as firmware          # noqa: E402

MANIFEST = ROOT / "INPUT_MANIFEST.md"

# The one place where a key on the front panel and MAIN's name for it are
# allowed to differ, and why.  GOAL.md lists the top row as `INFO` because that
# is what is printed on the plastic; MAIN's table spells the same bit
# `INFORMATION`.  Written down as data so that the exception is one line in a
# table instead of a special case hidden in an assertion.
LABEL_ALIASES = {"INFO": "INFORMATION"}

# Where the four SOURCE names may legitimately appear as a table.  Anything
# matching the shape is checked; this list is only to make the error message
# say what was scanned.
SCANNED = ("tools", "tests")


def firmware_catalogue() -> dict[tuple[int, int], str]:
    """payload (byte, bit) -> the name MAIN prints, read out of the image."""
    return firmware.catalogue(firmware.image())


def source_bit(catalogue: dict[tuple[int, int], str], name: str) -> tuple[int, int]:
    """The payload (byte, bit) the firmware calls `name`."""
    matches = [key for key, value in catalogue.items()
               if value.upper() == name.upper()]
    assert len(matches) == 1, "%s names %d bits in the firmware" % (name,
                                                                    matches)
    return matches[0]


# ------------------------------------------------- the firmware has a vote --
def test_the_host_name_table_is_the_firmwares_name_table():
    """`panel_control.FIRMWARE_KEY_NAMES` is a transcription; prove it.

    Every entry, both directions.  A missing row is as bad as a wrong one: the
    window labels its bit grid from this dict, and a bit with no label reads as
    a bit the firmware does not name, which is a claim about the board.
    """
    catalogue = firmware_catalogue()
    assert panel_control.FIRMWARE_KEY_NAMES == catalogue


def test_the_four_source_keys_agree_with_the_firmware():
    """The bug this file was written for, stated as an assertion."""
    catalogue = firmware_catalogue()
    for name, (byte, mask) in panel_control.BUTTON_NAMES.items():
        bit = mask.bit_length() - 1
        assert mask == 1 << bit, "%s: %#x is not a single bit" % (name, mask)
        assert (byte, bit) == source_bit(catalogue, name), (
            "panel_control.BUTTON_NAMES calls payload %d.%d %r, MAIN calls it "
            "%r" % (byte, bit, name, catalogue.get((byte, bit))))


def test_both_launchers_press_the_key_they_name():
    """`boot_vm --sd` and `view_vm --source-key sd` must press MAIN's SD bit.

    boot_vm belongs to the other strand and is only *read* here.  That is the
    point: the two copies are in different owners' files, which is exactly how
    they came apart, and a test that reads both is the only thing that spans
    the boundary.
    """
    catalogue = firmware_catalogue()
    for module in (boot_vm, view_vm):
        for name, mask in module.SOURCE_KEYS.items():
            byte, bit = source_bit(catalogue, name)
            assert byte == 19, "%s.SOURCE_KEYS writes payload byte 19" % module.__name__
            assert mask == 1 << bit, (
                "%s.SOURCE_KEYS presses %#x for %r; MAIN's %s key is bit %d, "
                "i.e. %#x" % (module.__name__, mask, name, name.upper(), bit,
                              1 << bit))


def test_every_key_the_window_offers_carries_the_firmwares_name():
    """The eight hardware keys, and the 40 bits in the grid.

    A label is a promise about which bit a click sends.  `view_ui` keeps the
    front panel's spelling, so the comparison goes through `LABEL_ALIASES` --
    one row, visible, rather than a `startswith` that would quietly accept
    `INFO` for `INFORMATION` and also `TAG` for `TAG TRACK`.
    """
    catalogue = firmware_catalogue()
    for label, key in view_ui.TOP_BUTTONS + view_ui.LEFT_BUTTONS:
        if key is None:
            continue
        byte, mask = panel_control.button_mask(key)
        bit = mask.bit_length() - 1
        expected = LABEL_ALIASES.get(label, label)
        assert catalogue.get((byte, bit)) == expected, (
            "the window's %r key sends payload %d.%d, which MAIN calls %r"
            % (label, byte, bit, catalogue.get((byte, bit))))


def test_the_manifests_catalogue_rows_are_the_firmwares_names():
    """INPUT_MANIFEST.md's `| payload | status | code | name |` table.

    Parsed rather than eyeballed, because that table is what every later
    chapter of the file copies its physical names out of -- including the two
    world tables whose `SOURCE DISC` rows were wrong for weeks.
    """
    catalogue = firmware_catalogue()
    row = re.compile(r"^\|\s*\**(\d+)\.(\d)\**\s*\|\s*(\d+)\.(\d)\s*\|"
                     r"\s*(\d+)\s*\|\s*\**`([^`]+)`\**\s*\|")
    found = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        match = row.match(line)
        if match:
            # A cell escapes its own separator, so `PREVIOUS |<<` is written
            # `PREVIOUS \|<<`.  Undone here rather than in the table, because
            # the table has to stay valid Markdown.
            found[(int(match.group(1)), int(match.group(2)))] = \
                match.group(6).replace("\\|", "|")
    assert len(found) >= 38, "the manifest's name table did not parse"
    for payload, name in found.items():
        assert catalogue.get(payload) == name, (
            "INPUT_MANIFEST.md calls payload %d.%d %r; MAIN calls it %r"
            % (payload[0], payload[1], name, catalogue.get(payload)))


def test_the_manifest_names_the_source_keys_the_same_way_everywhere_else():
    """`SOURCE <NAME>` wherever it appears in a table row must match the bit.

    The world tables (`## The buttons in the r026 world` and the r096 one) put
    the physical name in a column of their own, and those were the rows a
    reader would copy a conclusion out of.  They said `19.0 SOURCE DISC`.
    """
    catalogue = firmware_catalogue()
    pattern = re.compile(r"^\|\s*\**(\d+)\.(\d)\**\s*\|[^|]*\|\s*\**SOURCE\s+"
                         r"([A-Z]+)\**\s*\|")
    checked = 0
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        payload = (int(match.group(1)), int(match.group(2)))
        checked += 1
        assert catalogue.get(payload) == match.group(3), (
            "INPUT_MANIFEST.md calls payload %d.%d SOURCE %s; MAIN calls it %r"
            % (payload[0], payload[1], match.group(3), catalogue.get(payload)))
    assert checked >= 4, "no `SOURCE <NAME>` rows found; the tables moved"


# ------------------------------------- and one layer that needs no image ----
def source_tables() -> list[tuple[Path, int, dict]]:
    """Every dict literal in the tree keyed by the four source names.

    An AST walk rather than a grep, so that a table survives being renamed,
    reformatted or moved to a file nobody thought of.  Keys are compared in
    lower case because the same table is written `{"sd": ...}` in the tools and
    `{"SD": ...}` in the tests.
    """
    wanted = {"link", "usb", "sd", "disc"}
    out: list[tuple[Path, int, dict]] = []
    for directory in SCANNED:
        for path in sorted((ROOT / directory).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:                      # pragma: no cover
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict) or len(node.keys) != 4:
                    continue
                try:
                    table = ast.literal_eval(node)
                except ValueError:
                    continue
                if not all(isinstance(key, str) for key in table):
                    continue
                if {key.lower() for key in table} != wanted:
                    continue
                out.append((path, node.lineno, table))
    return out


def mask_of(value: object) -> int | None:
    """The payload mask a table entry carries, whatever shape it is written in.

    `{"sd": 0x04}` and `{"SD": (19, 0x04)}` are the two that exist; anything
    else is returned as None so the test reports it rather than passing it.
    """
    if isinstance(value, int):
        return value
    if isinstance(value, tuple) and len(value) == 2 and \
            all(isinstance(item, int) for item in value):
        return value[1]
    return None


def test_the_tree_has_more_than_one_source_table_and_this_test_finds_them():
    """The scan is only worth anything if it actually finds the copies."""
    found = source_tables()
    files = {path.name for path, _, _ in found}
    assert {"boot_vm.py", "view_vm.py", "panel_control.py"} <= files, (
        "the AST scan found %s -- it no longer sees the tables it is meant to "
        "police" % sorted(files))


def test_every_source_table_in_the_tree_says_the_same_thing():
    """One order, everywhere, with no image needed to run the check.

    The order itself is asserted against the firmware by the tests above; this
    one guarantees that fixing it in one place and forgetting another is a red
    test rather than a run that presses the wrong key.
    """
    expected = {name: mask for name, (_, mask)
                in panel_control.BUTTON_NAMES.items()}
    for path, line, table in source_tables():
        got = {name.lower(): mask_of(value) for name, value in table.items()}
        assert got == expected, (
            "%s:%d disagrees with panel_control.BUTTON_NAMES: %s"
            % (path.relative_to(ROOT), line, got))


def test_the_four_masks_are_the_low_four_bits_of_payload_byte_19():
    """Not a tautology: it is what makes `w18 = bit index + 1` checkable.

    MAIN reports the selected source as bit index + 1 in status word 18 and the
    GUI subtracts one without remapping, so the *index* is the KIND.  A table
    that used, say, 0x10 for one of them would break that arithmetic silently.
    """
    masks = sorted(mask for _, mask in panel_control.BUTTON_NAMES.values())
    assert masks == [0x01, 0x02, 0x04, 0x08]
    order = [name for name, (_, mask) in
             sorted(panel_control.BUTTON_NAMES.items(),
                    key=lambda item: item[1][1])]
    # A-025 measured MAIN's KIND enumeration; bit index n IS the KIND.
    assert order == ["link", "usb", "sd", "disc"]


@pytest.mark.parametrize("bits", [(20, 3), (21, 3)])
def test_the_two_late_bits_are_driven_now(bits):
    """MENU and MEMORY were decoded, named, and left out of `BUTTON_BITS`.

    Held out on 2026-08-07 only because `plan coverage` was in flight as
    B-016/r160 and two more windows would have moved HEAD under a running
    measurement.  That run is finished; the denominator is 48.
    """
    assert bits in panel_control.BUTTON_BITS
