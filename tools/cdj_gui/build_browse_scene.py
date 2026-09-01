"""Emit the three MAIN payloads that reproduce the reference photo's screen.

``cdj2000-interface-real-unit.jpg`` shows a CDJ-2000 browsing a USB stick with
no CD in the drive.  Reproducing it needs three things served together, and
getting any one of them wrong leaves a region blank in a way that looks like a
render fault:

* a **status record** that selects USB, reports it mounted, and turns on the
  player-row elements the photo shows (``REMAIN``, ``QUANTIZE``),
* a **type-1 list** whose entry 0 is the source header and whose entries 1..6
  are the browse categories,
* a **second list for cursor 11**, the right pane (see below),
* a **type-9 player state** carrying the message-line text in string 2.

A request is a ``(type, cursor)`` pair: word 1 of the 48-byte request is
``0x8000|type`` and word 2 is the cursor.  The GUI asks for type 1 three times
over -- cursor 1 (left list), cursor 3, and **cursor 11 (the right pane)** --
and the peer used to key its payloads on the type alone, so all three got the
left list's bytes.  That, and not an unfilled buffer, is why the two panes
mirrored each other.  ``FUN_00b7d396`` handles cursor 11; it parses the *same*
wire format as ``FUN_00b7d61c`` into its own state:

===================  ====================  ====================
field                left (``0xb7d61c``)   right (``0xb7d396``)
===================  ====================  ====================
total (w1..2)        ``0x4b53d8``          ``0x4b53e4``
cursor (w3..4)       ``0x4b53dc``          ``0x4b53e8``
highlight (w6)       ``0x4b53e0``          ``0x4b53ec``
entries 1..9         ``0x4b53ee``          ``0x4b6624``
===================  ====================  ====================

Row *n* of a pane is entry *n+1*, so entries 1..6 are the six visible rows in
both cases and :func:`build_type1_list` builds either one unchanged.

Run it, then feed the outputs to ``run_headless``::

    python -m tools.cdj_gui.build_browse_scene packets
    python -m tools.cdj_gui.run_headless --seconds 300 \
      --packet packets/status-standalone.bin \
      --output runs/scene.ppm --log runs/scene.log \
      --tx-output runs/scene-tx.bin \
      --env BFIN_PARALLEL_WRITEBACK=1 --env BFIN_MAIN_PEER=1 \
      --env BFIN_MAIN_PEER_STATUS=packets/scene-status.bin \
      --env BFIN_MAIN_PEER_STATUS_HOLD=250 \
      --env BFIN_MAIN_PEER_PAYLOAD_1=packets/scene-type1.bin \
      --env BFIN_MAIN_PEER_PAYLOAD_1_11=packets/scene-type1-c11.bin \
      --env BFIN_MAIN_PEER_PAYLOAD_9=packets/scene-type9.bin

``BFIN_PARALLEL_WRITEBACK=1`` is not optional: without it the firmware's
resource-table relocation is off by one entry and every small label draws its
neighbour's bitmap.  ``BFIN_SKIP_WIDGET`` is *not* wanted -- with USB mounted the
screen router leaves screen 0, and the "Wait" platter and teal box are not part
of the screen it lands on.
"""

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import argparse
from pathlib import Path

from .build_player_state import STRING_COUNT, build_player_state
from .build_status_record import (
    SOURCE_USB,
    STATE_ABSENT,
    STATE_MOUNTED,
    STRIP_REMAIN,
    STRIP_TEMPO,
    STRIP_TRACK,
    STRIP_BPM,
    WORD25_QUANTIZE,
    build_status_record,
)
from .build_type1_list import BROWSE_ROOT, build_type1_list, category

# Word 11's own captured idle value already carries TEMPO/BPM/TRACK; naming them
# here keeps the intent readable rather than relying on the default.
PLAYER_ROW = STRIP_TEMPO | STRIP_BPM | STRIP_TRACK | STRIP_REMAIN

# The message line reads type-9 string 2 (-> 0x4b9642).
MESSAGE_STRING = 2

# The right pane of the reference photo: the ARTIST category expanded.
BROWSE_ARTISTS = (
    "ABC",
    "Agent Greg vs Audiopu",
    "Armand de France",
    "Armand Pena",
    "armand van helden",
    "Armin van Buuren",
)

