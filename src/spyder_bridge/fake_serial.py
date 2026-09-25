"""In-memory stand-in for a serial port, for testing the bridge without hardware."""

from __future__ import annotations

import asyncio


class FakeSerial:
    """Plays the control system's side of the cable.

    ``feed()`` is what the control system sends; ``read_written()`` returns
    what the bridge sent back.
    """

    def __init__(self) -> None:
        self._inbound: asyncio.Queue[bytes] = asyncio.Queue()
        self._outbound: asyncio.Queue[bytes] = asyncio.Queue()

    def feed(self, data: bytes) -> None:
        self._inbound.put_nowait(data)

    def close(self) -> None:
        """Simulate the port going away; the bridge's read() then returns b""."""
        self._inbound.put_nowait(b"")

    async def read_written(self, timeout: float = 1.0) -> bytes:
        return await asyncio.wait_for(self._outbound.get(), timeout)

    def written_so_far(self) -> list[bytes]:
        out = []
        while not self._outbound.empty():
            out.append(self._outbound.get_nowait())
        return out

    # SerialLink interface, used by the bridge.

    async def read(self) -> bytes:
        return await self._inbound.get()

    async def write(self, data: bytes) -> None:
        self._outbound.put_nowait(data)
