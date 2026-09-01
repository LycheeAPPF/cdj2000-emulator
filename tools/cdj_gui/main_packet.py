"""Build checksum-valid packets for the CDJ-2000 GUI SPORT receiver."""

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import argparse
import struct
from pathlib import Path


CRC_POLYNOMIAL = 0x01102100

# Runtime command 0x10 carries the 400-column overview waveform.
#
# Verified against the handler at 0x00b7ea4e (2026-07-15).  Payloads land at
# receive-buffer offset 0x40, after the 64-byte announcement, so payload word N
# sits at 0xf00040 + 2N.  The handler reads:
#
#   word 1 (0xf00042) -- mode; must be 1 to take the direct 16-bit sample path
#   word 2 (0xf00044) -- first sample index
#   word 3 (0xf00046) -- last sample index, clamped to 399
#   word 4+i          -- sample i, read at 0xf00000 + (i + 36) * 2
#
# and copies each sample into the 400-entry buffer at 0x4b4ca2.  The earlier
# "header ends at word 35, samples start at word 36" reading was off by exactly
# the 32-word announcement offset and left word 1 zero, so the mode check failed
# and the handler bailed after three reads.
OVERVIEW_WAVEFORM_SAMPLES = 400
OVERVIEW_WAVEFORM_HEADER_WORDS = 4
OVERVIEW_WAVEFORM_DIRECT_MODE = 1
# Keep the captured 436-word length: the announcement advertises it and the
# simulator's MAIN peer keys its harvested payload off that size.
OVERVIEW_WAVEFORM_WORDS = 436

# Runtime command 0x11 contains the player/beat-grid state.  The stock parser
# follows several count-prefixed arrays and, with all counts zero, still reads
# fixed fields through word 85.  Keep extra zero padding between those fields
# and the CRC so a neutral packet never exposes the receive buffer's old data.
NEUTRAL_PLAYER_STATE_WORDS = 128


def firmware_crc(data: bytes) -> int:
    """Reproduce the 16-bit checksum routine at GUI address 0x00b7cb2a."""

    state = 0
    for byte in data:
        state |= byte
        for _ in range(8):
            state <<= 1
            if state & (1 << 24):
                state ^= CRC_POLYNOMIAL
    for _ in range(16):
        state <<= 1
        if state & (1 << 24):
            state ^= CRC_POLYNOMIAL
    return (state >> 8) & 0xFFFF


def build_command_payload(
    command: int,
    *,
    word_count: int = 8,
    payload_words: dict[int, int] | None = None,
) -> bytes:
    """Build a variable-length MAIN payload including its trailing CRC word."""

    if not 0 <= command <= 0x7FFF:
        raise ValueError("command must fit in 15 bits")
    if not 2 <= word_count <= 0x800:
        raise ValueError("payload word count must be in the range 2..0x800")
    payload = bytearray(word_count * 2)
    struct.pack_into("<H", payload, 0, command)
    if payload_words:
        for index, value in payload_words.items():
            if not 1 <= index < word_count - 1:
                raise ValueError("payload word index must exclude command and CRC")
            if not 0 <= value <= 0xFFFF:
                raise ValueError("payload word values must fit in 16 bits")
            struct.pack_into("<H", payload, index * 2, value)
    struct.pack_into("<H", payload, len(payload) - 2, firmware_crc(payload[:-2]))
    return bytes(payload)


