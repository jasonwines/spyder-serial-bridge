"""The Pi's own network settings: hostname and DHCP/static IPv4 on eth0.

Changes go through NetworkManager (``nmcli``), which the web GUI's service
user is allowed to drive by a polkit rule that install.sh adds. The
installer's recovery addresses -- the fixed fallback IP and the automatic
link-local address -- are kept whatever the tech chooses, so a typo'd
static address can never lock anyone out.

The fallback can be turned off, so several units can share a site network
without all claiming the same address. That's remembered in a file next to
the config so install.sh keeps it off on updates. Turn it back on from the
config page or, as root on the unit's console or over SSH, with:

    spyder-bridge-fallback on
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field, replace
from ipaddress import IPv4Address, IPv4Interface
from pathlib import Path
from typing import Callable, Mapping

log = logging.getLogger(__name__)

DEVICE = "eth0"
# Written by install.sh; holds e.g. "192.168.254.254/24", or nothing.
FALLBACK_FILE = Path("/opt/spyder-bridge/FALLBACK_IP")
# Present when the fallback has been turned off; install.sh checks it too.
FALLBACK_OFF_FILENAME = "fallback-off"
FALLBACK_OFF_FILE = Path("/etc/spyder-bridge") / FALLBACK_OFF_FILENAME
LINK_LOCAL_NET = ipaddress.ip_network("169.254.0.0/16")
MIN_PREFIX, MAX_PREFIX = 8, 30
MAX_DNS = 3

_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


class NetworkError(Exception):
    """nmcli is missing or refused a request."""


@dataclass(frozen=True)
class NetworkSettings:
    """What a tech can set. Static fields are empty in DHCP mode."""

    hostname: str
    mode: str  # "dhcp" or "static"
    address: str = ""
    prefix: int = 24
    gateway: str = ""
    dns: tuple[str, ...] = ()

    @property
    def is_static(self) -> bool:
        return self.mode == "static"

    def ip_part(self) -> NetworkSettings:
        """These settings minus the hostname, for change detection."""
        return replace(self, hostname="")


@dataclass(frozen=True)
class AddressInfo:
    address: str  # "192.168.55.20/24"
    kind: str  # "DHCP", "Static", "Fallback", "Link-local"


@dataclass
class NetworkStatus:
    settings: NetworkSettings  # as configured
    connected: bool
    state: str  # NetworkManager's device state, e.g. "connected"
    addresses: list[AddressInfo] = field(default_factory=list)  # live
    gateway: str = ""
    dns: list[str] = field(default_factory=list)
    fallback: str | None = None  # the configured fallback, on or off
    fallback_enabled: bool = False

    @property
    def has_site_address(self) -> bool:
        """A live DHCP or static address, i.e. reachable without the fallback."""
        return any(a.kind in ("DHCP", "Static") for a in self.addresses)


def prefix_to_mask(prefix: int) -> str:
    return str(ipaddress.ip_network(f"0.0.0.0/{prefix}").netmask)


def parse_mask(text: str) -> int:
    """Accept ``255.255.255.0``, ``24`` or ``/24``; return the prefix length."""
    text = text.strip().lstrip("/")
    if text.isdigit():
        prefix = int(text)
    else:
        try:
            prefix = ipaddress.ip_network(f"0.0.0.0/{text}").prefixlen
        except ValueError:
            raise ValueError("must be a subnet mask like 255.255.255.0") from None
    if not MIN_PREFIX <= prefix <= MAX_PREFIX:
        raise ValueError(
            f"must be between {prefix_to_mask(MIN_PREFIX)} and {prefix_to_mask(MAX_PREFIX)}"
        )
    return prefix


def _parse_ipv4(text: str) -> IPv4Address:
    try:
        return IPv4Address(text.strip())
    except ValueError:
        raise ValueError("must be an IPv4 address like 192.168.1.20") from None


def _unusable_reason(ip: IPv4Address) -> str | None:
    if ip.is_unspecified or ip.is_loopback or ip.is_multicast or ip.is_reserved:
        return "can't be used as a device address"
    if ip in LINK_LOCAL_NET:
        return "169.254.x.x is for automatic addresses; pick another"
    return None


def validate_network_form(
    form: Mapping[str, str], fallback: IPv4Interface | None
) -> tuple[dict[str, str], dict[str, str], NetworkSettings | None]:
    """Check submitted network fields.

    Returns the raw values (to re-show the form), per-field errors, and the
    settings if everything is valid.
    """
    values = {k: form.get(k, "").strip() for k in ("hostname", "mode", "address", "mask", "gateway", "dns")}
    values["hostname"] = values["hostname"].lower()
    errors: dict[str, str] = {}

    if not _HOSTNAME_RE.match(values["hostname"]):
        errors["hostname"] = (
            "use 1-63 letters, digits and hyphens, not starting or ending with a hyphen"
        )
    if values["mode"] not in ("dhcp", "static"):
        errors["mode"] = "choose DHCP or static"
    if errors.get("mode") or values["mode"] == "dhcp":
        if errors:
            return values, errors, None
        return values, errors, NetworkSettings(values["hostname"], "dhcp")

    iface = None
    try:
        prefix = parse_mask(values["mask"])
    except ValueError as e:
        errors["mask"] = str(e)
        prefix = None
    try:
        ip = _parse_ipv4(values["address"])
        if reason := _unusable_reason(ip):
            errors["address"] = reason
        elif prefix is not None:
            iface = IPv4Interface(f"{ip}/{prefix}")
            net = iface.network
            if ip in (net.network_address, net.broadcast_address):
                errors["address"] = f"is the network or broadcast address of {net}"
            elif fallback is not None and net.overlaps(fallback.network):
                errors["address"] = (
                    f"overlaps the fallback network {fallback.network}; pick another subnet"
                )
    except ValueError as e:
        errors["address"] = str(e)

    gateway = ""
    if values["gateway"]:
        try:
            gw = _parse_ipv4(values["gateway"])
            if iface is not None:
                net = iface.network
                if gw not in net:
                    errors["gateway"] = f"must be inside {net}"
                elif gw == iface.ip:
                    errors["gateway"] = "can't be the unit's own address"
                elif gw in (net.network_address, net.broadcast_address):
                    errors["gateway"] = f"is the network or broadcast address of {net}"
            gateway = str(gw)
        except ValueError as e:
            errors["gateway"] = str(e)

    dns: list[str] = []
    for item in re.split(r"[,\s]+", values["dns"]):
        if not item:
            continue
        try:
            server = _parse_ipv4(item)
            if reason := _unusable_reason(server):
                raise ValueError(f"{item} {reason}")
            dns.append(str(server))
        except ValueError as e:
            errors["dns"] = str(e) if item in str(e) else f"{item}: {e}"
            break
    if len(dns) > MAX_DNS:
        errors["dns"] = f"at most {MAX_DNS} servers"

    if errors or iface is None:
        return values, errors, None
    return values, errors, NetworkSettings(
        values["hostname"], "static", str(iface.ip), iface.network.prefixlen, gateway, tuple(dns)
    )


def read_fallback(path: Path = FALLBACK_FILE) -> IPv4Interface | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    try:
        return IPv4Interface(text) if text else None
    except ValueError:
        log.warning("ignoring unreadable fallback address in %s: %r", path, text)
        return None


Runner = Callable[[list[str]], str]


def run_nmcli(args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["nmcli", *args], capture_output=True, text=True, timeout=20, check=False
        )
    except FileNotFoundError:
        raise NetworkError("nmcli not found; network settings need NetworkManager") from None
    except subprocess.TimeoutExpired:
        raise NetworkError("nmcli timed out") from None
    if proc.returncode != 0:
        raise NetworkError(proc.stderr.strip() or f"nmcli exited with {proc.returncode}")
    return proc.stdout


def _split_multi(text: str) -> list[str]:
    """nmcli -g joins multiple values with " | " (device) or "," (connection)."""
    return [part.strip() for part in re.split(r"[|,\n]", text) if part.strip()]


class NmcliBackend:
    def __init__(
        self,
        device: str = DEVICE,
        fallback: IPv4Interface | None = None,
        runner: Runner = run_nmcli,
        off_file: Path = FALLBACK_OFF_FILE,
    ) -> None:
        self.device = device
        self.fallback = fallback if fallback is not None else read_fallback()
        self.off_file = off_file
        self._run = runner

    @property
    def fallback_enabled(self) -> bool:
        return self.fallback is not None and not self.off_file.exists()

    def _get(self, *args: str) -> str:
        value = self._run(["-g", *args]).strip()
        return "" if value == "--" else value

    def connection_name(self) -> str:
        con = self._get("GENERAL.CONNECTION", "device", "show", self.device)
        if con:
            return con
        # Cable unplugged: fall back to any wired profile.
        for line in self._get("NAME,TYPE", "connection", "show").splitlines():
            name, _, kind = line.rpartition(":")
            if kind == "802-3-ethernet":
                return name.replace("\\:", ":")
        raise NetworkError(f"no wired connection profile for {self.device}")

    def status(self) -> NetworkStatus:
        con = self.connection_name()
        method = self._get("ipv4.method", "connection", "show", con)
        static = method == "manual"
        configured = [IPv4Interface(a) for a in _split_multi(
            self._get("ipv4.addresses", "connection", "show", con))]
        own = [a for a in configured if a != self.fallback]
        settings = NetworkSettings(
            hostname=self._run(["general", "hostname"]).strip(),
            mode="static" if static else "dhcp",
            address=str(own[0].ip) if static and own else "",
            prefix=own[0].network.prefixlen if static and own else 24,
            gateway=self._get("ipv4.gateway", "connection", "show", con) if static else "",
            dns=tuple(_split_multi(self._get("ipv4.dns", "connection", "show", con))) if static else (),
        )

        state = self._get("GENERAL.STATE", "device", "show", self.device)
        # e.g. "100 (connected)" -> "connected"
        state_text = state.partition("(")[2].rstrip(")") or state
        live = []
        for text in _split_multi(self._get("IP4.ADDRESS", "device", "show", self.device)):
            addr = IPv4Interface(text)
            if self.fallback is not None and addr == self.fallback:
                kind = "Fallback"
            elif addr.ip in LINK_LOCAL_NET:
                kind = "Link-local"
            else:
                kind = "Static" if static else "DHCP"
            live.append(AddressInfo(text, kind))
        return NetworkStatus(
            settings=settings,
            connected=state.startswith("100"),
            state=state_text,
            addresses=live,
            gateway=self._get("IP4.GATEWAY", "device", "show", self.device),
            dns=_split_multi(self._get("IP4.DNS", "device", "show", self.device)),
            fallback=str(self.fallback) if self.fallback else None,
            fallback_enabled=self.fallback_enabled,
        )

    def save(self, new: NetworkSettings) -> bool:
        """Store ``new``; return True if the address settings changed.

        The hostname takes effect at once. Address changes are stored but
        only take effect on ``activate()``, so the caller can first tell the
        tech where the unit is about to move to.
        """
        current = self.status().settings
        if new.hostname != current.hostname:
            self._run(["general", "hostname", new.hostname])
        if new.ip_part() == current.ip_part():
            return False
        addresses = [f"{new.address}/{new.prefix}"] if new.is_static else []
        if self.fallback_enabled:
            addresses.append(str(self.fallback))
        self._run([
            "connection", "modify", self.connection_name(),
            "ipv4.method", "manual" if new.is_static else "auto",
            "ipv4.addresses", ", ".join(addresses),
            "ipv4.gateway", new.gateway,
            "ipv4.dns", ",".join(new.dns),
        ])
        return True

    def set_fallback(self, enabled: bool) -> None:
        """Turn the fallback address on or off. Takes effect on ``activate()``."""
        if self.fallback is None:
            raise NetworkError("this unit has no fallback address configured")
        con = self.connection_name()
        others = [a for a in _split_multi(self._get("ipv4.addresses", "connection", "show", con))
                  if IPv4Interface(a) != self.fallback]
        addresses = others + [str(self.fallback)] if enabled else others
        # Connection first, then the marker: a failed nmcli call changes nothing.
        self._run(["connection", "modify", con, "ipv4.addresses", ", ".join(addresses)])
        if enabled:
            self.off_file.unlink(missing_ok=True)
        else:
            self.off_file.write_text("The fallback address was turned off.\n", encoding="utf-8")
        log.warning("fallback address %s turned %s", self.fallback, "on" if enabled else "off")

    def activate(self) -> None:
        """Apply stored address settings to the live interface."""
        try:
            self._run(["device", "reapply", self.device])
            return
        except NetworkError as e:
            log.warning("reapply failed (%s); reactivating the connection", e)
        try:
            self._run(["connection", "up", self.connection_name()])
        except NetworkError as e:
            # Unplugged cable, typically; settings apply when it reconnects.
            log.warning("could not apply network settings now: %s", e)


def main(argv: list[str] | None = None) -> None:
    """``spyder-bridge-fallback on|off|status``, for the unit's console."""
    parser = argparse.ArgumentParser(
        prog="spyder-bridge-fallback",
        description="Turn this unit's fallback address on or off (run as root).",
    )
    parser.add_argument("action", choices=("on", "off", "status"))
    args = parser.parse_args(argv)
    if args.action != "status" and os.geteuid() != 0:
        raise SystemExit("run as root: sudo spyder-bridge-fallback " + args.action)
    net = NmcliBackend()
    if net.fallback is None:
        raise SystemExit("this unit has no fallback address configured")
    if args.action != "status":
        try:
            net.set_fallback(args.action == "on")
            net.activate()
        except (NetworkError, OSError) as e:
            raise SystemExit(f"error: {e}") from None
    print(f"fallback address {net.fallback}: {'on' if net.fallback_enabled else 'off'}")


if __name__ == "__main__":
    main()
