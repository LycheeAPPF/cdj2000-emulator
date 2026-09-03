"""A transparent proxy on the MAIN/GUI link that can inject GUI requests.

The GUI simulator normally connects straight to the two TCP chardevs of the
emulated MAIN board (requests to port, records from port+2).  Run this
between them -- ``BFIN_MAIN_LINK=127.0.0.1:5990`` on the simulator, MAIN on its
usual 5980/5982 -- and every byte is forwarded both ways while, at the times
given, a valid 48-byte request of your own goes to MAIN as if the GUI had sent
it.  That is how a track was first loaded in the emulator (2026-09-03): the
NXS GUI's own browse keys never produced an "enter", so the browse and load
requests were injected instead::

    python -m tools.cdj_main.link_inject --inject 90:1:3:7:1:0 \\
        --inject 110:1:3:7:1:0 --inject 130:1:3:7:1:0 --inject 155:7:1:0:0

An injection is ``SECONDS:TYPE:CURSOR[:W3:W4:...]``: word 1 of the request is
``0x8000 | TYPE``, word 2 the cursor, words 3.. as given, the firmware CRC in
the last word.  Seconds count from the moment the GUI connected.

What MAIN's requests mean, as measured (``runs/nxs-swap/trackload-11/-12``):

* type 1 cursor 11, w3 = 7, w4 = 2 (the SD KIND), w5 = N -- the preview pane
  of row N of MAIN's current list;
* type 1 cursor 3, w3 = 7, w4 = 1, w5 = N -- ENTER row N: the answer (command
  0x13) becomes MAIN's current list, its header word 5 is the new level
  (categories 1, PLAYLIST root 2, a folder 3, a playlist's tracks 4);
* type 7 cursor 1, words 3/4 = a 32-bit index into MAIN's current list --
  the track load.  MAIN's loader (task 60) walks the list from that index and
  needs a track row; on any other row it logs an error-ring entry
  (``boot_vm --caution`` prints the ring) and loads nothing.

So from the library screen (current list = the categories, PLAYLIST first):
enter 0 (the playlist root), enter 0 (the first folder), enter 0 (its first
playlist: the track list), load 0 (its first track).

The request format and checksum come from CDJ2000-revival
``codex/tools/main_link_proxy.py``, which injected the load alone.
"""

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import argparse
import selectors
import socket
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..cdj_gui.main_packet import firmware_crc

REQUEST_BYTES = 48


def build_request(request_type: int, cursor: int, words: tuple[int, ...] = ()) -> bytes:
    """A 48-byte GUI request: word 1 = 0x8000 | type, word 2 = cursor, words 3.. as given."""

    if not 0 <= request_type <= 0x3FFF:
        raise ValueError("request type must fit in 14 bits")
    if not 0 <= cursor <= 0xFFFF:
        raise ValueError("cursor must fit in 16 bits")
    if len(words) > REQUEST_BYTES // 2 - 4:
        raise ValueError("too many words for a 48-byte request")
    packet = bytearray(REQUEST_BYTES)
    struct.pack_into("<3H", packet, 0, 0, request_type | 0x8000, cursor)
    for index, word in enumerate(words, 3):
        struct.pack_into("<H", packet, index * 2, word & 0xFFFF)
    struct.pack_into("<H", packet, REQUEST_BYTES - 2, firmware_crc(packet[:-2]))
    return bytes(packet)


@dataclass(frozen=True, order=True)
class Injection:
    seconds: float
    request_type: int
    cursor: int
    words: tuple[int, ...]


def parse_injection(spec: str) -> Injection:
    parts = spec.split(":")
    if len(parts) < 3:
        raise ValueError("an injection is SECONDS:TYPE:CURSOR[:W3:W4:...]")
    result = Injection(float(parts[0]), int(parts[1], 0), int(parts[2], 0),
                       tuple(int(word, 0) for word in parts[3:]))
    if result.seconds < 0:
        raise ValueError("injection time cannot be negative")
    build_request(result.request_type, result.cursor, result.words)   # validates
    return result


def connect_retry(host: str, port: int, deadline: float) -> socket.socket:
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            stream = socket.create_connection((host, port), timeout=1.0)
            stream.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return stream
        except OSError as exc:
            last_error = exc
            time.sleep(0.1)
    raise TimeoutError(f"could not connect to {host}:{port}: {last_error}")


