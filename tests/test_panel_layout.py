"""The operator window's geometry, checked against GOAL.md and a stored frame.

`GOAL.md` calls this the commonest mistake in the project:

    Only the inner rectangle is the 480x234 panel.  BROWSE / TAG LIST / INFO /
    MENU across the top and LINK / USB / SD / DISC down the left are hardware
    buttons.  They are *not* list rows; a browse list containing them is
    invented content.  The virtual buttons therefore belong **beside** the
    panel image, not drawn into it.

So the two things asserted here are (a) the frame is passed through untouched
apart from having the blanking cut off, which is the strongest available way to
say "no button is painted onto the LCD", and (b) the picture has a grid cell of
its own that no button block shares.
"""
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from tools.cdj_gui import view_ui
from tools.cdj_main import panel_control

ROOT = Path(__file__).resolve().parents[1]
GOAL = ROOT / "GOAL.md"


# ------------------------------------------------------------------ crop ---
def test_the_panel_is_480x234_out_of_a_255_line_capture():
    assert (view_ui.PANEL_WIDTH, view_ui.PANEL_HEIGHT) == (480, 234)
    assert view_ui.CAPTURE_HEIGHT == 255
    # 140 top surface + 94 bottom surface, per memory cdj-display-geometry.
    assert view_ui.PANEL_HEIGHT == 140 + 94
    assert view_ui.PANEL_CROP == (0, 0, 480, 234)


def test_crop_keeps_every_pixel_of_the_panel_and_paints_nothing(tmp_path):
    """Cropped, not resampled, and not drawn on.

    Resizing 255 lines into 234 would blend rows and the test below would fail
    on the very first row; drawing a button into the picture would fail it too.
    """
    source = Image.new("RGB", (480, 255))
    for y in range(255):
        for x in range(0, 480, 3):
            source.putpixel((x, y), ((x + y) % 251, (x * 7) % 251, y % 251))
    path = tmp_path / "frame.ppm"
    source.save(path)

    with Image.open(path) as stored:
        cropped = view_ui.crop_panel(stored.convert("RGB"))

    assert cropped.size == (480, 234)
    assert cropped.tobytes() == source.crop((0, 0, 480, 234)).tobytes()


def test_crop_leaves_an_already_cropped_frame_alone():
    frame = Image.new("RGB", (480, 234), (1, 2, 3))
    assert view_ui.crop_panel(frame).size == (480, 234)


@pytest.mark.parametrize("path", sorted(
    (ROOT / "runs" / "frames").glob("*.ppm"))[:1])
def test_a_real_frame_has_its_content_above_the_crop_line(path):
    """The 21 discarded lines are blanking, and row 233 is not.

    Evidence is untracked, so this parametrises to nothing when it is absent
    rather than failing on a fresh checkout.
    """
    with Image.open(path) as stored:
        frame = stored.convert("RGB")
    assert frame.size == (480, 255)
    blanking = frame.crop((0, view_ui.PANEL_HEIGHT, 480, 255))
    assert blanking.getbbox() is None, "the discarded band is not blank"
    assert frame.crop((0, 200, 480, 234)).getbbox() is not None


@pytest.mark.parametrize("path", [p for p in
                                  [ROOT / "runs" / "frame.png"]
                                  if p.exists()])
def test_a_frame_from_the_world_these_controls_drive_crops_exactly(path):
    """Not r026's world: a capture from the run the last day's plan is modelled
    on.

    Two things at once.  The crop is byte-exact against a real 255-line
    capture, which is the strongest available way to say no control is painted
    onto the LCD -- every control in this window is a widget in another grid
    cell and has no panel coordinate at all.  And the jog rectangle the memory
    `cdj-display-geometry` records (98,50,56,52) lies inside the cropped panel,
    so the jog *display* stays where the firmware paints it while the jog
    *control* sits beside the picture.
    """
    with Image.open(path) as stored:
        frame = stored.convert("RGB")
    assert frame.size == (480, view_ui.CAPTURE_HEIGHT)
    # The 21 lines that are cut away carry nothing and the last active row
    # carries something: that is what makes 234 a crop line rather than a
    # resample ratio, on this capture and not only on r026's.
    assert frame.crop((0, view_ui.PANEL_HEIGHT, 480,
                       view_ui.CAPTURE_HEIGHT)).getbbox() is None
    assert frame.crop((0, 200, 480, view_ui.PANEL_HEIGHT)).getbbox() is not None
    cropped = view_ui.crop_panel(frame)
    x, y, width, height = 98, 50, 56, 52
    assert x + width <= view_ui.PANEL_WIDTH
    assert y + height <= view_ui.PANEL_HEIGHT
    assert cropped.crop((x, y, x + width, y + height)).getbbox() is not None


