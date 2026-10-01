"""Local web GUI: bridge settings, the Pi's network settings, and the password.

Runs as its own process so a GUI problem can never take the bridge down.
Bridge settings go to the config file, which the bridge watches and reloads
(see ``__main__``). Network settings go to NetworkManager (see ``network``).

    python -m spyder_bridge.web --port 8080

Every page but the login page needs the unit's password (see ``auth``).
Cross-site form posts are refused so a web page open in a tech's browser
can't silently change anything.
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import threading
import time
from dataclasses import fields
from datetime import timedelta
from ipaddress import IPv4Interface, IPv6Address, ip_address
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from flask import Flask, abort, redirect, render_template, request, session, url_for
from serial.tools import list_ports

from . import auth
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
from .logsetup import setup_logging
from .network import (
    FALLBACK_OFF_FILENAME,
    NetworkError,
    NetworkSettings,
    NmcliBackend,
    prefix_to_mask,
    validate_network_form,
)

log = logging.getLogger(__name__)

# Shown under the title on every page.
DESCRIPTION = (
    "RS232-to-IP bridge for Christie Spyder video processors — lets serial-only "
    "connections from control systems (Crestron, etc.) convert the serial Spyder "
    "API commands to network commands with no changes to existing control system "
    "programming."
)
PARITY_LABELS = {"N": "None", "E": "Even", "O": "Odd"}
_INT_FIELDS = {"baud_rate", "data_bits", "stop_bits", "spyder_port", "response_timeout_ms"}
_BOOL_FIELDS = {"udp_append_cr"}

# Long enough for the "here's where the unit is moving to" page to reach
# the browser before the old address goes away.
NETWORK_APPLY_DELAY_S = 2.0
FAILED_LOGIN_DELAY_S = 1.0
# Written by install.sh: the release tag, or git describe for a checkout.
VERSION_FILE = Path("/opt/spyder-bridge/VERSION")
SESSION_LIFETIME = timedelta(hours=12)

Scheduler = Callable[[float, Callable[[], None]], None]


def _schedule_in_background(delay: float, fn: Callable[[], None]) -> None:
    timer = threading.Timer(delay, fn)
    timer.daemon = True
    timer.start()


def detect_serial_ports() -> list[str]:
    """Candidate serial devices, stable /dev/serial/by-id names first."""
    by_id = sorted(glob.glob("/dev/serial/by-id/*"))
    return by_id + sorted(p.device for p in list_ports.comports())


def parse_form(form: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    """Turn submitted bridge fields into Config values plus per-field errors.

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


def read_version(path: Path = VERSION_FILE) -> str:
    """Installed version, or "development" when not installed by install.sh."""
    try:
        return path.read_text(encoding="utf-8").strip() or "unknown"
    except FileNotFoundError:
        return "development"


def _network_form_values(settings: NetworkSettings) -> dict[str, str]:
    return {
        "hostname": settings.hostname,
        "mode": settings.mode,
        "address": settings.address,
        "mask": prefix_to_mask(settings.prefix) if settings.is_static else "255.255.255.0",
        "gateway": settings.gateway,
        "dns": ", ".join(settings.dns),
    }


