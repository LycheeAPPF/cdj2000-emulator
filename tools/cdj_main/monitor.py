"""Drive MAIN's service monitor over the emulated console SCIF.

The CDJ's MAIN firmware carries a two-letter command monitor on its debug
serial port.  With the receive half of the SCIF modelled (see
``emulator/qemu/cdj2000_main.c``) it can simply be typed at:

    python -m tools.cdj_main.monitor "1,2,GU" "4fc45ec,1,LR"

**Arguments come first, then the command.**  The parser at ``0x101936`` scans
the line and stops at the first letter, taking it and the one after it as the
name — so everything before it is the argument list, separated by commas or
spaces.  The firmware's own message spells the order out: ``GUIcmd: 1,2,GU``.

The name is the little-endian halfword of the table entry, not its byte order:
the entry for the debug dump holds ``'U','G'`` and the command is ``GU``.  Read
the wrong way round the whole table looks like nonsense (``WL``, ``RB``); read
correctly it is ``LW``/``BW`` for long/byte write and ``LR``/``BR`` for read.

The 38 commands, from the table at ``0xa405c310`` — ``{c1, c0, argc, base,
handler}``, ``base`` being the radix the arguments are parsed in:

    MC 1d  PL 0h  PS 0h  DC 0   BW 2h  WW 2h  LW 2h   write byte/word/long
    BR 2h  WR 2h  LR 2h  RR 1h  RW 2h  ?T 0   !T 2h   read byte/word/long
    BP 0h  GU 2d  TT 2d  DJ 2h  YR 0   YE 0   YI 2d
    ER 0   ?X 0   ?V 0   KL 0   KY 1h  KI 1h  KO 1h
    HP 0   MO 1d  TE 1d  _D 0   _L 2d  _S 0   GD 0    GL 1d  GS 0  ZZ 0

Known replies: ``?V`` prints the firmware version (4.33), ``E04`` is an
unknown command and ``E06`` a malformed one — which is what a command typed
name-first gets, since no arguments are then parsed.

``1,2,GU`` turns the debug dump on at level CMD: it prints
``GUIcmd: $$$ Dubug Dump ON + Level=CMD $$$`` and stores 100 in the verbosity
threshold at ``0x0489bcb4``.  Levels are 1 = ERR, 2 = CMD, 3 = verbose, 0 = off.

Two things have to be right on the emulator side or nothing comes back, and
both are silent failures: the receive flags RDF and DR are cleared one at a
time so they must be modelled as individually write-to-clear, and output is
queued to a task that is woken by the *transmit* interrupt (INTEVT 0x760), so
without it the console stops after a handful of characters.
"""
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
from tools.paths import FIRMWARE as FIRMWARE_DIR, QEMU, qemu_environment

FIRMWARE = FIRMWARE_DIR / "main-firmware.bin"
PORT = int(os.environ.get("CDJ_LINK_PORT", "5960"))


def drain(console: socket.socket, seconds: float) -> bytes:
    """Collect whatever MAIN prints for a while."""
    out = bytearray()
    end = time.time() + seconds
    while time.time() < end:
        try:
            out.extend(console.recv(4096))
        except OSError:
            pass
    return bytes(out)


def monitor(sock: socket.socket, commands: list[str]) -> dict[int, int]:
    """Read words out of the QEMU monitor, for state the console cannot show."""
    for command in commands:
        sock.sendall((command + "\n").encode())
        time.sleep(0.25)
    text = ""
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            text += chunk.decode("ascii", "replace")
    except socket.timeout:
        pass
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    words: dict[int, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not re.match(r"^[0-9a-f]{8}:", line):
            continue
        address, _, rest = line.partition(":")
        base = int(address, 16)
        for index, value in enumerate(rest.split()):
            words[base + 4 * index] = int(value, 16)
    return words


def main() -> int:
    if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        # This tool takes no options, so an unguarded --help would be
        # read as input and would boot a machine to say so.
        print(__doc__)
        return 0
    commands = sys.argv[1:] or ["?V", "1,2,GU"]
    settle = float(os.environ.get("CDJ_SETTLE", "26"))
    wait = float(os.environ.get("CDJ_WAIT", "7"))
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    env = qemu_environment()
    process = subprocess.Popen(
        [
            str(QEMU), "-M", "cdj2000-main", "-bios", str(FIRMWARE),
            "-display", "none", "-no-reboot",
            "-serial", f"tcp:127.0.0.1:{PORT},server,nowait",
            "-serial", f"tcp:127.0.0.1:{PORT + 2},server,nowait",
            "-serial", f"tcp:127.0.0.1:{PORT + 4},server,nowait",
            "-monitor", f"telnet:127.0.0.1:{PORT + 1},server,nowait",
        ],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    console = sock = None
    try:
        time.sleep(3)
        # Attach before anything is printed: a chardev drops what it is handed
        # while no peer is there.
        console = socket.create_connection(("127.0.0.1", PORT + 4), timeout=5)
        console.settimeout(0.02)
        sock = socket.create_connection(("127.0.0.1", PORT + 1), timeout=5)
        sock.settimeout(1.2)

        print(f"# letting MAIN reach its steady state ({settle:.0f}s)")
        banner = drain(console, settle)
        if banner.strip():
            print(banner.decode("cp932", "replace"))

        for command in commands:
            console.sendall(command.encode() + b"\r")
            reply = drain(console, wait).decode("cp932", "replace")
            print(f"\n>>> {command}\n{reply}")

        words = monitor(sock, ["xp /2wx 0x045a01a8", "xp /1wx 0x0489bcb4"])
        print(f"\n# parsed arguments 0x{words.get(0x045A01A8, 0):x},"
              f"0x{words.get(0x045A01AC, 0):x}"
              f"   dump level {words.get(0x0489BCB4, 0)}")
        sock.sendall(b"quit\n")
        time.sleep(1.0)
    finally:
        for handle in (console, sock):
            if handle:
                handle.close()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
