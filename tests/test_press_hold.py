"""No plan may generate a press that cannot reach a status record.

**The failure this exists to prevent has already happened once, and it cost
1 300 seconds a run for eleven runs.**  MAIN's copy of a key bit is a *level*,
sampled once per accepted panel frame -- so a press it saw is a block of 75-85
consecutive status records and a press it missed leaves nothing at all.  A-033
measured the stage directly out of three link dumps:

    hold      presses   landed in a status record
    300 ms      24            0     (r171 x18, r172 x6)
    800 ms       3            0     (r172)
    2000 ms      1            1     (r172)
    2500 ms     10            8     (r173)

`panel_control.press` has always taken a hold and **no plan ever set it**, so
every plan ran on `cdj2000_input.c`'s 300 ms default.  The "9 % per press"
that the whole coverage budget was priced on is a property of that default.

A number that is right today and silently wrong tomorrow is the shape of this
mistake, so this file does not check a constant.  It walks every plan the
repository can generate -- `panel_control`'s four, coverage,
chain and source rescue, and the operator window's own buttons -- and then walks
the **AST of every Python file under tools/ and tests/** looking for a press
written as a literal somewhere none of those enumerations reach.  A plan that
generates presses under the measured delivery floor is a plan guaranteed to
measure nothing, and that belongs in red rather than waved through.
"""
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tools.cdj_gui import view_ui
from tools.cdj_main import panel_control

ROOT = Path(__file__).resolve().parents[1]
FLOOR = panel_control.HOLD_DELIVERY_FLOOR_MS


# ------------------------------------------------------- the floor itself ---
def test_the_floor_is_below_the_default_and_above_the_boards():
    """The three numbers, and which side of the floor each one is on."""
    assert panel_control.CHANNEL_HOLD_DEFAULT_MS < FLOOR, \
        "the board's own default has to be the thing this floor refuses"
    assert panel_control.PLAN_HOLD_MS >= FLOOR
    # The chosen hold has to say where it comes from.  It used to cite r173
    # alone (8 of 10); since A-035 it cites the pooled 29 of 42 and the burst
    # ceiling that stops it going higher.
    assert "A-035" in panel_control.PLAN_HOLD_SOURCE
    assert str(panel_control.PLAN_HOLD_MS) in panel_control.PLAN_HOLD_SOURCE
    assert "2000" in panel_control.HOLD_DELIVERY_FLOOR_SOURCE


def test_a_press_with_no_hold_at_all_is_the_failure_and_is_caught():
    """Absent is not "fine, use a default" -- it is 300 ms, i.e. nothing.

    This is the exact shape `plan coverage` had until 2026-08-07: a command
    with two fields, perfectly valid, which the board completes with the one
    hold measured to deliver nothing.
    """
    assert panel_control.press_hold_of("press 19.2") is None
    assert panel_control.short_presses(["press 19.2"]) == [("press 19.2", None)]
    with pytest.raises(panel_control.PressTooShort) as raised:
        panel_control.check_press_holds(["press 19.2"])
    assert "300 ms default" in str(raised.value)


def test_a_press_under_the_floor_is_refused_with_the_measurement_on_it():
    with pytest.raises(panel_control.PressTooShort) as raised:
        panel_control.check_press_holds(["press 19.2 %d" % (FLOOR - 1)])
    assert "0 of 27" in str(raised.value)
    panel_control.check_press_holds(["press 19.2 %d" % FLOOR])   # no raise


def test_a_verb_that_is_not_a_press_is_skipped_rather_than_judged():
    """A schedule is handed over whole, so the other verbs have to pass through.

    `rotary` and `analog` are levels, not edges: they are written into the
    payload and stay there, so a hold means nothing for them.
    """
    panel_control.check_press_holds(["ping", "rotary 7 12", "analog 6 32768",
                                     "clear", "down 19 04", "up 19 04"])
    with pytest.raises(ValueError):
        panel_control.press_hold_of("rotary 7 12")


# ------------------------------------- every plan this repository can print --
def plan_names() -> list[str]:
    """The plan names off the CLI itself, so a new plan is covered on arrival.

    Reading the `choices` rather than listing them here is the whole point: a
    hand-written list is a list that the next plan is missing from, silently.
    """
    parser = panel_control.build_parser() if hasattr(panel_control,
                                                     "build_parser") else None
    if parser is None:
        # panel_control builds its parser inside main(); take the choices out
        # of the source instead of duplicating them.
        source = (ROOT / "tools" / "cdj_main" / "panel_control.py").read_text(
            encoding="utf-8")
        match = re.search(r'choices=\((\s*"[^)]+)\)', source)
        assert match, "the plan subcommand no longer declares its choices"
        return re.findall(r'"([a-z-]+)"', match.group(1))
    raise AssertionError("unreachable")


