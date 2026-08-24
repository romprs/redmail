from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import keyring

from redmail.ews_client import EwsAccount
from redmail.imap_client import Account
from redmail.paths import app_dir
from redmail.smtp_client import SmtpAccount

_KEYRING_SERVICE = "redmail"


_DEFAULT_POLL_INTERVAL_MINUTES = 5
_DEFAULT_PANE_ORIENTATION = "vertical"
_DEFAULT_FONT_SCALE = 1.0


def _config_path() -> Path:
    return app_dir() / "account.json"


def _settings_path() -> Path:
    return app_dir() / "settings.json"


def _load_settings_dict() -> dict:
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_settings_dict(data: dict) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_poll_interval_minutes() -> int:
    try:
        return int(_load_settings_dict().get("poll_interval_minutes", _DEFAULT_POLL_INTERVAL_MINUTES))
    except (TypeError, ValueError):
        return _DEFAULT_POLL_INTERVAL_MINUTES


def save_poll_interval_minutes(minutes: int) -> None:
    data = _load_settings_dict()
    data["poll_interval_minutes"] = minutes
    _save_settings_dict(data)


def load_pane_orientation() -> str:
    value = _load_settings_dict().get("pane_orientation", _DEFAULT_PANE_ORIENTATION)
    return value if value in ("vertical", "horizontal") else _DEFAULT_PANE_ORIENTATION


def save_pane_orientation(orientation: str) -> None:
    data = _load_settings_dict()
    data["pane_orientation"] = orientation
    _save_settings_dict(data)


def load_caldav_url() -> str:
    """Адрес CalDAV-сервера — вводится пользователем вручную (закрытая
    корпоративная сеть, автоопределить неоткуда), логин/пароль берутся из
    уже подключённого почтового аккаунта (тот же keyring, отдельно не
    хранятся)."""
    value = _load_settings_dict().get("caldav_url", "")
    return value if isinstance(value, str) else ""


def save_caldav_url(url: str) -> None:
    data = _load_settings_dict()
    data["caldav_url"] = url
    _save_settings_dict(data)


@dataclass
class MailRule:
    field: str  # "from" | "subject"
    contains: str
    target_folder: str


def load_mail_rules() -> list[MailRule]:
    """Правила сортировки почты по подпапкам — применяются только вручную
    (кнопка "Применить правила"), не автоматически при поступлении письма:
    сервер (VK Mail/Exchange) ещё ни разу не тестировался вживую с этой
    функцией, тихая автоматическая раскладка почты без возможности сверить
    результат — больший риск, чем явное действие пользователя."""
    raw = _load_settings_dict().get("mail_rules", [])
    if not isinstance(raw, list):
        return []
    rules = []
    for item in raw:
        try:
            rules.append(MailRule(field=item["field"], contains=item["contains"], target_folder=item["target_folder"]))
        except (TypeError, KeyError):
            continue  # повреждённая запись — пропускаем, не валим всю загрузку
    return rules


def save_mail_rules(rules: list[MailRule]) -> None:
    data = _load_settings_dict()
    data["mail_rules"] = [asdict(rule) for rule in rules]
    _save_settings_dict(data)


def load_font_scale() -> float:
    try:
        value = float(_load_settings_dict().get("font_scale", _DEFAULT_FONT_SCALE))
    except (TypeError, ValueError):
        return _DEFAULT_FONT_SCALE
    return value if 0.5 <= value <= 2.0 else _DEFAULT_FONT_SCALE


def save_font_scale(scale: float) -> None:
    data = _load_settings_dict()
    data["font_scale"] = scale
    _save_settings_dict(data)


def load_window_geometry() -> bytes | None:
    value = _load_settings_dict().get("window_geometry")
    return base64.b64decode(value) if value else None


def save_window_geometry(data: bytes) -> None:
    settings = _load_settings_dict()
    settings["window_geometry"] = base64.b64encode(data).decode("ascii")
    _save_settings_dict(settings)


def load_mail_columns_state() -> bytes | None:
    value = _load_settings_dict().get("mail_columns_state")
    return base64.b64decode(value) if value else None


def save_mail_columns_state(data: bytes) -> None:
    settings = _load_settings_dict()
    settings["mail_columns_state"] = base64.b64encode(data).decode("ascii")
    _save_settings_dict(settings)


def load_open_archives() -> list[str]:
    value = _load_settings_dict().get("open_archives", [])
    return [str(p) for p in value] if isinstance(value, list) else []


def save_open_archives(paths: list[str]) -> None:
    data = _load_settings_dict()
    data["open_archives"] = paths
    _save_settings_dict(data)


