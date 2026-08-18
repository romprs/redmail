from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from redmail.config_store import (
    load_account,
    load_font_scale,
    load_pane_orientation,
    load_poll_interval_minutes,
    save_account,
    save_font_scale,
    save_pane_orientation,
    save_poll_interval_minutes,
)
from redmail.imap_client import Account
from redmail.smtp_client import SmtpAccount


def _fake_keyring(store: dict[str, str]):
    set_patch = patch(
        "redmail.config_store.keyring.set_password",
        side_effect=lambda _service, user, pw: store.__setitem__(user, pw),
    )
    get_patch = patch(
        "redmail.config_store.keyring.get_password",
        side_effect=lambda _service, user: store.get(user),
    )
    return set_patch, get_patch


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    config_file = tmp_path / "account.json"
    store: dict[str, str] = {}
    set_patch, get_patch = _fake_keyring(store)

    with patch("redmail.config_store._config_path", return_value=config_file), set_patch, get_patch:
        account = Account(host="imap.example.com", username="ivan@example.com", password="secret", port=993, use_ssl=True)
        smtp = SmtpAccount(host="smtp.example.com", username="ivan@example.com", password="secret", port=587, use_ssl=False)
        save_account(account, smtp)

        loaded = load_account()

    assert loaded is not None
    loaded_account, loaded_smtp = loaded
    assert loaded_account.host == "imap.example.com"
    assert loaded_account.username == "ivan@example.com"
    assert loaded_account.password == "secret"
    assert loaded_account.port == 993
    assert loaded_smtp is not None
    assert loaded_smtp.host == "smtp.example.com"
    assert loaded_smtp.port == 587
    assert loaded_smtp.password == "secret"


def test_save_without_smtp_round_trips_to_none(tmp_path: Path) -> None:
    config_file = tmp_path / "account.json"
    store: dict[str, str] = {}
    set_patch, get_patch = _fake_keyring(store)

    with patch("redmail.config_store._config_path", return_value=config_file), set_patch, get_patch:
        account = Account(host="imap.example.com", username="ivan", password="secret")
        save_account(account, None)
        loaded = load_account()

    assert loaded is not None
    _account, smtp = loaded
    assert smtp is None


def test_load_account_returns_none_when_no_config_file(tmp_path: Path) -> None:
    config_file = tmp_path / "account.json"
    with patch("redmail.config_store._config_path", return_value=config_file):
        assert load_account() is None


def test_load_account_returns_none_when_password_missing_from_keyring(tmp_path: Path) -> None:
    # Файл настроек сохранился, но пароль в системном хранилище недоступен
    # (например, отозван или это другая машина) — не должны падать, а
    # попросить войти заново.
    config_file = tmp_path / "account.json"
    with patch("redmail.config_store._config_path", return_value=config_file), \
         patch("redmail.config_store.keyring.set_password"), \
         patch("redmail.config_store.keyring.get_password", return_value=None):
        account = Account(host="imap.example.com", username="ivan", password="secret")
        save_account(account, None)
        assert load_account() is None


def test_poll_interval_defaults_to_five_minutes(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        assert load_poll_interval_minutes() == 5


def test_poll_interval_round_trip(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        save_poll_interval_minutes(15)
        assert load_poll_interval_minutes() == 15


def test_poll_interval_survives_corrupt_file(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("not json", encoding="utf-8")
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        assert load_poll_interval_minutes() == 5


def test_pane_orientation_defaults_to_vertical(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        assert load_pane_orientation() == "vertical"


def test_pane_orientation_round_trip(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        save_pane_orientation("horizontal")
        assert load_pane_orientation() == "horizontal"


def test_pane_orientation_rejects_unknown_value(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text('{"pane_orientation": "diagonal"}', encoding="utf-8")
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        assert load_pane_orientation() == "vertical"


def test_font_scale_defaults_to_one(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        assert load_font_scale() == 1.0


def test_font_scale_round_trip(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        save_font_scale(1.25)
        assert load_font_scale() == 1.25


def test_font_scale_out_of_range_falls_back_to_default(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text('{"font_scale": 9.0}', encoding="utf-8")
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        assert load_font_scale() == 1.0


def test_saving_one_setting_does_not_clobber_another(tmp_path: Path) -> None:
    # Раньше save_* полностью перезаписывал файл настроек — второй вызов
    # стирал бы всё, что сохранил первый.
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        save_poll_interval_minutes(20)
        save_pane_orientation("horizontal")
        save_font_scale(1.5)

        assert load_poll_interval_minutes() == 20
        assert load_pane_orientation() == "horizontal"
        assert load_font_scale() == 1.5
