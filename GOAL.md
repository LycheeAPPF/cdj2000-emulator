# What this emulator is trying to be

A CDJ-2000 that boots its own firmware and can be operated. Not a mock-up of
one: every pixel on the panel is drawn by Pioneer's code running on an emulated
Blackfin, and every button press travels the same path a real press does.

This file is the standing contract. The code and the tests are written against
it, and two tests read it directly.

## What is LCD and what is not

This is the commonest mistake in the project, and it has cost real work twice.

Only the inner rectangle is the 480x234 panel. Everything else on the front of
the player is backlit plastic and appears in no frame dump:

- `BROWSE` / `TAG LIST` / `INFO` / `MENU` across the top are **hardware
  buttons**.
- `LINK` / `USB` / `SD` / `DISC` down the left are **hardware buttons**. They
  are *not* list rows; a browse list containing them is invented content.

The consequence for the operator window is not cosmetic: the virtual buttons
belong **beside** the panel image, never drawn into it. A control painted onto
the LCD is a claim about what the firmware rendered, and it is a false one.
`tests/test_panel_layout.py` enforces both halves -- that the captured frame is
passed through untouched apart from cutting the blanking rows, and that the
picture occupies a grid cell no button block shares.

## What the panel owes, top to bottom

1. A source header: icon plus `USB` or `SD`, above the list.
2. The browse list, in **two columns**. Left: the six category names in
   U+FFFA/U+FFFB brackets. Right: the entries, with a scrollbar at the far
   right. The categories arrive as **type-1 entry text from MAIN**, not from
   firmware string ids -- which is why a browse list is only evidence when the
   file peer is switched off.
3. The player row: a `PLAYER` box with its number, `TRACK` and digits,
   `REMAIN`, `00M:00S 00.0F`, `QUANTIZE`, `TEMPO` with a percentage, and a
   `BPM` box. Brightness carries meaning: `QUANTIZE` grey is inactive, `TEMPO`
   white is active.
4. `NO DISC` in orange, centred under the player row.
5. `±10` in orange, bottom right.

## What is not a fault

- **`NO DISC` belongs on the screen.** An empty drive correctly reports
  `NO DISC`. What must not be there is the centred red caution banner
  (`E-7001: DISC DRIVE ERROR` and its relatives) -- a different thing entirely.
- Neither the "Wait" platter nor the teal `1 2 3` box belongs on a booted
  screen.

## Method

Every rule here was learned expensively.

- **Nothing is asserted that was not measured.** Every claim needs a run.
- **Measure every change against a control run of the same binary.** The board
  has switches for exactly this (`CDJ_*_ABSENT=1`, `CDJ_SDHI_CARD_HIGH=1`); add
  another rather than compare against a different build.
- **Read bus surfaces off the firmware, never off a datasheet.** The SoC is not
  an SH7750 and no datasheet is public. Interrupts come from MAIN's own vector
  table.
- **Run QEMU serially.** Several TCG instances distort the timing races this
  firmware depends on, and a distorted race is a void measurement.
- **A number is not a look.** Enlarge the region before judging it. Counting
  changed pixels tells you something changed, not what.
- **A proven no-op and an unmeasured window are different rows.** They look
  alike in a summary and mean opposite things.
- **Rebuild immediately after touching `emulator/qemu/*.c`.** If the build
  fails the old `qemu-system-sh4` is still there, and every later run keeps
  measuring the old behaviour while looking as though the change did nothing.
  This is the most expensive silent error in the project. Check that the
  binary is newer than the source you changed before you measure.
- **Clean up before starting QEMU.** The runners use fixed ports and take no
  lock. An orphaned process either fails the next run or -- worse -- talks to
  it. `tools/cdj_main/procs.py` exists because fourteen orphaned simulators
  were once found at once.
