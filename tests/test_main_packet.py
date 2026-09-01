# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import struct
import unittest

from tools.cdj_gui.main_packet import (
    NEUTRAL_PLAYER_STATE_WORDS,
    OVERVIEW_WAVEFORM_HEADER_WORDS,
    OVERVIEW_WAVEFORM_SAMPLES,
    OVERVIEW_WAVEFORM_WORDS,
    build_neutral_overview_waveform,
    build_neutral_player_state,
    firmware_crc,
)


class NeutralRuntimePacketTests(unittest.TestCase):
    def assert_valid_packet(self, packet: bytes) -> tuple[int, ...]:
        self.assertEqual(len(packet) % 2, 0)
        words = struct.unpack(f"<{len(packet) // 2}H", packet)
        self.assertEqual(words[-1], firmware_crc(packet[:-2]))
        return words

    def test_blank_overview_waveform_covers_all_400_columns(self) -> None:
        words = self.assert_valid_packet(build_neutral_overview_waveform())
        self.assertEqual(words[0], 0x10)
        # Words 2 and 3 are the first and last sample index.  The packet is
        # OVERVIEW_WAVEFORM_WORDS long rather than header+400, because the
        # announcement advertises the captured 436-word length and the
        # simulator's MAIN peer keys its payload off exactly that size.
        self.assertEqual(words[2], 0)
        self.assertEqual(words[3], OVERVIEW_WAVEFORM_SAMPLES - 1)
        self.assertEqual(len(words), OVERVIEW_WAVEFORM_WORDS)
        self.assertTrue(
            all(word == 0
                for word in words[OVERVIEW_WAVEFORM_HEADER_WORDS:-1]))

    def test_player_state_keeps_crc_beyond_all_fixed_fields(self) -> None:
        words = self.assert_valid_packet(build_neutral_player_state())
        self.assertEqual(words[0], 0x11)
        self.assertEqual(len(words), NEUTRAL_PLAYER_STATE_WORDS)
        self.assertTrue(all(word == 0 for word in words[1:-1]))


if __name__ == "__main__":
    unittest.main()
