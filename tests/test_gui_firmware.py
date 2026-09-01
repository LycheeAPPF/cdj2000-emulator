# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from pathlib import Path
import struct
import unittest

from tools.cdj_gui.firmware import (
    BLACKFIN_BF531_ENTRY_POINT,
    GUI_FLASH_SIZE,
    GUI_RESOURCE_FLASH_GAP,
    build_blackfin_elf,
    parse_gui_update,
    write_outputs,
)
from tools.cdj_gui.main_unpack import decompress_lzss


ROOT = Path(__file__).resolve().parents[1]
EXTRACTED_UPDATE = ROOT / "firmware" / "C2KGUI.UPD"


class GuiFirmwareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not EXTRACTED_UPDATE.exists():
            raise unittest.SkipTest(
                "place your own C2KGUI.UPD and C2KMAIN.UPD in "
                "firmware/, then run tools.cdj_gui.extract and "
                "tools.cdj_gui.main_unpack -- see FIRMWARE.md")
        cls.update = parse_gui_update(EXTRACTED_UPDATE)

    def test_known_update_layout(self) -> None:
        self.assertEqual(self.update.version_text, "CDJ-2000 GUI    Ver4.200 0234561")
        self.assertEqual(len(self.update.raw), 2_015_268)
        self.assertEqual(len(self.update.body), 0x1EC000)
        self.assertEqual(self.update.trailer, bytes.fromhex("00005604"))
        self.assertTrue(self.update.crc_valid)

    def test_known_boot_stream(self) -> None:
        self.assertEqual(len(self.update.blocks), 898)
        self.assertEqual(self.update.boot_stream_end, 0xC372A)
        self.assertEqual(32 + self.update.boot_stream_end, 0xC374A)
        self.assertEqual(len(self.update.resource_tail), 0x1288D6)
        self.assertTrue(self.update.blocks[0].is_ignored)
        self.assertEqual(int.from_bytes(self.update.blocks[0].data, "little"), 0xC371C)
        self.assertTrue(self.update.blocks[-1].is_final)
        self.assertFalse(self.update.blocks[-1].uses_bf533_reset_vector)
        self.assertEqual(self.update.entry_point, BLACKFIN_BF531_ENTRY_POINT)

    def test_generated_elf_identifies_blackfin_and_entry(self) -> None:
        elf = build_blackfin_elf(self.update.memory_spans())
        section_header_offset = struct.unpack_from("<I", elf, 32)[0]
        self.assertEqual(elf[:7], b"\x7fELF\x01\x01\x01")
        self.assertEqual(struct.unpack_from("<H", elf, 18)[0], 106)
        self.assertEqual(struct.unpack_from("<I", elf, 24)[0], BLACKFIN_BF531_ENTRY_POINT)
        self.assertGreater(section_header_offset, 0)
        self.assertEqual(struct.unpack_from("<H", elf, 46)[0], 40)
        self.assertEqual(
            struct.unpack_from("<H", elf, 48)[0],
            len(self.update.memory_spans()) + 2,
        )

    def test_generated_flash_image_is_padded_to_two_mebibytes(self) -> None:
        output = ROOT / "runs" / "test-gui-output"
        write_outputs(self.update, output)
        flash = (output / "gui-flash-image.bin").read_bytes()
        self.assertEqual(len(flash), GUI_FLASH_SIZE)
        split = self.update.boot_stream_end
        self.assertEqual(flash[:split], self.update.body[:split])
        self.assertEqual(
            flash[split : split + GUI_RESOURCE_FLASH_GAP],
            bytes([0xFF]) * GUI_RESOURCE_FLASH_GAP,
        )
        resource_start = split + GUI_RESOURCE_FLASH_GAP
        resource_end = resource_start + len(self.update.resource_tail)
        self.assertEqual(flash[resource_start:resource_end], self.update.resource_tail)
        self.assertEqual(
            flash[resource_end:], bytes([0xFF]) * (GUI_FLASH_SIZE - resource_end)
        )

    def test_all_firmware_resource_banks_decompress_to_declared_size(self) -> None:
        output = ROOT / "runs" / "test-gui-output"
        write_outputs(self.update, output)
        flash = (output / "gui-flash-image.bin").read_bytes()
        banks = (
            (0x0EBBA0, 0x39D55, 0xF8853),
            (0x1258F5, 0x12A4D, 0x6A692),
            (0x138342, 0x007B4, 0x02000),
            (0x138AF6, 0x00749, 0x02000),
            (0x13923F, 0x0078A, 0x02000),
            (0x1399C9, 0x0077F, 0x02000),
            (0x13A148, 0x007B2, 0x02000),
            (0x13A8FA, 0x23D23, 0x36400),
            (0x15E61D, 0x06B12, 0x1D600),
            (0x16512F, 0x73ED8, 0xA7700),
        )
        for source, packed_size, output_size in banks:
            with self.subTest(source=f"0x{source:x}"):
                packed = flash[source : source + packed_size]
                self.assertEqual(len(decompress_lzss(packed)), output_size)



if __name__ == "__main__":
    unittest.main()
