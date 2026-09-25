import pytest

from spyder_bridge.config import Config, load_config, save_config
from spyder_bridge.web import create_app, parse_form


def form_for(cfg: Config) -> dict[str, str]:
    """What the browser would submit for ``cfg``."""
    data = {k: str(v) for k, v in cfg.to_dict().items() if k != "udp_append_cr"}
    if cfg.udp_append_cr:
        data["udp_append_cr"] = "on"
    return data


@pytest.fixture
def path(tmp_path):
    return tmp_path / "config.yaml"


@pytest.fixture
def client(path):
    app = create_app(path)
    app.testing = True
    return app.test_client()


def test_get_shows_current_values(client, path):
    save_config(Config(spyder_ip="10.9.8.7", baud_rate=19200), path)
    html = client.get("/").get_data(as_text=True)
    assert 'value="10.9.8.7"' in html
    assert '<option value="19200" selected>' in html
    assert str(path) in html


def test_get_with_missing_file_shows_defaults(client):
    html = client.get("/").get_data(as_text=True)
    assert 'value="192.168.0.100"' in html


def test_get_with_broken_file_warns_and_shows_defaults(client, path):
    path.write_text("baud_rate: 96000\n")
    html = client.get("/").get_data(as_text=True)
    assert "config file has a problem" in html
    assert "96000" in html
    assert '<option value="9600" selected>' in html


def test_valid_post_saves_and_redirects(client, path):
    wanted = Config(spyder_ip="10.1.1.1", baud_rate=115200, parity="E",
                    response_timeout_ms=1200, udp_append_cr=True)
    resp = client.post("/", data=form_for(wanted))
    assert resp.status_code == 303
    assert load_config(path) == wanted
    assert "Saved." in client.get(resp.headers["Location"]).get_data(as_text=True)


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
    assert "Nothing was saved" in html
    assert "must be an IP address" in html
    assert "must be a whole number" in html
    assert "must be 1-65535" in html
    assert 'value="999.1.1.1"' in html  # keeps what the user typed
    assert not path.exists()


def test_cross_site_post_refused(client, path):
    resp = client.post("/", data=form_for(Config()), headers={"Origin": "http://evil.example"})
    assert resp.status_code == 403
    assert not path.exists()


def test_same_origin_post_allowed(client, path):
    resp = client.post("/", data=form_for(Config()), headers={"Origin": "http://localhost"})
    assert resp.status_code == 303


def test_save_failure_is_reported(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    app = create_app(blocker / "config.yaml")
    resp = app.test_client().post("/", data=form_for(Config()))
    assert resp.status_code == 500
    assert "Could not write" in resp.get_data(as_text=True)


def test_parse_form_strips_field_name_from_messages():
    _, errors = parse_form({**form_for(Config()), "baud_rate": "96000"})
    assert errors["baud_rate"].startswith("must be one of")
