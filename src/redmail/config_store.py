from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import keyring  # noqa: F401 - тесты подменяют keyring.get_password/set_password

from redmail import secret_store

from redmail.ews_client import EwsAccount
from redmail.imap_client import Account
from redmail.paths import app_dir
from redmail.smtp_client import SmtpAccount

_KEYRING_SERVICE = "redmail"


_DEFAULT_POLL_INTERVAL_MINUTES = 5
_DEFAULT_PANE_ORIENTATION = "vertical"
_DEFAULT_FONT_SCALE = 1.0
_DEFAULT_THEME = "light"


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


def load_theme() -> str:
    """Своя тема (светлая/тёмная), не зависящая от темы рабочего стола/GTK
    хоста (жалоба: "сделай фон программы независимым") — раньше приложение
    вообще не задавало стиль, и его вид определялся тем, что было настроено
    в системе, вплоть до нечитаемых сочетаний (тёмная система + не
    предусмотренные под неё жёстко белые фоны в календаре и т.п.)."""
    value = _load_settings_dict().get("theme", _DEFAULT_THEME)
    return value if value in ("light", "dark") else _DEFAULT_THEME


def save_theme(theme: str) -> None:
    data = _load_settings_dict()
    data["theme"] = theme
    _save_settings_dict(data)


_MAIL_VIEW_MODES = ("table", "cards")

# ---- Хранилище (профиль, аналог OST/PST) --------------------------------------
DEFAULT_BODY_MAX_SIZE_MB = 25
DEFAULT_AUTO_ARCHIVE_SIZE_MB = 500


def load_profile_dir() -> Path:
    from redmail import profile

    return profile.load_profile_dir()


def save_profile_dir(directory: Path | None) -> None:
    """Пустое значение — вернуться к каталогу по умолчанию."""
    data = _load_settings_dict()
    data["profile_dir"] = str(directory) if directory else ""
    _save_settings_dict(data)


def load_body_max_size_mb() -> int:
    """Письма больше этого размера не скачиваются фоном — только при
    открытии (договорённость: вложения > 25 МБ по запросу; порог
    настраивается в обе стороны)."""
    try:
        value = int(_load_settings_dict().get("body_max_size_mb", DEFAULT_BODY_MAX_SIZE_MB))
    except (TypeError, ValueError):
        value = DEFAULT_BODY_MAX_SIZE_MB
    return max(1, value)


def save_body_max_size_mb(value: int) -> None:
    data = _load_settings_dict()
    data["body_max_size_mb"] = max(1, int(value))
    _save_settings_dict(data)


def load_auto_archive_enabled() -> bool:
    return bool(_load_settings_dict().get("auto_archive_enabled", True))


def save_auto_archive_enabled(enabled: bool) -> None:
    data = _load_settings_dict()
    data["auto_archive_enabled"] = bool(enabled)
    _save_settings_dict(data)


def load_auto_archive_delete_on_server() -> bool:
    """Удалять ли письма с сервера после переноса в архив. По умолчанию
    НЕТ: автоархив только освобождает локальную базу."""
    return bool(_load_settings_dict().get("auto_archive_delete_on_server", False))


def save_auto_archive_delete_on_server(enabled: bool) -> None:
    data = _load_settings_dict()
    data["auto_archive_delete_on_server"] = bool(enabled)
    _save_settings_dict(data)


def load_auto_archive_confirmed() -> list[str]:
    value = _load_settings_dict().get("auto_archive_confirmed", [])
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def save_auto_archive_confirmed(keys: list[str]) -> None:
    data = _load_settings_dict()
    data["auto_archive_confirmed"] = list(keys)
    _save_settings_dict(data)


def load_auto_archive_size_mb() -> int:
    try:
        value = int(_load_settings_dict().get("auto_archive_size_mb", DEFAULT_AUTO_ARCHIVE_SIZE_MB))
    except (TypeError, ValueError):
        value = DEFAULT_AUTO_ARCHIVE_SIZE_MB
    return max(50, value)


def save_auto_archive_size_mb(value: int) -> None:
    data = _load_settings_dict()
    data["auto_archive_size_mb"] = max(50, int(value))
    _save_settings_dict(data)


def load_mail_view_mode() -> str:
    """Режим списка писем: "table" — колонки (как раньше), "cards" — плитки
    с аватаром/двумя строками (по дизайн-референсу; договорённость —
    два режима с переключателем)."""
    value = _load_settings_dict().get("mail_view_mode", "table")
    return value if value in _MAIL_VIEW_MODES else "table"


def save_mail_view_mode(mode: str) -> None:
    data = _load_settings_dict()
    data["mail_view_mode"] = mode if mode in _MAIL_VIEW_MODES else "table"
    _save_settings_dict(data)


def load_thread_grouping() -> bool:
    """Группировка писем одной темы в списке: показывается последнее письмо
    цепочки, остальные скрыты под раскрывающимся значком."""
    return bool(_load_settings_dict().get("thread_grouping", True))


def save_thread_grouping(enabled: bool) -> None:
    data = _load_settings_dict()
    data["thread_grouping"] = bool(enabled)
    _save_settings_dict(data)


