import pytest

from spyder_bridge import auth, web
from spyder_bridge.config import Config, load_config, save_config
from spyder_bridge.network import NetworkError, NetworkSettings, NmcliBackend
from spyder_bridge.web import create_app, parse_form

from test_network import FALLBACK, FakeNmcli

PASSWORD = "test-password-1"


def form_for(cfg: Config) -> dict[str, str]:
    """What the browser would submit for ``cfg``."""
    data = {k: str(v) for k, v in cfg.to_dict().items() if k != "udp_append_cr"}
    if cfg.udp_append_cr:
        data["udp_append_cr"] = "on"
    return data


@pytest.fixture(autouse=True)
def no_login_delay(monkeypatch):
    monkeypatch.setattr(web, "FAILED_LOGIN_DELAY_S", 0)


@pytest.fixture
def path(tmp_path):
    return tmp_path / "config.yaml"


@pytest.fixture
def nmcli():
    return FakeNmcli()


@pytest.fixture
def scheduled():
    return []


@pytest.fixture
def anon(path, nmcli, scheduled):
    """A client that hasn't logged in."""
    auth.set_password(auth.password_path(path), PASSWORD)
    app = create_app(
        path,
        network=NmcliBackend(fallback=FALLBACK, runner=nmcli, off_file=path.parent / "fallback-off"),
        schedule=lambda delay, fn: scheduled.append((delay, fn)),
    )
    app.testing = True
    return app.test_client()


@pytest.fixture
def client(anon):
    resp = anon.post("/login", data={"password": PASSWORD})
    assert resp.status_code == 303
    return anon


# ------------------------------------------------------------------- login

@pytest.mark.parametrize("method, url", [("get", "/"), ("post", "/"), ("post", "/network"), ("post", "/password")])
def test_everything_needs_login(anon, path, method, url):
    resp = getattr(anon, method)(url, data=form_for(Config()))
    assert resp.status_code == 303
    assert resp.headers["Location"].endswith("/login")
    assert not path.exists()


def test_wrong_password_refused(anon):
    resp = anon.post("/login", data={"password": "nope"})
    assert resp.status_code == 401
    assert "Wrong password" in resp.get_data(as_text=True)
    assert anon.get("/").status_code == 303


def test_login_page_warns_when_no_password_set(tmp_path):
    app = create_app(tmp_path / "config.yaml", network=NmcliBackend(fallback=FALLBACK, runner=FakeNmcli()))
    html = app.test_client().get("/login").get_data(as_text=True)
    assert "No password has been set" in html


def test_logout(client):
    assert client.get("/").status_code == 200
    client.post("/logout")
    assert client.get("/").status_code == 303


# ------------------------------------------------------------ bridge form

def test_get_shows_current_values(client, path):
    save_config(Config(spyder_ip="10.9.8.7", baud_rate=19200), path)
    html = client.get("/").get_data(as_text=True)
    assert 'value="10.9.8.7"' in html
    assert '<option value="19200" selected>' in html
    assert str(path) in html


def test_get_with_broken_file_warns_and_shows_defaults(client, path):
    path.write_text("baud_rate: 96000\n")
    html = client.get("/").get_data(as_text=True)
    assert "config file has a problem" in html
    assert '<option value="9600" selected>' in html


def test_valid_post_saves_and_redirects(client, path):
    wanted = Config(spyder_ip="10.1.1.1", baud_rate=115200, parity="E",
                    response_timeout_ms=1200, udp_append_cr=True)
    resp = client.post("/", data=form_for(wanted))
    assert resp.status_code == 303
    assert load_config(path) == wanted
    assert "Bridge settings saved" in client.get(resp.headers["Location"]).get_data(as_text=True)


def test_unchecked_box_saves_false(client, path):
    save_config(Config(udp_append_cr=True), path)
    client.post("/", data=form_for(Config()))
    assert load_config(path).udp_append_cr is False


def test_invalid_post_shows_every_error_and_saves_nothing(client, path):
    data = form_for(Config())
    data.update(spyder_ip="999.1.1.1", response_timeout_ms="abc", spyder_port="0")
    resp = client.post("/", data=data)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 400
    assert "Bridge settings not saved" in html
    assert "must be an IP address" in html
    assert "must be a whole number" in html
    assert "must be 1-65535" in html
    assert 'value="999.1.1.1"' in html
    assert not path.exists()


def test_cross_site_post_refused(client, path):
    resp = client.post("/", data=form_for(Config()), headers={"Origin": "http://evil.example"})
    assert resp.status_code == 403
    assert not path.exists()


def test_same_origin_post_allowed(client):
    resp = client.post("/", data=form_for(Config()), headers={"Origin": "http://localhost"})
    assert resp.status_code == 303


