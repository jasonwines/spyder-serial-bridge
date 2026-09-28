"""Config page password: a single salted hash in a file next to the config.

There is one password per unit and no usernames. install.sh sets it to
``DEFAULT_PASSWORD`` on first install, and the config page nags until a tech
changes it. To put a forgotten password back to the default, re-run the
installer with ``--reset-password``, or::

    sudo -u spyder-bridge PYTHONPATH=/opt/spyder-bridge/src \\
        python3 -m spyder_bridge.auth --reset
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from .config import atomic_write_text, resolve_config_path

PASSWORD_FILENAME = "password"
# Applies to passwords a tech chooses. The default is exempt: it's public
# (it's in the README) either way, so its length adds nothing, and the
# config page warns until it's changed.
MIN_PASSWORD_LEN = 8
DEFAULT_PASSWORD = "spyder"


def password_path(config_path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_config_path(config_path).parent / PASSWORD_FILENAME


def set_password(path: Path, password: str) -> None:
    """Store a password a tech chose; must be at least MIN_PASSWORD_LEN long."""
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    _store(path, password)


def reset_to_default(path: Path) -> None:
    _store(path, DEFAULT_PASSWORD)


def _store(path: Path, password: str) -> None:
    atomic_write_text(path, generate_password_hash(password) + "\n", 0o600)


def password_is_set(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def check_password(path: Path, password: str) -> bool:
    try:
        stored = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return False
    return bool(stored) and check_password_hash(stored, password)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the config page password.")
    parser.add_argument("--config", help="config file; the password lives beside it")
    parser.add_argument(
        "--reset", action="store_true",
        help=f"set the password back to the default ({DEFAULT_PASSWORD}) and print it",
    )
    parser.add_argument(
        "--if-missing", action="store_true",
        help="with --reset, do nothing if a password is already set",
    )
    args = parser.parse_args(argv)
    path = password_path(args.config)
    if not args.reset:
        print("set" if password_is_set(path) else "not set")
        return 0
    if args.if_missing and password_is_set(path):
        return 0
    reset_to_default(path)
    print(DEFAULT_PASSWORD)
    return 0


if __name__ == "__main__":
    sys.exit(main())
