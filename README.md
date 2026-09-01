# cdj2000-emulator

An emulator for the Pioneer CDJ-2000 that runs the player's own firmware.

Not a mock-up: the CDJ-2000 has two processors, and this emulates both of them.
The SH-4 that runs the player boots on a QEMU machine written for it; the
Blackfin BF531 that paints the display boots on GNU's Blackfin simulator; the
two talk to each other over the same serial link they use on real hardware.
Every pixel on the screen is drawn by Pioneer's code, and every button press
travels the path a real press travels.

## Read this first

**This is a developer tool, and it is barebones.** It exists as a foundation for
firmware modding and reverse engineering — somewhere to run a change and see
what the machine does with it, without risking a real player. It is not a way to
use a CDJ-2000 on your desktop, and it will not become one by itself.

Three things to expect before you build anything:

- **It is very slow.** A boot takes minutes of wall clock — plan for around
  five, sometimes more — to reach a state a real player reaches in seconds.
  Two emulated CPUs, one of them an interpreting instruction-set simulator.
  Leave it running; it is not hung.
- **It is unreliable, and it gets worse when the host is busy.** The GUI board
  double-faults intermittently at `0x00b99196`, usually one to three minutes
  in; the panel then freezes wherever it got to and the launcher reports
  `simulator exited with code 1`. Measured here: about one run in three faults
  on an idle machine, and five of six while a compile was running alongside.
  So do not build something else in another window and expect a clean run.
  This is long-standing behaviour, not a broken checkout.
- **Almost nothing is finished past booting.** See "What does not" below. If you
  need a working player, this is the wrong repository.

If you are here to build on it, that is exactly what it is for.

```
    ┌─────────────────────┐  serial link   ┌────────────────────────┐
    │  MAIN board  SH-4   │◄──────────────►│  GUI board  BF531      │
    │  QEMU: cdj2000-main │                │  GNU sim, patched      │
    │  flash, SDRAM, DMAC │                │  PPI → 480x234 RGB555  │
    │  panel, SD, ATAPI,  │                │  SPORT, DMA, CFI flash │
    │  USB, audio DSP     │                │                        │
    └──────────┬──────────┘                └───────────┬────────────┘
               │                                       │
               │  TCP control channel                  │  framebuffer
               ▼                                       ▼
         panel_control                            the window
```

## What works

* Both boards boot their stock firmware and complete their startup handshake:
  MAIN's panel handshake publishes an operating mode, the GUI link comes up, and
  the record stream runs.
* The panel is drawn: 480x234, RGB555, cropped out of the 255 lines the display
  DMA actually emits. The boot splash animates, and a run that gets far enough
  reaches the player screen with `PLAYER`, `TRACK`, `REMAIN`, `TEMPO` and `BPM`.
* All **48 inputs** the board decodes are reachable from the host — 40 button
  bits and 8 analogue fields, including the rotary encoder. A press travels the
  real path: merged into the panel payload before the checksum, as an edge, and
  MAIN's own service-mode name table is what says which bit is which key. The
  window refuses to start if any input has no control.
* Devices are modelled far enough to clear the caution banners: the disc drive
  (`E-7001`), the audio DSP (`E-7010`) and the USB device (`E-7020`) all report
  up.
* MAIN's own service monitor is reachable over the emulated debug serial port,
  and its caution store can be read back and decoded, so the machine can be
  asked what it thinks rather than guessed at.
* An SD card image is accepted by the card controller and the guest reads
  sectors from it.

## What does not

Be clear about this: **the player is not usable as a player.**

* **The GUI board double-faults.** Intermittent, at `0x00b99196`, typically
  after one to three minutes. The panel stops updating and the launcher says
  `simulator exited with code 1`. Run it again, and keep the machine quiet
  while you do -- see the note above.
* **Speed.** Minutes, not seconds. Every measurement you take costs a run.
* **The browse list does not come from the card.** MAIN answers every browse
  request as `DISC` even with a card present, and the panel shows `NO CARD`.
  Browse screens can be reached, but only by feeding the GUI canned MAIN
  answers — which proves the GUI renders them, not that the machine produced
  them.