def test_parse_form_strips_field_name_from_messages():
    _, errors = parse_form({**form_for(Config()), "baud_rate": "96000"})
    assert errors["baud_rate"].startswith("must be one of")


# ------------------------------------------------------------ network form

def net_form(**kw):
    base = {"hostname": "spyder-serial-bridge", "mode": "dhcp", "address": "",
            "mask": "255.255.255.0", "gateway": "", "dns": ""}
    return {**base, **kw}


def test_page_shows_current_address_and_mode(client):
    html = client.get("/").get_data(as_text=True)
    assert "DHCP (automatic)" in html
    assert "192.168.55.20/24" in html
    assert "192.168.254.254/24" in html and "Fallback" in html
    assert "169.254.7.9/16" in html and "Link-local" in html
    assert "spyder-serial-bridge.local" in html


def test_page_shows_static_mode(client, nmcli):
    nmcli.props.update({"ipv4.method": "manual", "ipv4.addresses": "192.168.55.40/24, 192.168.254.254/24"})
    nmcli.live = ["192.168.55.40/24", "192.168.254.254/24"]
    html = client.get("/").get_data(as_text=True)
    assert '<td>Static</td>' in html
    assert 'value="192.168.55.40"' in html


def test_static_save_shows_new_address_then_applies(client, nmcli, scheduled):
    resp = client.post("/network", data=net_form(mode="static", address="192.168.55.40", gateway="192.168.55.1"))
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "http://192.168.55.40/" in html
    assert "http://192.168.254.254/" in html
    assert nmcli.props["ipv4.addresses"] == "192.168.55.40/24, 192.168.254.254/24"
    # Not applied until after the response has gone out.
    assert not any(c[:2] == ["device", "reapply"] for c in nmcli.calls)
    [(delay, fn)] = scheduled
    assert delay == web.NETWORK_APPLY_DELAY_S
    fn()
    assert nmcli.calls[-1] == ["device", "reapply", "eth0"]


def test_hostname_change_needs_no_reapply(client, nmcli, scheduled):
    html = client.post("/network", data=net_form(hostname="Rack-2")).get_data(as_text=True)
    assert nmcli.hostname == "rack-2"
    assert "http://rack-2.local/" in html
    assert scheduled == []


def test_invalid_network_form_shows_errors(client, nmcli):
    resp = client.post("/network", data=net_form(mode="static", address="192.168.254.9"))
    html = resp.get_data(as_text=True)
    assert resp.status_code == 400
    assert "Network settings not saved" in html
    assert "overlaps the fallback network" in html
    assert not any(c[:2] == ["connection", "modify"] for c in nmcli.calls)


def test_network_unavailable_is_reported(anon, path):
    def broken(args):
        raise NetworkError("nmcli not found; network settings need NetworkManager")

    app = create_app(path, network=NmcliBackend(fallback=FALLBACK, runner=broken))
    c = app.test_client()
    c.post("/login", data={"password": PASSWORD})
    html = c.get("/").get_data(as_text=True)
    assert "Network settings aren't available: nmcli not found" in html
    assert "Save bridge settings" in html  # rest of the page still works


# ---------------------------------------------------------------- fallback

def test_turn_fallback_off_from_site_address(client, nmcli, path, scheduled):
    assert "Turn off fallback address" in client.get("/").get_data(as_text=True)
    resp = client.post("/network/fallback", data={"fallback": "off"},
                       environ_base={"REMOTE_ADDR": "192.168.55.9"})
    assert resp.status_code == 303 and "saved=fallback-off" in resp.location
    assert nmcli.props["ipv4.addresses"] == ""
    assert (path.parent / "fallback-off").exists()
    [(_, fn)] = scheduled
    fn()
    assert nmcli.calls[-1] == ["device", "reapply", "eth0"]
    html = client.get("/?saved=fallback-off").get_data(as_text=True)
    assert "Fallback address turned off" in html
    assert "Turn on fallback address (192.168.254.254)" in html


def test_cannot_turn_fallback_off_while_using_it(client, nmcli, path, scheduled):
    resp = client.post("/network/fallback", data={"fallback": "off"},
                       environ_base={"REMOTE_ADDR": "192.168.254.1"})
    assert resp.status_code == 409
    assert "connected through the fallback address" in resp.get_data(as_text=True)
    assert not (path.parent / "fallback-off").exists() and scheduled == []
    assert nmcli.props["ipv4.addresses"] == "192.168.254.254/24"


def test_cannot_turn_fallback_off_without_site_address(client, nmcli, path):
    nmcli.live = ["192.168.254.254/24", "169.254.7.9/16"]
    resp = client.post("/network/fallback", data={"fallback": "off"},
                       environ_base={"REMOTE_ADDR": "169.254.1.2"})
    assert resp.status_code == 409
    assert "no DHCP or static address" in resp.get_data(as_text=True)
    assert not (path.parent / "fallback-off").exists()


