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


def test_status_prefix_rewrites_beat_words_and_crc():
    import struct
    from tools.cdj_gui.main_packet import firmware_crc
    from tools.cdj_main.link_inject import StatusPrefix

    words = [0] * 32
    words[5], words[6] = 3, (17 << 8) | 80      # remaining 3:17.80
    words[7], words[8] = 3, (17 << 8) | 86      # length 3:17.86 -> elapsed 6/150 s
    words[10] = 123
    body = struct.pack("<31H", *words[:31])
    record = body + struct.pack("<H", firmware_crc(body))
    prefix = StatusPrefix.parse("beat:1:0")
    frame = b"CDJL" + struct.pack("<I", 64) + record
    out = prefix.feed(frame[:20]) + prefix.feed(frame[20:])
    assert len(out) == len(frame) and out[:8] == frame[:8]
    new = struct.unpack("<32H", out[8:])
    assert new[1] == (1 << 15) | (1 << 12) | (1 << 11) | (1 << 8) | (1 << 4)
    assert new[2] == 0xffff
    assert new[31] == firmware_crc(out[8:8 + 62])
    assert new[3:31] == tuple(words[3:31])
    assert prefix.rewritten == 1 and prefix.last_beat == 1
    # 30 s in at 123 BPM: beat 61.5 -> in-bar beat 2
    words[5], words[6] = 2, (47 << 8) | 86
    body = struct.pack("<31H", *words[:31])
    new = struct.unpack("<32H", prefix.rewrite(body + struct.pack("<H", firmware_crc(body))))
    assert (new[1] >> 12) & 7 == 2 and (new[1] >> 8) & 7 == 2
    # blank time passes unchanged
    words[5] = 99
    body = struct.pack("<31H", *words[:31])
    record = body + struct.pack("<H", firmware_crc(body))
    assert prefix.rewrite(record) == record


def test_status_prefix_schedule_switches_variants():
    from tools.cdj_main.link_inject import StatusPrefix

    prefix = StatusPrefix.parse_all(["beat:2:2:0x21@240", "beat:1:0", "beat:5:5:0x12:0x34@260"])
    assert (prefix.mode, prefix.state, prefix.counter) == (1, 0, 0x1ff)
    prefix.advance(100.0)
    assert prefix.mode == 1
    prefix.advance(240.0)
    assert (prefix.mode, prefix.state, prefix.counter) == (2, 2, 0x21)
    prefix.advance(300.0)
    assert (prefix.mode, prefix.state, prefix.counter, prefix.counter2) == (5, 5, 0x12, 0x34)
    assert not prefix.schedule


def test_status_word_patches_under_mask_with_crc():
    import struct
    from tools.cdj_gui.main_packet import firmware_crc
    from tools.cdj_main.link_inject import StatusPrefix

    words = [0] * 32
    words[18] = 0x0004
    body = struct.pack("<31H", *words[:31])
    record = body + struct.pack("<H", firmware_crc(body))
    prefix = StatusPrefix()
    prefix.beat = False
    prefix.add_words(["18=0x20/0x38@240", "19=0x4000/0x4000"])
    assert prefix.rewrite(record) == record          # nothing scheduled yet
    prefix.advance(0.0)
    new = struct.unpack("<32H", prefix.rewrite(record))
    assert new[19] == 0x4000 and new[18] == 0x0004
    prefix.advance(240.0)
    new = struct.unpack("<32H", prefix.rewrite(record))
    assert new[18] == 0x0024 and new[19] == 0x4000 and new[1] == 0
    assert new[31] == firmware_crc(struct.pack("<31H", *new[:31]))