def create_app(
    config_path: str | os.PathLike[str] | None = None,
    network: Any = None,
    schedule: Scheduler = _schedule_in_background,
    version: str | None = None,
) -> Flask:
    """``network`` is an NmcliBackend or a stand-in with the same methods."""
    app = Flask(__name__)
    app.config.update(
        # A fresh key per process: a restart just means logging in again.
        SECRET_KEY=os.urandom(32),
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=SESSION_LIFETIME,
    )
    app.jinja_env.globals["description"] = DESCRIPTION
    app.jinja_env.globals["version"] = version if version is not None else read_version()
    path = resolve_config_path(config_path)
    pw_path = auth.password_path(path)
    net = network if network is not None else NmcliBackend(
        off_file=path.parent / FALLBACK_OFF_FILENAME)

    def network_status() -> tuple[Any, str | None]:
        try:
            return net.status(), None
        except NetworkError as e:
            return None, str(e)

    def render(
        *,
        bridge_values: Mapping[str, Any] | None = None,
        bridge_errors: Mapping[str, str] | None = None,
        net_values: Mapping[str, str] | None = None,
        net_errors: Mapping[str, str] | None = None,
        pw_errors: Mapping[str, str] | None = None,
        **extra: Any,
    ) -> str:
        load_error = None
        if bridge_values is None:
            try:
                bridge_values = load_config(path).to_dict()
            except ConfigError as e:
                # Show defaults so the tech can fix things by saving over it.
                bridge_values = Config().to_dict()
                load_error = str(e)
        status, network_error = network_status()
        if net_values is None and status is not None:
            net_values = _network_form_values(status.settings)
        return render_template(
            "config.html",
            values=bridge_values,
            errors=bridge_errors or {},
            load_error=load_error,
            config_path=path,
            detected_ports=detect_serial_ports(),
            baud_rates=VALID_BAUD_RATES,
            data_bits=VALID_DATA_BITS,
            parities=PARITY_LABELS,
            stop_bits=VALID_STOP_BITS,
            min_timeout=MIN_TIMEOUT_MS,
            max_timeout=MAX_TIMEOUT_MS,
            status=status,
            network_error=network_error,
            net=net_values or {},
            net_errors=net_errors or {},
            pw_errors=pw_errors or {},
            min_password=auth.MIN_PASSWORD_LEN,
            saved=request.args.get("saved"),
            default_password=session.get("default_password", False),
            **extra,
        )

    @app.before_request
    def guard() -> Any:
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin is not None and urlsplit(origin).netloc != request.host:
                abort(403)
        if request.endpoint in ("login", "static") or session.get("authed"):
            return None
        return redirect(url_for("login"), code=303)

    @app.route("/login", methods=["GET", "POST"])
    def login() -> Any:
        no_password = not auth.password_is_set(pw_path)
        if request.method == "GET":
            return render_template("login.html", no_password=no_password)
        password = request.form.get("password", "")
        if auth.check_password(pw_path, password):
            session.clear()
            session["authed"] = True
            session.permanent = True
            # Remembered here rather than re-checked per page: hash checks
            # are deliberately slow, especially on a Pi 3.
            session["default_password"] = password == auth.DEFAULT_PASSWORD
            return redirect(url_for("show"), code=303)
        time.sleep(FAILED_LOGIN_DELAY_S)  # slow down guessing
        log.warning("failed login from %s", request.remote_addr)
        return render_template(
            "login.html", error="Wrong password.", no_password=no_password
        ), 401

    @app.post("/logout")
    def logout() -> Any:
        session.clear()
        return redirect(url_for("login"), code=303)

    @app.get("/")
    def show() -> str:
        return render()

    @app.post("/")
    def save() -> Any:
        values, errors = parse_form(request.form)
        if errors:
            return render(bridge_values=values, bridge_errors=errors), 400
        try:
            save_config(Config(**values), path)
        except OSError as e:
            return render(bridge_values=values, save_error=f"Could not write {path}: {e}"), 500
        # Redirect so a browser refresh doesn't resubmit the form.
        return redirect(url_for("show", saved="bridge"), code=303)

    @app.post("/network")
    def save_network() -> Any:
        fallback: IPv4Interface | None = net.fallback
        values, errors, settings = validate_network_form(request.form, fallback)
        if errors:
            return render(net_values=values, net_errors=errors), 400
        try:
            before = net.status().settings
            address_changed = net.save(settings)
        except NetworkError as e:
            return render(net_values=values, network_save_error=str(e)), 500
        if address_changed:
            schedule(NETWORK_APPLY_DELAY_S, net.activate)
        log.warning(
            "network settings changed: hostname %s, %s%s",
            settings.hostname, settings.mode,
            f" {settings.address}/{settings.prefix}" if settings.is_static else "",
        )
        return render_template(
            "network_applied.html",
            settings=settings,
            hostname_changed=settings.hostname != before.hostname,
            address_changed=address_changed,
            fallback_ip=str(fallback.ip) if fallback and net.fallback_enabled else None,
            fallback_net=str(fallback.network) if fallback else None,
        )

    @app.post("/network/fallback")
    def set_fallback() -> Any:
        enable = request.form.get("fallback") == "on"
        if not enable:
            # Only from a working site address, so turning it off can't strand
            # the tech who's using it.
            try:
                status = net.status()
            except NetworkError as e:
                return render(network_save_error=str(e)), 500
            if net.fallback is not None and _connected_via(request.remote_addr, net.fallback):
                return render(fallback_error=(
                    "You're connected through the fallback address. Connect through "
                    "this unit's DHCP or static address, then turn it off.")), 409
            if not status.has_site_address:
                return render(fallback_error=(
                    "This unit has no DHCP or static address right now, so the "
                    "fallback is the only dependable way in. It stays on.")), 409
        try:
            net.set_fallback(enable)
        except (NetworkError, OSError) as e:
            return render(network_save_error=str(e)), 500
        schedule(NETWORK_APPLY_DELAY_S, net.activate)
        return redirect(url_for("show", saved="fallback-on" if enable else "fallback-off"), code=303)

    @app.post("/password")
    def change_password() -> Any:
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        confirm = request.form.get("confirm", "")
        errors = {}
        if not auth.check_password(pw_path, current):
            time.sleep(FAILED_LOGIN_DELAY_S)
            errors["current"] = "wrong password"
        if len(new) < auth.MIN_PASSWORD_LEN:
            errors["new"] = f"must be at least {auth.MIN_PASSWORD_LEN} characters"
        elif new != confirm:
            errors["confirm"] = "doesn't match"
        if errors:
            return render(pw_errors=errors), 400
        auth.set_password(pw_path, new)
        session["default_password"] = new == auth.DEFAULT_PASSWORD
        log.warning("config page password changed from %s", request.remote_addr)
        return redirect(url_for("show", saved="password"), code=303)

    return app


def _connected_via(remote_addr: str | None, fallback: IPv4Interface) -> bool:
    """Whether a client at ``remote_addr`` is on the fallback network."""
    try:
        ip = ip_address(remote_addr or "")
    except ValueError:
        return False
    if isinstance(ip, IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip in fallback.network


def listen_spec(host: str, port: int) -> str:
    """waitress ``listen=`` value. ``*`` means every IPv4 and IPv6 address."""
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Spyder bridge config web GUI.")
    parser.add_argument("--config", help="config file (default: standard location)")
    parser.add_argument(
        "--host", default="*",
        help="address to listen on (default *: all IPv4 and IPv6 addresses)",
    )
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    setup_logging()
    app = create_app(args.config)
    try:
        # Installed on the Pi by install.sh; sturdier than Flask's dev server.
        from waitress import serve
    except ImportError:
        # "::" is dual-stack on Linux and macOS.
        app.run(host="::" if args.host == "*" else args.host, port=args.port)
    else:
        # IPv6 matters: Windows resolves <name>.local to the Pi's fe80::
        # link-local address first, so an IPv4-only page looks unreachable.
        listen = listen_spec(args.host, args.port)
        log.info("serving on %s", listen)
        serve(app, listen=listen, threads=4)


if __name__ == "__main__":
    main()