def build_overview_waveform(samples: list[int] | None = None) -> bytes:
    """Build a complete command-0x10 packet carrying *samples*.

    ``samples`` defaults to a blank (all-zero) waveform.  Values are 16-bit and
    the handler passes each through a lookup before storing it, so the column
    height is not simply the raw value.
    """

    if samples is None:
        samples = [0] * OVERVIEW_WAVEFORM_SAMPLES
    if len(samples) != OVERVIEW_WAVEFORM_SAMPLES:
        raise ValueError(f"expected {OVERVIEW_WAVEFORM_SAMPLES} samples")

    end = OVERVIEW_WAVEFORM_SAMPLES - 1
    payload_words = {
        1: OVERVIEW_WAVEFORM_DIRECT_MODE,
        2: 0,  # first sample index
        3: end,  # last sample index
    }
    for index, value in enumerate(samples):
        payload_words[OVERVIEW_WAVEFORM_HEADER_WORDS + index] = value & 0xFFFF
    return build_command_payload(
        0x10,
        word_count=OVERVIEW_WAVEFORM_WORDS,
        payload_words=payload_words,
    )


def build_neutral_overview_waveform() -> bytes:
    """Build a complete command-0x10 packet containing a blank waveform."""

    return build_overview_waveform()


def build_neutral_player_state() -> bytes:
    """Build a fully initialized, idle command-0x11 player-state packet."""

    return build_command_payload(0x11, word_count=NEUTRAL_PLAYER_STATE_WORDS)


def build_bootstrap_packet(
    *,
    mode: int = 2,
    player_mask: int = 0xF,
    followup_words: int = 0,
    status_words: dict[int, int] | None = None,
) -> bytes:
    """Return the firmware's fixed 64-byte bootstrap/status packet.

    ``mode`` is the low byte of word 13.  Values 2..5 enter the recognized
    status paths in the GUI parser.  Bits 8..11 of that word advertise the
    four player/link slots.
    """

    if not 0 <= mode <= 0xFF:
        raise ValueError("mode must fit in one byte")
    if not 0 <= player_mask <= 0xF:
        raise ValueError("player_mask must fit in four bits")
    if not 0 <= followup_words <= 0x7FFF:
        raise ValueError("followup_words must fit in 15 bits")

    words = [0] * 32
    # These six words are rendered as three MAIN/GUI version pairs during
    # startup.  Nonzero values make that code path observable.
    words[6:12] = [1, 43, 1, 43, 1, 43]
    words[13] = mode | (player_mask << 8)
    # Word 29 selects the optional variable-payload path.  Word 30 is the
    # word count used to validate the fixed mode-2 follow-up header.
    words[29] = 0
    words[30] = followup_words

    if status_words:
        for index, value in status_words.items():
            if not 0 <= index < 29:
                raise ValueError("status word index must be in the range 0..28")
            if index == 13:
                raise ValueError("word 13 is controlled by mode and player_mask")
            if not 0 <= value <= 0xFFFF:
                raise ValueError("status word values must fit in 16 bits")
            words[index] = value

    packet = bytearray(struct.pack("<32H", *words))
    struct.pack_into("<H", packet, 62, firmware_crc(packet[:62]))
    return bytes(packet)


