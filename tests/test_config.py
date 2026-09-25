from pathlib import Path

import pytest

from spyder_bridge.config import (
    CONFIG_PATH_ENV,
    DEFAULT_CONFIG_PATH,
    Config,
    ConfigError,
    load_config,
    resolve_config_path,
    save_config,
)

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.yaml"


def test_defaults_match_brief():
    cfg = Config()
    assert cfg.baud_rate == 9600
    assert (cfg.data_bits, cfg.parity, cfg.stop_bits) == (8, "N", 1)
    assert cfg.spyder_port == 11116
    assert cfg.response_timeout_s == 0.5


def test_missing_file_returns_defaults(tmp_path):
    assert load_config(tmp_path / "nope.yaml") == Config()


def test_empty_file_returns_defaults(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("")
    assert load_config(p) == Config()


def test_partial_file_overrides_only_given_keys(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("baud_rate: 19200\nspyder_ip: 10.0.0.5\n")
    cfg = load_config(p)
    assert cfg.baud_rate == 19200
    assert cfg.spyder_ip == "10.0.0.5"
    assert cfg.serial_port == Config().serial_port


def test_example_file_loads_as_defaults():
    assert load_config(EXAMPLE) == Config()


def test_parity_is_normalised(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("parity: e\n")
    assert load_config(p).parity == "E"


def test_ipv6_accepted():
    assert Config(spyder_ip="fe80::1").spyder_ip == "fe80::1"


@pytest.mark.parametrize(
    "yaml_text, fragment",
    [
        ("baud_rate: 96000\n", "baud_rate"),
        ("baud_rate: '9600'\n", "baud_rate"),
        ("baud_rate: yes\n", "baud_rate"),
        ("data_bits: 9\n", "data_bits"),
        ("parity: X\n", "parity"),
        ("stop_bits: 3\n", "stop_bits"),
        ("spyder_ip: spyder.local\n", "spyder_ip"),
        ("spyder_ip: 192.168.0.300\n", "spyder_ip"),
        ("spyder_port: 0\n", "spyder_port"),
        ("spyder_port: 70000\n", "spyder_port"),
        ("response_timeout_ms: 10\n", "response_timeout_ms"),
        ("response_timeout_ms: 0.5\n", "response_timeout_ms"),
        ("serial_port: ''\n", "serial_port"),
        ("udp_append_cr: 1\n", "udp_append_cr"),
        ("baud: 9600\n", "unknown config key"),
        ("- 9600\n", "YAML mapping"),
        ("baud_rate: [\n", "not valid YAML"),
    ],
)
def test_invalid_files_raise_with_useful_message(tmp_path, yaml_text, fragment):
    p = tmp_path / "config.yaml"
    p.write_text(yaml_text)
    with pytest.raises(ConfigError, match=fragment) as exc:
        load_config(p)
    assert str(p) in str(exc.value)


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "sub" / "config.yaml"
    cfg = Config(baud_rate=115200, parity="O", spyder_ip="10.1.2.3",
                 response_timeout_ms=1500)
    assert save_config(cfg, p) == p
    assert load_config(p) == cfg


def test_save_replaces_existing_and_leaves_no_temp_files(tmp_path):
    p = tmp_path / "config.yaml"
    save_config(Config(baud_rate=4800), p)
    save_config(Config(baud_rate=38400), p)
    assert load_config(p).baud_rate == 38400
    assert [f.name for f in tmp_path.iterdir()] == ["config.yaml"]


def test_failed_save_keeps_old_file_and_cleans_up(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    save_config(Config(baud_rate=4800), p)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("spyder_bridge.config.os.replace", boom)
    with pytest.raises(OSError, match="disk full"):
        save_config(Config(baud_rate=38400), p)
    assert load_config(p).baud_rate == 4800
    assert [f.name for f in tmp_path.iterdir()] == ["config.yaml"]


def test_path_resolution(monkeypatch, tmp_path):
    monkeypatch.delenv(CONFIG_PATH_ENV, raising=False)
    assert resolve_config_path() == DEFAULT_CONFIG_PATH
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "env.yaml"))
    assert resolve_config_path() == tmp_path / "env.yaml"
    assert resolve_config_path(tmp_path / "x.yaml") == tmp_path / "x.yaml"
