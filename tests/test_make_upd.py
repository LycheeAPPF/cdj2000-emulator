from tools.cdj_gui.main_unpack import decode_srecords
from tools.cdj_main.make_upd import HEADER_SIZE, build, check, crc16, header


def test_crc_is_xmodem():
    # CRC-16/XMODEM of "123456789" is the textbook 0x31c3.
    assert crc16(b"123456789") == 0x31C3


def test_header_layout():
    h = header("4.34")
    assert len(h) == HEADER_SIZE
    assert h[:0x17] == b"CDJ-2000 MAIN   Ver4.34"
    assert h[0x17] == 0 and h[0x1F] == ord("0")
    assert h[0x13] == ord("4") and h[0x15] == ord("3") and h[0x16] == ord("4")


def test_round_trip_through_the_decoder():
    image = bytes(range(256)) * 4 + b"\xff" * 64
    upd = build(image, "4.35", end=len(image))
    assert check(upd) == ("4.35", True)
    body = upd[HEADER_SIZE:-2]
    assert body.startswith(b"S00E0000") and body.endswith(b"S705A00000005A\r\n")
    assert decode_srecords(body) == image


def test_short_image_is_padded_with_erased_flash():
    upd = build(b"\x12\x34", "4.34", end=64)
    assert decode_srecords(upd[HEADER_SIZE:-2]) == b"\x12\x34" + b"\xff" * 62
