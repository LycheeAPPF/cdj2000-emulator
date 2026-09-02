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

**It takes half a minute to become interesting.** Measured with
`boot_vm --poll-every 5 --frames`: black until about 15 s, the Pioneer logo at
about 20 s, the rekordbox logo at 25 s, and the player screen showing `NO DISC`
-- the handshake with MAIN complete, the record stream running -- at about
35 s. MAIN's handshake words (`GuiCom mode`, the ready words) are set within
the first three seconds; the rest is MAIN's own boot sequence.

**And it may still not get there.** The GUI board's double fault at
`0x00b99196` is a link race (README, "Read this first"): a plain status record
lands on an announcement the firmware is mid-transaction on, halfword 30 reads
0 where it validated 112, and the checksum loop walks off the CPLB map. On the
wall-clock time base it hit four of six 90 s boots with the simulator's
defaults and none of three with `BFIN_LINK_ANNOUNCE_STICKY=1`, which the
launchers therefore set; pass `--gui-env BFIN_LINK_ANNOUNCE_STICKY=` to
`boot_vm` to switch it off for an A/B. The earlier note that the switch made no
difference was measured on the instruction-counted time base, where the race
was much less likely to be hit at all. The simulator writes the fault line to
its log:

```sh
tail "$TEMP/vm-ui-sim.log"      # or vm-gui.log for a headless run
```

A frozen panel with no fault line in the log is a different thing: that is the
firmware simply not having drawn anything new, which is normal for long
stretches.

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
See the README.

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

## Speed

The GUI board's simulator interprets Blackfin instructions, and it used to
count guest time in instructions: every firmware delay, time-out and animation
stretched by however slow the interpreter was -- a factor of eleven, and a
five-minute boot. It now runs on the **wall clock** (`BFIN_TIME_BASE=wall`, the
default): guest ticks are delivered at `BFIN_CCLK_HZ` per second (400 MHz, the
clock the firmware programs its core timer for), and when the firmware parks
its main loop or executes `IDLE` the simulator sleeps until the next event or
the next record from MAIN. Guest time never runs ahead of the wall clock, so
this board and the QEMU board, which was always on the wall clock, see the same
time. Bursts the interpreter cannot keep up with make guest time fall behind,
up to `BFIN_WALL_LAG_MS` (50); the excess is dropped and reported.

The display DMA is paced per frame at `BFIN_PPI_FPS` (60) instead of one
scanline per simulated cycle, and a receive with nothing to hand over retries
after `BFIN_SPORT_RETRY_US` (1000) of guest time. `--ppi-delay` on the
launchers still forces a per-line tick count for a run that needs the old
pacing.

Give the simulator `BFIN_STATS=<seconds>` and it prints a line per interval to
its log: instructions and MIPS, guest ticks, guest seconds against wall
seconds, time spent parked, dropped lag, frames scanned and published, bytes
from the link. `boot_vm --poll-every 5` prints MAIN's side on the same grid --
the handshake words, the RTOS tick counter and its rate, the CPU time of both
emulators -- and `--poll-output` keeps it as TSV.

A sampling profiler is built into the simulator for Windows, because gprof
on MinGW produces call counts but no samples: `BFIN_SAMPLE_PROFILE=<file>`
records the run thread's instruction pointer about a thousand times a second
(from `BFIN_SAMPLE_PROFILE_AT=<seconds>` on, if given), and `addr2line -f -e
bin/cdj-run.exe` resolves the addresses after rebasing them to `0x140000000`;
the first eight bytes of the file are the module base. This is what showed
that the GUI board sleeps through most of a boot and that the display
conversion, not the interpreter, was the largest consumer of the rest.

Two switches exist for reproducing a run rather than timing it:
`BFIN_TIME_BASE=insn` is the old instruction-counted time, and
`BFIN_EXIT_AFTER_TICKS=<n>` halts at a guest time, so two builds run to the
same tick count from the same packet file must produce the same picture and
the same instruction count. `BFIN_EXIT_AFTER_WALL=<seconds>` halts after a
wall time, which is what a `gprof` build needs to write its data.

Measured on an i7-13700H, GUI board alone (`run_headless --packet
packets/status.bin --seconds 60`):

| simulator | MIPS | guest ticks/s | wall clock to 1e9 ticks |
|---|---|---|---|
| as shipped before September 2026 | 8.4 | 41 M | 32.7 s |
| probes gated, page cache | 18.7 | 91 M | 10.9 s |
| + CPLB memo, no `getenv` on hot paths | 31.7 | 150 M | 6.6 s |
| + wall-clock time base | guest = wall, 400 M ticks/s | | |
| + display converted only on change | 2e9 ticks in 7.1 s (was 15.0 s), instruction-counted | | |
| + PLL lock event, MMU check without a call | 40 MIPS busy (was 28) on the same firmware timeline; 2.7 s off the top of every boot | | |

Two of those came out of the profiler after the time base was in. The first
`IDLE` of a boot -- the PLL programming sequence, `SIC_IWR`, `PLL_CTL`,
`PLL_DIV`, `IDLE` -- slept 2.68 s, because the PLL model had no lock event
and the only event pending that early was the simulator's own poll event; the
PLL now locks after `PLL_LOCKCNT` system clocks, and an `IDLE` whose wake-up
has already happened returns at once, as the chip does when `SIC_IWR` already
shows the source. The MMU check's memo hit still made two calls per access; a
window per table now answers an aligned, granted access inline. A deliberate
wait is no longer charged as lag either: an event further away than the lag
cap fires once, at its time, instead of being chased in 50 ms slices. The
full boot now shows the player screen at 28-34 s (was 34-36).

