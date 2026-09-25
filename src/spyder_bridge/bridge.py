"""The bridge: serial commands in, Spyder responses back out, one at a time.

Two tasks share a bounded queue:

- the reader frames serial bytes into commands and enqueues them;
- the worker takes one command, sends it over UDP, and waits for the reply
  (or times out) before taking the next.

The worker's wait is the ``AWAITING_RESPONSE`` state; waiting on an empty
queue is ``IDLE``. Stale UDP datagrams that arrive while idle are discarded
by SpyderUdpClient.
"""

from __future__ import annotations

import asyncio
import enum
import logging
from typing import Protocol

from .framing import CommandFramer
from .protocol import error_response, to_serial
from .udp_client import SpyderTimeout, SpyderUdpClient

log = logging.getLogger(__name__)

DEFAULT_QUEUE_DEPTH = 16


class SerialLink(Protocol):
    """What the bridge needs from a serial port."""

    async def read(self) -> bytes:
        """Return the next chunk of received bytes, or b"" when the port closes."""

    async def write(self, data: bytes) -> None: ...


class State(enum.Enum):
    IDLE = "idle"
    AWAITING_RESPONSE = "awaiting_response"


class Bridge:
    def __init__(
        self,
        serial: SerialLink,
        spyder: SpyderUdpClient,
        queue_depth: int = DEFAULT_QUEUE_DEPTH,
    ) -> None:
        self.serial = serial
        self.spyder = spyder
        self.state = State.IDLE
        self._framer = CommandFramer()
        # None is the end-of-input sentinel.
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=queue_depth)

    async def run(self) -> None:
        """Run until the serial link closes and queued commands are drained."""
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._read_serial())
            tg.create_task(self._process_commands())

    async def _read_serial(self) -> None:
        while data := await self.serial.read():
            for command in self._framer.feed(data):
                try:
                    self._queue.put_nowait(command)
                except asyncio.QueueFull:
                    # No reply is sent: an out-of-order error would be matched
                    # to the wrong command by a driver that pipelines. The
                    # driver's own timeout covers this.
                    log.error(
                        "queue full (%d waiting), dropping command %r",
                        self._queue.maxsize, command,
                    )
        await self._queue.put(None)

    async def _process_commands(self) -> None:
        while (command := await self._queue.get()) is not None:
            self.state = State.AWAITING_RESPONSE
            try:
                response = to_serial(await self.spyder.request(command))
            except SpyderTimeout as e:
                log.error("%s; replying with execution error", e)
                response = error_response()
            finally:
                self.state = State.IDLE
            await self.serial.write(response)
