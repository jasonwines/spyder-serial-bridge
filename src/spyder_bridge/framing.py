"""CR-framing for the serial side: raw bytes in, complete commands out."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Well beyond any real Spyder command. A buffer this long with no CR is
# almost certainly line noise (e.g. a baud mismatch), not a command.
MAX_COMMAND_LEN = 512


class CommandFramer:
    def __init__(self, max_len: int = MAX_COMMAND_LEN) -> None:
        self.max_len = max_len
        self._buf = bytearray()
        self._discarding = False

    def feed(self, data: bytes) -> list[bytes]:
        """Add received bytes; return any commands completed by them.

        Commands are returned without the CR and with surrounding whitespace
        (including a stray LF from CRLF senders) removed. Blank lines are
        dropped.
        """
        self._buf += data
        commands = []
        while (i := self._buf.find(b"\r")) >= 0:
            line = bytes(self._buf[:i])
            del self._buf[: i + 1]
            if self._discarding:
                # Tail end of an oversized line; its CR ends the junk.
                self._discarding = False
                continue
            line = line.strip()
            if line:
                commands.append(line)
        if len(self._buf) > self.max_len:
            log.warning(
                "discarding %d bytes with no CR (baud mismatch?): %r...",
                len(self._buf), bytes(self._buf[:32]),
            )
            self._buf.clear()
            self._discarding = True
        return commands