**What the SOURCE key costs.** Measured with `boot_vm --source-key usb
--source-key-at 40` and `CDJ_PANEL_HOLD_MS=2800` (the default 300 ms hold
reaches MAIN -- `0x04c084d4` goes to 1 -- but the GUI never learns of it): the
`Wait` platter appears six seconds after the key, animates at about 2.4 frames
a second, and the GUI's browse requests switch to the USB source about a
minute later. None of that is interpreter speed: the GUI board runs at
2-5 MIPS the whole time. The platter is the router's answer to a source whose
media state (status word 26) MAIN leaves at zero, which without a USB host it
always does; the latency and the frame rate are the rate at which the GUI
parses MAIN's status records, and MAIN's answers to the GUI's ~60 requests a
second arrive in one burst every 3.000 s -- hundreds of them 0.1 ms apart
behind one status record. Both smelled like a completion reported inside the
arm write, before the task that waits for it is waiting, so each side got a
switch that delays it by a frame's time on the wire: `CDJ_LINK_TX_US` on the
board and `BFIN_SPORT_RX_US` on the simulator. Measured at 500 us, each one
alone: the GUI-side delay stopped the GUI sending any request for 45 s, and
the board-side delay left two boots of three without a player screen. The
two protocol tasks are balanced against each other by timing on both sides,
and moving one edge upsets it. Both switches are off by default and stay in
for the experiment that moves both sides together.

MAIN's RTOS tick, read back through the monitor: 120-880 a second with the
old interrupt patch depending on host load, ~830 of the programmed 1000 with
the decline fix, the real 54 MHz timer clock and the Windows timer resolution
raised to 0.5 ms, and the full 1000 with QEMU's main loop waiting for less
than a millisecond (a patched `util/main-loop.c`; `CDJ_MAIN_LOOP_HIRES=0`
switches it off). At the full rate the GUI board double-faulted at
`0x00b99196` in every run until the launchers set
`BFIN_LINK_ANNOUNCE_STICKY=1`; with it, three of three 90 s boots at 1000
ticks a second were clean. `CDJ_TMU_FREQ` still overrides the timer clock; the
old default of 270 MHz asked for 5000 interrupts a second that the guest never
serviced, and is gone from the launchers.

Measurements are only worth anything on an idle host. A configure script or a
compile running alongside halves the simulator's throughput, and it did so
twice during this work before the rule was learnt.

## Environment

| variable | does |
|---|---|
| `CDJ_QEMU` | path to `qemu-system-sh4` if it is not on `PATH` |
| `CDJ_BFIN_SIM` | path to the Blackfin simulator, default `bin/cdj-run` |
| `CDJ_QEMU_DLL_DIR` | directory of the DLLs a MSYS2-built QEMU needs |
| `CDJ_FIRMWARE_DIR`, `CDJ_PACKETS_DIR`, `CDJ_RUNS_DIR`, `CDJ_BIN_DIR` | move the working directories |

The board itself takes a long list of its own, all read with `getenv` in
`emulator/qemu/`: `CDJ_INPUT_PORT`, `CDJ_PANEL_KEYS`, `CDJ_SD_INSERT`,
`CDJ_DSP_ABSENT`, `CDJ_USB_ABSENT`, `CDJ_ATAPI_ABSENT`, `CDJ_BUS_TRACE`,
`CDJ_LINK_TRACE` (arm, acknowledge and gate lines with virtual-clock stamps,
and the header words of every request delivered), `CDJ_LINK_TX_US` (off) and
more. The simulator likewise: `BFIN_MAIN_LINK`, `BFIN_GUI_OUTPUT`,
`BFIN_GUI_COLOR`, `BFIN_PPI_DMA_DELAY`, `BFIN_SPORT_TX_OUTPUT`,
`BFIN_SPORT_RX_US` (off), and the time-base knobs above: `BFIN_TIME_BASE`,
`BFIN_CCLK_HZ`, `BFIN_PPI_FPS`, `BFIN_SPORT_RETRY_US`, `BFIN_WALL_LAG_MS`,
`BFIN_STATS`, `BFIN_EXIT_AFTER_WALL`, `BFIN_EXIT_AFTER_TICKS`,
`BFIN_MEM_FAST`. Each is documented where it is read.

The simulator's diagnostic probes -- every `BFIN_*_TRACE`, `BFIN_PC_*`,
`BFIN_*_DUMP`, watch, peek and poke variable -- are behind one gate. Setting
any of them puts the whole per-instruction probe path back, which is what the
probes need and costs about a third of the throughput; a plain run pays one
branch per instruction for them. The simulator says `bfin: probes on (NAME is
set)` on its log when that happens, so a slow run can be explained.

## One QEMU at a time

Run QEMU serially. Several TCG instances distort the timing races this firmware
depends on, and a distorted race is a void measurement. The runners use fixed
ports and take no lock, so an orphaned process either fails the next run or --
worse -- talks to it. `tools/cdj_main/procs.py` cleans up; it exists because
fourteen orphaned simulators were once found at once.
