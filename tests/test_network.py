from ipaddress import IPv4Interface

import pytest

from spyder_bridge.network import (
    NetworkError,
    NetworkSettings,
    NmcliBackend,
    parse_mask,
    prefix_to_mask,
    read_fallback,
    validate_network_form,
)

FALLBACK = IPv4Interface("192.168.254.254/24")
CON = "Wired connection 1"


class FakeNmcli:
    """Just enough nmcli to exercise NmcliBackend, output formats included."""

    def __init__(self, method="auto", addresses=("192.168.254.254/24",), gateway="",
                 dns="", hostname="spyder-serial-bridge", connected=True,
                 live=("192.168.55.20/24", "192.168.254.254/24", "169.254.7.9/16")):
        self.props = {
            "ipv4.method": method,
            "ipv4.addresses": ", ".join(addresses),
            "ipv4.gateway": gateway,
            "ipv4.dns": dns,
        }
        self.hostname = hostname
        self.connected = connected
        self.live = list(live)
        self.calls: list[list[str]] = []
        self.fail_reapply = False

    def __call__(self, args):
        self.calls.append(args)
        match args:
            case ["-g", "GENERAL.CONNECTION", "device", "show", "eth0"]:
                return (CON if self.connected else "") + "\n"
            case ["-g", "NAME,TYPE", "connection", "show"]:
                return f"lo:loopback\n{CON}:802-3-ethernet\n"
            case ["-g", prop, "connection", "show", c] if c == CON:
                return (self.props[prop] or "") + "\n"
            case ["general", "hostname"]:
                return self.hostname + "\n"
            case ["general", "hostname", name]:
                self.hostname = name
                return ""
            case ["-g", "GENERAL.STATE", "device", "show", "eth0"]:
                return ("100 (connected)" if self.connected else "20 (unavailable)") + "\n"
            case ["-g", "IP4.ADDRESS", "device", "show", "eth0"]:
                return " | ".join(self.live) + "\n"
            case ["-g", "IP4.GATEWAY", "device", "show", "eth0"]:
                return "192.168.55.1\n" if self.connected else "--\n"
            case ["-g", "IP4.DNS", "device", "show", "eth0"]:
                return "192.168.55.1 | 1.1.1.1\n" if self.connected else "\n"
            case ["connection", "modify", c, *pairs] if c == CON:
                for k, v in zip(pairs[::2], pairs[1::2]):
                    self.props[k] = v
                return ""
            case ["device", "reapply", "eth0"]:
                if self.fail_reapply:
                    raise NetworkError("reapply not possible")
                return ""
            case ["connection", "up", c] if c == CON:
                return ""
        raise AssertionError(f"unexpected nmcli call: {args}")


@pytest.fixture(autouse=True)
def off_file(tmp_path, monkeypatch):
    """Keep every backend away from the real /etc/spyder-bridge."""
    path = tmp_path / "fallback-off"
    monkeypatch.setattr("spyder_bridge.network.FALLBACK_OFF_FILE", path)
    return path


def backend(fake, off_file=None):
    kw = {"off_file": off_file} if off_file else {}
    return NmcliBackend(fallback=FALLBACK, runner=fake, **kw)


# ------------------------------------------------------------------ status

def test_status_dhcp_labels_each_live_address():
    s = backend(FakeNmcli()).status()
    assert s.settings == NetworkSettings("spyder-serial-bridge", "dhcp")
    assert s.connected and s.state == "connected"
    assert [(a.address, a.kind) for a in s.addresses] == [
        ("192.168.55.20/24", "DHCP"),
        ("192.168.254.254/24", "Fallback"),
        ("169.254.7.9/16", "Link-local"),
    ]
    assert s.gateway == "192.168.55.1"
    assert s.dns == ["192.168.55.1", "1.1.1.1"]
    assert s.fallback == "192.168.254.254/24"


def test_status_static_reads_configured_address_not_fallback():
    fake = FakeNmcli(method="manual", addresses=("10.0.0.5/16", "192.168.254.254/24"),
                     gateway="10.0.0.1", dns="10.0.0.1,8.8.8.8",
                     live=("10.0.0.5/16", "192.168.254.254/24"))
    s = backend(fake).status()
    assert s.settings == NetworkSettings(
        "spyder-serial-bridge", "static", "10.0.0.5", 16, "10.0.0.1", ("10.0.0.1", "8.8.8.8"))
    assert s.addresses[0].kind == "Static"


def test_status_unplugged_uses_any_wired_profile():
    s = backend(FakeNmcli(connected=False, live=())).status()
    assert not s.connected and s.state == "unavailable"
    assert s.gateway == "" and s.addresses == []


