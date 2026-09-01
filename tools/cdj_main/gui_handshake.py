"""Measure — and unblock — MAIN's GUI-link startup handshake.

    python -m tools.cdj_main.gui_handshake            write the mode early
    python -m tools.cdj_main.gui_handshake control    change nothing

MAIN emits a couple of status records per ten seconds instead of streaming
them, and with its debug dump on it says why thousands of times over::

    GU送:bAnsReceive=受信処理完了が3msec以内で完了していない!!!(Cmd=0x0)

That message is the *last* symptom of a race lost about ten seconds after
reset, not a slow link.  The chain, from the message back to the cause:

* ``0x215722`` (the send path) prints it whenever ``[0x489b368]`` — the word
  the message names, ``bAnsReceive`` — is not 1.
* Every transmit clears that word (``0x21659c``, in the record builder
  ``0x216400``); only the receive handler ``0x21345a`` sets it again.
* ``GuiCom_RcvTASK`` calls that handler only while ``[0x489bcf4] == 1``.
* ``GuiCom_SndTASK``'s startup loop (``0x215254``..``0x215304``) raises that
  word when it first polls ``0xfff10048`` and finds bit 2 clear, and needs the
  GuiCom mode at ``0x04c06fb0`` to read 1, 3 or 4 within **ten seconds**
  (``0x2152f0``, limit ``0x2710``).  On timeout it takes the other exit and
  **clears the word again** at ``0x215320``, which disables the receive handler
  for the rest of the run.
* The mode is published by ``PnlCom_SndTASK``.  With a panel board attached it
  comes out of the panel handshake in milliseconds.  With none — and no panel
  is modelled — ``[0x04fe29f4]`` stays 0, that task's inner loop breaks on its
  first pass, and the mode arrives only from its own ten-second fallback, at
  the same moment the GUI task gives up.

So two ten-second timers start together and MAIN loses its own race.  Writing
the mode word early with the firmware's own monitor (``LW`` is long-write,
arguments first — see ``tools.cdj_main.monitor``) settles it: 21 frames sent in
70 s becomes 11 840, ``bAnsReceive`` toggles instead of sticking at 0, and
``0xfff10048`` cycles 0x20 ↔ 0x24 instead of holding bit 2 forever.

The write is a *diagnosis*, not the fix.  The fix is to deliver one valid panel
frame: ``[0x04fe29f4]`` is written only by ``0x28cef4`` in ``FUN_a428ceb8``,
which ``PnlCom_RcvTASK`` (``0x28cc18``) calls once ``FUN_a428cdf8`` has
validated a frame, and nothing in the image writes the ``[0x04fe2b10]``
short-circuit that would end the wait early.

Three counters discriminate cheaply, without the console: ``0x489bc88``
(checksum errors), ``0x489bcb0`` (re-send requests) and ``0x489bcac`` (branch
marker).  All three staying 0 means the receive handler was never entered at
all — which is a different fault from one that runs and rejects.
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

from tools.cdj_main.procs import stop_tree

ROOT = Path(__file__).resolve().parents[2]
from tools.paths import BFIN_SIM, FIRMWARE, PACKETS, QEMU, qemu_environment
TEMP = Path(os.environ.get("TEMP", "."))
PORT = int(os.environ.get("CDJ_LINK_PORT", "5990"))
SECONDS = float(os.environ.get("CDJ_SECONDS", "70"))
POKE_AT = float(os.environ.get("CDJ_POKE_AT", "5"))

WATCH = [
    ("xp /1wx 0xfff10048", 0xFFF10048, "link flag"),
    ("xp /2wx 0x0489bcf4", 0x0489BCF4, "gate"),
    (None, 0x0489BCF8, "gate+4"),
    ("xp /1wx 0x0489b368", 0x0489B368, "bAnsReceive"),
    ("xp /1wx 0x04c06fb0", 0x04C06FB0, "mode"),
    ("xp /1wx 0x0489bcb0", 0x0489BCB0, "resend"),
    ("xp /1wx 0x0489bcac", 0x0489BCAC, "branch"),
]


def sample(sock: socket.socket) -> dict[int, int]:
    for command, _, _ in WATCH:
        if command:
            sock.sendall((command + "\n").encode())
            time.sleep(0.04)
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


def start_gui(env: dict[str, str], seconds: int) -> subprocess.Popen:
    """The Blackfin GUI, as a link client so it never misses MAIN's first frame."""
    return subprocess.Popen(
        [
            sys.executable, "-m", "tools.cdj_gui.run_headless",
            "--seconds", str(seconds),
            "--simulator", str(BFIN_SIM),
            "--packet", str(PACKETS / "status-standalone.bin"),
            "--output", str(TEMP / "handshake-frame.ppm"),
            "--log", str(TEMP / "handshake-gui.log"),
            "--env", "BFIN_PARALLEL_WRITEBACK=1",
            "--env", f"BFIN_MAIN_LINK=127.0.0.1:{PORT}",
            "--env", "BFIN_MAIN_PEER=1",
            "--env", "BFIN_MAIN_PEER_STATUS=" + str(PACKETS / "status-usb-q.bin"),
            "--env", "BFIN_MAIN_PEER_STATUS_HOLD=250",
            "--env", "BFIN_MAIN_PEER_PAYLOAD_1=" + str(PACKETS / "t1-browse.bin"),
        ],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def main() -> int:
    if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        # This tool takes no options, so an unguarded --help would be
        # read as input and would boot a machine to say so.
        print(__doc__)
        return 0
    poke = (sys.argv[1:] or ["poke"])[0] != "control"
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    env = qemu_environment()
    log = TEMP / f"handshake-{'poke' if poke else 'control'}.log"
    if log.exists():
        log.unlink()

    board = subprocess.Popen(
        [
            str(QEMU), "-M", "cdj2000-main", "-bios", str(FIRMWARE / "main-firmware.bin"),
            "-display", "none", "-no-reboot", "-d", "unimp", "-D", str(log),
            "-serial", f"tcp:127.0.0.1:{PORT},server,nowait",
            "-serial", f"tcp:127.0.0.1:{PORT + 2},server,nowait",
            "-serial", f"tcp:127.0.0.1:{PORT + 4},server,nowait",
            "-monitor", f"telnet:127.0.0.1:{PORT + 1},server,nowait",
        ],
        env=dict(env, CDJ_TMU_FREQ=os.environ.get("CDJ_TMU_FREQ", "270000000")),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    gui = console = mon = None
    start = time.time()
    try:
        time.sleep(1.5)
        console = socket.create_connection(("127.0.0.1", PORT + 4), timeout=5)
        console.settimeout(0.05)
        mon = socket.create_connection(("127.0.0.1", PORT + 1), timeout=5)
        mon.settimeout(0.4)
        gui = start_gui(env, int(SECONDS + 20))

        print(f"# {'writing the mode at %.0fs' % POKE_AT if poke else 'control'}")
        print("   t     0xfff10048  gate  gate+4  bAnsRcv  mode  resend  branch")
        previous = None
        written = not poke
        while time.time() - start < SECONDS:
            if not written and time.time() - start >= POKE_AT:
                console.sendall(b"4c06fb0,1,LW\r")
                written = True
                print(f"  --> 4c06fb0,1,LW at {time.time() - start:.1f}s")
            words = sample(mon)
            row = tuple(words.get(address, -1) for _, address, _ in WATCH)
            if row != previous:
                print(f"  {time.time() - start:5.1f}s  0x{row[0]:08x} {row[1]:5d}"
                      f" {row[2]:6d} {row[3]:8d} {row[4]:5d} {row[5]:7d} {row[6]:7d}")
                previous = row
            time.sleep(0.4)
        mon.sendall(b"quit\n")
        time.sleep(1.5)
    finally:
        for handle in (console, mon):
            if handle:
                handle.close()
        stop_tree(gui)
        try:
            board.wait(timeout=15)
        except subprocess.TimeoutExpired:
            board.kill()

    if log.exists():
        text = log.read_text(errors="replace")
        print(f"\n# link: MAIN sent {text.count('link-tx: sent')} frames, "
              f"received {text.count('link-rx: delivered')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
