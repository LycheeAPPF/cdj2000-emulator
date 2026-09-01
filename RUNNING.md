# Running

Everything below assumes you have built both emulators (BUILD.md) and extracted
your own firmware into `firmware/` (FIRMWARE.md). Run every command from the
repository root.

## The whole player, in a window

```sh
python -m tools.cdj_main.view_vm
```

MAIN boots on QEMU, the GUI board boots on the Blackfin simulator, the two are
linked, and the GUI's framebuffer appears in a window with the player's controls
drawn around it.

**It takes a while to become interesting.** The GUI paints its chrome within a
few seconds, but MAIN only publishes its operating mode once the panel handshake
completes, and the record stream starts after that. Give it a minute or two.

Closing the window stops both boards. QEMU is asked to quit through its monitor
rather than killed, so its log is flushed instead of truncated.

### With a card

```sh
python -m tools.cdj_main.make_sd_image <contents-dir> runs/card.img --size 128M
python -m tools.cdj_main.view_vm --sd runs/card.img
```

`make_sd_image` builds a FAT32 image with an MBR partition where the firmware
expects one. Giving `--sd` also presses the `SD` source key by default, because
nothing else selects the medium.

If you pass a card image that does not exist, `view_vm` says so and stops. That
check exists because MAIN exits before the GUI ever reaches it, and the window
would otherwise show the boot screen for ever without saying why.

### The buttons

The virtual buttons sit **beside** the picture, never on it. `BROWSE`,
`TAG LIST`, `INFO`, `MENU` across the top and `LINK`, `USB`, `SD`, `DISC` down
the left are backlit plastic on the real player and appear in no frame the
firmware draws -- so drawing them onto the panel would be inventing content.
See GOAL.md.

The window refuses to start unless it can reach every input the board decodes.
`view_ui --coverage` prints `48 of 48` and exits non-zero on a gap.

### Driving it from another shell

The window opens a control channel into the running machine, and it is not the
only thing that can talk to it:

```sh
python -m tools.cdj_main.panel_control --port 5984 press sd
python -m tools.cdj_main.panel_control --port 5984 rotary 4 +8
python -m tools.cdj_main.panel_control --port 5984 state
```

A press is a **pulse, not a state**: the firmware's handlers are rising-edge
detectors, so a bit held down for ever is the same as one never pressed. And a
press must be held long enough to land in a status record -- the plans use
2800 ms, and at the board's 300 ms default not one of 24 measured presses ever
arrived. `panel_control` refuses a press below the floor rather than sending one
that cannot be seen.

`--no-control` leaves the channel closed. That is what makes a run a control
run: nothing binds, nothing is merged into the panel payload, and the difference
between two runs is then attributable to the input rather than to the machinery.

## Headless, for a capture

```sh
python -m tools.cdj_main.boot_vm --seconds 150 --output runs/frame.png
```

Same two boards, no window, one frame at the end. It finishes by reading MAIN's
own words back through the QEMU monitor, so the report says what the machine
thought rather than what the picture suggests:

| word | address | means |
|---|---|---|
| panel state | `0x04fe29f4` | non-zero once a panel frame was accepted |
| GuiCom mode | `0x04c06fb0` | 1, 3 or 4 makes the send task serve the GUI |
| ready words | `0x0489bcf4` | both non-zero means the handshake completed |
| bAnsReceive | `0x0489b368` | toggles while answers are coming back |

Useful flags: `--frames DIR --frame-every 2` samples the screen on a fixed grid,
`--caution` decodes MAIN's error store, `--watch ADDR` reads memory as it runs,
and `--no-peer` switches off the canned MAIN answers so that what appears on
screen came from the machine rather than from a file.

## The GUI board on its own

The Blackfin board runs without MAIN if you feed it MAIN's side of the
conversation:

```sh
python -m tools.cdj_gui.main_packet packets/status.bin --mode 2 --player-mask 0xf
python -m tools.cdj_gui.view_ui --packet packets/status.bin
```

The `tools/cdj_gui/build_*` modules generate the rest of the stimulus: status
records, player state, browse lists, marker streams, waveform answers. This is
the fast path when you are working on the display and do not care what MAIN
thinks.

## Did that input change anything?

```sh
# 1. a control run of the same length with nothing pressed, into runs/control/
# 2. the mask of what moves by itself
python -m tools.cdj_main.frame_delta mask runs/control runs/anim-mask.bin --from 60
# 3. score the windows of the real run: <frames-dir> then SECONDS:NAME per input
python -m tools.cdj_main.frame_delta windows runs/frames 150:18.1 175:18.2     --mask runs/anim-mask.bin
```

A single pair, without any of that:

```sh
python -m tools.cdj_main.frame_delta pair runs/before.ppm runs/after.ppm
```

Comparing two frames straight is not enough: parts of the screen animate on
their own, and "the frame changed" is then not evidence of anything. The mask
comes from a control run of the same length with the same switches, and it says
which pixels move by themselves.

A mask from a different world erases exactly the fields that carry the evidence,
so a mask is only usable against the run it was made for.

## Diagnostics

```sh
python -m tools.cdj_main.monitor "1,2,GU"      # MAIN's own service monitor
python -m tools.cdj_main.caution --live        # decode the caution store
python -m tools.cdj_main.gui_handshake         # measure the link handshake
```

`caution` turns MAIN's internal codes into the `E-nnnn` numbers the player would
show: `E-7010` is the audio DSP, `E-7020` the USB device, `E-7001` the disc
drive. One caution per priority survives, so killing the loudest one usually
reveals another that was pending all along.

## Environment

| variable | does |
|---|---|
| `CDJ_QEMU` | path to `qemu-system-sh4` if it is not on `PATH` |
| `CDJ_BFIN_SIM` | path to the Blackfin simulator, default `bin/cdj-run` |
| `CDJ_QEMU_DLL_DIR` | directory of the DLLs a MSYS2-built QEMU needs |
| `CDJ_FIRMWARE_DIR`, `CDJ_PACKETS_DIR`, `CDJ_RUNS_DIR`, `CDJ_BIN_DIR` | move the working directories |

The board itself takes a long list of its own, all read with `getenv` in
`emulator/qemu/`: `CDJ_INPUT_PORT`, `CDJ_PANEL_KEYS`, `CDJ_SD_INSERT`,
`CDJ_DSP_ABSENT`, `CDJ_USB_ABSENT`, `CDJ_ATAPI_ABSENT`, `CDJ_BUS_TRACE` and
more. The simulator likewise: `BFIN_MAIN_LINK`, `BFIN_GUI_OUTPUT`,
`BFIN_GUI_COLOR`, `BFIN_PPI_DMA_DELAY`, `BFIN_SPORT_TX_OUTPUT`. Each is
documented where it is read.

## One QEMU at a time

Run QEMU serially. Several TCG instances distort the timing races this firmware
depends on, and a distorted race is a void measurement. The runners use fixed
ports and take no lock, so an orphaned process either fails the next run or --
worse -- talks to it. `tools/cdj_main/procs.py` cleans up; it exists because
fourteen orphaned simulators were once found at once.