# -------------------------------------------------------------------- save

def test_save_static_keeps_fallback_and_defers_activation():
    fake = FakeNmcli()
    b = backend(fake)
    new = NetworkSettings("spyder-serial-bridge", "static", "192.168.55.40", 24,
                          "192.168.55.1", ("192.168.55.1",))
    assert b.save(new) is True
    assert fake.props == {
        "ipv4.method": "manual",
        "ipv4.addresses": "192.168.55.40/24, 192.168.254.254/24",
        "ipv4.gateway": "192.168.55.1",
        "ipv4.dns": "192.168.55.1",
    }
    assert not any(c[:2] == ["device", "reapply"] for c in fake.calls)
    b.activate()
    assert fake.calls[-1] == ["device", "reapply", "eth0"]


def test_save_back_to_dhcp_clears_static_settings():
    fake = FakeNmcli(method="manual", addresses=("10.0.0.5/16", "192.168.254.254/24"),
                     gateway="10.0.0.1", dns="10.0.0.1")
    assert backend(fake).save(NetworkSettings("spyder-serial-bridge", "dhcp")) is True
    assert fake.props == {
        "ipv4.method": "auto",
        "ipv4.addresses": "192.168.254.254/24",
        "ipv4.gateway": "",
        "ipv4.dns": "",
    }


def test_hostname_only_change_does_not_touch_addresses():
    fake = FakeNmcli()
    assert backend(fake).save(NetworkSettings("rack-2-bridge", "dhcp")) is False
    assert fake.hostname == "rack-2-bridge"
    assert not any(c[:2] == ["connection", "modify"] for c in fake.calls)


def test_no_fallback_configured():
    fake = FakeNmcli(addresses=())
    b = NmcliBackend(fallback=None, runner=fake)
    b.fallback = None
    b.save(NetworkSettings("spyder-serial-bridge", "static", "10.1.1.1", 24))
    assert fake.props["ipv4.addresses"] == "10.1.1.1/24"


# ---------------------------------------------------------------- fallback

def test_turn_fallback_off_and_on(off_file):
    fake = FakeNmcli(method="manual", addresses=("10.0.0.5/16", "192.168.254.254/24"))
    b = backend(fake, off_file)
    assert b.fallback_enabled and b.status().fallback_enabled
    b.set_fallback(False)
    assert fake.props["ipv4.addresses"] == "10.0.0.5/16"
    assert off_file.exists() and not b.fallback_enabled
    assert not b.status().fallback_enabled
    b.set_fallback(True)
    assert fake.props["ipv4.addresses"] == "10.0.0.5/16, 192.168.254.254/24"
    assert not off_file.exists() and b.fallback_enabled


def test_fallback_off_in_dhcp_leaves_no_addresses(off_file):
    fake = FakeNmcli()
    backend(fake, off_file).set_fallback(False)
    assert fake.props["ipv4.addresses"] == ""


def test_save_leaves_fallback_out_while_off(off_file):
    fake = FakeNmcli()
    b = backend(fake, off_file)
    b.set_fallback(False)
    b.save(NetworkSettings("spyder-serial-bridge", "static", "192.168.55.40", 24))
    assert fake.props["ipv4.addresses"] == "192.168.55.40/24"


def test_failed_nmcli_leaves_fallback_on(off_file):
    fake = FakeNmcli()

    def failing(args):
        if args[:2] == ["connection", "modify"]:
            raise NetworkError("nope")
        return fake(args)

    b = backend(failing, off_file)
    with pytest.raises(NetworkError):
        b.set_fallback(False)
    assert not off_file.exists()


def test_set_fallback_needs_one_configured(off_file):
    b = NmcliBackend(fallback=None, runner=FakeNmcli(addresses=()), off_file=off_file)
    b.fallback = None
    with pytest.raises(NetworkError):
        b.set_fallback(True)


def test_has_site_address():
    assert backend(FakeNmcli()).status().has_site_address
    fake = FakeNmcli(live=("192.168.254.254/24", "169.254.7.9/16"))
    assert not backend(fake).status().has_site_address


def test_activate_falls_back_to_connection_up():
    fake = FakeNmcli()
    fake.fail_reapply = True
    backend(fake).activate()
    assert fake.calls[-1] == ["connection", "up", CON]


def test_read_fallback(tmp_path):
    p = tmp_path / "FALLBACK_IP"
    assert read_fallback(p) is None
    p.write_text("192.168.254.254/24\n")
    assert read_fallback(p) == FALLBACK
    p.write_text("\n")
    assert read_fallback(p) is None
    p.write_text("junk")
    assert read_fallback(p) is None