# ---------------------------------------------------------------- layout ---
def test_the_panel_has_a_cell_of_its_own():
    cells = view_ui.LAYOUT
    panel = cells["panel"]
    for name in ("top", "left", "side", "analog", "channel"):
        assert cells[name] != panel, f"{name} shares the panel's cell"


def test_the_top_row_is_above_and_the_left_column_is_left():
    panel = view_ui.LAYOUT["panel"]
    assert view_ui.LAYOUT["top"][0] < panel[0]
    assert view_ui.LAYOUT["left"][1] < panel[1]
    assert view_ui.LAYOUT["side"][1] > panel[1]


def test_the_hardware_buttons_are_the_ones_goal_md_names():
    text = GOAL.read_text(encoding="utf-8")
    top = [label for label, _ in view_ui.TOP_BUTTONS]
    left = [label for label, _ in view_ui.LEFT_BUTTONS]
    assert top == ["BROWSE", "TAG LIST", "INFO", "MENU"]
    assert left == ["LINK", "USB", "SD", "DISC"]
    for label in top + left:
        assert label in text, f"GOAL.md no longer mentions {label}"


def test_the_source_keys_are_bound_to_their_measured_bits():
    """LINK/USB/SD/DISC are known from 0x28ddc8, so they are wired for real.

    **These four were reversed here until 2026-08-07**, and the assertion below
    was one of the five places that agreed with the reversal -- which is why
    the check that matters is not this one but
    `tests/test_panel_names_match_the_firmware.py`, where the same four masks
    are compared with MAIN's own name table instead of with each other.
    """
    bound = {label: panel_control.button_mask(key)
             for label, key in view_ui.LEFT_BUTTONS if key}
    assert bound == {
        "LINK": (19, 0x01),
        "USB": (19, 0x02),
        "SD": (19, 0x04),
        "DISC": (19, 0x08),
    }


def test_the_top_row_is_bound_to_the_firmwares_own_names():
    """BROWSE/TAG LIST/INFO/MENU are 20.0..20.3, and that is not a guess.

    It used to be one, and this test used to assert the opposite: position on
    the front panel is not evidence, so the four stayed unbound.  What changed
    is that MAIN's SERVICE MODE name table was read out of the image, and it
    names 20.0..20.3 BROWSE / TAG LIST / INFORMATION / MENU in that order --
    the same table, and the same reading, that settled the four SOURCE keys
    whose host-side labels turned out to be backwards.

    `INFO` keeps GOAL.md's spelling on the key; the alias is data in
    `test_panel_names_match_the_firmware.LABEL_ALIASES`.
    """
    assert [key for _, key in view_ui.TOP_BUTTONS] == ["20.0", "20.1", "20.2",
                                                       "20.3"]
    assert all(panel_control.button_mask(key)[0] == 20
               for _, key in view_ui.TOP_BUTTONS)


def test_every_decoded_bit_is_reachable_from_the_window():
    """Goal 3 wants all of them driven, so all of them must have a button.

    40, not 22: the decoder starts at 0x28e1ae and reads payload bytes 15, 16
    and 17 as bit sources before it reaches byte 18, where the old table began,
    and 0x28e59a/0x28e61e decode 20.3 and 21.3 after it.
    """
    assert len(panel_control.BUTTON_BITS) == 40


# The inputs that have no row in any world table, because no run has ever
# driven them.  20.3 (`MENU`) and 21.3 (`MEMORY`) were decoded by the firmware
# and missing from `BUTTON_BITS` until 2026-08-07, so every plan that ever ran
# skipped them.  Named here rather than tolerated, for the same reason
# `DECODED_BUT_NOT_DRIVEN` existed: a set that has to be edited is a decision,
# and an absence that nothing counts is a hole.  The first `plan coverage` run
# after 2026-08-07 drives both, and this set then goes back to being empty.
NEVER_DRIVEN = {"20.3", "21.3"}


