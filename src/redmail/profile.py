"""Профиль пользователя — каталог с локальными базами (по образцу профиля
Outlook: OST/PST лежат в одном настраиваемом месте).

Договорённость с пользователем (переход на хранение «как в Outlook»):
профиль — это папка, внутри три SQLite-базы (почта, календарь, контакты)
и архивы; путь настраивается (на тестовой машине был инцидент с диском —
базы должны уметь жить не только в ~/.config).

    <профиль>/mail.sqlite3          — полная локальная копия ящиков (аналог OST)
    <профиль>/calendar.rmcal        — календари и события
    <профиль>/contacts.rmcontacts   — адресная книга
    <профиль>/archives/             — архивы .rmarchive по умолчанию

Старые файлы из ~/.config/redmail (cache.sqlite3, calendar.rmcal,
contacts.rmcontacts) переезжают в профиль при первом запуске, если в
профиле их ещё нет — данные пользователя не теряются.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from redmail.applog import get_logger
from redmail.paths import app_dir

_log = get_logger("profile")

MAIL_DB = "mail.sqlite3"
CALENDAR_DB = "calendar.rmcal"
CONTACTS_DB = "contacts.rmcontacts"
ARCHIVES_DIR = "archives"

_LEGACY_FILES = {
    "cache.sqlite3": MAIL_DB,
    "calendar.rmcal": CALENDAR_DB,
    "contacts.rmcontacts": CONTACTS_DB,
}

_override: Path | None = None


def default_profile_dir() -> Path:
    return app_dir() / "profile"


def _settings_path() -> Path:
    return app_dir() / "settings.json"


def load_profile_dir() -> Path:
    """Каталог профиля из настроек (ключ profile_dir) или по умолчанию.
    Читается напрямую из settings.json, а не через config_store — тот сам
    импортирует хранилища, и получился бы цикл импортов."""
    if _override is not None:
        return _override
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
        value = data.get("profile_dir", "")
    except (OSError, ValueError):
        value = ""
    return Path(value) if value else default_profile_dir()


def set_profile_dir_override(path: Path | None) -> None:
    """Для тестов и для смены каталога без перезапуска."""
    global _override
    _override = path


def profile_dir() -> Path:
    return load_profile_dir()


def mail_db_path() -> Path:
    return profile_dir() / MAIL_DB


def calendar_db_path() -> Path:
    return profile_dir() / CALENDAR_DB


def contacts_db_path() -> Path:
    return profile_dir() / CONTACTS_DB


def archives_dir() -> Path:
    return profile_dir() / ARCHIVES_DIR


def ensure_profile() -> Path:
    """Создаёт каталог профиля и переносит в него старые базы из
    ~/.config/redmail (один раз: только если в профиле файла ещё нет).
    Возвращает путь к профилю."""
    target = profile_dir()
    target.mkdir(parents=True, exist_ok=True)
    legacy_dir = app_dir()
    for old_name, new_name in _LEGACY_FILES.items():
        old_path = legacy_dir / old_name
        new_path = target / new_name
        if old_path.exists() and not new_path.exists():
            try:
                shutil.move(str(old_path), str(new_path))
                _log.info("Профиль: %s перенесён в %s", old_path, new_path)
            except OSError as exc:
                _log.error("Профиль: не удалось перенести %s: %s", old_path, exc)
    return target


def profile_size_bytes() -> int:
    total = 0
    try:
        for path in profile_dir().rglob("*"):
            if path.is_file():
                total += path.stat().st_size
    except OSError:
        pass
    return total