# -------------------------------------------------------------- validation

def form(**kw):
    base = {"hostname": "spyder-serial-bridge", "mode": "static", "address": "192.168.55.40",
            "mask": "255.255.255.0", "gateway": "", "dns": ""}
    return {**base, **kw}


def test_valid_static():
    _, errors, s = validate_network_form(
        form(gateway="192.168.55.1", dns="192.168.55.1, 8.8.8.8"), FALLBACK)
    assert errors == {}
    assert s == NetworkSettings("spyder-serial-bridge", "static", "192.168.55.40", 24,
                                "192.168.55.1", ("192.168.55.1", "8.8.8.8"))


def test_dhcp_ignores_static_fields():
    _, errors, s = validate_network_form(form(mode="dhcp", address="junk"), FALLBACK)
    assert errors == {} and s == NetworkSettings("spyder-serial-bridge", "dhcp")


def test_hostname_is_lowercased():
    _, _, s = validate_network_form(form(hostname="Rack-2"), FALLBACK)
    assert s.hostname == "rack-2"


@pytest.mark.parametrize("name", ["", "-lead", "trail-", "has_underscore", "dot.ted", "x" * 64, "sp ace"])
def test_bad_hostnames(name):
    _, errors, s = validate_network_form(form(hostname=name), FALLBACK)
    assert "hostname" in errors and s is None


@pytest.mark.parametrize(
    "overrides, field, fragment",
    [
        ({"address": "192.168.55"}, "address", "IPv4"),
        ({"address": "127.0.0.5"}, "address", "can't be used"),
        ({"address": "224.0.0.1"}, "address", "can't be used"),
        ({"address": "169.254.3.3"}, "address", "169.254"),
        ({"address": "192.168.55.0"}, "address", "network or broadcast"),
        ({"address": "192.168.55.255"}, "address", "network or broadcast"),
        ({"address": "192.168.254.10"}, "address", "fallback"),
        ({"address": "192.168.0.10", "mask": "255.255.0.0"}, "address", "fallback"),
        ({"mask": "255.255.0.255"}, "mask", "subnet mask"),
        ({"mask": "255.255.255.255"}, "mask", "between"),
        ({"mask": "4"}, "mask", "between"),
        ({"gateway": "10.0.0.1"}, "gateway", "inside 192.168.55.0/24"),
        ({"gateway": "192.168.55.40"}, "gateway", "own address"),
        ({"dns": "1.1.1.1, nope"}, "dns", "nope"),
        ({"dns": "1.1.1.1 2.2.2.2 3.3.3.3 4.4.4.4"}, "dns", "at most 3"),
        ({"mode": "bogus"}, "mode", "DHCP or static"),
    ],
)
def test_static_errors(overrides, field, fragment):
    _, errors, s = validate_network_form(form(**overrides), FALLBACK)
    assert s is None
    assert fragment in errors[field]


def test_every_static_error_reported_at_once():
    _, errors, _ = validate_network_form(
        form(hostname="-", address="1.2.3", mask="x", gateway="y", dns="z"), FALLBACK)
    assert set(errors) == {"hostname", "address", "mask", "gateway", "dns"}


@pytest.mark.parametrize("text, prefix", [("255.255.255.0", 24), ("24", 24), ("/16", 16), ("255.255.252.0", 22)])
def test_parse_mask(text, prefix):
    assert parse_mask(text) == prefix


def test_prefix_to_mask():
    assert prefix_to_mask(24) == "255.255.255.0"
    assert prefix_to_mask(22) == "255.255.252.0"


# --------------------------------------------------- spyder-bridge-fallback

def test_fallback_command(monkeypatch, capsys, off_file):
    from spyder_bridge import network
    fake = FakeNmcli()
    monkeypatch.setattr(network, "NmcliBackend", lambda: backend(fake, off_file))
    monkeypatch.setattr(network.os, "geteuid", lambda: 0)
    network.main(["off"])
    assert off_file.exists() and fake.calls[-1] == ["device", "reapply", "eth0"]
    assert "192.168.254.254/24: off" in capsys.readouterr().out
    network.main(["on"])
    assert not off_file.exists() and fake.props["ipv4.addresses"] == "192.168.254.254/24"
    network.main(["status"])
    assert capsys.readouterr().out.strip().endswith(": on")


def test_fallback_command_needs_root(monkeypatch):
    from spyder_bridge import network
    monkeypatch.setattr(network.os, "geteuid", lambda: 1000)
    with pytest.raises(SystemExit, match="run as root"):
        network.main(["off"])
