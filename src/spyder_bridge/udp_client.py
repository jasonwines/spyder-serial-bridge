"""Single-flight UDP request/response client for the Spyder.

The protocol has no transaction ID, so at most one request is ever in
flight. A datagram that arrives when nothing is pending (e.g. a reply that
showed up after its request timed out) is stale and gets discarded.

Also runnable for bench spot-checks against a real or fake Spyder::

    python -m spyder_bridge.udp_client "RSC 1"
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import socket
import sys

from .config import Config, ConfigError, load_config
from .protocol import DEFAULT_PORT, frame_command

log = logging.getLogger(__name__)


class SpyderTimeout(TimeoutError):
    """No response from the Spyder within the timeout."""


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, client: SpyderUdpClient) -> None:
        self._client = client

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self._client._on_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        # e.g. ICMP port unreachable. The pending request will time out and
        # be reported then; failing it here would just duplicate that path.
        log.warning("UDP error: %s", exc)


class SpyderUdpClient:
    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        timeout: float = 0.5,
        append_cr: bool = False,
    ) -> None:
        self._ip = ipaddress.ip_address(host)
        self._addr = (host, port)
        self.timeout = timeout
        self.append_cr = append_cr
        self._transport: asyncio.DatagramTransport | None = None
        self._pending: asyncio.Future[bytes] | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_config(cls, cfg: Config) -> SpyderUdpClient:
        return cls(
            cfg.spyder_ip,
            cfg.spyder_port,
            timeout=cfg.response_timeout_s,
            append_cr=cfg.udp_append_cr,
        )

    @property
    def local_address(self) -> tuple:
        assert self._transport is not None, "client not open"
        return self._transport.get_extra_info("sockname")

    async def open(self) -> None:
        family = socket.AF_INET6 if self._ip.version == 6 else socket.AF_INET
        # Unconnected socket bound to an ephemeral port: replies are filtered
        # by source IP only, in case the Spyder answers from another port.
        wildcard = "::" if family == socket.AF_INET6 else "0.0.0.0"
        self._transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: _Protocol(self), local_addr=(wildcard, 0), family=family
        )

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    async def __aenter__(self) -> SpyderUdpClient:
        await self.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.close()

    async def request(self, command: bytes) -> bytes:
        """Send one command and return the raw response datagram.

        Concurrent callers are serialised. Raises SpyderTimeout if no reply
        arrives in time.
        """
        payload = frame_command(command, self.append_cr)
        async with self._lock:
            if self._transport is None:
                raise RuntimeError("client not open")
            self._pending = asyncio.get_running_loop().create_future()
            try:
                self._transport.sendto(payload, self._addr)
                return await asyncio.wait_for(self._pending, self.timeout)
            except asyncio.TimeoutError:
                raise SpyderTimeout(
                    f"no response to {command!r} within {self.timeout:.3f}s"
                ) from None
            finally:
                self._pending = None

    def _on_datagram(self, data: bytes, addr: tuple) -> None:
        try:
            # Strip any IPv6 zone ("fe80::1%eth0") before comparing.
            src = ipaddress.ip_address(addr[0].split("%", 1)[0])
        except ValueError:
            src = None
        if src != self._ip:
            log.warning("discarding datagram from unexpected host %s: %r", addr[0], data)
            return
        if self._pending is None or self._pending.done():
            log.warning("discarding stale datagram (no request pending): %r", data)
            return
        self._pending.set_result(data)


async def _send_once(cfg: Config, command: bytes) -> bytes:
    async with SpyderUdpClient.from_config(cfg) as client:
        return await client.request(command)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send one command to a Spyder over UDP.")
    parser.add_argument("command", nargs="+", help="command text, e.g. RSC 1")
    parser.add_argument("--config", help="config file (default: standard location)")
    parser.add_argument("--host", help="override spyder_ip")
    parser.add_argument("--port", type=int, help="override spyder_port")
    parser.add_argument("--timeout-ms", type=int, help="override response_timeout_ms")
    parser.add_argument("--append-cr", action="store_true", help="append CR to the UDP payload")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    overrides = {
        k: v
        for k, v in {
            "spyder_ip": args.host,
            "spyder_port": args.port,
            "response_timeout_ms": args.timeout_ms,
            "udp_append_cr": True if args.append_cr else None,
        }.items()
        if v is not None
    }
    try:
        cfg = Config.from_mapping({**load_config(args.config).to_dict(), **overrides})
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    command = " ".join(args.command).encode("ascii")
    try:
        response = asyncio.run(_send_once(cfg, command))
    except SpyderTimeout as e:
        print(f"timeout: {e}", file=sys.stderr)
        return 1
    print(repr(response))
    return 0


if __name__ == "__main__":
    sys.exit(main())