def build_framed_boot_stream(
    bootstrap: bytes,
    status_packet: bytes,
    *,
    repeat: int,
    drain_word: int = 0,
    header_command: int = 0,
    payload_commands: list[int] | None = None,
    payloads: list[bytes] | None = None,
) -> bytes:
    """Build the SPORT byte sequence consumed by a mode-2 cold boot.

    The GUI performs a 200-byte housekeeping receive before every protocol
    packet.  Its first mode-2 packet additionally requests an eight-word
    header whose last word is a CRC over the preceding seven words.
    """

    if len(bootstrap) != 64 or len(status_packet) != 64:
        raise ValueError("bootstrap and status packets must each be 64 bytes")
    if repeat <= 0:
        raise ValueError("repeat must be positive")
    if not 0 <= drain_word <= 0xFFFF:
        raise ValueError("drain_word must fit in 16 bits")
    if not 0 <= header_command <= 0x7FFF:
        raise ValueError("header_command must fit in 15 bits")

    if payload_commands is None:
        payload_commands = []
    if payloads is None:
        payloads = []
    if payload_commands and payloads:
        raise ValueError("use payload_commands or payloads, not both")
    if payload_commands:
        payloads = [build_command_payload(command) for command in payload_commands]
    if len(payloads) > repeat:
        raise ValueError("payload command count cannot exceed repeat count")
    for payload in payloads:
        if len(payload) < 4 or len(payload) > 0x1000 or len(payload) % 2:
            raise ValueError("payloads must contain 2..0x800 complete words")
        if firmware_crc(payload[:-2]) != struct.unpack_from("<H", payload, len(payload) - 2)[0]:
            raise ValueError("payload has an invalid trailing CRC")

    drain = struct.pack("<H", drain_word) * 100
    output = bytearray(bootstrap + drain + build_command_payload(header_command))
    for index in range(repeat):
        if index < len(payloads):
            payload = payloads[index]
            announced_status = bytearray(status_packet)
            struct.pack_into("<H", announced_status, 58, 1)
            struct.pack_into("<H", announced_status, 60, len(payload) // 2)
            struct.pack_into(
                "<H", announced_status, 62, firmware_crc(announced_status[:62])
            )
            output += drain + announced_status
            output += payload
        else:
            output += drain + status_packet
    return bytes(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--mode", type=lambda value: int(value, 0), default=2)
    parser.add_argument(
        "--player-mask", type=lambda value: int(value, 0), default=0xF
    )
    parser.add_argument(
        "--status-word",
        action="append",
        default=[],
        metavar="INDEX=VALUE",
        help="override one normal-status word; may be supplied more than once",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="write this many consecutive packets for a persistent MAIN stream",
    )
    parser.add_argument(
        "--followup-words",
        type=lambda value: int(value, 0),
        default=0,
        help="bootstrap word 30; use 8 for the mode-2 framed boot header",
    )
    parser.add_argument(
        "--framed-status",
        type=Path,
        help="64-byte status packet to interleave after the mode-2 boot header",
    )
    parser.add_argument(
        "--drain-word",
        type=lambda value: int(value, 0),
        default=0,
        help="word repeated through each 200-byte housekeeping receive",
    )
    parser.add_argument(
        "--header-command",
        type=lambda value: int(value, 0),
        default=0,
        help="command in the first mode-2 follow-up header",
    )
    parser.add_argument(
        "--payload-command",
        action="append",
        type=lambda value: int(value, 0),
        default=[],
        help=(
            "command in an optional payload following the next status packet; "
            "may be supplied repeatedly to build a staged handshake"
        ),
    )
    parser.add_argument(
        "--payload-file",
        action="append",
        type=Path,
        default=[],
        help=(
            "prebuilt variable-length payload with a trailing firmware CRC; "
            "may be supplied repeatedly"
        ),
    )
    args = parser.parse_args()

    status_words: dict[int, int] = {}
    for assignment in args.status_word:
        try:
            index_text, value_text = assignment.split("=", 1)
            status_words[int(index_text, 0)] = int(value_text, 0)
        except ValueError as error:
            parser.error(f"invalid --status-word {assignment!r}: {error}")

    packet = build_bootstrap_packet(
        mode=args.mode,
        player_mask=args.player_mask,
        followup_words=args.followup_words,
        status_words=status_words,
    )
    if args.repeat <= 0:
        parser.error("--repeat must be positive")
    if args.payload_command and args.payload_file:
        parser.error("use --payload-command or --payload-file, not both")
    if args.framed_status:
        status_packet = args.framed_status.read_bytes()[:64]
        output = build_framed_boot_stream(
            packet,
            status_packet,
            repeat=args.repeat,
            drain_word=args.drain_word,
            header_command=args.header_command,
            payload_commands=args.payload_command,
            payloads=[path.read_bytes() for path in args.payload_file],
        )
    else:
        output = packet * args.repeat
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)
    print(
        f"wrote {len(output)} bytes ({args.repeat} packet(s)) "
        f"to {args.output} "
        f"(crc=0x{struct.unpack_from('<H', packet, 62)[0]:04x})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
