"""Build the internal type 9 payload — the player state (command 0x19).

Decompiled from the handler ``FUN_00b7e536`` (Ghidra + the Blackfin processor
module).  The record is a run of scalars
followed by **seven variable-length UTF-16 strings**, each preceded by a length.

Payload word N sits at ``0xf00040 + 2N``.  Two layout rules are easy to get
wrong and both were read straight out of the handler:

* The scalars arrive as **32-bit fields, high word first**, and the handler only
  keeps the low half — so a value written at word ``2k+1`` is what survives and
  word ``2k`` is ignored.  This is the same convention as the type-1 header's
  ``CONCAT22(w1, w2)``.
* After the **first** string the layout gains a one-word gap before every
  following length, because that length is itself the low half of a 32-bit
  field.  The first string is the exception: its length sits at word 21 and its
  text starts immediately at word 22.

String destinations, in transmission order::

    string 1 -> 0x4b95f6      string 5 -> 0x4b978c
    string 2 -> 0x4b9642      string 6 -> 0x4b974a
    string 3 -> 0x4b9684      string 7 -> 0x4b96c6
    string 4 -> 0x4b9708

Slots 2..7 are the six 66-byte entries at ``0x4b9642..0x4b97ce`` (stride 0x42);
note the transmission order is **not** the address order.  Every string is
capped at 0x20 characters — a longer one makes the handler skip the copy but
still advance its cursor, which desynchronises everything after it.
"""

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import argparse
from pathlib import Path

from .main_packet import build_command_payload

TYPE9_COMMAND = 0x19
MAX_STRING_CHARS = 0x20
STRING_COUNT = 7
DEFAULT_WORD_COUNT = 128

# The five scalars that follow the first string, in transmission order.
TRAILING_SCALARS = 7  # 0x4b97ce, 0x4b97d0, 0x4b97d2, 0x4b95f4, 0x4b97d6, d8, da

# Clamps the handler applies to the trailing scalars; a value above the clamp is
# silently replaced by 0, so passing one is always a mistake worth rejecting.
TRAILING_CLAMPS = (None, 2, 1, 1, 1, 1, 1)


def build_player_state(
    *,
    scalars: list[int] | None = None,
    strings: list[str] | None = None,
    after_first: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0),
    trailing: tuple[int, ...] = (0,) * TRAILING_SCALARS,
    word_count: int = DEFAULT_WORD_COUNT,
) -> bytes:
    """Return a complete command-0x19 payload.

    *scalars* are the nine 32-bit head fields (only the low half is kept);
    *strings* are the seven UTF-16 fields in transmission order; *after_first*
    are the five scalars that sit between string 1 and string 2; *trailing* are
    the seven scalars after the last string.

    The defaults describe an idle player with **no track loaded**: every scalar
    zero and every string empty.
    """

    scalars = list(scalars or [0] * 9)
    strings = list(strings or [""] * STRING_COUNT)
    if len(scalars) != 9:
        raise ValueError("the handler reads nine head scalars")
    if len(strings) != STRING_COUNT:
        raise ValueError(f"the handler reads {STRING_COUNT} strings")
    if len(trailing) != TRAILING_SCALARS:
        raise ValueError(f"the handler reads {TRAILING_SCALARS} trailing scalars")
    for text in strings:
        if len(text) > MAX_STRING_CHARS:
            raise ValueError(
                f"a string longer than {MAX_STRING_CHARS} chars desynchronises the record"
            )
    for value, clamp in zip(trailing, TRAILING_CLAMPS):
        if clamp is not None and value > clamp:
            raise ValueError(f"a trailing scalar above {clamp} is clamped to 0 by the handler")

    words: dict[int, int] = {}
    # Head scalars: low half of each 32-bit field, i.e. the odd words 3..19.
    for index, value in enumerate(scalars):
        words[3 + index * 2] = value & 0xFFFF

    first = strings[0]
    words[21] = len(first)
    for offset, char in enumerate(first):
        words[22 + offset] = ord(char)

    base = len(first)
    for index, value in enumerate(after_first):
        words[23 + base + index * 2] = value & 0xFFFF

    second = strings[1]
    words[33 + base] = len(second)
    for offset, char in enumerate(second):
        words[34 + base + offset] = ord(char)

    # Mirror the handler's own cursor so the gap-before-length rule cannot drift.
    cursor = base + 0x22 + len(second)
    for text in strings[2:]:
        words[cursor + 1] = len(text)
        cursor += 2
        for offset, char in enumerate(text):
            words[cursor + offset] = ord(char)
        cursor += len(text)

    for index, value in enumerate(trailing):
        words[cursor + 1 + index * 2] = value & 0xFFFF

    needed = cursor + 1 + (TRAILING_SCALARS - 1) * 2
    if needed >= word_count - 1:
        raise ValueError(f"record needs {needed + 2} words; raise word_count above {word_count}")
    return build_command_payload(
        TYPE9_COMMAND, word_count=word_count, payload_words=words
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--string",
        action="append",
        default=[],
        metavar="INDEX=TEXT",
        help="set string INDEX (1..7); default is all empty, i.e. no track loaded",
    )
    parser.add_argument("--word-count", type=lambda value: int(value, 0), default=DEFAULT_WORD_COUNT)
    args = parser.parse_args()

    strings = [""] * STRING_COUNT
    for assignment in args.string:
        name, _, text = assignment.partition("=")
        index = int(name, 0)
        if not 1 <= index <= STRING_COUNT:
            parser.error(f"string index must be 1..{STRING_COUNT}")
        strings[index - 1] = text

    payload = build_player_state(strings=strings, word_count=args.word_count)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    print(f"wrote {args.output}; {len(payload)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