def test_the_manifest_verdicts_parse():
    verdicts = view_ui.manifest_verdicts()
    # Every input the board decodes, not only the 38 bits.  The eight analogue
    # fields were measured in r115/r117 and then written up in prose while the
    # bits each got a row, and that asymmetry is exactly what let both the
    # canonical plan and this window leave them out without saying so.
    assert set(panel_control.input_ids()) - set(verdicts) == NEVER_DRIVEN
    assert set(verdicts) - set(panel_control.input_ids()) == set()
    # The newest table wins, and it says which run it came from.  Showing a
    # verdict without its world is how 372 bytes measured on a screen with a
    # "Wait" platter would end up describing a screen that has none.
    assert all(world for _, world in verdicts.values())
    # The newest table for a given bit wins, and that is no longer one run for
    # all 38.  Each bit must show the last run that drove it.
    expected = {}
    for byte, bit in panel_control.BUTTON_BITS:
        if "%d.%d" % (byte, bit) in NEVER_DRIVEN:
            continue
        if (byte, bit) in {(17, 0), (19, 0), (19, 1), (19, 2), (19, 3),
                           (20, 0)}:
            # r133/r134 are the runs with a key-dispatcher trace, so they are
            # the last word on the bits they drove -- including the two that
            # open the library screen and the one whose 4 582 bytes turned out
            # to have no dispatch behind them.
            expected["%d.%d" % (byte, bit)] = "r133/r134"
        elif byte in (15, 16, 17):
            expected["%d.%d" % (byte, bit)] = "r117"   # the sixteen late bits
        else:
            expected["%d.%d" % (byte, bit)] = "r116"
    for index in range(len(panel_control.ANALOG_FIELDS)):
        expected["field%d" % index] = "r115/r117"
    assert {k: w for k, (_, w) in verdicts.items()} == expected
    # r113 reported 18.7 as the one attributed change; r116 drove the same bit
    # on the same binary and got a proven no-op.  The window has to show the
    # later reading, or it would present an unconfirmed pairing as a fact.
    assert verdicts["18.7"][0].startswith("no-op, proven")
    assert verdicts["18.0"] == ("no-op, proven", "r116")
    # The sixteen bits that had never been sent were sent in r117, and the
    # window must not still be offering "never driven" for them.
    assert verdicts["16.3"] == ("no-op, proven", "r117")
    # And 19.1 must show the attribution, not the zero that preceded it: it is
    # the one row in this file that passes every guard including the trace.
    assert verdicts["19.1"][0].startswith("changes the display")
    # 17.0's arm is a plain RTS, which is a no-op nothing later can undermine.
    assert "plain RTS" in verdicts["17.0"][0]
    # field7 is the encoder, and its row has to say it arrived -- a zero from a
    # value that never reached 0x04fe2a44 would be a fact about the channel.
    assert "0x04fe2a44" in verdicts["field7"][0]
    # field6's row must carry the limit, not a bare no-op: bit 15 has a
    # destination of its own and no run has ever set it.
    assert "position only" in verdicts["field6"][0]


# --------------------------------------------------------------- coverage ---
#
# The question these answer is the one that found an eight-input hole in the
# canonical plan, asked of the click surface instead: can a human trigger every
# input from this window, or only a subset, with the window silent about the
# rest?  It was a subset -- 38 of 46 -- and the missing eight were the same
# eight, for the same reason: the analogue half had no controls of its own.
def test_every_input_on_the_board_has_a_control():
    reached, missing, stray = view_ui.coverage()
    assert missing == [], "no control for %s" % ", ".join(missing)
    assert stray == [], "controls for inputs this board does not decode: %s" % stray
    assert len(reached) == len(panel_control.BUTTON_BITS) \
        + len(panel_control.ANALOG_FIELDS) == 48


def test_each_analogue_field_has_a_control_of_its_own():
    """Not one spinbox holding a field number for all eight.

    A control you reach by typing its index into a box is a command line with
    a button on it, and the box's caption -- "which field is the encoder is not
    identified; sweep 0..6" -- had been contradicted by
    `panel_control.ENCODER_FIELD` for as long as that constant existed.
    """
    fields = [control for control in view_ui.controls()
              if control.group == "analog" and control.kind != "touch"]
    assert [control.input_id for control in fields] == \
        ["field%d" % index for index in range(len(panel_control.ANALOG_FIELDS))]
    for control in fields:
        # Both directions, because a rotary the manifest asks about is "left,
        # right and press" and left is the same field walked the other way.
        deltas = [int(line.split()[2]) for line in control.lines
                  if line.startswith("rotary")]
        assert min(deltas) < 0 < max(deltas), \
            "%s can only be turned one way" % control.label


def test_the_encoder_is_labelled_as_the_one_that_was_measured():
    encoder = [control for control in view_ui.controls()
               if control.input_id == "field%d" % panel_control.ENCODER_FIELD]
    assert len(encoder) == 1
    assert encoder[0].kind == "encoder"
    assert "0x04fe2a44" in encoder[0].note


def test_the_flag_inside_field_six_has_a_control_and_rotary_is_not_it():
    """The input nothing in this project could reach until it had a control.

    0x28e230 tests bytes 12/13 against 0x8000 before the position is masked to
    0x1ff, so the flag has a byte of its own.  `rotary` walks one count per
    panel exchange; `analog` is the only verb that can set it.
    """
    touch = [control for control in view_ui.controls()
             if control.kind == "touch"]
    assert len(touch) == 1
    assert touch[0].input_id == "field%d-touch" % panel_control.ANALOG_TOUCH_FIELD
    assert all(line.startswith("analog") for line in touch[0].lines)
    assert any(int(line.split()[2]) & panel_control.ANALOG_TOUCH_MASK
               for line in touch[0].lines)