def listener(host: str, port: int) -> socket.socket:
    stream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    stream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    stream.bind((host, port))
    stream.listen(1)
    return stream


def accept_until(stream: socket.socket, deadline: float) -> socket.socket:
    stream.settimeout(max(0.1, deadline - time.monotonic()))
    client, _ = stream.accept()
    client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return client


def run_proxy(listen_host: str, listen_port: int, main_host: str, main_port: int,
              injections: list[Injection], timeout: float,
              request_dump: Path | None = None) -> int:
    request_listener = listener(listen_host, listen_port)
    record_listener = listener(listen_host, listen_port + 2)
    streams: list[socket.socket] = [request_listener, record_listener]
    dump = request_dump.open("wb") if request_dump else None
    deadline = time.monotonic() + timeout
    try:
        main_request = connect_retry(main_host, main_port, deadline)
        main_record = connect_retry(main_host, main_port + 2, deadline)
        streams += [main_request, main_record]
        print(f"proxy: MAIN connected at {main_host}:{main_port}/{main_port + 2}", flush=True)
        gui_request = accept_until(request_listener, deadline)
        gui_record = accept_until(record_listener, deadline)
        streams += [gui_request, gui_record]
        print(f"proxy: GUI connected at {listen_host}:{listen_port}/{listen_port + 2}", flush=True)

        pairs = {
            gui_request: (main_request, "gui-request"),
            main_request: (gui_request, "main-request"),
            gui_record: (main_record, "gui-record"),
            main_record: (gui_record, "main-record"),
        }
        selector = selectors.DefaultSelector()
        for source in pairs:
            source.setblocking(False)
            selector.register(source, selectors.EVENT_READ)

        link_started = time.monotonic()
        pending = sorted(injections)
        counts = {name: 0 for _, name in pairs.values()}
        while time.monotonic() < deadline:
            elapsed = time.monotonic() - link_started
            while pending and pending[0].seconds <= elapsed:
                injection = pending.pop(0)
                packet = build_request(injection.request_type, injection.cursor, injection.words)
                main_request.sendall(packet)
                if dump:
                    dump.write(packet)
                    dump.flush()
                print("proxy: injected type=%d cursor=%d words=%s crc=%04x at t%.3f"
                      % (injection.request_type, injection.cursor,
                         " ".join("%04x" % word for word in injection.words),
                         struct.unpack_from("<H", packet, REQUEST_BYTES - 2)[0], elapsed),
                      flush=True)
            wait = 0.2
            if pending:
                wait = min(wait, max(0.0, pending[0].seconds - elapsed))
            for key, _ in selector.select(wait):
                source = key.fileobj
                target, name = pairs[source]
                try:
                    data = source.recv(1 << 16)
                except BlockingIOError:
                    continue
                except ConnectionResetError:
                    # QEMU closes its chardevs with RST when boot_vm tears the
                    # machine down; the capture is complete by then.
                    print(f"proxy: {name} reset during teardown", flush=True)
                    return 0
                if not data:
                    print(f"proxy: {name} closed", flush=True)
                    return 0
                try:
                    target.sendall(data)
                except ConnectionResetError:
                    print(f"proxy: {name} target reset during teardown", flush=True)
                    return 0
                counts[name] += len(data)
                if dump and name == "gui-request":
                    dump.write(data)
                    dump.flush()
        print("proxy: timeout; " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())),
              flush=True)
        return 0
    finally:
        if dump:
            dump.close()
        for stream in reversed(streams):
            try:
                stream.close()
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     epilog="See the module docstring for the request vocabulary.")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=5990,
                        help="where the GUI connects (records on +2); default 5990")
    parser.add_argument("--main-host", default="127.0.0.1")
    parser.add_argument("--main-port", type=int, default=5980,
                        help="MAIN's request chardev (records on +2); default 5980")
    parser.add_argument("--inject", action="append", default=[], metavar="SECONDS:TYPE:CURSOR[:W3..]",
                        help="a request to send MAIN at SECONDS after the GUI connected; repeatable")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--request-dump", type=Path,
                        help="append every GUI request and every injection, 48 bytes each")
    args = parser.parse_args(argv)
    try:
        injections = [parse_injection(spec) for spec in args.inject]
        return run_proxy(args.listen_host, args.listen_port, args.main_host, args.main_port,
                         injections, args.timeout, args.request_dump)
    except (OSError, TimeoutError, ValueError) as exc:
        print(f"link_inject: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
