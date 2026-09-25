"""Local web GUI for editing the bridge config.

Runs as its own process so a GUI problem can never take the bridge down.
It only reads and writes the config file; the bridge notices the change and
reloads itself (see ``__main__``).

    python -m spyder_bridge.web --port 8080

There is no login: the page is meant for the isolated control network or
localhost (kiosk mode). Cross-site form posts are refused so a web page
open in a tech's browser can't silently rewrite the config.
"""

from __future__ import annotations

import argparse
import glob
import os
from dataclasses import fields
from typing import Any, Mapping
from urllib.parse import urlsplit

from flask import Flask, abort, redirect, render_template, request, url_for
from serial.tools import list_ports

from .config import (
    MAX_TIMEOUT_MS,
    MIN_TIMEOUT_MS,
    VALID_BAUD_RATES,
    VALID_DATA_BITS,
    VALID_STOP_BITS,
    Config,
    ConfigError,
    load_config,
    resolve_config_path,
    save_config,
)

PARITY_LABELS = {"N": "None", "E": "Even", "O": "Odd"}
_INT_FIELDS = {"baud_rate", "data_bits", "stop_bits", "spyder_port", "response_timeout_ms"}
_BOOL_FIELDS = {"udp_append_cr"}


def detect_serial_ports() -> list[str]:
    """Candidate serial devices, stable /dev/serial/by-id names first."""
    by_id = sorted(glob.glob("/dev/serial/by-id/*"))
    return by_id + sorted(p.device for p in list_ports.comports())


def parse_form(form: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    """Turn submitted form fields into Config values plus per-field errors.

    Values are returned even when invalid so the form can be re-shown with
    what the user typed.
    """
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for f in fields(Config):
        name = f.name
        if name in _BOOL_FIELDS:
            values[name] = name in form  # unchecked boxes aren't submitted
            continue
        raw = form.get(name, "").strip()
        values[name] = raw
        if name in _INT_FIELDS:
            try:
                values[name] = int(raw)
            except ValueError:
                errors[name] = "must be a whole number"

    # Config validates each field independently, so checking one field at a
    # time against the defaults yields every error, not just the first.
    for name, value in values.items():
        if name in errors:
            continue
        try:
            Config(**{name: value})
        except ConfigError as e:
            errors[name] = str(e).removeprefix(f"{name} ")
    return values, errors


def create_app(config_path: str | os.PathLike[str] | None = None) -> Flask:
    app = Flask(__name__)
    path = resolve_config_path(config_path)

    def render(values: Mapping[str, Any], errors: Mapping[str, str], **extra: Any) -> str:
        return render_template(
            "config.html",
            values=values,
            errors=errors,
            config_path=path,
            detected_ports=detect_serial_ports(),
            baud_rates=VALID_BAUD_RATES,
            data_bits=VALID_DATA_BITS,
            parities=PARITY_LABELS,
            stop_bits=VALID_STOP_BITS,
            min_timeout=MIN_TIMEOUT_MS,
            max_timeout=MAX_TIMEOUT_MS,
            **extra,
        )

    @app.before_request
    def refuse_cross_site_posts() -> None:
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin is not None and urlsplit(origin).netloc != request.host:
                abort(403)

    @app.get("/")
    def show() -> str:
        load_error = None
        try:
            cfg = load_config(path)
        except ConfigError as e:
            # Show defaults so the tech can fix things by saving over it.
            cfg = Config()
            load_error = str(e)
        return render(
            cfg.to_dict(), {},
            saved=request.args.get("saved") == "1",
            load_error=load_error,
        )

    @app.post("/")
    def save() -> Any:
        values, errors = parse_form(request.form)
        if errors:
            return render(values, errors), 400
        try:
            save_config(Config(**values), path)
        except OSError as e:
            return render(values, {}, save_error=f"Could not write {path}: {e}"), 500
        # Redirect so a browser refresh doesn't resubmit the form.
        return redirect(url_for("show", saved=1), code=303)

    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Spyder bridge config web GUI.")
    parser.add_argument("--config", help="config file (default: standard location)")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    create_app(args.config).run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