@dataclass
class Signature:
    id: str
    name: str
    body_html: str
    # Картинки, вставленные в подпись (жалоба: "редактор подписи не даёт
    # вставлять картинку... сделай как при создании письма") — тот же приём
    # cid-ссылок, что у черновиков/писем: body_html ссылается на них как
    # <img src="cid:...">, а сами байты хранятся отдельно.
    inline_images: dict[str, tuple[str, bytes]] = field(default_factory=dict)


def load_signatures() -> list[Signature]:
    """Подписи для писем (жалоба: "нет возможности задать подпись или
    несколько подписей и выбрать нужную") — хранятся тем же способом, что
    и MailRule выше: список словарей в settings.json, повреждённые записи
    молча пропускаются, а не валят загрузку всех остальных."""
    raw = _load_settings_dict().get("signatures", [])
    if not isinstance(raw, list):
        return []
    signatures = []
    for item in raw:
        try:
            inline_images = {}
            for cid, entry in (item.get("inline_images") or {}).items():
                inline_images[cid] = (entry["content_type"], base64.b64decode(entry["data"]))
            signatures.append(
                Signature(id=item["id"], name=item["name"], body_html=item["body_html"], inline_images=inline_images)
            )
        except (TypeError, KeyError):
            continue
    return signatures


def save_signatures(signatures: list[Signature]) -> None:
    data = _load_settings_dict()
    data["signatures"] = [
        {
            "id": sig.id,
            "name": sig.name,
            "body_html": sig.body_html,
            "inline_images": {
                cid: {"content_type": content_type, "data": base64.b64encode(payload).decode("ascii")}
                for cid, (content_type, payload) in sig.inline_images.items()
            },
        }
        for sig in signatures
    ]
    _save_settings_dict(data)


def load_default_signature_id() -> str | None:
    value = _load_settings_dict().get("default_signature_id")
    return value if isinstance(value, str) else None


def save_default_signature_id(signature_id: str | None) -> None:
    data = _load_settings_dict()
    data["default_signature_id"] = signature_id
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


def default_archive_storage_dir() -> Path:
    """Каталог архивов по умолчанию — в профиле рядом с базами (договорённость
    по хранилищу); явно заданный в настройках каталог сохраняется."""
    from redmail import profile

    return profile.archives_dir()


def load_archive_storage_dir() -> Path:
    """Каталог, куда автоматически кладутся новые архивы при импорте
    .pst/mbox/Maildir — раньше при первом импорте приходилось руками
    выбирать полный путь к файлу через диалог сохранения; теперь
    спрашиваем только имя (см. _prompt_new_archive_name в main_window.py),
    а каталог настраивается один раз здесь."""
    value = _load_settings_dict().get("archive_storage_dir", "")
    return Path(value) if value else default_archive_storage_dir()


def save_archive_storage_dir(directory: Path) -> None:
    data = _load_settings_dict()
    data["archive_storage_dir"] = str(directory)
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


def load_compose_geometry() -> bytes | None:
    """Размер и положение окна письма (написать/ответить/переслать) —
    запоминаются между открытиями (жалоба: "размер окна пересылки и
    создания всегда один, не запоминается изменение и очень маленькое")."""
    value = _load_settings_dict().get("compose_geometry")
    return base64.b64decode(value) if value else None


def save_compose_geometry(data: bytes) -> None:
    settings = _load_settings_dict()
    settings["compose_geometry"] = base64.b64encode(data).decode("ascii")
    _save_settings_dict(settings)


def load_mail_columns_state() -> bytes | None:
    value = _load_settings_dict().get("mail_columns_state")
    return base64.b64decode(value) if value else None


def save_mail_columns_state(data: bytes) -> None:
    settings = _load_settings_dict()
    settings["mail_columns_state"] = base64.b64encode(data).decode("ascii")
    _save_settings_dict(settings)


def load_mail_date_column_pinned() -> bool:
    return bool(_load_settings_dict().get("mail_date_column_pinned", False))


def save_mail_date_column_pinned(pinned: bool) -> None:
    settings = _load_settings_dict()
    settings["mail_date_column_pinned"] = pinned
    _save_settings_dict(settings)


def load_mail_splitters_state() -> dict[str, bytes] | None:
    value = _load_settings_dict().get("mail_splitters_state")
    if not value:
        return None
    return {key: base64.b64decode(encoded) for key, encoded in value.items()}


def save_mail_splitters_state(state: dict[str, bytes]) -> None:
    settings = _load_settings_dict()
    settings["mail_splitters_state"] = {key: base64.b64encode(data).decode("ascii") for key, data in state.items()}
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
        secret_store.set_password(_KEYRING_SERVICE, account.username, account.password)


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
        password = secret_store.get_password(_KEYRING_SERVICE, username)
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
            password = secret_store.get_password(_KEYRING_SERVICE, username)
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
            secret_store.set_password(_KEYRING_SERVICE, account.username, account.password)
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
            password = secret_store.get_password(_EWS_KEYRING_SERVICE, email)
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
            secret_store.set_password(_EWS_KEYRING_SERVICE, account.email, account.password)
        entries.append(
            {
                "email": account.email,
                "username": account.username,
                "server": account.server,
                "auth_type": account.auth_type,
            }
        )
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
