# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from pathlib import Path
import hashlib
import unittest

from tools.cdj_gui.main_unpack import PACKED_REGIONS, decode_srecords, unpack_region


ROOT = Path(__file__).resolve().parents[1]
MAIN_UPDATE = ROOT / "firmware" / "C2KMAIN.UPD"


class MainUnpackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not MAIN_UPDATE.exists():
            raise unittest.SkipTest(
                "place your own C2KGUI.UPD and C2KMAIN.UPD in "
                "firmware/, then run tools.cdj_gui.extract and "
                "tools.cdj_gui.main_unpack -- see FIRMWARE.md")
        cls.image = decode_srecords(MAIN_UPDATE.read_bytes())
        cls.regions = [unpack_region(cls.image, address) for address in PACKED_REGIONS]

    def test_srecord_layout(self) -> None:
        self.assertEqual(len(self.image), 0x287D60)

    def test_packed_checksums(self) -> None:
        self.assertTrue(all(region.checksum_valid for region in self.regions))
        self.assertEqual([len(region.packed) for region in self.regions], [0x21643, 0x247D3C])

    def test_known_unpacked_images(self) -> None:
        self.assertEqual([len(region.unpacked) for region in self.regions], [0x30000, 0x3C0000])
        self.assertEqual(
            [hashlib.sha256(region.unpacked).hexdigest() for region in self.regions],
            [
                "a49b7ab0860cd168ffd71f76da37ce930c284d4406e5e83af05db84819d03c25",
                "3923c839465095c24f70cf7a0bee97899f8c825c6e62632c918eaa4c102ec550",
            ],
        )


if __name__ == "__main__":
    unittest.main()
