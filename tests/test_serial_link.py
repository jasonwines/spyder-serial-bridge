"""Serial links over real pseudo-terminals, driven with pyserial like a real port."""

import asyncio
import contextlib
import os
import tty

import serial

from spyder_bridge.bridge import Bridge
from spyder_bridge.fake_spyder import start_fake_spyder
from spyder_bridge.serial_link import PtyLink, PySerialLink
from spyder_bridge.udp_client import SpyderUdpClient


def read_until_cr(port, timeout=2.0):
    port.timeout = timeout
    return port.read_until(b"\r")


def test_pty_link_roundtrip():
    async def go():
        link = PtyLink()
        client = serial.Serial(link.device, 9600, timeout=1)
        try:
            client.write(b"RSC 1\r")
            assert await asyncio.wait_for(link.read(), 2) == b"RSC 1\r"
            await link.write(b"0\r")
            assert await asyncio.to_thread(read_until_cr, client) == b"0\r"
        finally:
            client.close()
            link.close()

    asyncio.run(go())


def test_pyserial_link_roundtrip():
    # Put PySerialLink on the slave end of a pty and drive the master directly.
    async def go():
        master, slave = os.openpty()
        tty.setraw(slave)
        device = os.ttyname(slave)
        os.close(slave)
        link = PySerialLink(serial.Serial(device, 9600))
        try:
            os.write(master, b"RSC 1\r")
            assert await asyncio.wait_for(link.read(), 2) == b"RSC 1\r"
            await link.write(b"0\r")
            assert os.read(master, 16) == b"0\r"
        finally:
            link.close()
            os.close(master)

    asyncio.run(go())


def test_bridge_through_virtual_serial_port():
    async def go():
        transport, _ = await start_fake_spyder(responder=lambda c: b"0 " + c)
        port = transport.get_extra_info("sockname")[1]
        link = PtyLink()
        terminal = serial.Serial(link.device, 9600)
        try:
            async with SpyderUdpClient("127.0.0.1", port) as client:
                bridge_task = asyncio.create_task(Bridge(link, client).run())
                terminal.write(b"RSC 1\rRSC 2\r")
                got = [await asyncio.to_thread(read_until_cr, terminal) for _ in range(2)]
                assert got == [b"0 RSC 1\r", b"0 RSC 2\r"]
                bridge_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await bridge_task
        finally:
            terminal.close()
            link.close()
            transport.close()

    asyncio.run(asyncio.wait_for(go(), 10))
