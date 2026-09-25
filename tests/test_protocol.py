import pytest

from spyder_bridge.protocol import (
    HEADER,
    ResultCode,
    error_response,
    frame_command,
    result_code,
    to_serial,
    unframe_command,
)


def test_header_is_spyder_plus_four_nulls():
    assert HEADER == b"spyder" + bytes(4)


def test_frame_has_no_separator_and_no_cr_by_default():
    assert frame_command(b"RSC 1") == b"spyder\x00\x00\x00\x00RSC 1"


def test_frame_strips_serial_terminator():
    assert frame_command(b"RSC 1\r") == HEADER + b"RSC 1"
    assert frame_command(b"RSC 1\r\n") == HEADER + b"RSC 1"


def test_frame_append_cr():
    assert frame_command(b"RSC 1\r", append_cr=True) == HEADER + b"RSC 1\r"


@pytest.mark.parametrize("cmd", [b"", b"\r", b"\r\n"])
def test_frame_rejects_empty(cmd):
    with pytest.raises(ValueError):
        frame_command(cmd)


def test_unframe_roundtrip():
    assert unframe_command(frame_command(b"RSC 1", append_cr=True)) == b"RSC 1"


def test_unframe_rejects_bad_header():
    assert unframe_command(b"RSC 1") is None
    assert unframe_command(b"spyder\x00RSC 1") is None


@pytest.mark.parametrize(
    "response, code",
    [(b"0", 0), (b"0 some data\r", 0), (b"5\r", 5), (b"  4 x", 4), (b"", None), (b"ok", None)],
)
def test_result_code(response, code):
    assert result_code(response) == code


@pytest.mark.parametrize(
    "response, expected",
    [(b"0", b"0\r"), (b"0\r", b"0\r"), (b"0 x\r\n", b"0 x\r"), (b"0\x00", b"0\r")],
)
def test_to_serial_ends_in_exactly_one_cr(response, expected):
    assert to_serial(response) == expected


def test_error_response_defaults_to_execution_error():
    assert error_response() == b"5\r"
    assert error_response(ResultCode.INVALID_HEADER) == b"2\r"
