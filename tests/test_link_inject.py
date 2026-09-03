# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

"""The request builder of the link proxy against a frame MAIN accepted from the real GUI."""

from __future__ import annotations

import unittest

from tools.cdj_gui.main_packet import firmware_crc
from tools.cdj_main.link_inject import build_request, parse_injection


class LinkInjectTests(unittest.TestCase):
    def test_boot_handshake_frame_matches_the_gui_byte_for_byte(self) -> None:
        # The first live GUI request of r002 (type 7, cursor 7), accepted by MAIN.
        saved = bytes.fromhex(
            "00 00 07 00 07 00 00 00 00 00 00 00 00 00 00 00"
            "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00"
            "00 00 00 00 00 00 00 00 00 00 00 00 00 00 de b7")
        packet = build_request(7, 7)
        self.assertEqual(packet[2:4], b"\x07\x80")     # the active form
        self.assertEqual(packet[4:46], saved[4:46])
        self.assertEqual(firmware_crc(saved[:-2]), 0xB7DE)

    def test_words_land_from_word_3_and_the_crc_covers_them(self) -> None:
        packet = build_request(1, 3, (7, 1, 0))
        self.assertEqual(packet[:12].hex(" "), "00 00 01 80 03 00 07 00 01 00 00 00")
        self.assertEqual(int.from_bytes(packet[46:], "little"), firmware_crc(packet[:46]))

    def test_injection_spec(self) -> None:
        spec = parse_injection("155:7:1:0:0x2a")
        self.assertEqual((spec.seconds, spec.request_type, spec.cursor, spec.words), (155.0, 7, 1, (0, 42)))
        with self.assertRaises(ValueError):
            parse_injection("155:7")
        with self.assertRaises(ValueError):
            parse_injection("-1:7:1")


if __name__ == "__main__":
    unittest.main()
