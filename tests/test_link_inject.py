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


def test_marker_feed_answers_a_typed_request_with_announcement_and_part():
    import struct
    from tools.cdj_gui.main_packet import firmware_crc
    from tools.cdj_main.link_inject import MarkerFeed, StatusPrefix, marker_record, MARKER_PART_HALFWORDS

    assert marker_record(3, 0x123456) == bytes((3, 0x12, 0x56, 0x34))
    feed = MarkerFeed("beat:120:0:1:2,cue:5000:7,at:10")
    feed.waveform = bytes(0x400)
    words = [0] * 32
    words[7], words[8] = 3, (17 << 8) | 0
    req20 = bytearray(48); struct.pack_into("<HH", req20, 2, 0x8020, 0x20)
    req21 = bytearray(48); struct.pack_into("<HH", req21, 2, 0x8021, 0x21)
    assert feed.answer(bytes(req20)) is None          # no record seen yet
    feed.on_record(words, 12.0)
    out = feed.answer(bytes(req20))                   # announcement + part 1 together
    assert out is not None and len(out) == (8 + 64) + (8 + 896)
    rec = struct.unpack("<32H", out[8:72])
    assert (rec[29], rec[30]) == (1, MARKER_PART_HALFWORDS) and rec[31] == firmware_crc(out[8:70])
    assert out[72:76] == b"CDJL" and out[80:82] == b"\x20\x00"
    assert len(feed.queues[0x20]) == 1 and feed.transferring()
    # MAIN's own announcement is cleared while the transfer runs
    words[29], words[30] = 1, 136
    assert feed.on_record(words, 12.5) is True and words[29] == 0 and feed.hidden == 1
    assert feed.answer(bytes(48)) is None             # the zero poll goes to MAIN
    assert feed.answer(bytes(req20)) is None          # every second typed request goes to MAIN
    out = feed.answer(bytes(req20))                   # part 2
    assert out[80:82] == b"\x20\x00" and not feed.queues[0x20] and feed.requested == 0
    assert feed.answer(bytes(req20)) is None          # no 0x20 parts left
    words[29], words[30] = 1, 136
    assert feed.on_record(words, 13.0) is False and words[29] == 1   # not transferring: untouched
    assert feed.answer(bytes(req21))[80:82] == b"\x21\x00"
    browse = bytearray(48); browse[2] = 1; browse[3] = 0x80
    assert feed.answer(bytes(browse)) is None
    # through the prefix's frame parser, MAIN's payload frames pass during a transfer
    prefix = StatusPrefix()
    prefix.beat = False
    prefix.markers = MarkerFeed("beat:120")
    prefix.markers.waveform = bytes(0x800)
    prefix.advance(50.0)
    body = struct.pack("<31H", *words[:31])
    status = b"CDJL" + struct.pack("<I", 64) + body + struct.pack("<H", firmware_crc(body))
    prefix.feed(status)
    assert prefix.markers.answer(bytes(req20)) is not None and prefix.markers.transferring()
    payload = b"CDJL" + struct.pack("<I", 272) + bytes(272)
    assert prefix.feed(payload) == payload
    for _ in range(12):
        prefix.markers.answer(bytes(req20))
    assert not prefix.markers.transferring()


def test_waveform_parts_precede_markers():
    import struct
    from tools.cdj_gui.main_packet import firmware_crc
    from tools.cdj_main.link_inject import MarkerFeed, waveform_parts, MARKER_PART_HALFWORDS

    entries = bytes(range(256)) * 8            # 2048 bytes -> 0x370 + 0x378 + rest
    parts = waveform_parts(entries)
    assert len(parts) == 3 and all(len(p) == MARKER_PART_HALFWORDS * 2 for p in parts)
    head = struct.unpack("<7H", parts[0][:14])
    assert head[:3] == (0x20, 1, 0) and head[3] | (head[4] << 16) == 2048
    assert parts[0][14:14 + 0x370] == entries[:0x370]
    assert struct.unpack("<3H", parts[1][:6]) == (0x20, 2, 0)
    assert parts[1][6:6 + 0x378] == entries[0x370:0x370 + 0x378]
    assert struct.unpack("<H", parts[2][-2:])[0] == firmware_crc(parts[2][:-2])
    feed = MarkerFeed("cue:1000:3")
    feed.waveform = entries
    words = [0] * 32
    words[7], words[8] = 0, (10 << 8)
    feed.build(10000)
    assert len(feed.queues[0x20]) == 3 and len(feed.queues[0x21]) == 1