def save_account(account: Account, smtp: SmtpAccount | None) -> None:
    """Сохраняет настройки подключения. Пароль — не в этом файле, а в
    keyring (и вовсе не сохраняется для auth_type="kerberos": SSO
    использует Kerberos-билет из ОС, пароль приложению не нужен)."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _account_dict(account, smtp)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if account.auth_type != "kerberos":
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

    auth_type = data.get("auth_type", "password")
    password = ""
    if auth_type != "kerberos":
        password = keyring.get_password(_KEYRING_SERVICE, username)
        if password is None:
            # Файл настроек есть, а пароля в хранилище секретов нет
            # (например, его отозвали или это другая машина) — просим
            # ввести заново. Для SSO (kerberos) пароль и не хранился —
            # сюда не попадаем.
            return None

    return _account_from_dict(data, username, password)


def _account_dict(account: Account, smtp: SmtpAccount | None) -> dict:
    return {
        "imap_host": account.host,
        "imap_port": account.port,
        "imap_use_ssl": account.use_ssl,
        "username": account.username,
        "auth_type": account.auth_type,
        "smtp_host": smtp.host if smtp else "",
        "smtp_port": smtp.port if smtp else 587,
        "smtp_use_ssl": smtp.use_ssl if smtp else False,
    }


def _account_from_dict(data: dict, username: str, password: str) -> tuple[Account, SmtpAccount | None]:
    auth_type = data.get("auth_type", "password")
    account = Account(
        host=data["imap_host"],
        username=username,
        password=password,
        port=data["imap_port"],
        use_ssl=data["imap_use_ssl"],
        auth_type=auth_type,
    )
    smtp = (
        SmtpAccount(
            host=data["smtp_host"],
            username=username,
            password=password,
            port=data["smtp_port"],
            use_ssl=data["smtp_use_ssl"],
            auth_type=auth_type,
        )
        if data.get("smtp_host")
        else None
    )
    return account, smtp


def _accounts_path() -> Path:
    return app_dir() / "accounts.json"


def load_accounts() -> list[tuple[Account, SmtpAccount | None]]:
    """Несколько одновременно подключённых учётных записей (жалоба:
    "несколько учётных записей одновременно — сейчас клиент держит только
    одно подключение"). Формат — отдельный файл со списком; если его ещё
    нет, но есть старый однозаписевый account.json — переносим его в новый
    формат один раз, не теряя уже сохранённое подключение."""
    path = _accounts_path()
    if not path.exists():
        single = load_account()
        return [single] if single else []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    result = []
    for entry in raw:
        username = entry.get("username") if isinstance(entry, dict) else None
        if not username:
            continue
        auth_type = entry.get("auth_type", "password") if isinstance(entry, dict) else "password"
        password = ""
        if auth_type != "kerberos":
            password = keyring.get_password(_KEYRING_SERVICE, username)
            if password is None:
                continue  # пароль недоступен (другая машина/отозван) — эту запись пропускаем, а не всё подряд
        try:
            result.append(_account_from_dict(entry, username, password))
        except KeyError:
            continue
    return result


def save_accounts(accounts: list[tuple[Account, SmtpAccount | None]]) -> None:
    path = _accounts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    for account, smtp in accounts:
        if account.auth_type != "kerberos":
            keyring.set_password(_KEYRING_SERVICE, account.username, account.password)
        entries.append(_account_dict(account, smtp))
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def _ews_accounts_path() -> Path:
    return app_dir() / "ews_accounts.json"


# Отдельный keyring-"сервис" от обычных IMAP-аккаунтов (_KEYRING_SERVICE) —
# ключ там username, здесь email, пространства имён не должны пересекаться,
# даже если у пользователя случайно совпадут username и email.
_EWS_KEYRING_SERVICE = "redmail-ews"


def load_ews_accounts() -> list[EwsAccount]:
    path = _ews_accounts_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    result = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        email = entry.get("email")
        if not email:
            continue
        auth_type = entry.get("auth_type", "basic")
        password = ""
        if auth_type != "kerberos":
            password = keyring.get_password(_EWS_KEYRING_SERVICE, email)
            if password is None:
                continue  # пароль недоступен (другая машина/отозван) — пропускаем эту запись
        try:
            result.append(
                EwsAccount(
                    email=email,
                    username=entry.get("username", ""),
                    password=password,
                    server=entry.get("server", ""),
                    auth_type=auth_type,
                )
            )
        except KeyError:
            continue
    return result


def save_ews_accounts(accounts: list[EwsAccount]) -> None:
    path = _ews_accounts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    for account in accounts:
        if account.auth_type != "kerberos":
            keyring.set_password(_EWS_KEYRING_SERVICE, account.email, account.password)
        entries.append(
            {
                "email": account.email,
                "username": account.username,
                "server": account.server,
                "auth_type": account.auth_type,
            }
        )
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
