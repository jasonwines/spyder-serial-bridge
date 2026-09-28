import stat

import pytest

from spyder_bridge import auth


def test_set_and_check(tmp_path):
    p = tmp_path / "password"
    auth.set_password(p, "correct horse")
    assert auth.check_password(p, "correct horse")
    assert not auth.check_password(p, "wrong horse")
    assert "correct horse" not in p.read_text()  # stored hashed
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_missing_or_empty_file_never_matches(tmp_path):
    p = tmp_path / "password"
    assert not auth.password_is_set(p)
    assert not auth.check_password(p, "")
    p.write_text("")
    assert not auth.password_is_set(p)
    assert not auth.check_password(p, "")


def test_minimum_length(tmp_path):
    with pytest.raises(ValueError):
        auth.set_password(tmp_path / "password", "short")


def test_password_lives_next_to_config(tmp_path):
    assert auth.password_path(tmp_path / "config.yaml") == tmp_path / "password"


def test_cli_reset_sets_default(tmp_path, capsys):
    cfg = tmp_path / "config.yaml"
    auth.set_password(tmp_path / "password", "forgotten-one")
    assert auth.main(["--config", str(cfg), "--reset"]) == 0
    assert capsys.readouterr().out.strip() == "spyder"
    assert auth.check_password(tmp_path / "password", "spyder")


def test_short_default_allowed_only_via_reset(tmp_path):
    p = tmp_path / "password"
    with pytest.raises(ValueError):
        auth.set_password(p, auth.DEFAULT_PASSWORD)
    auth.reset_to_default(p)
    assert auth.check_password(p, "spyder")


def test_cli_if_missing_keeps_existing(tmp_path, capsys):
    cfg = tmp_path / "config.yaml"
    auth.set_password(tmp_path / "password", "keep-this-one")
    auth.main(["--config", str(cfg), "--reset", "--if-missing"])
    assert capsys.readouterr().out == ""
    assert auth.check_password(tmp_path / "password", "keep-this-one")
