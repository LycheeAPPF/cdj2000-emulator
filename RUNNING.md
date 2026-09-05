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
python -m tools.cdj_gui.decode_link_dump run/main-link-dump.bin   # what MAIN's link handed the GUI
python -m tools.cdj_main.link_exchanges run/vm-main.log --dump run/main-link-dump.bin   # every request against MAIN's answers
```

`decode_link_dump` reads a `BFIN_MAIN_LINK_DUMP` (every record the simulator
handed the GUI firmware) and prints the status-word changes, every payload with
its list rows or player-state strings, and which announced payload lengths
were never delivered -- the shape of a frame the link lost.

`link_exchanges` reads MAIN's own log (`boot_vm --main-output`), pairs every
request the GUI sent (a delivered frame with bit 15 of word 1 set) with the
payloads MAIN sent before the next request, and prints a table per request
class -- type, cursor, words 3..5 -- of how often it was asked, how often a
payload answered it, which (length and command word), how fast, and how many
times the GUI asked again meanwhile; then the classes MAIN never answered
with a payload, and the frames delivered less than 0.5 ms after the one
before, which is the shape of a request the firmware read twice or not at
all. With `--dump` one payload of every answer signature is decoded from the
GUI's dump. On `trackload-45-final` it says, for instance, that the NXS
GUI's type-9 player-state request is answered by MAIN 4.33 with 224 bytes
under command `0x0009` (not the `0x19` layout the GUI tools build), that the
type-7 encoder-LED commands never get a payload, and that 93 of 8263 frames
went in back to back (see `CDJ_LINK_RX_GAP_US`).

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
| + no `sprintf` per instruction | 45.7 ns per instruction busy (was 48), wall-clock run | | |

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
full boot now shows the player screen at 28-34 s (was 34-36). The LZSS
accelerator was suspected for the same seconds and is innocent: with
`BFIN_FAST_LZSS_TRACE=1` all ten resource banks go through it natively
inside the first second of a boot, and the guest's own byte loop never
runs. The last per-instruction cost the profiler found was the
instruction text itself: every decoder formatted its immediate operands
with `sprintf` for a trace line nothing printed; that is gated now.

**Switching to a medium.** With a card image (`--sd card.img`, a rekordbox
export on it) the launchers put the card in at 10 s and press its key at
12 s, before the GUI's first browse, and the card's library -- the `SD`
header, the six categories, the card's folders and playlists -- is on the
screen together with the player screen, at 31-35 s, seven runs of seven.

Pressing the card's key on a running machine is a different path, and it
had two blockers. The first was ours: the board used to report the card
on the wrong media-state word. Status halfword 26 carries a 3-bit state
per source in MAIN's own order -- bits [11:9] LINK, [8:6] USB, [5:3] SD,
[2:0] DISC, built from the words at `0x0489bd68/6c/70/74` in that order --
and the GUI's key dispatcher (`0xb9b98c`) routes a SOURCE key on the state
of the source MAIN reports as current. The board held `0x0489bd6c` at 1,
which is the stick's word, so the SD key always found 0 and went to the
`Wait` platter (measured with the GUI's table watched: w4). Worse, holding
any of those words is harmful: with it MAIN answered the card's list
request with one-row records for 20 s (e2), without it the lists came at
once (e5, m3). The report is now opt-in (`CDJ_SD_MEDIA_STATE=1`,
`CDJ_SD_MOUNT_S`) and points at the SD word.

The second is the GUI's browse loop for the boot source, and it is a
command nibble. After the player screen the GUI browses the LINK source
(type 1, cursor 3, KIND 0) 30-40 times a second; MAIN, with no network
behind LINK, answers each poll with the one-row list `NO DISC` stamped as
command `0x11`, the answer to a cursor-*1* request. The GUI's consumer
(`0xb7eb48`) compares the command's low nibble with the cursor of the
request it has outstanding, flags the mismatch and re-sends -- for ever,
consuming every answer on the way (`BFIN_RECORD_TRACE`: 88 in 30 s). The
loop ended on its own only when the GUI was slowed down (three machines on
the host), and a link dump of such a run (e4) shows why: under load MAIN's
answers came stamped `0x13`. So the board now stamps the nibble of the
request being answered into MAIN's answer on the way out and re-stamps the
checksum (`CDJ_LINK_LINK_ROWS=match`, the default; `off` sends MAIN's
bytes untouched, `drop`/`empty` were tried and change nothing): the loop
is over by 40 s in six runs of six.

With both in place the SD key on a running machine brought the card's
library in 1.5-5.6 s in 3 runs of 9. In the others the key reached MAIN
(`0x04c084d8` goes to 1) but the GUI never saw halfword 18 change, because
MAIN's status records stopped flowing: 1-11 per 5 s against 200-400 in the
runs that worked. The GUI polls status in two shapes -- `0001 0000 ...`
when it opens a query and `0000 8000 ...` on every repeat -- and MAIN
answers the first at once and the second on its 3 s timer; once one answer
is late the GUI repeats and the rate collapses. `CDJ_REQ_STATUS_FRESH`
(on by default now) rewrites the repeat into the fresh shape, both words
(clearing bit 15 alone leaves an all-zero header, which MAIN answers by
re-sending its last browse answer). With it the key brings the library in
0.6-2.6 s in 6 runs of 8 (n1-n3, t1-t2, p1-p3). The two failures are the
open end: MAIN answering `0000 0000` polls -- the GUI fetching a payload
MAIN's record still announces -- with its last 48-byte browse answer, and
the card's list request with that same stale answer (n1 at 55.66 s).
Give a freshly inserted card ~25 s before pressing its key: MAIN's scan of
the card takes that long at 40 MIPS, and a key before it is done gets
one-row answers the GUI gives up on after ~5 s (m1).

Switching **away** from the card works: the USB key with no stick shows
the platter 1.5-3.5 s after the key, three of three.

**Loading a track.** The track list of a playlist is one 896-byte link
frame, and until 2026-09-03 both link models threw anything over 512 bytes
away -- the board without reporting the completion, which left MAIN's sender
dead for the rest of the run (`runs/nxs-swap/twoboard-10-load`: the answer
announced in 9 545 status records, never delivered). Both carry 4096 bytes
now, the protocol's own limit. What the browse keys of the NXS GUI do on this
MAIN is measured in `runs/nxs-swap/NOTE-trackload-2026-09-03.md`: the select
encoder (`rotary 7`) moves the left column, the pane follows, and no key was
found that sends the "enter" request. So the requests are injected instead:
`tools/cdj_main/link_inject.py` sits between the GUI (`BFIN_MAIN_LINK=
127.0.0.1:5990`) and MAIN and sends, at given seconds after the GUI connected,
an ENTER (`type 1 cursor 3, words 7 1 N` -- row N of MAIN's current list
becomes the list) or a LOAD (`type 7 cursor 1, words 0 N` -- the track at
index N of the current list). From the library screen, `--inject 90:1:3:7:1:0
--inject 110:1:3:7:1:0 --inject 130:1:3:7:1:0 --inject 155:7:1:0:0` opens
PLAYLIST, its first folder, that folder's first playlist -- the track list
draws -- and loads its first track: TRACK 01, overview waveform, and after a
second load the duration, BPM and key of the track (`trackload-12-load`,
frames t165-t250). MAIN's own console (`CDJ_DEBUG_CONSOLE=70`, the file
`TEMP/vm-console.txt`, Shift-JIS) narrates it: `BlackFin → ロード要求`,
`♪WAVE[0,400]`, `♪CUE(U/S)[3]`; arm it after the library is up, at 5 s it
broke the card switch.

The load itself is a handshake with the audio DSP, and the built-in DSP
model answers it only with `CDJ_DSP_ACK=1` (off by default; see the comments
in `emulator/qemu/cdj2000_dsp_model.c` for every word and the run that
measured it). With it MAIN writes the load's parameters, the stream format
and header into the DSP window, reads the file from the card and streams it
into the DSP over DMAC channel 5 -- the whole track, the way the player loads
into the DSP's 32 MB of SDRAM -- and reports the load complete when the last
buffer is in: `Musicﾛｰﾄﾞ要求完了通知受理 PL→全`, NOW LOADING ends, the
tempo field shows the track's BPM (`trackload-36-levels`, ~130 s of guest
time for a 3:17 WAV). Without the switch the load stays pending behind NOW
LOADING, or stops with `E-8302` once the model answers some words but not
others. `CDJ_DSP_TRACE=1` prints every acknowledged request with its
parameters, a per-second census of the control block's changed words and the
two buffer levels. The recipe that held (trackload-49..52, four of four): the
card given at launch (`--sd`), the SD SOURCE key at 60 s (`--source-key sd
--source-key-at 60` -- the card alone leaves the NXS GUI browsing LINK, and
its cursor-3 polls then collide with the injected ENTERs, trackload-47/48),
ENTERs at 90/110/130 and the LOAD at 155 s of the injector's clock; the
receive gap (`CDJ_LINK_RX_GAP_US`) is what made it hold. That recipe is a
tool:

```
python -m tools.cdj_main.twoboard trackload-60 --card runs/nxs-swap/rbstick1g.img
python -m tools.cdj_main.twoboard play-1 --card CARD --keys keys.txt --env CDJ_DSP_ACK=1 \
    --bootvm-arg=--trace=0x41bd304 --dry-run
