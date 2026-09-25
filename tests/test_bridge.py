"""Bridge end to end: fake serial <-> bridge <-> real loopback UDP <-> fake Spyder."""

import asyncio

from spyder_bridge.bridge import Bridge, State
from spyder_bridge.fake_serial import FakeSerial
from spyder_bridge.fake_spyder import start_fake_spyder
from spyder_bridge.protocol import HEADER
from spyder_bridge.udp_client import SpyderUdpClient


def echo(command):
    return b"0 " + command


def run(test, timeout=0.5, queue_depth=16, **fake_kwargs):
    """Start fake Spyder + bridge, run ``test(serial, fake, bridge)``, tear down."""

    async def go():
        transport, fake = await start_fake_spyder(**fake_kwargs)
        port = transport.get_extra_info("sockname")[1]
        serial = FakeSerial()
        async with SpyderUdpClient("127.0.0.1", port, timeout=timeout) as client:
            bridge = Bridge(serial, client, queue_depth=queue_depth)
            bridge_task = asyncio.create_task(bridge.run())
            try:
                await test(serial, fake, bridge)
            finally:
                serial.close()
                await asyncio.wait_for(bridge_task, 5)
                transport.close()

    asyncio.run(asyncio.wait_for(go(), 10))


def test_command_relayed_and_response_returned_with_cr():
    async def t(serial, fake, bridge):
        serial.feed(b"RSC 1\r")
        assert await serial.read_written() == b"0 RSC 1\r"
        assert fake.received == [HEADER + b"RSC 1"]

    run(t, responder=echo)


def test_response_already_ending_in_cr_is_not_doubled():
    async def t(serial, fake, bridge):
        serial.feed(b"RSC 1\r")
        assert await serial.read_written() == b"0\r"

    run(t, responder=lambda c: b"0\r")


def test_timeout_synthesizes_execution_error_then_recovers():
    replies = iter([None, b"0 ok"])

    async def t(serial, fake, bridge):
        serial.feed(b"SLOW\r")
        assert await serial.read_written() == b"5\r"
        serial.feed(b"FAST\r")
        assert await serial.read_written() == b"0 ok\r"

    run(t, timeout=0.05, responder=lambda c: next(replies))


def test_burst_is_processed_in_order_one_at_a_time():
    in_flight = 0
    max_in_flight = 0

    async def t(serial, fake, bridge):
        nonlocal in_flight, max_in_flight
        original = bridge.spyder.request

        async def counting_request(cmd):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            try:
                return await original(cmd)
            finally:
                in_flight -= 1

        bridge.spyder.request = counting_request
        serial.feed(b"A\rB\r")
        serial.feed(b"C\rD\r")
        got = [await serial.read_written() for _ in range(4)]
        assert got == [b"0 A\r", b"0 B\r", b"0 C\r", b"0 D\r"]
        assert max_in_flight == 1

    run(t, responder=echo, delay=0.01)


def test_state_reflects_in_flight_request():
    async def t(serial, fake, bridge):
        assert bridge.state is State.IDLE
        serial.feed(b"RSC 1\r")
        await asyncio.sleep(0.02)
        assert bridge.state is State.AWAITING_RESPONSE
        await serial.read_written()
        assert bridge.state is State.IDLE

    run(t, responder=echo, delay=0.1)


def test_queue_overflow_drops_newest_commands():
    async def t(serial, fake, bridge):
        # All five are framed in one read before the worker runs, so A and B
        # fill the 2-deep queue and C, D, E are dropped.
        serial.feed(b"A\rB\rC\rD\rE\r")
        assert await serial.read_written() == b"0 A\r"
        assert await serial.read_written() == b"0 B\r"
        await asyncio.sleep(0.1)
        assert serial.written_so_far() == []
        assert len(fake.received) == 2

    run(t, queue_depth=2, responder=echo, delay=0.01)


def test_queued_commands_drain_after_serial_closes():
    written = []

    async def go():
        transport, fake = await start_fake_spyder(responder=echo, delay=0.01)
        port = transport.get_extra_info("sockname")[1]
        serial = FakeSerial()
        async with SpyderUdpClient("127.0.0.1", port) as client:
            serial.feed(b"A\rB\r")
            serial.close()  # run() must still finish A and B before returning
            await asyncio.wait_for(Bridge(serial, client).run(), 5)
        transport.close()
        written.extend(serial.written_so_far())

    asyncio.run(go())
    assert written == [b"0 A\r", b"0 B\r"]
