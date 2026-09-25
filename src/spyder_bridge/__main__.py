"""Run the bridge: ``python -m spyder_bridge``.

With ``--virtual-serial`` it creates a pseudo-terminal instead of opening
the configured port and prints its device path, so the whole bridge can be
driven from a terminal on the dev machine without any adapters.

The bridge watches its config file and restarts itself with the new
settings whenever the web GUI saves a change.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import serial

from .bridge import Bridge
from .config import Config, ConfigError, load_config, resolve_config_path
from .logsetup import setup_logging
from .serial_link import PtyLink, PySerialLink
from .udp_client import SpyderUdpClient

log = logging.getLogger("spyder_bridge")


# How often to check the config file for changes saved by the web GUI.
RELOAD_POLL_S = 2.0


def _file_stamp(path: Path) -> tuple[int, int, int] | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    # The inode changes on every atomic save, even within one mtime tick.
    return (st.st_mtime_ns, st.st_size, st.st_ino)


async def watch_config(path: Path, current: Config, poll: float | None = None) -> Config:
    """Return once ``path`` holds a valid config that differs from ``current``.

    An invalid file is logged and ignored, so a bad edit never stops a
    working bridge.
    """
    last = _file_stamp(path)
    while True:
        await asyncio.sleep(RELOAD_POLL_S if poll is None else poll)
        stamp = _file_stamp(path)
        if stamp == last:
            continue
        last = stamp
        try:
            new = load_config(path)
        except ConfigError as e:
            log.error("ignoring invalid config change, keeping current settings: %s", e)
            continue
        if new != current:
            return new


async def _run_bridge(cfg: Config, virtual_link: PtyLink | None) -> None:
    """Run the bridge on ``cfg`` until the serial port closes."""
    if virtual_link is not None:
        link: PtyLink | PySerialLink = virtual_link
    else:
        link = PySerialLink.open(cfg)
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
        # The virtual port outlives restarts so its device path stays put.
        if link is not virtual_link:
            link.close()


async def supervise(path: Path, cfg: Config, virtual_serial: bool = False) -> int:
    """Run the bridge, restarting it whenever the config file changes.

    Returns (with a failure code, for the service manager to restart us)
    only if the serial port can't be opened or closes.
    """
    virtual_link = PtyLink() if virtual_serial else None
    if virtual_link is not None:
        log.warning("virtual serial port ready: %s", virtual_link.device)
    try:
        while True:
            bridge = asyncio.create_task(_run_bridge(cfg, virtual_link))
            watcher = asyncio.create_task(watch_config(path, cfg))
            await asyncio.wait({bridge, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if watcher.done():
                cfg = watcher.result()
                log.warning("config changed; restarting bridge with new settings")
                # A command in flight at this moment gets no reply.
                bridge.cancel()
                await asyncio.gather(bridge, return_exceptions=True)
                continue
            watcher.cancel()
            exc = bridge.exception()
            if isinstance(exc, serial.SerialException):
                log.error("cannot open serial port %s: %s", cfg.serial_port, exc)
            elif exc is not None:
                raise exc
            else:
                log.error("serial port closed; exiting")
            return 1
    finally:
        if virtual_link is not None:
            virtual_link.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spyder_bridge", description=__doc__.splitlines()[0])
    parser.add_argument("--config", help="config file (default: standard location)")
    parser.add_argument(
        "--virtual-serial", action="store_true",
        help="use a pseudo-terminal instead of serial_port (for testing)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        log.error("config error: %s", e)
        return 2
    path = resolve_config_path(args.config)
    log.info("config: %s", path)

    try:
        return asyncio.run(supervise(path, cfg, args.virtual_serial))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
