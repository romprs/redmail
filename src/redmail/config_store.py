from __future__ import annotations

import json
from pathlib import Path

import keyring

from redmail.imap_client import Account
from redmail.paths import app_dir
from redmail.smtp_client import SmtpAccount

_KEYRING_SERVICE = "redmail"


_DEFAULT_POLL_INTERVAL_MINUTES = 5


def _config_path() -> Path:
    return app_dir() / "account.json"


def _settings_path() -> Path:
    return app_dir() / "settings.json"


def load_poll_interval_minutes() -> int:
    path = _settings_path()
    if not path.exists():
        return _DEFAULT_POLL_INTERVAL_MINUTES
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("poll_interval_minutes", _DEFAULT_POLL_INTERVAL_MINUTES))
    except (json.JSONDecodeError, TypeError, ValueError):
        return _DEFAULT_POLL_INTERVAL_MINUTES


def save_poll_interval_minutes(minutes: int) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"poll_interval_minutes": minutes}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def save_account(account: Account, smtp: SmtpAccount | None) -> None:
    """Сохраняет настройки подключения. Пароль — не в этом файле, а в keyring."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "imap_host": account.host,
        "imap_port": account.port,
        "imap_use_ssl": account.use_ssl,
        "username": account.username,
        "smtp_host": smtp.host if smtp else "",
        "smtp_port": smtp.port if smtp else 587,
        "smtp_use_ssl": smtp.use_ssl if smtp else False,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    keyring.set_password(_KEYRING_SERVICE, account.username, account.password)


def load_account() -> tuple[Account, SmtpAccount | None] | None:
    path = _config_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        username = data["username"]
    except (json.JSONDecodeError, KeyError):
        return None

    password = keyring.get_password(_KEYRING_SERVICE, username)
    if password is None:
        # Файл настроек есть, а пароля в хранилище секретов нет (например,
        # его отозвали или это другая машина) — просим ввести заново.
        return None

    account = Account(
        host=data["imap_host"],
        username=username,
        password=password,
        port=data["imap_port"],
        use_ssl=data["imap_use_ssl"],
    )
    smtp = (
        SmtpAccount(
            host=data["smtp_host"],
            username=username,
            password=password,
            port=data["smtp_port"],
            use_ssl=data["smtp_use_ssl"],
        )
        if data.get("smtp_host")
        else None
    )
    return account, smtp
