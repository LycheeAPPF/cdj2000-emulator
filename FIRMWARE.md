# Firmware

**This repository contains no firmware, and it never will.** Everything the
emulator needs is built on your machine from a firmware update file you supply
yourself.

## What you need

The CDJ-2000 firmware update package, which the manufacturer distributes free of
charge to owners of the player. Get it from there. From the package you need two
files:

| file | drives |
|---|---|
| `C2KGUI.UPD` | the GUI board -- the Blackfin that paints the 480x234 panel |
| `C2KMAIN.UPD` | the MAIN board -- the SH-4 that runs the player |

The NXS package uses the same names.

## Turning them into boot images

Put both files in `firmware/` and run the two extractors from the repository
root:

```sh
mkdir -p firmware
# ... copy C2KGUI.UPD and C2KMAIN.UPD into firmware/ ...

python -m tools.cdj_gui.extract      firmware/C2KGUI.UPD  firmware
python -m tools.cdj_gui.main_unpack  firmware/C2KMAIN.UPD firmware
```

The first prints the update's version string and writes what the Blackfin
simulator loads:

| file | what it is |
|---|---|
| `firmware/gui-boot-memory.elf` | the decompressed boot image, as an ELF the simulator loads |
| `firmware/gui-flash-image.bin` | the 2 MiB parallel flash, byte for byte as the board sees it |
| `firmware/gui-flash-body.bin` | the update body before flash padding |
| `firmware/gui-resource-tail.bin` | the packed resources the firmware decompresses at run time |
| `firmware/gui-memory-map.json` | where each span was placed, for the tools that need it |

The second prints a SHA-256 per region and writes what QEMU loads:

| file | what it is |
|---|---|
| `firmware/main-firmware.bin` | the MAIN flash image, passed to QEMU as `-bios` |
| `firmware/main-unpacked.bin` | the decompressed application, which host tools read tables out of |
| `firmware/main-loader-unpacked.bin` | the decompressed loader |

Both refuse rather than guess: the GUI extractor checks the update's CRC and the
MAIN unpacker checks a checksum per packed region, so a truncated or edited file
stops there instead of producing an image that boots into nonsense.

**The ELF and the flash image are a matched pair.** The firmware's own
decompressor reads packed resource bytes straight out of the memory-mapped flash
while it runs, so a boot image from one release against a flash image from
another still boots and still draws the right rectangles -- only the *content*
comes out wrong. "Geometry correct, content is noise" means you mixed a pair.

## For NXS firmware

Extract into `firmware/nxs/` instead and use `emulator/cdj2000-gui-nxs.hw` as the
board file, which points there.

## Verifying you got the right file

`tests/test_gui_firmware.py` and `tests/test_main_unpack.py` skip when
`firmware/` is empty and run when it is not. They check the update's structure --
version string, lengths, block count, CRC trailer, and that all ten resource
banks decompress to their declared size. If those pass, the parser understood
your file. If they fail, it is a different release than the one this was
developed against, and the addresses the tools use may not apply.

## Why none of it is here

The firmware is Pioneer's, not ours. Redistributing it -- as the original update
file, as a decompressed image, as a disassembly, or as screenshots of it
running -- is not ours to do. The extractors are the whole of what this project
contributes, and they are useless without a file you already have a right to.