* **No track loading**, for the same reason.
* **No audio at all.** The DSP is a register model with a position counter and
  no signal path.
* No USB passthrough: a real stick on the host does not appear as a source.
* No link between players.

Most inputs, measured properly against a control run, are proven no-ops on the
screens reached so far. `INPUT_MANIFEST.md` says which, in which run, and how it
was measured.

## Firmware is not included

**This repository contains no Pioneer firmware and never will.** You supply your
own copy of the firmware update — the manufacturer distributes it free to
owners — and the extractors here turn it into the boot images the two emulators
load. See [FIRMWARE.md](FIRMWARE.md). Nothing in this tree is derived from
Pioneer's code: no images, no disassembly, no screenshots.

## Getting started

```sh
pip install -r requirements.txt
sh scripts/build-bfin-sim.sh                                   # the GUI board
git clone --depth 1 https://gitlab.com/qemu-project/qemu.git /c/qemu-src
sh scripts/build-qemu-sh4.sh /c/qemu-src                       # the MAIN board
# ... put C2KGUI.UPD and C2KMAIN.UPD in firmware/, then:
python -m tools.cdj_gui.extract     firmware/C2KGUI.UPD  firmware
python -m tools.cdj_gui.main_unpack firmware/C2KMAIN.UPD firmware
python -m tools.cdj_main.view_vm
```

Then wait. See the note on speed above.

[BUILD.md](BUILD.md) has the details and the platform notes.
[RUNNING.md](RUNNING.md) has everything you can do once it boots.

## One rule worth knowing before you touch the UI

Only the inner rectangle is the 480x234 panel. `BROWSE` / `TAG LIST` / `INFO` /
`MENU` across the top and `LINK` / `USB` / `SD` / `DISC` down the left are
**hardware buttons** — backlit plastic on the real player, appearing in no frame
the firmware draws. They are not list rows; a browse list containing them is
invented content.

So the virtual buttons belong **beside** the panel image, never drawn into it. A
control painted onto the LCD is a claim about what the firmware rendered, and it
is a false one. `tests/test_panel_layout.py` enforces both halves: that the
captured frame is passed through untouched apart from cutting the blanking rows,
and that the picture occupies a grid cell no button block shares.

This is the commonest mistake in the project and it has cost real work twice.

## Layout

| path | what |
|---|---|
| `emulator/qemu/` | the SH-4 MAIN board: machine, panel, link, SD, ATAPI, USB, DSP |
| `emulator/*.hw` | GNU sim board descriptions for the Blackfin side |
| `patches/` | what has to change in QEMU and in GDB's simulator, and why |
| `tools/cdj_main/` | launchers, panel control, the service monitor, card images |
| `tools/cdj_gui/` | the viewer, the firmware extractors, stimulus generators |
| `tests/` | the host-side test suite; most of it needs no emulator |
| `INPUT_MANIFEST.md` | all 48 inputs, what was done with each, what was measured |

## The patches are the interesting part

`patches/README.md` is worth reading on its own. Four omissions in QEMU's SH-4
interrupt handling are invisible to Linux and fatal to a uITRON RTOS that masks
with `SR.IMASK`; without them this firmware never survives its first timer tick.
On the Blackfin side, one packed-ALU instruction committed its result a cycle
early, which made a parallel store write the wrong value and sent the firmware
into a fatal loop — and GNU sim's CFI flash model has no AMD command set, which
is the part the CDJ's flash actually speaks. Each patch says what was measured
before and after.

## Licence

`GPL-2.0-or-later`. See [LICENSE](LICENSE), and [THIRD_PARTY.md](THIRD_PARTY.md)
for what is patched and under what terms.

## Not affiliated with Pioneer

This is an independent project. It is not endorsed by, affiliated with, or
supported by Pioneer DJ, AlphaTheta, or any successor. "CDJ" and "Pioneer" are
their trademarks and are used here only to say which hardware this emulates.
