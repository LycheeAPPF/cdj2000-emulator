"""Pair every GUI request MAIN received with what MAIN sent back, from MAIN's log.

Input is the ``-D`` log ``boot_vm --main-output`` writes.  The board logs both
halves of the link there with the virtual clock::

    cdj2000.link-rx: delivered 48 bytes to 0xa4500000, status 0x04 words 0000 8001 000b 000a 0002 0000 queued 0 t=99.1234
    cdj2000.link-tx: sent 896 bytes from 0xa4500800 (connected=1 written=904, first words 001b 0000) t=99.1501

A delivered frame is a **request** when word 1 has bit 15 set (type = the low
14 bits, word 2 = cursor, words 3..5 = its parameters); with bit 15 clear it is
either a status poll (word 1 zero) or the GUI asking again for the same
request (a repeat).  A 64-byte send is a status record, anything else a
payload whose first word is the answer's command.

Every request opens a window that lasts until the next request.  The payloads
MAIN sent in that window are its answers; a window with none is a request MAIN
did not answer with a payload -- the type-9 player-state request of the NXS
GUI on MAIN 4.33 is the standing example (2026-09-03: 17 requests, 0 answers).
Requests are grouped by type, cursor and parameter words; the report gives per
group how many were asked, how many answered, the answer signatures (length
and command) with counts, the answer latency, and the repeats the GUI needed.

With ``--dump`` (the GUI's ``BFIN_MAIN_LINK_DUMP`` of the same run) one payload
of every answer signature is decoded into its rows or strings, so the table
says not just "896 bytes, command 0x13" but which list that was.

The last lines count the frames delivered less than half a millisecond after
the one before -- the signature of the receive FIFO handing a frame over
before the firmware's task had read the previous one (see
``CDJ_LINK_RX_HANDOVER`` in ``emulator/qemu/cdj2000_main.c``).
"""

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import argparse
import collections
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

DELIVERED = re.compile(
    r"link-rx: delivered (\d+) bytes to 0x[0-9a-fA-F]+, status 0x[0-9a-fA-F]+ words"
    r"((?: [0-9a-fA-F]{4}){6})(?: queued \d+)? t=([0-9.]+)")
SENT = re.compile(
    r"link-tx: sent (\d+) bytes from 0x[0-9a-fA-F]+ \(connected=\d+ written=-?\d+,"
    r" first words ([0-9a-fA-F]{4}) ([0-9a-fA-F]{4})\) t=([0-9.]+)")

STATUS_LENGTH = 64
REQUEST_BIT = 0x8000
BACK_TO_BACK_S = 0.0005


@dataclass(frozen=True)
class Frame:
    """One delivered frame: its header words and the virtual time."""

    time: float
    length: int
    words: tuple[int, ...]

    @property
    def is_request(self) -> bool:
        return bool(self.words[1] & REQUEST_BIT)

    @property
    def is_poll(self) -> bool:
        return self.words[1] == 0

    @property
    def key(self) -> tuple[int, int, int, int, int]:
        return (self.words[1] & 0x3FFF, self.words[2], self.words[3], self.words[4], self.words[5])


@dataclass(frozen=True)
class Send:
    time: float
    length: int
    command: int
    word1: int


@dataclass
class Exchange:
    request: Frame
    answers: list[Send] = field(default_factory=list)
    status_records: int = 0
    repeats: int = 0
    polls: int = 0


def parse(lines: Iterable[str]) -> tuple[list[Frame], list[Send]]:
    frames: list[Frame] = []
    sends: list[Send] = []
    for line in lines:
        match = DELIVERED.search(line)
        if match:
            words = tuple(int(word, 16) for word in match.group(2).split())
            frames.append(Frame(float(match.group(3)), int(match.group(1)), words))
            continue
        match = SENT.search(line)
        if match:
            sends.append(Send(float(match.group(4)), int(match.group(1)),
                              int(match.group(2), 16), int(match.group(3), 16)))
    return frames, sends


def exchanges(frames: list[Frame], sends: list[Send]) -> list[Exchange]:
    """Open a window per request; everything until the next request belongs to it."""

    result: list[Exchange] = []
    current: Exchange | None = None
    sends_iter = iter(sorted(sends, key=lambda send: send.time))
    pending = next(sends_iter, None)

    def take_sends_until(limit: float | None) -> None:
        nonlocal pending
        while pending is not None and (limit is None or pending.time < limit):
            if current is not None and pending.time >= current.request.time:
                if pending.length == STATUS_LENGTH:
                    current.status_records += 1
                else:
                    current.answers.append(pending)
            pending = next(sends_iter, None)

    for frame in frames:
        if frame.length != 48:
            continue
        if frame.is_request:
            take_sends_until(frame.time)
            current = Exchange(frame)
            result.append(current)
        elif current is not None:
            if frame.is_poll:
                current.polls += 1
            elif (frame.words[1] & 0x3FFF) == current.request.key[0] \
                    and frame.words[2] == current.request.key[1]:
                current.repeats += 1
    take_sends_until(None)
    return result


def back_to_back(frames: list[Frame], gap: float = BACK_TO_BACK_S) -> list[tuple[Frame, Frame]]:
    pairs = []
    for previous, frame in zip(frames, frames[1:]):
        if 0 <= frame.time - previous.time < gap:
            pairs.append((previous, frame))
    return pairs


def signature(send: Send) -> str:
    return "%dB/0x%04x" % (send.length, send.command)


