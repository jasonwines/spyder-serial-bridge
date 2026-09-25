"""Serial links for the bridge: a real port (pyserial) and a virtual one (pty).

Both read through the asyncio event loop's fd watcher rather than a thread,
so they work the same on the Pi and on a dev Mac. Both are POSIX-only.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import tty

import serial

from .config import Config

log = logging.getLogger(__name__)

_READ_SIZE = 1024


async def _wait_readable(fd: int) -> None:
    loop = asyncio.get_running_loop()
    ready = loop.create_future()
    loop.add_reader(fd, lambda: ready.done() or ready.set_result(None))
    try:
        await ready
    finally:
        loop.remove_reader(fd)


class PySerialLink:
    """A physical serial port, e.g. the FTDI USB-RS232 adapter."""

    def __init__(self, port: serial.Serial) -> None:
        port.timeout = 0  # non-blocking reads; readiness comes from the loop
        self._port = port
        self.device = port.port

    @classmethod
    def open(cls, cfg: Config) -> PySerialLink:
        return cls(
            serial.Serial(
                cfg.serial_port,
                cfg.baud_rate,
                bytesize=cfg.data_bits,
                parity=cfg.parity,
                stopbits=cfg.stop_bits,
                timeout=0,
                write_timeout=2,
            )
        )

    async def read(self) -> bytes:
        while True:
            try:
                data = self._port.read(self._port.in_waiting or 1)
            except (serial.SerialException, OSError) as e:
                # USB adapter unplugged, typically. Report EOF and let the
                # service manager restart us once it's back.
                log.error("serial read failed on %s: %s", self.device, e)
                return b""
            if data:
                return data
            await _wait_readable(self._port.fileno())

    async def write(self, data: bytes) -> None:
        # A short response at 9600 baud takes a few ms; don't stall the loop.
        await asyncio.to_thread(self._port.write, data)

    def close(self) -> None:
        self._port.close()


class PtyLink:
    """A virtual serial port backed by a pseudo-terminal pair.

    The bridge owns the master side; ``device`` is the slave path (e.g.
    ``/dev/ttys012`` on macOS, ``/dev/pts/3`` on Linux) that a serial
    terminal or the ``serial_console`` tool opens as if it were the cable.
    """

    def __init__(self) -> None:
        self._master, self._slave = os.openpty()
        # Raw mode: no echo (which would feed our own responses back in as
        # commands) and no CR/LF translation.
        tty.setraw(self._slave)
        os.set_blocking(self._master, False)
        # Holding the slave open keeps the pty alive between terminal
        # sessions; otherwise reads fail with EIO once the client disconnects.
        self.device = os.ttyname(self._slave)

    async def read(self) -> bytes:
        while True:
            try:
                data = os.read(self._master, _READ_SIZE)
            except BlockingIOError:
                await _wait_readable(self._master)
                continue
            except OSError as e:
                if e.errno == errno.EIO:
                    return b""
                raise
            return data

    async def write(self, data: bytes) -> None:
        os.write(self._master, data)

    def close(self) -> None:
        for fd in (self._master, self._slave):
            try:
                os.close(fd)
            except OSError:
                pass