# Index 7 in the 47-entry glyph table at 0xb5d64c is the person symbol the photo
# draws left of every artist row.
ICON_PERSON = 7


def build_scene(
    *,
    header: str = "USB",
    header_icon: int = 0,
    categories: tuple[str, ...] = BROWSE_ROOT,
    artists: tuple[str, ...] = BROWSE_ARTISTS,
    artist_icon: int = ICON_PERSON,
    message: str = "NO DISC",
    total: int | None = None,
) -> tuple[bytes, bytes, bytes, bytes]:
    """Return ``(status_record, type1_payload, type1_cursor11, type9_payload)``.

    *header_icon* is the low byte of entry 0's attribute word, which selects the
    16x16 glyph drawn at (1,0) left of the header text; 0 draws none.  The photo
    wants the USB plug symbol and that index is not identified yet -- 1 is a
    folder-with-arrow, 7 a person, 11 a pie.  The category rows keep index 0
    because the photo's left column has no icons.
    """

    if len(categories) > 6 or len(artists) > 6:
        raise ValueError("the browse panes show at most six rows")

    status = build_status_record(
        source=SOURCE_USB,
        states=(STATE_ABSENT, STATE_ABSENT, STATE_MOUNTED, STATE_ABSENT),
        colours=(0, 0, 0, 0),  # nibble 0 is the photo's colour; 3 tints it amber
        overrides={11: PLAYER_ROW, 25: 0x4000 | WORD25_QUANTIZE},
    )

    rows = [header] + [category(name) for name in categories]
    attrs = [(0x0100 | (header_icon & 0xFF), 0)] + [(0x0100, 0)] * len(categories)
    # A row only renders while its index is below MIN(total - cursor, 6), so
    # total has to cover the rows actually sent.
    type1 = build_type1_list(
        rows,
        total=len(categories) if total is None else total,
        cursor=0,
        highlight=0,
        attrs=attrs,
        word_count=256,
    )

    # The right pane's own list.  Row n is entry n+1 there too, so entry 0 is a
    # header slot the pane itself does not draw -- it is kept because the
    # handler indexes from 1 either way.
    right_rows = [header] + list(artists)
    right_attrs = [(0x0100, 0)] + [
        (0x0100 | (artist_icon & 0xFF), 0) for _ in artists
    ]
    type1_right = build_type1_list(
        right_rows,
        total=len(artists) if total is None else total,
        cursor=0,
        highlight=0,
        attrs=right_attrs,
        word_count=256,
    )

    strings = [""] * STRING_COUNT
    strings[MESSAGE_STRING - 1] = message
    type9 = build_player_state(strings=strings)
    return status, type1, type1_right, type9


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("outdir", type=Path)
    parser.add_argument("--prefix", default="scene")
    parser.add_argument("--header", default="USB")
    parser.add_argument(
        "--header-icon",
        type=lambda value: int(value, 0),
        default=0,
        help="16x16 glyph index for the header; 0 = none",
    )
    parser.add_argument("--message", default="NO DISC")
    parser.add_argument(
        "--right-rows",
        default=",".join(BROWSE_ARTISTS),
        help="comma-separated rows for the right pane (cursor 11), up to six",
    )
    parser.add_argument(
        "--right-icon",
        type=lambda value: int(value, 0),
        default=ICON_PERSON,
        help=f"16x16 glyph index for the right rows; 0 = none (default {ICON_PERSON} = person)",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=4,
        help="how many copies of the status record to write; the peer holds the "
        "last one forever, so more than a couple only matters when you want a "
        "transition",
    )
    args = parser.parse_args()

    status, type1, type1_right, type9 = build_scene(
        header=args.header,
        header_icon=args.header_icon,
        artists=tuple(row for row in args.right_rows.split(",") if row),
        artist_icon=args.right_icon,
        message=args.message,
    )
    args.outdir.mkdir(parents=True, exist_ok=True)
    written = {
        f"{args.prefix}-status.bin": status * max(1, args.repeat),
        f"{args.prefix}-type1.bin": type1,
        f"{args.prefix}-type1-c11.bin": type1_right,
        f"{args.prefix}-type9.bin": type9,
    }
    for name, data in written.items():
        (args.outdir / name).write_bytes(data)
        print(f"wrote {args.outdir / name} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
