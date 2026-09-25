"""Spyder UDP wire format.

Outgoing commands are ``HEADER + command`` with no separator. Responses come
back unframed; their first whitespace-separated token is a result code.
"""

from __future__ import annotations

from enum import IntEnum

HEADER = b"spyder\x00\x00\x00\x00"
CR = b"\r"
DEFAULT_PORT = 11116


class ResultCode(IntEnum):
    SUCCESS = 0
    EMPTY = 1
    INVALID_HEADER = 2
    MISSING_ARGUMENTS = 3
    INVALID_ARGUMENT = 4
    EXECUTION_ERROR = 5


def frame_command(command: bytes, append_cr: bool = False) -> bytes:
    """Build the UDP payload for one command.

    Any trailing CR/LF from the serial side is stripped first, so the only
    thing deciding whether a CR goes on the wire is ``append_cr``.
    """
    command = command.rstrip(b"\r\n")
    if not command:
        raise ValueError("empty command")
    return HEADER + command + (CR if append_cr else b"")


def unframe_command(datagram: bytes) -> bytes | None:
    """Inverse of frame_command: the bare command, or None if the header is wrong."""
    if not datagram.startswith(HEADER):
        return None
    return datagram[len(HEADER):].rstrip(b"\r\n")


def result_code(response: bytes) -> int | None:
    """The leading result code of a response, or None if it has none."""
    parts = response.split(None, 1)
    if not parts:
        return None
    try:
        return int(parts[0])
    except ValueError:
        return None


def to_serial(response: bytes) -> bytes:
    """Normalise a Spyder response for the serial side: exactly one trailing CR.

    Also drops trailing LF/NUL, which a CR-framed serial driver would
    otherwise see as the start of its next response.
    """
    return response.rstrip(b"\r\n\x00") + CR


def error_response(code: ResultCode = ResultCode.EXECUTION_ERROR) -> bytes:
    """A synthesized serial response carrying only a result code."""
    return str(int(code)).encode("ascii") + CR