def test_turn_fallback_back_on(client, nmcli, path, scheduled):
    client.post("/network/fallback", data={"fallback": "off"})
    resp = client.post("/network/fallback", data={"fallback": "on"},
                       environ_base={"REMOTE_ADDR": "169.254.1.2"})
    assert resp.status_code == 303
    assert nmcli.props["ipv4.addresses"] == "192.168.254.254/24"
    assert not (path.parent / "fallback-off").exists()


def test_static_save_with_fallback_off_promises_nothing(client, nmcli):
    client.post("/network/fallback", data={"fallback": "off"})
    html = client.post("/network", data=net_form(mode="static", address="192.168.55.40")).get_data(as_text=True)
    assert nmcli.props["ipv4.addresses"] == "192.168.55.40/24"
    assert "http://192.168.254.254/" not in html
    assert "fallback address is off" in html


@pytest.mark.parametrize("addr, via", [
    ("192.168.254.7", True), ("::ffff:192.168.254.7", True),
    ("192.168.55.9", False), ("fe80::1", False), (None, False), ("junk", False),
])
def test_connected_via(addr, via):
    assert web._connected_via(addr, FALLBACK) is via


# ---------------------------------------------------------------- password

def test_change_password(client, path):
    resp = client.post("/password", data={"current": PASSWORD, "new": "brand-new-pw", "confirm": "brand-new-pw"})
    assert resp.status_code == 303
    assert auth.check_password(auth.password_path(path), "brand-new-pw")


@pytest.mark.parametrize(
    "data, fragment",
    [
        ({"current": "wrong", "new": "brand-new-pw", "confirm": "brand-new-pw"}, "wrong password"),
        ({"current": PASSWORD, "new": "short", "confirm": "short"}, "at least 8"),
        ({"current": PASSWORD, "new": "brand-new-pw", "confirm": "different-pw"}, "match"),
    ],
)
def test_bad_password_change(client, path, data, fragment):
    resp = client.post("/password", data=data)
    assert resp.status_code == 400
    assert fragment in resp.get_data(as_text=True)
    assert auth.check_password(auth.password_path(path), PASSWORD)


# -------------------------------------------------------- default password

def test_default_password_nags_until_changed(anon, path):
    auth.reset_to_default(auth.password_path(path))
    anon.post("/login", data={"password": auth.DEFAULT_PASSWORD})
    assert "still uses the default password" in anon.get("/").get_data(as_text=True)
    anon.post("/password", data={"current": auth.DEFAULT_PASSWORD,
                                 "new": "site-specific-pw", "confirm": "site-specific-pw"})
    assert "still uses the default password" not in anon.get("/").get_data(as_text=True)


def test_no_nag_with_own_password(client):
    assert "still uses the default password" not in client.get("/").get_data(as_text=True)


def test_description_on_login_and_main_page(anon):
    assert web.DESCRIPTION in anon.get("/login").get_data(as_text=True)
    anon.post("/login", data={"password": PASSWORD})
    assert web.DESCRIPTION in anon.get("/").get_data(as_text=True)


def test_version_shown_only_after_login(path, nmcli):
    auth.set_password(auth.password_path(path), PASSWORD)
    app = create_app(path, network=NmcliBackend(fallback=FALLBACK, runner=nmcli), version="v1.2.3")
    anon = app.test_client()
    assert "v1.2.3" not in anon.get("/login").get_data(as_text=True)
    anon.post("/login", data={"password": PASSWORD})
    assert "Version v1.2.3" in anon.get("/").get_data(as_text=True)


def test_read_version(tmp_path):
    f = tmp_path / "VERSION"
    assert web.read_version(f) == "development"
    f.write_text("v1.0.0\n")
    assert web.read_version(f) == "v1.0.0"
    f.write_text("")
    assert web.read_version(f) == "unknown"


@pytest.mark.parametrize(
    "host, spec",
    [("*", "*:80"), ("0.0.0.0", "0.0.0.0:80"), ("::", "[::]:80"), ("fe80::1", "[fe80::1]:80")],
)
def test_listen_spec(host, spec):
    assert web.listen_spec(host, 80) == spec


def test_cannot_change_back_to_short_default(anon, path):
    auth.reset_to_default(auth.password_path(path))
    anon.post("/login", data={"password": auth.DEFAULT_PASSWORD})
    resp = anon.post("/password", data={"current": auth.DEFAULT_PASSWORD,
                                        "new": "spyder", "confirm": "spyder"})
    assert resp.status_code == 400
    assert "at least 8" in resp.get_data(as_text=True)
