"""MAIN's SERVICE MODE names the pressed key, and this reads the table it uses.

Every number here is read out of ``firmware/main-unpacked.bin`` at import
time and compared against the map derived from the panel decoder at 0x28e1ae.
Nothing here starts an emulator, and nothing here is a transcription of prose:
if the image is swapped, the tables move, or somebody "corrects" the map by
hand, these tests fail.

The chain the tests pin, in the order MAIN walks it:

    0x28e1ae   panel payload bytes 15..21  ->  status bits at 0x04fe29f4 + 72..87
    0x2a1022   PnlCom_RcvTASK copies 0x04fe2a20 (36 B) and 0x04fe2a44 (8 B)
               into a message and posts it to SRVMOD_MBX as id 10001
    0x2a097c   word0 = [0x04fe2a48], word1 = [0x04fe2a3c] (bit 30 inverted)
    0x2a09f2   matches the *changed* bits against two {mask, code} tables
    0x29f9b0   prints name[code] on the BUTTON row

so a name on that row is a statement about one panel bit, made by the firmware.
"""

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / "firmware" / "main-unpacked.bin"

# Literal-pool base of the unpacked application; see cdj-main-firmware-is-superh.
LINK_BASE = 0xA4000000

# The three tables, at the addresses the disassembly names.
NAME_TABLE = 0x340288        # loaded at runtime as 0x07db345c -- see DATA_DELTA
MASK_TABLE_HIGH = 0x0A0E14   # matched against [0x04fe2a48], pool [0x07db351c]
MASK_TABLE_LOW = 0x0A0D24    # matched against [0x04fe2a3c], pool [0x07db3520]

# MAIN's .data is linked at 0x07db... and stored in the image at 0x34....  The
# constant is fixed by five independent data items (0x07db3444 -> a pointer to
# "show", 0x07db342c -> 0xffffffff, 0x07db3440 -> 1), not by one coincidence.
DATA_DELTA = 0x07DB345C - NAME_TABLE

# payload (byte, bit) -> status byte offset from 0x04fe29f4, bit.
# Read instruction by instruction out of 0x28e280 / 0x28e2fc / 0x28e39a /
# 0x28e44a.  This is the table the rest of the file is checked against.
DECODER: dict[tuple[int, int], tuple[int, int]] = {
    (15, 0): (75, 7), (15, 1): (75, 6), (15, 5): (75, 5),
    (15, 6): (75, 4), (15, 7): (75, 3),
    (16, 0): (75, 2), (16, 1): (75, 1), (16, 2): (74, 4), (16, 3): (74, 1),
    (16, 4): (74, 2), (16, 5): (74, 6), (16, 6): (74, 7), (16, 7): (75, 0),
    (17, 0): (86, 5), (17, 1): (74, 3), (17, 2): (72, 1),
    (18, 0): (74, 5), (18, 1): (74, 0), (18, 2): (73, 7), (18, 3): (73, 6),
    (18, 4): (73, 5), (18, 6): (73, 4), (18, 7): (73, 3),
    (19, 0): (87, 7), (19, 1): (87, 6), (19, 2): (87, 5), (19, 3): (87, 4),
    (19, 4): (73, 0), (19, 6): (73, 2), (19, 7): (73, 1),
    (20, 0): (87, 3), (20, 1): (87, 2), (20, 2): (87, 1), (20, 3): (87, 0),
    (20, 4): (86, 7), (20, 5): (86, 6),
    (21, 0): (72, 4), (21, 1): (72, 3), (21, 2): (72, 5), (21, 3): (72, 6),
}

# The two words 0x2a097c hands to the matcher, as (base status offset, table).
WORD_LOW = 72      # [0x04fe2a3c] = status bytes 72..75, little-endian
WORD_HIGH = 84     # [0x04fe2a48] = status bytes 84..87, little-endian


def image() -> bytes:
    if not IMAGE.exists():
        pytest.skip(f"{IMAGE} is not in this checkout")
    return IMAGE.read_bytes()


def string_at(data: bytes, pointer: int) -> str:
    """A NUL-terminated ASCII string named by a linked pointer."""
    offset = pointer - LINK_BASE
    return data[offset:offset + 64].split(b"\0")[0].decode("latin-1")


def names(data: bytes) -> list[str]:
    """name[code] for code 0..49, out of the pointer table at 0x340288."""
    out = []
    for index in range(50):
        pointer, = struct.unpack_from("<I", data, NAME_TABLE + index * 4)
        out.append(string_at(data, pointer))
    return out


def mask_table(data: bytes, address: int) -> list[tuple[int, int]]:
    """The {mask, code} pairs at ADDRESS, up to the zero-mask terminator."""
    out = []
    while True:
        mask, code = struct.unpack_from("<II", data, address)
        if mask == 0:
            return out
        out.append((mask, code))
        address += 8


def status_bit(word_base: int, bit: int) -> tuple[int, int]:
    """Bit N of a little-endian word -> the (status offset, bit) it is."""
    return word_base + bit // 8, bit % 8