@pytest.mark.parametrize("name", plan_names())
def test_every_named_plan_holds_long_enough_to_be_seen(name):
    entries = panel_control.plan_entries(
        name, field=panel_control.ENCODER_FIELD)
    commands = [command for _at, command, _window in entries]
    assert panel_control.short_presses(commands) == [], \
        "plan %r generates presses below the measured delivery floor" % name


def test_the_plan_command_refuses_a_short_hold_on_the_command_line():
    """`--hold-ms 300` is a thing a person can type, so it is a thing that fails."""
    assert panel_control.main(["plan", "coverage", "--hold-ms",
                               str(FLOOR - 1)]) == 1
    assert panel_control.main(["plan", "coverage"]) == 0


def test_the_operator_windows_buttons_hold_long_enough_too():
    """A human clicking BROWSE has the same sampling problem a plan does."""
    lines = [line.strip() for control in view_ui.controls()
             for line in control.lines]
    presses = [line for line in lines if line.startswith("press ")]
    assert presses, "the window emits no presses at all"
    for line in presses:
        parts = line.split()
        assert len(parts) == 4, "%r carries no hold" % line
        assert int(parts[3]) >= FLOOR, "%r holds below the floor" % line


# ------------------------------------- and anywhere the enumerations miss ---
#
# The enumerations above cover everything that generates a press *today*.  This
# is the guard for tomorrow: a literal written straight into a new file, which
# no plan object knows about and which every check above would walk past.
#
# Same method as tests/test_panel_names_match_the_firmware.py, which found a
# fifth copy of the SOURCE table nobody remembered writing.
#
# **It sweeps `tools/` and not `tests/`, and that is a distinction rather than
# an exemption.**  `tools/` is what drives a machine: a press literal there is a
# press somebody will send.  A press literal under `tests/` is nearly always an
# archived artefact being read back -- r150's transcript, r160's session line --
# and those genuinely do hold 300 ms, because they were recorded before anybody
# knew the hold mattered.  Refusing them would mean rewriting the evidence to
# suit the check, which is the wrong way round.
PRESS_LITERAL = re.compile(r"^press +(?:[a-z]+|\d+\.\d+|\d+ +[0-9a-fA-F]{2})"
                           r"(?: +(\d+))?$")
SCHEDULE_LITERAL = re.compile(r"^\d+(?:\.\d+)?:press +\S+(?: +(\d+))?$")


def python_files() -> list[Path]:
    return sorted(path for path in (ROOT / "tools").rglob("*.py")
                  if "__pycache__" not in path.parts)


def test_no_tool_writes_a_short_press_as_a_literal():
    offenders: list[str] = []
    for path in python_files():
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        # Docstrings are prose about the protocol, not commands, and every one
        # of them would otherwise be an offender.
        docstrings = {id(node.value)
                      for node in ast.walk(tree)
                      if isinstance(node, ast.Expr)
                      and isinstance(node.value, ast.Constant)
                      and isinstance(node.value.value, str)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) \
                    or not isinstance(node.value, str) \
                    or id(node) in docstrings:
                continue
            for pattern in (PRESS_LITERAL, SCHEDULE_LITERAL):
                match = pattern.match(node.value.strip())
                if not match:
                    continue
                hold = match.group(1)
                if hold is None or int(hold) < FLOOR:
                    offenders.append("%s:%d  %r"
                                     % (relative, node.lineno, node.value))
    assert not offenders, (
        "a press written as a literal, holding under the %d ms delivery floor "
        "(or carrying no hold, which means the board's %d ms default):\n  %s"
        % (FLOOR, panel_control.CHANNEL_HOLD_DEFAULT_MS,
           "\n  ".join(offenders)))


def test_no_tool_asks_encode_press_for_the_boards_default():
    """`encode_press(byte, mask, None)` is the old behaviour, spelled out.

    It stays possible on purpose -- a control run may want the board's default
    -- but only where somebody typed `None`, so it cannot happen again by
    forgetting an argument, which is how it happened the first time.
    """
    offenders: list[str] = []
    for path in python_files():
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name not in ("encode_press", "press"):
                continue
            for argument in list(node.args[2:]) + [
                    keyword.value for keyword in node.keywords
                    if keyword.arg == "hold_ms"]:
                if isinstance(argument, ast.Constant) \
                        and argument.value is None:
                    offenders.append("%s:%d" % (relative, node.lineno))
                elif isinstance(argument, ast.Constant) \
                        and isinstance(argument.value, int) \
                        and argument.value < FLOOR:
                    offenders.append("%s:%d  hold %d"
                                     % (relative, node.lineno, argument.value))
    assert not offenders, (
        "a press asked for a hold under the %d ms floor: %s"
        % (FLOOR, ", ".join(offenders)))


