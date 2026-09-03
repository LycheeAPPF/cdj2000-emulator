"""link_exchanges pairs requests with answers from the two log line kinds."""

from tools.cdj_main import link_exchanges as le

LOG = """\
cdj2000.link-rx: delivered 48 bytes to 0xa4500000, status 0x04 words 0000 0000 0000 0000 0000 0000 queued 0 t=10.0000
cdj2000.link-tx: sent 64 bytes from 0xa4500800 (connected=1 written=72, first words 0000 0000) t=10.0025
cdj2000.link-rx: delivered 48 bytes to 0xa4500000, status 0x04 words 0000 8001 0003 0007 0001 0000 queued 0 t=10.0100
cdj2000.link-tx: sent 64 bytes from 0xa4500800 (connected=1 written=72, first words 0000 0000) t=10.0125
cdj2000.link-rx: delivered 48 bytes to 0xa4500000, status 0x04 words 0000 0001 0003 0007 0001 0000 queued 0 t=10.0300
cdj2000.link-tx: sent 896 bytes from 0xa4500800 (connected=1 written=904, first words 0013 0000) t=10.0400
cdj2000.link-rx: delivered 48 bytes to 0xa4500000, status 0x04 words 0000 0000 0000 0000 0000 0000 queued 0 t=10.0500
cdj2000.link-rx: delivered 48 bytes to 0xa4500000, status 0x04 words 0000 8009 0001 0000 0000 0000 queued 0 t=10.0501
cdj2000.link-tx: sent 64 bytes from 0xa4500800 (connected=1 written=72, first words 0000 0000) t=10.0525
cdj2000.link-rx: delivered 48 bytes to 0xa4500000, status 0x04 words 0000 8007 0001 0000 0000 0000 t=10.1000
cdj2000.link-tx: sent 64 bytes from 0xa4500800 (connected=1 written=-1, first words 0000 0000) t=10.1025
cdj2000.link-rx: delivered 48 bytes to 0xa4500000, status 0x04 words 0000 0000 0000 0000 0000 0000 queued 0 t=10.2000
"""


def test_parse_reads_both_directions():
    frames, sends = le.parse(LOG.splitlines())
    assert len(frames) == 7
    assert len(sends) == 5
    assert frames[1].is_request and frames[1].key == (1, 3, 7, 1, 0)
    assert not frames[2].is_request and not frames[2].is_poll     # the repeat
    assert frames[0].is_poll
    assert sends[2].length == 896 and sends[2].command == 0x13
    # the board's older log line without the queue count still parses
    assert frames[5].words[1] == 0x8007


def test_windows_collect_answers_repeats_and_polls():
    frames, sends = le.parse(LOG.splitlines())
    windows = le.exchanges(frames, sends)
    assert [w.request.key[:2] for w in windows] == [(1, 3), (9, 1), (7, 1)]
    enter = windows[0]
    assert [s.length for s in enter.answers] == [896]
    assert enter.status_records == 1
    assert enter.repeats == 1
    assert enter.polls == 1
    assert windows[1].answers == [] and windows[1].status_records == 1
    assert windows[2].answers == [] and windows[2].polls == 1


def test_report_names_the_unanswered_and_the_back_to_back_pair():
    frames, sends = le.parse(LOG.splitlines())
    text = "\n".join(le.report(frames, sends))
    assert "requests 3, repeats 1, status polls 3" in text
    assert "896B/0x0013 x1" in text
    assert "type 9 cursor 1 words 0000 0000 0000 (x1)" in text
    assert "type 7 cursor 1 words 0000 0000 0000 (x1)" in text
    assert "deliveries closer than 0.5 ms to the one before: 1 of 7" in text
    assert "with a request in the pair: 1" in text


def test_timeline_lists_every_request():
    frames, sends = le.parse(LOG.splitlines())
    text = "\n".join(le.report(frames, sends, timeline=True))
    assert "t=  10.0100  type  1 cursor  3" in text
    assert "896B/0x0013 @+30.0 ms" in text


def test_decode_examples_takes_one_payload_per_signature():
    import struct
    body = struct.pack("<9H", 0x13, 0, 2, 0, 0, 0, 0, 0x80, 1) + struct.pack("<3H", 0x58, 0, 2) + "NO".encode("utf-16-le")
    dump = b"SPRX" + len(body).to_bytes(4, "little") + body
    dump += b"SPRX" + (64).to_bytes(4, "little") + bytes(64)
    dump += b"SPRX" + len(body).to_bytes(4, "little") + body
    examples = le.decode_examples(dump)
    assert list(examples) == ["%dB/0x0013" % len(body)]
    assert any("'NO'" in line for line in examples["%dB/0x0013" % len(body)])