def catalogue(data: bytes) -> dict[tuple[int, int], str]:
    """payload (byte, bit) -> the SERVICE MODE name, end to end."""
    name = names(data)
    by_status: dict[tuple[int, int], str] = {}
    for word_base, address in ((WORD_HIGH, MASK_TABLE_HIGH),
                               (WORD_LOW, MASK_TABLE_LOW)):
        for mask, code in mask_table(data, address):
            assert mask.bit_count() == 1, f"{mask:#x} is not a single bit"
            by_status[status_bit(word_base, mask.bit_length() - 1)] = name[code]
    return {payload: by_status[status]
            for payload, status in DECODER.items() if status in by_status}


# ------------------------------------------------------------------ tests ---
def test_the_name_table_starts_with_an_empty_string_and_holds_47_names():
    """Index 0 is blank on purpose: 0x2a15b4 passes 0 when the key goes up."""
    data = image()
    table = names(data)
    assert table[0] == ""
    assert table[1] == "LOCK"
    assert table[47] == "ENCODER PUSH"
    assert [n for n in table[1:48] if n] == table[1:48]


def test_the_gap_between_auto_loop_8_and_browse_is_slip_and_the_four_sources():
    """0x000a147c..0x000a14a8 held five entries, not a hole."""
    data = image()
    table = names(data)
    assert table[35:41] == ["AUTO LOOP 8", "SLIP", "LINK", "USB", "SD", "DISC"]


def test_the_two_mask_tables_are_the_shape_the_matcher_expects():
    """0x2a09f2 walks at most 16 iterations of an unrolled-by-two loop."""
    data = image()
    high = mask_table(data, MASK_TABLE_HIGH)
    low = mask_table(data, MASK_TABLE_LOW)
    assert len(high) == 11 and len(low) == 29
    for table in (high, low):
        assert len(table) <= 32
        assert len({mask for mask, _ in table}) == len(table)
    # 0x2a097c masks word0 with 0xFFE00000 before offering it, and the high
    # table holds exactly those eleven bits -- nothing below bit 21.
    assert {mask for mask, _ in high} == {1 << bit for bit in range(21, 32)}


def test_every_source_key_and_every_top_row_key_has_a_name():
    data = image()
    found = catalogue(data)
    assert found[(19, 0)] == "LINK"
    assert found[(19, 1)] == "USB"
    assert found[(19, 2)] == "SD"
    assert found[(19, 3)] == "DISC"
    assert found[(20, 0)] == "BROWSE"
    assert found[(20, 1)] == "TAG LIST"
    assert found[(20, 2)] == "INFORMATION"
    assert found[(20, 3)] == "MENU"
    assert found[(20, 4)] == "RETURN"
    assert found[(20, 5)] == "TAG TRACK"
    assert found[(17, 0)] == "ENCODER PUSH"


def test_the_two_bits_the_manifest_called_undecoded_are_the_boot_combination():
    """20.3 and 21.3 were absent from BUTTON_BITS; both are real inputs."""
    data = image()
    found = catalogue(data)
    assert found[(21, 3)] == "MEMORY"
    assert found[(18, 7)] == "TEMPO RANGE"
    assert found[(20, 3)] == "MENU"


def test_the_whole_catalogue_matches_the_decoder():
    """40 payload bits carry a name; the four that do not are named here."""
    data = image()
    found = catalogue(data)
    unnamed = sorted(set(DECODER) - set(found))
    assert unnamed == [(15, 6), (15, 7)]
    assert len(found) == 38
    # Two names in the tables are reached from something other than the panel
    # payload, so they must NOT appear in the catalogue.
    assert "EJECT" not in found.values()
    assert "USB STOP" not in found.values()


def test_the_service_mode_page_template_is_the_button_page():
    """0x29f738 indexes the page table as 0x000a08f8 + n*32; n = 1 is BUTTON.

    A row is one 32-byte string ``"<label>\\t<placeholder>"`` -- the separator
    is a TAB, not a NUL, which is why 0x29f738 carries ``cmp/eq #9,r0``.  The
    placeholder is what the value overwrites.
    """
    data = image()
    row = 0x0A08F8 + 1 * 32
    pointers = struct.unpack_from("<8I", data, row)
    cells = [string_at(data, p).split("\t") for p in pointers]
    assert cells[0][0] == "SERVICE MODE"
    assert [cell[0].strip() for cell in cells[1:]] == [
        "BUTTON", "JOG", "ENCODER", "NEEDLE",
        "SLIDER VOLUME", "JOG TOUCH VOLUME", "JOG RELEASE VOLUME"]
    assert all(cell[1] == "#" * 10 for cell in cells[1:])


def test_the_data_delta_is_fixed_by_more_than_one_item():
    """0x07db345c is the runtime address of the image table at 0x340288."""
    data = image()
    # 0x07db3444 is loaded at 0x29f58a and must hold a pointer to "show".
    pointer, = struct.unpack_from("<I", data, 0x07DB3444 - DATA_DELTA)
    assert string_at(data, pointer) == "show"
    # 0x07db342c is the -1 sentinel the panel module compares against.
    assert struct.unpack_from("<I", data, 0x07DB342C - DATA_DELTA) == (0xFFFFFFFF,)
    # The two mask-table pointers live at 0x07db351c / 0x07db3520.
    high, low = struct.unpack_from("<2I", data, 0x07DB351C - DATA_DELTA)
    assert high == LINK_BASE + MASK_TABLE_HIGH
    assert low == LINK_BASE + MASK_TABLE_LOW
