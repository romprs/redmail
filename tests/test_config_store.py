from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from redmail.config_store import load_account, save_account
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
