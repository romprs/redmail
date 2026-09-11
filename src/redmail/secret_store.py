"""Пароли учётных записей: системное хранилище (keyring → Secret Service
gnome-keyring/KWallet) с повторами и запасным файлом в профиле.

Жалоба: «периодически получаю "Хранилище паролей недоступно: No
recommended backend was available"». keyring выбирает бэкенд один раз при
первом обращении: если в этот момент D-Bus-сессия или gnome-keyring ещё
не поднялись (автозапуск при входе, запуск из ssh/сервиса без
DBUS_SESSION_BUS_ADDRESS), он навсегда остаётся с «fail»-бэкендом, и
приложение считает, что паролей нет. Здесь: (1) несколько попыток с
паузой, каждый раз заново инициализируя бэкенд; (2) если хранилища так и
нет — по явному согласию пользователя пароли хранятся в файле профиля
secrets.json (права 0600, значения обфусцированы base64: это НЕ
шифрование, файл защищён только правами доступа — пользователю об этом
сообщается при включении).
"""
from __future__ import annotations

import base64
import json
import os
import stat
import time
from pathlib import Path

import keyring
import keyring.errors

from redmail import profile
from redmail.applog import get_logger

_log = get_logger("secrets")

RETRIES = 3
RETRY_DELAY_SECONDS = 2.0
_FALLBACK_KEY = "password_file_fallback"


class SecretsUnavailable(Exception):
    """Системное хранилище недоступно, а запасной файл не включён."""


def _settings_path() -> Path:
    from redmail.paths import app_dir

    return app_dir() / "settings.json"


def _read_settings() -> dict:
    try:
        return json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def is_fallback_enabled() -> bool:
    return bool(_read_settings().get(_FALLBACK_KEY, False))


def set_fallback_enabled(enabled: bool) -> None:
    path = _settings_path()
    data = _read_settings()
    data[_FALLBACK_KEY] = bool(enabled)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---- системное хранилище с повторами ------------------------------------------


def _reset_keyring_backend() -> None:
    """Заставить keyring заново выбрать бэкенд (после того как D-Bus/демон
    поднялись). keyring кэширует выбор в _keyring_backend."""
    try:
        keyring.core._keyring_backend = None  # type: ignore[attr-defined]
        keyring.core.init_backend()
    except Exception:
        pass


def _is_unavailable(exc: Exception) -> bool:
    text = str(exc)
    return isinstance(exc, keyring.errors.NoKeyringError) or isinstance(exc, keyring.errors.InitError) or (
        "No recommended backend" in text or "no backend" in text.lower()
    )


def _with_retries(operation, *, sleep=time.sleep):
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            return operation()
        except keyring.errors.KeyringError as exc:
            if not _is_unavailable(exc):
                raise
            last = exc
            _log.warning("Хранилище паролей недоступно (попытка %d/%d): %s", attempt + 1, RETRIES, exc)
            if attempt + 1 < RETRIES:
                sleep(RETRY_DELAY_SECONDS)
                _reset_keyring_backend()
        except RuntimeError as exc:
            # keyring 25: "No recommended backend was available" — RuntimeError
            if not _is_unavailable(exc):
                raise
            last = exc
            _log.warning("Хранилище паролей недоступно (попытка %d/%d): %s", attempt + 1, RETRIES, exc)
            if attempt + 1 < RETRIES:
                sleep(RETRY_DELAY_SECONDS)
                _reset_keyring_backend()
    assert last is not None
    raise last


# ---- запасной файл в профиле --------------------------------------------------


def _fallback_path() -> Path:
    return profile.profile_dir() / "secrets.json"


def _read_fallback() -> dict[str, str]:
    try:
        raw = json.loads(_fallback_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}


def _write_fallback(data: dict[str, str]) -> None:
    path = _fallback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _key(service: str, username: str) -> str:
    return f"{service}\x00{username}"


def _encode(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _decode(value: str) -> str | None:
    try:
        return base64.b64decode(value.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


# ---- публичный интерфейс ------------------------------------------------------


def get_password(service: str, username: str, *, sleep=time.sleep) -> str | None:
    try:
        return _with_retries(lambda: keyring.get_password(service, username), sleep=sleep)
    except Exception as exc:
        if not _is_unavailable(exc):
            raise
        if not is_fallback_enabled():
            raise SecretsUnavailable(str(exc)) from exc
        _log.warning("Пароль %s читается из файла профиля (системное хранилище недоступно)", username)
        stored = _read_fallback().get(_key(service, username))
        return _decode(stored) if stored else None


def set_password(service: str, username: str, password: str, *, sleep=time.sleep) -> None:
    try:
        _with_retries(lambda: keyring.set_password(service, username, password), sleep=sleep)
        return
    except Exception as exc:
        if not _is_unavailable(exc):
            raise
        if not is_fallback_enabled():
            raise SecretsUnavailable(str(exc)) from exc
    data = _read_fallback()
    data[_key(service, username)] = _encode(password)
    _write_fallback(data)
    _log.warning("Пароль %s сохранён в файл профиля (системное хранилище недоступно)", username)


def delete_password(service: str, username: str) -> None:
    try:
        keyring.delete_password(service, username)
    except Exception:
        pass
    data = _read_fallback()
    if data.pop(_key(service, username), None) is not None:
        _write_fallback(data)
