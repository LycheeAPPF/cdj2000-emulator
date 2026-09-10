# cdj2000-emulator

An emulator for the Pioneer CDJ-2000 that runs the player's own firmware.

The CDJ-2000 has two processors, and this emulates both. The SH-4 that runs
the player boots on a QEMU machine written for it; the Blackfin BF531 that
paints the display boots on GNU's Blackfin simulator, patched; the two talk
over the same serial link they use on the real board. Every pixel is drawn by
Pioneer's code, and every button press travels the path a real press travels.

**It is a developer tool for firmware modding and reverse engineering**, a
place to run a change and watch what the machine does with it without risking
a player. It is not a way to use a CDJ-2000 on a desktop.

```
    ┌─────────────────────┐  serial link   ┌────────────────────────┐
    │  MAIN board  SH-4   │◄──────────────►│  GUI board  BF531      │
    │  QEMU: cdj2000-main │                │  GNU sim, patched      │
    │  flash, SDRAM, DMAC │                │  PPI → 480x234 RGB555  │
    │  panel, SD, ATAPI,  │                │  SPORT, DMA, CFI flash │
    │  USB, audio DSP     │                │                        │
    └──────────┬──────────┘                └───────────┬────────────┘
               │  TCP control channel                  │  framebuffer
               ▼                                       ▼
         panel_control                            the window
```

## Status by profile

The original CDJ-2000 profile and the experimental CDJ-2000NXS profile share
the Blackfin viewer, panel-control channel, link tools and build/test
infrastructure. Their MAIN machines, firmware layouts, panel maps and DSP
contracts are different. See [CHANGE_SCOPE.md](CHANGE_SCOPE.md) for the file
and review boundary.

### CDJ-2000

The original `cdj2000-main` path boots the supplied CDJ-2000 firmware with the
legacy panel profile and two-board viewer. SD images, firmware update images,
panel controls and the stock GUI workflow remain supported here.

### CDJ-2000NXS

The NXS path boots the NXS MAIN/GUI pair, uses its 128 MiB memory map and NXS
panel contacts, and includes a partial C6747 DSP/peripheral model. A genuine
FAT32 SD image can be browsed and a WAV track can be selected and loaded by
the firmware; the loading state clears and the duration is rendered on the
panel. This is documented evidence for the NXS profile, not a claim about the
original CDJ-2000 path.

### Explicit limitations

The emulator does not currently produce verified audible output, jog/pitch
behavior, or a link between players. NXS DSP execution is partial and its
strict diagnostic profile stops the serializer clock, so a successful NXS
track-load display is not an audio-output test. Firmware is never included;
you supply your own legally obtained images.

## Firmware is not included

**This repository contains no Pioneer firmware and never will.** You supply
your own copy of the firmware update, which the manufacturer distributes free
to owners, and the extractors turn it into the images the emulators load. See
[FIRMWARE.md](FIRMWARE.md). Nothing here is derived from Pioneer's code: no
images, no disassembly, no screenshots.

## Getting started

Build the shared Blackfin/QEMU tools and extract your own firmware as described
in [FIRMWARE.md](FIRMWARE.md). Then use the single profile selector:

```sh
# Original CDJ-2000
python -m tools.cdj_main.launch 2000

# Original CDJ-2000 with a FAT32 card image
python -m tools.cdj_main.launch 2000 --sd runs/card.img

# CDJ-2000NXS interactive deck
python -m tools.cdj_main.launch nxs runs/nxs-interactive --seconds 3600 --ui
```

The first argument is mandatory (`2000` or `nxs`). Remaining arguments are
passed to that profile's existing launcher, including its model-specific
`--help` output. Choose a new NXS run directory for each run.

### NXS research profile

For the NXS firmware already prepared under `firmware/nxs/`, the direct
launcher remains available when its model-specific options are clearer:

```sh
python -m tools.cdj_main.nxs_vm runs/nxs-interactive --seconds 3600 --ui
```

Choose a new run-directory name each time. `--ui` opens the current interactive
deck and connects its buttons to that run's panel port; without it the run is
headless. Close the deck to stop both emulators. Restart an older viewer to pick
up Python changes, and rebuild `bin/cdj-run` after simulator patch changes.

