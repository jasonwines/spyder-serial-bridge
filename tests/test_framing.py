from spyder_bridge.framing import CommandFramer


def test_single_command():
    assert CommandFramer().feed(b"RSC 1\r") == [b"RSC 1"]


def test_command_split_across_reads():
    f = CommandFramer()
    assert f.feed(b"RS") == []
    assert f.feed(b"C 1") == []
    assert f.feed(b"\r") == [b"RSC 1"]


def test_several_commands_in_one_read():
    f = CommandFramer()
    assert f.feed(b"A 1\rB 2\rC") == [b"A 1", b"B 2"]
    assert f.feed(b" 3\r") == [b"C 3"]


def test_crlf_and_blank_lines():
    assert CommandFramer().feed(b"A 1\r\nB 2\r\n\r\r") == [b"A 1", b"B 2"]


def test_oversized_line_is_discarded_through_its_cr():
    f = CommandFramer(max_len=8)
    assert f.feed(b"x" * 20) == []
    assert f.feed(b"more junk\rRSC 1\r") == [b"RSC 1"]


def test_oversized_line_does_not_eat_earlier_commands():
    f = CommandFramer(max_len=8)
    assert f.feed(b"OK 1\r" + b"x" * 20) == [b"OK 1"]
    assert f.feed(b"\rOK 2\r") == [b"OK 2"]
