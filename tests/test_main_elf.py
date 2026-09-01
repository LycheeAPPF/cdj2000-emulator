# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

import struct
import unittest

from tools.cdj_gui.main_elf import (
    EM_SH,
    PAYLOAD_OFFSET,
    add_builder_shim,
    make_sh_elf,
)


class MainElfTests(unittest.TestCase):
    def test_emits_little_endian_sh_executable(self):
        image = b"\x12\x34\x56\x78"
        result = make_sh_elf(image, base=0x04000000, entry=0x042166F0)

        self.assertEqual(result[:6], b"\x7fELF\x01\x01")
        self.assertEqual(struct.unpack_from("<H", result, 18)[0], EM_SH)
        self.assertEqual(struct.unpack_from("<I", result, 24)[0], 0x042166F0)
        self.assertEqual(result[PAYLOAD_OFFSET : PAYLOAD_OFFSET + 4], image)

    def test_builder_shim_sets_packet_buffer_stack_and_target(self):
        image, entry = add_builder_shim(
            b"\0" * 0x20, base=0x04000000, builder=0x042166F0
        )

        self.assertEqual(entry, 0x04000020)
        self.assertEqual(image[0x20:0x30].hex(), "03d404df04d02a4004d02b4009000900")
        self.assertEqual(
            struct.unpack_from("<IIII", image, 0x30),
            (0x05000000, 0x05FFFFF0, 0x08000000, 0x042166F0),
        )


if __name__ == "__main__":
    unittest.main()
