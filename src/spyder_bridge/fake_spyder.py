"""A stand-in Spyder for testing without hardware.

Listens on UDP, checks each datagram for the ``spyder`` header, and replies
with a result code: 0 for a well-framed command, 2 for a bad header. It can
delay or suppress replies to exercise timeouts.

Run it on the dev machine or the Pi::

    python -m spyder_bridge.fake_spyder --port 11116
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Callable

from .protocol import DEFAULT_PORT, ResultCode, unframe_command

log = logging.getLogger(__name__)

Responder = Callable[[bytes], "bytes | None"]


def default_responder(command: bytes) -> bytes:
    return str(int(ResultCode.SUCCESS)).encode("ascii")


class FakeSpyder(asyncio.DatagramProtocol):
    def __init__(
        self,
        responder: Responder = default_responder,
        delay: float = 0.0,
    ) -> None:
        """``responder`` maps a bare command to a reply; returning None sends nothing."""
        self.responder = responder
        self.delay = delay
        self.received: list[bytes] = []  # raw datagrams, for tests to inspect
        self._transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self.received.append(data)
        command = unframe_command(data)
        if command is None:
            log.info("%s bad header: %r", addr[0], data)
            reply: bytes | None = str(int(ResultCode.INVALID_HEADER)).encode("ascii")
        else:
            log.info("%s command: %r", addr[0], command)
            reply = self.responder(command)
        if reply is None:
            return
        loop = asyncio.get_running_loop()
        loop.call_later(self.delay, self._send, reply, addr)

    def _send(self, reply: bytes, addr: tuple) -> None:
        if self._transport is not None and not self._transport.is_closing():
            self._transport.sendto(reply, addr)


async def start_fake_spyder(
    host: str = "127.0.0.1", port: int = 0, **kwargs
) -> tuple[asyncio.DatagramTransport, FakeSpyder]:
    """Start listening; port 0 picks a free port (read it from the transport)."""
    return await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: FakeSpyder(**kwargs), local_addr=(host, port)
    )


async def _serve(host: str, port: int, delay: float, silent: bool) -> None:
    responder: Responder = (lambda _cmd: None) if silent else default_responder
    transport, _ = await start_fake_spyder(host, port, responder=responder, delay=delay)
    log.info("fake Spyder listening on %s:%d", *transport.get_extra_info("sockname")[:2])
    try:
        await asyncio.Event().wait()
    finally:
        transport.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Fake Spyder UDP listener.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--delay-ms", type=int, default=0, help="delay before replying")
    parser.add_argument("--silent", action="store_true", help="never reply (test timeouts)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        asyncio.run(_serve(args.host, args.port, args.delay_ms / 1000, args.silent))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