def test_a_key_with_no_measured_bit_refuses_out_loud():
    """Refuse rather than guess -- and be heard doing it.

    **The window has no unbound key today**, because MAIN's own name table
    bound the last four.  That is exactly when this machinery rots: it was
    unreachable code once already (the four top keys were built `disabled`, so
    their `command` never ran and the sentence explaining them could not be
    reached), and "nothing is unbound right now" would make it unreachable
    again -- silently, and in the direction that matters, because the next key
    somebody adds without evidence has to refuse.

    So the refusal is exercised against a control built here, in the shape
    `button_controls()` produces for a key with no measured bit.
    """
    unbound = view_ui.Control("SOME NEW KEY", None, "button", "top", (),
                              "no run has attributed a payload bit to this key")
    reason = view_ui.refusal(unbound, 5984)
    assert reason and unbound.label in reason
    assert "not evidence" in reason
    # And it has to be visible before the click, in a way the default Windows
    # ttk theme cannot swallow: it draws TButton from a bitmap and ignores the
    # foreground the style sets.
    assert view_ui.button_text(unbound) != unbound.label
    for control in view_ui.controls():
        if control.input_id:
            assert view_ui.button_text(control) == control.label


def test_every_key_the_window_shows_now_has_a_bit_behind_it():
    """The state of the board today, asserted so a regression is visible.

    Not a restatement of the test above: that one is about what happens to an
    unbound key, this one is about there being none.  If a future key arrives
    without evidence this goes red and the reason is on the line.
    """
    unbound = [control.label for control in view_ui.controls()
               if control.input_id is None and control.kind != "channel"]
    assert unbound == [], "no payload bit is attributed to %s" % unbound


def test_without_a_channel_every_control_refuses_and_says_which_switch():
    for control in view_ui.controls():
        reason = view_ui.refusal(control, 0)
        assert reason, "%s pretends to work with no channel" % control.label
    # ...and with one, everything that names an input goes through.
    for control in view_ui.controls():
        if control.input_id or control.kind == "channel":
            assert view_ui.refusal(control, 5984) is None


def test_the_coverage_line_names_what_is_missing():
    """The line the window shows about itself, and the failure it would report.

    A gap that only shows up in a report six weeks later is the failure mode
    this whole exercise is about, twice over.
    """
    assert view_ui.coverage_line().startswith("48 of 48 inputs have a control")
    thinned = [control for control in view_ui.controls()
               if control.input_id != "field7"]
    assert "NO CONTROL FOR field7" in view_ui.coverage_line(thinned)


def test_the_window_and_the_plan_count_the_same_inputs():
    """One namespace for the window, the plan and the manifest.

    `plan coverage`'s window names are what `frame_delta windows` scores and
    what the manifest's rows are keyed by, so a control that named its input
    differently would be a fourth list nobody could join to the other three.
    """
    entries = panel_control.plan_entries(
        "coverage", field=panel_control.ENCODER_FIELD)
    windows = {name for _, name in panel_control.plan_windows(entries)}
    reached = {control.input_id.split("-")[0]
               for control in view_ui.controls() if control.input_id}
    # `rotary-left` is the encoder's second direction, which is a window in the
    # plan and a button on one control here.
    assert windows - {"rotary-left"} == reached
    # The manifest has a row for every input a run has ever driven, which is
    # everything except the two the firmware decodes and no plan ever sent --
    # see NEVER_DRIVEN above.  The first `plan coverage` run after 2026-08-07
    # closes that gap and this subtraction goes to nothing.
    assert reached - set(view_ui.manifest_verdicts()) == NEVER_DRIVEN
    assert set(view_ui.manifest_verdicts()) - reached == set()


def test_a_bit_named_outside_a_button_table_is_not_a_verdict(tmp_path):
    """The manifest has other tables keyed by a bit name, and they are captions.

    Picking rows up by shape alone would put "the tempo range digits" where a
    verdict belongs, because that table comes later in the file and would win.
    """
    manifest = tmp_path / "INPUT_MANIFEST.md"
    manifest.write_text(
        "## The buttons in the r096 world\n"
        "| payload | window | verdict |\n"
        "|---|---|---|\n"
        "| 19.0 | 7.0 s | changes the display |\n"
        "\n"
        "## Looking, not only counting\n"
        "| window | delta | box | what the crop shows |\n"
        "|---|---|---|---|\n"
        "| 19.0 | 906 | 444,203..478,223 | the tempo range digits |\n",
        encoding="utf-8")
    verdicts = view_ui.manifest_verdicts(manifest)
    assert verdicts == {"19.0": ("changes the display", "r096")}
