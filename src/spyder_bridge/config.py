"""Bridge configuration: loading, validation, and atomic persistence.

The config lives in a small dedicated YAML file (default
``/etc/spyder-bridge/config.yaml``) kept apart from the app and OS so it can
later sit on the only writable partition of a read-only-root system.

A missing file is not an error: first boot runs on defaults until a tech
saves settings through the web GUI. A file that exists but is malformed *is*
an error -- silently falling back to defaults would hide a typo'd IP or baud
rate behind a bridge that "works" but talks to the wrong thing.
"""

from __future__ import annotations

import ipaddress
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Mapping

import yaml

DEFAULT_CONFIG_PATH = Path("/etc/spyder-bridge/config.yaml")
CONFIG_PATH_ENV = "SPYDER_BRIDGE_CONFIG"

# Standard rates only, so the web GUI can offer a dropdown and a typo like
# 96000 is caught at load time instead of producing line noise.
VALID_BAUD_RATES = (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)
VALID_DATA_BITS = (7, 8)
VALID_PARITIES = ("N", "E", "O")
VALID_STOP_BITS = (1, 2)

# Lower bound keeps a slow Spyder from being declared dead mid-reply; upper
# bound keeps a hung transaction from stalling the control system for long.
MIN_TIMEOUT_MS = 50
MAX_TIMEOUT_MS = 10_000


class ConfigError(ValueError):
    """Raised when a config file or value is invalid."""


@dataclass(frozen=True)
class Config:
    serial_port: str = "/dev/ttyUSB0"
    baud_rate: int = 9600
    data_bits: int = 8
    parity: str = "N"
    stop_bits: int = 1
    spyder_ip: str = "192.168.0.100"
    spyder_port: int = 11116
    response_timeout_ms: int = 500

    def __post_init__(self) -> None:
        _require_str(self, "serial_port")
        if not self.serial_port.strip():
            raise ConfigError("serial_port must not be empty")

        _require_choice(self, "baud_rate", VALID_BAUD_RATES)
        _require_choice(self, "data_bits", VALID_DATA_BITS)
        _require_str(self, "parity")
        # Normalise so "n" in a hand-edited file is accepted.
        object.__setattr__(self, "parity", self.parity.upper())
        _require_choice(self, "parity", VALID_PARITIES)
        _require_choice(self, "stop_bits", VALID_STOP_BITS)

        _require_str(self, "spyder_ip")
        try:
            ipaddress.ip_address(self.spyder_ip)
        except ValueError:
            raise ConfigError(
                f"spyder_ip must be an IP address, got {self.spyder_ip!r}"
            ) from None

        _require_int(self, "spyder_port")
        if not 1 <= self.spyder_port <= 65535:
            raise ConfigError(
                f"spyder_port must be 1-65535, got {self.spyder_port}"
            )

        _require_int(self, "response_timeout_ms")
        if not MIN_TIMEOUT_MS <= self.response_timeout_ms <= MAX_TIMEOUT_MS:
            raise ConfigError(
                f"response_timeout_ms must be {MIN_TIMEOUT_MS}-{MAX_TIMEOUT_MS}, "
                f"got {self.response_timeout_ms}"
            )

    @property
    def response_timeout_s(self) -> float:
        return self.response_timeout_ms / 1000

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Config:
        """Build a Config from a mapping, rejecting unknown keys.

        Keys absent from ``data`` take their defaults, so a file only needs
        to list what differs from stock.
        """
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ConfigError(
                f"unknown config key(s): {', '.join(map(str, unknown))} "
                f"(valid keys: {', '.join(sorted(known))})"
            )
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_config_path(path: str | os.PathLike[str] | None = None) -> Path:
    """Explicit path, else ``$SPYDER_BRIDGE_CONFIG``, else the default."""
    if path is not None:
        return Path(path)
    env = os.environ.get(CONFIG_PATH_ENV)
    if env:
        return Path(env)
    return DEFAULT_CONFIG_PATH


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load and validate the config, returning defaults if the file is absent."""
    path = resolve_config_path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Config()
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from e

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML: {e}") from e

    if data is None:  # empty file
        return Config()
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a YAML mapping of key: value")

    try:
        return Config.from_mapping(data)
    except ConfigError as e:
        raise ConfigError(f"{path}: {e}") from e


def save_config(config: Config, path: str | os.PathLike[str] | None = None) -> Path:
    """Atomically write ``config`` to disk and return the path written.

    Writes to a temp file in the same directory, fsyncs it, then renames it
    over the target, so a power cut mid-save leaves either the old file or
    the new one -- never a truncated mix.
    """
    path = resolve_config_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(config.to_dict(), sort_keys=False)

    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise

    # Persist the rename itself, not just the file contents.
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return path


def _require_str(cfg: Config, name: str) -> None:
    value = getattr(cfg, name)
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a string, got {value!r}")


def _require_int(cfg: Config, name: str) -> None:
    value = getattr(cfg, name)
    # bool is an int subclass; "yes" in YAML must not become baud 1.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{name} must be an integer, got {value!r}")


def _require_choice(cfg: Config, name: str, choices: tuple[Any, ...]) -> None:
    value = getattr(cfg, name)
    if isinstance(value, bool) or value not in choices:
        raise ConfigError(
            f"{name} must be one of {', '.join(map(str, choices))}, got {value!r}"
        )