Ordinary hardware buttons support mouse-down/up and Enter/Space holds, including
release outside the button or on focus loss. MENU holds open the real
firmware's UTILITY screen after the SIC mask-order fix (`69d0d88`). The NXS
SD/WAV track-load milestone is verified; audible playback remains unverified.
See [RUNNING.md](RUNNING.md) and [CHANGE_SCOPE.md](CHANGE_SCOPE.md) for current
usage and scope.

The NXS launcher also accepts experimental `--sd IMAGE` and `--usb IMAGE`
mounts. Generate a plain WAV/FAT32 fixture with
`python -m tools.cdj_main.test_media runs/test-media`, then supply
`runs/test-media/test-track.img`. Each image uses a disposable QEMU overlay;
guest writes are discarded when the run closes. Image attachment does not
establish firmware track loading or audible playback, which remain unverified.
Add `--trace-media` to record SD-controller and USB-host register activity in
`main-stderr.log`. This diagnostic adds host overhead and can change timing;
it does not enable the legacy fake media-state RAM write.
For insertion diagnostics, `--sd-insert-seconds 110` schedules the existing
card-presence transition 110 virtual seconds after reset; `0` keeps the slot
empty. This requires `--sd`. The default remains the controller's 20 seconds.
Virtual seconds are not a promise about wall-clock boot time.

### Original CDJ-2000 setup

```sh
pip install -r requirements.txt
sh scripts/build-bfin-sim.sh                                   # the GUI board
git clone --depth 1 https://gitlab.com/qemu-project/qemu.git /c/qemu-src
sh scripts/build-qemu-sh4.sh /c/qemu-src                       # the MAIN board
# put C2KGUI.UPD and C2KMAIN.UPD in firmware/, then:
python -m tools.cdj_gui.extract     firmware/C2KGUI.UPD  firmware
python -m tools.cdj_gui.main_unpack firmware/C2KMAIN.UPD firmware
python -m tools.cdj_main.view_vm
```

[BUILD.md](BUILD.md) has the platform notes. [RUNNING.md](RUNNING.md) has
everything you can do once it boots: the environment knobs, the two-board
recipe, the speed numbers, the update procedure, and the measurements behind
each claim above.

## One rule about the UI

Only the inner rectangle is the 480x234 panel. `BROWSE` / `TAG LIST` / `INFO`
/ `MENU` and `LINK` / `USB` / `SD` / `DISC` are hardware buttons, backlit
plastic that appears in no frame the firmware draws. Virtual buttons belong
beside the panel image, never in it; `tests/test_panel_layout.py` enforces
that the captured frame is shown untouched.

## Layout

| path | what |
|---|---|
| `emulator/qemu/` | the SH-4 MAIN board: machine, panel, link, SD, ATAPI, USB host, DSP |
| `emulator/*.hw` | GNU sim board descriptions for the Blackfin side |
| `patches/` | what has to change in QEMU and in GDB's simulator, and why |
| `tools/cdj_main/` | launchers, panel control, the two-board recipe, card and update images |
| `tools/cdj_gui/` | the viewer, the firmware extractors, link decoders, stimulus generators |
| `tests/` | the host-side test suite; most of it needs no emulator |
| `INPUT_MANIFEST.md` | all 48 inputs, what was done with each, what was measured |

`patches/README.md` is worth reading on its own: four omissions in QEMU's SH-4
interrupt handling that are invisible to Linux and fatal to a uITRON RTOS, a
Blackfin packed-ALU instruction that committed a cycle early, and the AMD
command set the CDJ's flash actually speaks.

## Licence

`GPL-2.0-or-later`. See [LICENSE](LICENSE), and [THIRD_PARTY.md](THIRD_PARTY.md)
for what is patched and under what terms.

## Not affiliated with Pioneer

This is an independent project, not endorsed by, affiliated with, or supported
by Pioneer DJ, AlphaTheta, or any successor. "CDJ" and "Pioneer" are their
trademarks and are used here only to say which hardware this emulates.
