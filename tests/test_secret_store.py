from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import keyring.errors
import pytest

from redmail import profile, secret_store


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(profile, "app_dir", lambda: tmp_path)
    profile.set_profile_dir_override(tmp_path / "profile")
    monkeypatch.setattr(secret_store, "_settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(secret_store, "_reset_keyring_backend", lambda: None)
    yield tmp_path
    profile.set_profile_dir_override(None)


def test_retries_until_system_keyring_becomes_available(isolated) -> None:
    # Жалоба: "периодически получаю 'No recommended backend was available'" —
    # при автозапуске gnome-keyring/D-Bus могут подняться позже приложения;
    # повторяем, а не сдаёмся с первого раза.
    attempts = {"n": 0}

    def flaky_get(service, username):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise keyring.errors.NoKeyringError("No recommended backend was available")
        return "secret"

    slept: list[float] = []
    with patch("keyring.get_password", side_effect=flaky_get):
        assert secret_store.get_password("redmail", "ivan", sleep=slept.append) == "secret"
    assert attempts["n"] == 3 and len(slept) == 2


def test_raises_clear_error_when_keyring_unavailable_and_fallback_off(isolated) -> None:
    with patch("keyring.get_password", side_effect=RuntimeError("No recommended backend was available")):
        with pytest.raises(secret_store.SecretsUnavailable):
            secret_store.get_password("redmail", "ivan", sleep=lambda _s: None)


def test_other_keyring_errors_are_not_retried(isolated) -> None:
    calls = {"n": 0}

    def broken(service, username):
        calls["n"] += 1
        raise keyring.errors.PasswordDeleteError("locked")

    with patch("keyring.get_password", side_effect=broken):
        with pytest.raises(keyring.errors.PasswordDeleteError):
            secret_store.get_password("redmail", "ivan", sleep=lambda _s: None)
    assert calls["n"] == 1


def test_fallback_file_in_profile_when_enabled(isolated) -> None:
    # По явному согласию пользователя пароли живут в файле профиля с
    # правами только для владельца; в открытом виде в файле их нет.
    secret_store.set_fallback_enabled(True)
    unavailable = RuntimeError("No recommended backend was available")
    with patch("keyring.set_password", side_effect=unavailable), patch("keyring.get_password", side_effect=unavailable):
        secret_store.set_password("redmail", "ivan", "p@ss", sleep=lambda _s: None)
        assert secret_store.get_password("redmail", "ivan", sleep=lambda _s: None) == "p@ss"
        assert secret_store.get_password("redmail", "petr", sleep=lambda _s: None) is None
    raw = (isolated / "profile" / "secrets.json").read_text(encoding="utf-8")
    assert "p@ss" not in raw
    assert json.loads(raw)
    with patch("keyring.delete_password", side_effect=unavailable):
        secret_store.delete_password("redmail", "ivan")
    with patch("keyring.get_password", side_effect=unavailable):
        assert secret_store.get_password("redmail", "ivan", sleep=lambda _s: None) is None


def test_system_keyring_preferred_over_fallback(isolated) -> None:
    secret_store.set_fallback_enabled(True)
    with patch("keyring.set_password") as set_pw, patch("keyring.get_password", return_value="sys"):
        secret_store.set_password("redmail", "ivan", "p")
        assert secret_store.get_password("redmail", "ivan") == "sys"
    set_pw.assert_called_once_with("redmail", "ivan", "p")
    assert not (isolated / "profile" / "secrets.json").exists()