```

`twoboard` starts MAIN (`boot_vm`, the card's work copy at launch, the SD key
at 60 s), the injecting proxy (`link_inject`, the four requests above unless
`--inject` or `--no-inject` says otherwise) and the GUI (`run_headless`),
presses panel keys from a `SECONDS ARGS` file through `panel_control`, and
leaves logs, frames, request dump, MAIN console and a README with every
command line in a fresh directory under `runs/nxs-swap/` -- an existing one is
refused, so runs never overwrite each other. `--env` is MAIN board environment
(`CDJ_*`), `--gui-env` the simulator's, `--bootvm-arg` anything else for
`boot_vm`; the gdb stub stays free for `--trace` because nothing is polled
unless `--poll-words` asks. Still blank: the time fields of the status record (words
5..8 read `0xbbbb`), because the position the DSP reports in its slot table
at window+0x7ce0 is not modelled yet (a guessed one, `trackload-38-memplay`,
changed nothing there). There is no audio path.

**The update file is not what the emulator boots.** The board loads
`firmware/main-unpacked.bin` -- the address-zero flash image, decoded from
`C2KMAIN.UPD` by `tools.cdj_gui.main_unpack` -- into its NOR flash model (a
CFI02 device in RAM, so the settings sectors the firmware erases and rewrites
never reach the file). A modified image is tested by putting it there; nothing
in the emulator exercises the updater. What the device itself checks on an
update file is host-side arithmetic: Motorola S-records with per-record
checksums, a little-endian CRC-16/XMODEM trailer over the container, the two
LZSS-packed application regions at 0x10000 and 0x40000 with their additive
checksums, and the model/version header at image offset 0x700 (`PIONEER`,
`CDJ-2000`, `4.33`, `20150209`). `main_unpack` verifies the first three on the
way in; the CDJ2000-revival repository's `build_main_patch.py` re-emits all of
them and refuses to patch unless the stock file round-trips byte for byte. The
updater task that reads the file (`UpDtae_TASK`, "*** Update END ! ***") lives
in the application image but names no file -- the 4.33 image contains neither
`.UPD` nor `C2K` in any encoding -- so its trigger and its medium handling are
open. Running it in the emulator would need a USB mass-storage or SD image
carrying the file, the menu path that starts it, and the flash model taking
the rewrite; that is a project of its own, and the decision here is not to
build it: the file's acceptance is proven on the host (unpack what was built,
compare with what was booted), the firmware change itself in the emulator.

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
behind one status record. MAIN builds a fresh status record on that same
3 s cadence when nothing changes, and a key is copied into the record built
while it is down, which is why `panel_control` holds a press for 2.8 s.

Both smelled like a completion reported inside the arm write, before the
task that waits for it is waiting, so each side got a switch that delays it
by a frame's time on the wire (the SPORT transmits at SCLK/34, about
2.9 Mbit/s, so a 64-byte record is ~180 us; the receive clock comes from
MAIN): `CDJ_LINK_TX_US` on the board and `BFIN_SPORT_RX_US` on the
simulator, the latter on the 64-byte record receives only. Measured, each
alone at 500 us and 200 us: the GUI-side delay left the screen black until
t94 in one run and the GUI silent for 45 s in another, the board-side delay
left two boots of three without a player screen. Together with a deep
receive ring on the GUI side (`BFIN_LINK_DEPTH=128`, so a plain record
cannot land on an announcement before its payload is read) the board-side
delay boots cleanly -- three of three -- and in one run of three the
browse requests moved to the USB source within ten seconds of the key
instead of a minute; the platter still came four seconds after the key at
~2 fps, and MAIN's idle cadence stayed at 3 s. That is not enough to change
a default: the two protocol tasks are balanced against each other by timing
on both sides, and every switch is off unless set. The launchers keep the
configuration that boots six times of seven.

The same cadence decides every key. A click in the `view_vm` window holds
the key 3.3 s, long enough to span one of MAIN's records, and the screen
follows about five seconds after the click. The firmware tells a short
press from a held one by whether the key is still down in the *next*
record, so a held key on this link is one held across two record builds:
Shift-click holds 6.5 s. Measured on `MENU`: 1.5 s and 3.3 s open the CUE
LINK box, 7 s opens the UTILITY screen, with its list empty because the
entries are payloads MAIN does not deliver. A second click on the same key
while the first is still down is refused with the time left: the board
queues presses one behind the other, and a queued MENU closes what the
first one opened, which is what repeated clicking looked like. Right-click
holds a key down until the next right-click.

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
and the header words of every request delivered), `CDJ_LINK_TX_US` (off),
`CDJ_LINK_RX_GAP_US` (the least guest time between two GUI frames going into
MAIN's receive buffer, default 2000: the simulator delivers frames in bursts
and two of them 0.1 ms apart made GuiCom_RcvTASK read the second twice --
an injected LOAD taken twice failed the load in `trackload-42-final`; 0
restores the burst) and `CDJ_LINK_RX_HANDOVER=answer` (hand the next queued
frame over only when MAIN transmits; measured worse in `trackload-48-launch2`
and kept for the A/B),
`CDJ_NO_PANEL` and `CDJ_NO_USB_POWER` (the two input bits of GPIO
`0xfff10060` the board holds high: the panel-present bit and the USB power
switch's sense line, whose absence made MAIN raise caution `0x92` "USB Error"
every 50 polls), `CDJ_DSP_ACK` (the DSP model zeroes its control block once
MAIN has seen it up and answers the request words a track load and its PCM
stream write there; off, an experiment -- see "Loading a track"),
`CDJ_DSP_TRACE` (firmware pages, mailbox, every acknowledged request, a
per-second census of the control block, every event posted), `CDJ_DSP_EVENT_PROBE=<start s>[:<interval s>[:<first>-<last>]]`
(post the DSP's event codes to MAIN on its interrupt line -- irq 0x7f, bit 24
of 0xffd4005c, GPIO 0xfff10040 bit 4, the code in bytes 2/3 of window+0xffe8
-- one every interval from the start second on, to measure what MAIN's
player task makes of each; the line itself is always modelled, only the
probe posts: trackload-50/51b measured that MAIN acknowledges every code
within a millisecond and answers with the player commands 1 and 2, and that
an event during a load stops the player with E-8302), `CDJ_DSP_SLOT_REPORT=<state>`
(after the load's closing commands 4 and 2 write that state and position 0
into the slot table at window+0x7ce0 and raise one event; state 2 got
E-8302 in trackload-52 -- the entry is read, its vocabulary is still open),
`CDJ_DMAC_TRACE` (every DMA start
with channel, SAR, DAR, TCR, CHCR and role), `CDJ_SDHI_TRACE` (every SD
command; walking the card image's FAT for the block addresses says which
file a read was -- `runs/nxs-swap/trackload-39-final/fatmap.py` does that)
and more. The simulator likewise: `BFIN_MAIN_LINK`, `BFIN_GUI_OUTPUT`,
`BFIN_GUI_COLOR`, `BFIN_PPI_DMA_DELAY`, `BFIN_SPORT_TX_OUTPUT`,
`BFIN_SPORT_RX_US` (off), `BFIN_LINK_REPEAT_ANNOUNCED` (on: a payload MAIN's
record still announces is handed over again when the firmware arms for it),
and the time-base knobs above: `BFIN_TIME_BASE`,
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
