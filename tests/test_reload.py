"""Bridge picks up config changes saved by the GUI."""

import asyncio

import serial

from spyder_bridge.__main__ import supervise, watch_config
from spyder_bridge.config import Config, save_config
from spyder_bridge.fake_spyder import start_fake_spyder

POLL = 0.02


def test_watch_returns_new_config(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Config(), path)

    async def go():
        watcher = asyncio.create_task(watch_config(path, Config(), poll=POLL))
        await asyncio.sleep(POLL * 3)
        save_config(Config(baud_rate=19200), path)
        return await asyncio.wait_for(watcher, 2)

    assert asyncio.run(go()).baud_rate == 19200


def test_watch_ignores_invalid_and_identical_saves(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Config(), path)

    async def go():
        watcher = asyncio.create_task(watch_config(path, Config(), poll=POLL))
        path.write_text("baud_rate: 96000\n")
        await asyncio.sleep(POLL * 5)
        save_config(Config(), path)  # rewritten but unchanged
        await asyncio.sleep(POLL * 5)
        assert not watcher.done()
        save_config(Config(spyder_ip="10.0.0.9"), path)
        return await asyncio.wait_for(watcher, 2)

    assert asyncio.run(go()).spyder_ip == "10.0.0.9"


def test_supervisor_restarts_bridge_on_config_change(tmp_path, monkeypatch):
    monkeypatch.setattr("spyder_bridge.__main__.RELOAD_POLL_S", POLL)
    path = tmp_path / "config.yaml"

    async def go():
        t1, _ = await start_fake_spyder(responder=lambda c: b"0 first")
        t2, _ = await start_fake_spyder(responder=lambda c: b"0 second")
        port1 = t1.get_extra_info("sockname")[1]
        port2 = t2.get_extra_info("sockname")[1]
        cfg = Config(spyder_ip="127.0.0.1", spyder_port=port1)
        save_config(cfg, path)

        # Grab the virtual port's device path from the log line.
        devices = []
        monkeypatch.setattr(
            "spyder_bridge.__main__.log.warning",
            lambda msg, *a: devices.append(a[0]) if "virtual" in msg else None,
        )
        sup = asyncio.create_task(supervise(path, cfg, virtual_serial=True))
        await asyncio.sleep(0.1)
        term = serial.Serial(devices[0], 9600, timeout=2)
        try:
            term.write(b"RSC 1\r")
            assert await asyncio.to_thread(term.read_until, b"\r") == b"0 first\r"

            save_config(Config(spyder_ip="127.0.0.1", spyder_port=port2), path)
            await asyncio.sleep(POLL * 10)

            term.write(b"RSC 1\r")
            assert await asyncio.to_thread(term.read_until, b"\r") == b"0 second\r"
        finally:
            term.close()
            sup.cancel()
            await asyncio.gather(sup, return_exceptions=True)
            t1.close()
            t2.close()

    asyncio.run(asyncio.wait_for(go(), 10))


def test_supervisor_exits_when_serial_port_missing(tmp_path):
    path = tmp_path / "config.yaml"
    cfg = Config(serial_port=str(tmp_path / "no-such-tty"))
    assert asyncio.run(supervise(path, cfg)) == 1