def format_key(key: tuple[int, int, int, int, int]) -> str:
    return "%2d %5d  %04x %04x %04x" % key


def report(frames: list[Frame], sends: list[Send], *, decoded: dict[str, list[str]] | None = None,
           timeline: bool = False) -> list[str]:
    windows = exchanges(frames, sends)
    lines: list[str] = []
    groups: dict[tuple, list[Exchange]] = collections.defaultdict(list)
    for window in windows:
        groups[window.request.key].append(window)

    polls = [frame for frame in frames if frame.length == 48 and frame.is_poll]
    requests = [frame for frame in frames if frame.length == 48 and frame.is_request]
    repeats = [frame for frame in frames
               if frame.length == 48 and not frame.is_poll and not frame.is_request]
    payloads = [send for send in sends if send.length != STATUS_LENGTH]
    lines.append("frames delivered: %d  (requests %d, repeats %d, status polls %d)"
                 % (len([f for f in frames if f.length == 48]), len(requests), len(repeats), len(polls)))
    lines.append("frames sent: %d  (status records %d, payloads %d)"
                 % (len(sends), len(sends) - len(payloads), len(payloads)))
    if frames:
        lines.append("guest time: %.1f .. %.1f s" % (frames[0].time, frames[-1].time))
    lines.append("")
    lines.append("requests by type, cursor and words 3..5 -- asked, answered with a payload, the answers,"
                 " latency of the first answer, repeats the GUI sent meanwhile:")
    lines.append("type cursor  w3   w4   w5    asked answered  answers                       first ms (median/max)  repeats")
    for key, group in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        answered = [window for window in group if window.answers]
        signatures = collections.Counter(signature(send) for window in group for send in window.answers)
        latencies = [(window.answers[0].time - window.request.time) * 1000 for window in answered]
        repeats_total = sum(window.repeats for window in group)
        answers_text = ", ".join("%s x%d" % item for item in signatures.most_common()) or "--"
        latency_text = ("%6.1f / %6.1f" % (statistics.median(latencies), max(latencies))
                        if latencies else "      --       ")
        flag = "" if answered else "  <-- never answered"
        lines.append("%s  %5d %8d  %-30s %s  %7d%s"
                     % (format_key(key), len(group), len(answered), answers_text, latency_text,
                        repeats_total, flag))
    unanswered = sorted(key for key, group in groups.items() if not any(w.answers for w in group))
    lines.append("")
    if unanswered:
        lines.append("never answered with a payload: " + "; ".join(
            "type %d cursor %d words %04x %04x %04x (x%d)" % (*key, len(groups[key]))
            for key in unanswered))
    else:
        lines.append("every request class got a payload at least once")

    if decoded is not None:
        lines.append("")
        lines.append("one payload per answer signature, decoded from the GUI dump:")
        for name in sorted(collections.Counter(signature(send) for send in payloads)):
            lines.append("  " + name)
            for text in decoded.get(name, ["      (not in the dump)"]):
                lines.append("  " + text)

    pairs = back_to_back(frames)
    lines.append("")
    lines.append("deliveries closer than %.1f ms to the one before: %d of %d"
                 % (BACK_TO_BACK_S * 1000, len(pairs), len(frames)))
    requests_in_pairs = [pair for pair in pairs if pair[0].is_request or pair[1].is_request]
    if requests_in_pairs:
        lines.append("  of them with a request in the pair: %d (each a request read twice or not at all)"
                     % len(requests_in_pairs))
        for previous, frame in requests_in_pairs[:12]:
            lines.append("    t=%.4f %s  then t=%.4f %s"
                         % (previous.time, " ".join("%04x" % w for w in previous.words),
                            frame.time, " ".join("%04x" % w for w in frame.words)))

    if timeline:
        lines.append("")
        lines.append("timeline of the requests:")
        for window in windows:
            answers = ", ".join("%s @+%.1f ms" % (signature(send), (send.time - window.request.time) * 1000)
                                for send in window.answers) or "--"
            lines.append("  t=%9.4f  type %2d cursor %2d  %04x %04x %04x  status x%d  repeats %d  answers %s"
                         % (window.request.time, *window.request.key, window.status_records,
                            window.repeats, answers))
    return lines


def decode_examples(dump: bytes) -> dict[str, list[str]]:
    """The first payload of every (length, command) in a BFIN_MAIN_LINK_DUMP, described."""

    from tools.cdj_gui.decode_link_dump import describe_payload, iter_records, words_of

    examples: dict[str, list[str]] = {}
    for body in iter_records(dump):
        if len(body) == STATUS_LENGTH or len(body) < 2:
            continue
        words = words_of(body)
        name = "%dB/0x%04x" % (len(body), words[0])
        if name not in examples:
            examples[name] = describe_payload(body, 0)
    return examples


def iter_lines(path: Path) -> Iterator[str]:
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        yield from stream


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log", type=Path, help="MAIN's -D log (boot_vm --main-output)")
    parser.add_argument("--dump", type=Path, metavar="FILE",
                        help="the GUI's BFIN_MAIN_LINK_DUMP of the same run: decode one payload per answer")
    parser.add_argument("--timeline", action="store_true", help="also list every request with its answers")
    args = parser.parse_args()

    frames, sends = parse(iter_lines(args.log))
    decoded = decode_examples(args.dump.read_bytes()) if args.dump else None
    print("\n".join(report(frames, sends, decoded=decoded, timeline=args.timeline)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
