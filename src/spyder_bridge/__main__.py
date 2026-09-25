"""Run the bridge: ``python -m spyder_bridge``.

With ``--virtual-serial`` it creates a pseudo-terminal instead of opening
the configured port and prints its device path, so the whole bridge can be
driven from a terminal on the dev machine without any adapters.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import serial

from .bridge import Bridge
from .config import Config, ConfigError, load_config, resolve_config_path
from .serial_link import PtyLink, PySerialLink
from .udp_client import SpyderUdpClient

log = logging.getLogger("spyder_bridge")


async def _run(cfg: Config, virtual_serial: bool) -> int:
    if virtual_serial:
        link: PtyLink | PySerialLink = PtyLink()
        log.warning("virtual serial port ready: %s", link.device)
    else:
        try:
            link = PySerialLink.open(cfg)
        except serial.SerialException as e:
            log.error("cannot open serial port %s: %s", cfg.serial_port, e)
            return 1
        log.info(
            "serial %s at %d %d%s%d",
            cfg.serial_port, cfg.baud_rate, cfg.data_bits, cfg.parity, cfg.stop_bits,
        )
    log.info(
        "Spyder at %s:%d, timeout %dms, append CR: %s",
        cfg.spyder_ip, cfg.spyder_port, cfg.response_timeout_ms, cfg.udp_append_cr,
    )
    try:
        async with SpyderUdpClient.from_config(cfg) as client:
            await Bridge(link, client).run()
    finally:
        link.close()
    log.error("serial port closed; exiting")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spyder_bridge", description=__doc__.splitlines()[0])
    parser.add_argument("--config", help="config file (default: standard location)")
    parser.add_argument(
        "--virtual-serial", action="store_true",
        help="use a pseudo-terminal instead of serial_port (for testing)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        log.error("config error: %s", e)
        return 2
    log.info("config: %s", resolve_config_path(args.config))

    try:
        return asyncio.run(_run(cfg, args.virtual_serial))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
