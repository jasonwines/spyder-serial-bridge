"""Client <-> fake Spyder over real loopback UDP."""

import asyncio
import socket

import pytest

from spyder_bridge.config import Config
from spyder_bridge.fake_spyder import start_fake_spyder
from spyder_bridge.protocol import HEADER
from spyder_bridge.udp_client import SpyderTimeout, SpyderUdpClient


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 5))


async def _pair(timeout=0.5, append_cr=False, **fake_kwargs):
    transport, fake = await start_fake_spyder(**fake_kwargs)
    port = transport.get_extra_info("sockname")[1]
    client = SpyderUdpClient("127.0.0.1", port, timeout=timeout, append_cr=append_cr)
    await client.open()
    return transport, fake, client


def test_request_gets_response_and_sends_exact_payload():
    async def go():
        transport, fake, client = await _pair()
        try:
            assert await client.request(b"RSC 1\r") == b"0"
            assert fake.received == [HEADER + b"RSC 1"]
        finally:
            client.close()
            transport.close()

    run(go())


def test_append_cr_reaches_the_wire():
    async def go():
        transport, fake, client = await _pair(append_cr=True)
        try:
            await client.request(b"RSC 1")
            assert fake.received == [HEADER + b"RSC 1\r"]
        finally:
            client.close()
            transport.close()

    run(go())


def test_timeout_when_spyder_silent():
    async def go():
        transport, _, client = await _pair(timeout=0.05, responder=lambda c: None)
        try:
            with pytest.raises(SpyderTimeout):
                await client.request(b"RSC 1")
        finally:
            client.close()
            transport.close()

    run(go())


def test_late_reply_after_timeout_is_discarded():
    async def go():
        replies = iter([b"0 late", b"0 fresh"])
        delays = iter([0.15, 0.0])
        transport, fake, client = await _pair(timeout=0.05)
        fake.responder = lambda c: next(replies)
        try:
            fake.delay = next(delays)
            with pytest.raises(SpyderTimeout):
                await client.request(b"FIRST")
            await asyncio.sleep(0.2)  # late reply lands while idle
            fake.delay = next(delays)
            assert await client.request(b"SECOND") == b"0 fresh"
        finally:
            client.close()
            transport.close()

    run(go())


def test_unsolicited_datagram_while_idle_is_discarded():
    async def go():
        transport, _, client = await _pair(responder=lambda c: b"0 real")
        try:
            # Spoof a datagram from the Spyder's address to the client while idle.
            transport.sendto(b"0 bogus", client.local_address[:2])
            await asyncio.sleep(0.05)
            assert await client.request(b"RSC 1") == b"0 real"
        finally:
            client.close()
            transport.close()

    run(go())


def test_datagram_from_other_host_is_ignored():
    async def go():
        client = SpyderUdpClient("10.0.0.5", timeout=1)
        loop = asyncio.get_running_loop()
        client._pending = loop.create_future()
        client._on_datagram(b"0 imposter", ("10.0.0.6", 11116))
        assert not client._pending.done()
        client._on_datagram(b"0 real", ("10.0.0.5", 40000))  # any source port
        assert client._pending.result() == b"0 real"

    run(go())


def test_concurrent_requests_are_serialised():
    async def go():
        transport, fake, client = await _pair(responder=lambda c: b"0 " + c, delay=0.02)
        try:
            results = await asyncio.gather(
                *(client.request(f"CMD {i}".encode()) for i in range(5))
            )
            assert results == [f"0 CMD {i}".encode() for i in range(5)]
            assert fake.received == [HEADER + f"CMD {i}".encode() for i in range(5)]
        finally:
            client.close()
            transport.close()

    run(go())


def test_from_config():
    cfg = Config(spyder_ip="10.1.2.3", response_timeout_ms=750, udp_append_cr=True)
    client = SpyderUdpClient.from_config(cfg)
    assert client._addr == ("10.1.2.3", 11116)
    assert client.timeout == 0.75
    assert client.append_cr is True


def test_fake_rejects_bad_header():
    async def go():
        transport, fake = await start_fake_spyder()
        port = transport.get_extra_info("sockname")[1]
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        try:
            sock.sendto(b"RSC 1", ("127.0.0.1", port))
            data = await asyncio.get_running_loop().sock_recv(sock, 1024)
            assert data == b"2"
        finally:
            sock.close()
            transport.close()

    run(go())
