"""Перенос профиля на другой компьютер и политика выгрузки данных.

Файл переноса .rmprofile — ZIP: настройки, базы почты, календаря, контактов
и категорий, архивы, свои оформления и manifest.json с контрольными суммами.
Пароли не переносятся: они в системном хранилище ключей этого компьютера, на
новом их вводят заново.

Загрузка профиля подменяет базы, которые открыты работающей программой,
поэтому она откладывается до следующего запуска (apply_pending_import
вызывается до открытия баз). Прежние данные не удаляются — переезжают в
резервную копию рядом с настройками.

Кто может выгружать переписку и переносить профиль, решает организация: если
администратор разложил фирменные оформления (branding), политика берётся из
них (поле data_export: "user" — сам сотрудник, "admin" — только
администратор, из командной строки от root). Без оформлений всё на откуп
пользователя. Это организационное правило, а не защита от копирования:
файлы профиля принадлежат пользователю.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable

from redmail import branding, profile
from redmail.applog import get_logger
from redmail.paths import app_dir

_log = get_logger("profile_transfer")
_audit_log = get_logger("audit")

FORMAT_VERSION = 1
EXTENSION = ".rmprofile"
MANIFEST = "manifest.json"

EXPORT_BY_USER = "user"
EXPORT_BY_ADMIN = "admin"

CONFIG_FILES = ("settings.json", "accounts.json", "ews_accounts.json", "account.json")
PROFILE_FILES = (profile.MAIL_DB, profile.CALENDAR_DB, profile.CONTACTS_DB, profile.TASKS_DB, "categories.sqlite3")
# Пути этого компьютера: на другом они не имеют смысла.
_LOCAL_SETTINGS = ("profile_dir", "archive_storage_dir")
_PENDING = "pending-import.json"
_ALLOWED_PREFIXES = ("config/", "profile/", "archives/", "brands/")


class TransferError(Exception):
    pass


# ---- политика ---------------------------------------------------------------------


def export_policy() -> tuple[str, branding.Brand | None]:
    """(режим, оформление, из которого он взят). Учитываются только
    оформления администратора: своё оформление пользователь мог бы и
    выключить, сменив тему."""
    system_brands = branding.load_brands([branding.brand_dirs()[0]])
    if not system_brands:
        return EXPORT_BY_USER, None
    strict = next((brand for brand in system_brands if brand.data_export == EXPORT_BY_ADMIN), None)
    if strict is not None:
        return EXPORT_BY_ADMIN, strict
    return EXPORT_BY_USER, system_brands[0]


def is_admin() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def allowed_here() -> bool:
    mode, _brand = export_policy()
    return mode == EXPORT_BY_USER or is_admin()


def audit(action: str, **details) -> None:
    """Запись о выгрузке или переносе: в журнал программы и в системный
    журнал (для администратора безопасности)."""
    actor = os.environ.get("SUDO_USER") or _user()
    text = f"{action}: " + ", ".join(f"{key}={value}" for key, value in details.items()) + f", выполнил={actor}"
    _audit_log.info(text)
    if sys.platform != "win32":
        try:
            import syslog

            syslog.openlog("redmail", 0, syslog.LOG_AUTH)
            syslog.syslog(syslog.LOG_NOTICE, text)
            syslog.closelog()
        except Exception as exc:
            _log.warning("Системный журнал недоступен: %s", exc)


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "?"


# ---- выгрузка --------------------------------------------------------------------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _snapshot(path: Path, temp_dir: Path) -> Path:
    """Согласованная копия базы, даже если программа сейчас в неё пишет."""
    if not _is_sqlite(path):
        return path
    copy = temp_dir / f"{len(list(temp_dir.iterdir()))}-{path.name}"
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=600)) as source, closing(
        sqlite3.connect(copy)
    ) as target:
        source.backup(target)
    return copy


def export_profile(dest: Path, *, progress: Callable[[str], None] | None = None) -> dict:
    from redmail import mail_export

    dest = Path(dest)
    if dest.suffix != EXTENSION:
        dest = dest.with_name(dest.name + EXTENSION)
    config = app_dir()
    profile_path = profile.profile_dir()
    entries: list[tuple[str, Path]] = []
    for name in CONFIG_FILES:
        if (config / name).is_file():
            entries.append((f"config/{name}", config / name))
    for name in PROFILE_FILES:
        if (profile_path / name).is_file():
            entries.append((f"profile/{name}", profile_path / name))
    archive_map: dict[str, str] = {}
    used: set[str] = set()
    for archive in mail_export.archive_files(_open_archives(config)):
        name = mail_export.safe_name(archive.stem) + ".rmarchive"
        while name in used:
            name = "_" + name
        used.add(name)
        archive_map[str(archive)] = f"archives/{name}"
        entries.append((f"archives/{name}", archive))
    brands_dir = branding.user_brands_dir()
    if brands_dir.is_dir():
        entries += [(f"brands/{path.name}", path) for path in sorted(brands_dir.glob("*.json"))]

    manifest = {
        "format": FORMAT_VERSION,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": socket.gethostname(),
        "user": _user(),
        "archive_paths": archive_map,
        "files": {},
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    with tempfile.TemporaryDirectory(prefix="redmail-export-") as temp:
        temp_dir = Path(temp)
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, allowZip64=True, compresslevel=1) as archive:
            for arcname, source in entries:
                if progress is not None:
                    progress(arcname)
                if arcname == "config/settings.json":
                    data = _read_json(source)
                    for key in _LOCAL_SETTINGS:
                        data.pop(key, None)
                    copy = temp_dir / "settings.json"
                    copy.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    source = copy
                else:
                    source = _snapshot(source, temp_dir)
                manifest["files"][arcname] = {"size": source.stat().st_size, "sha256": _sha256(source)}
                archive.write(source, arcname)
                if source.parent == temp_dir:
                    source.unlink()
            archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
    os.replace(partial, dest)
    audit("Выгрузка профиля", файл=dest, файлов=len(entries))
    return manifest


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _open_archives(config: Path) -> list[Path]:
    value = _read_json(config / "settings.json").get("open_archives", [])
    return [Path(item) for item in value if isinstance(item, str)] if isinstance(value, list) else []


# ---- загрузка --------------------------------------------------------------------


def _check_name(name: str) -> None:
    pure = PurePosixPath(name)
    if (
        name == MANIFEST
        or not name.startswith(_ALLOWED_PREFIXES)
        or "\\" in name
        or pure.is_absolute()
        or ".." in pure.parts
        or len(pure.parts) != 2
    ):
        raise TransferError(f"посторонний файл в переносе: {name}")


def read_manifest(src: Path) -> dict:
    """Проверка файла переноса без распаковки: формат и состав."""
    try:
        with zipfile.ZipFile(src) as archive:
            names = archive.namelist()
            if MANIFEST not in names:
                raise TransferError("это не файл переноса профиля (нет manifest.json)")
            manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
    except (OSError, zipfile.BadZipFile, ValueError, UnicodeDecodeError) as exc:
        raise TransferError(f"файл повреждён или не является переносом профиля: {exc}") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT_VERSION:
        raise TransferError("файл переноса другой версии программы")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise TransferError("в описании переноса нет списка файлов")
    for name in names:
        if name != MANIFEST:
            _check_name(name)
            if name not in files:
                raise TransferError(f"файл {name} не описан в manifest.json")
    for name in files:
        _check_name(name)
        if name not in names:
            raise TransferError(f"в переносе не хватает {name}")
    manifest["total_size"] = sum(int(item.get("size", 0)) for item in files.values())
    return manifest


def schedule_import(src: Path) -> dict:
    """Проверяет файл и откладывает загрузку до следующего запуска."""
    manifest = read_manifest(src)
    config = app_dir()
    config.mkdir(parents=True, exist_ok=True)
    (config / _PENDING).write_text(json.dumps({"path": str(Path(src).resolve())}, ensure_ascii=False), encoding="utf-8")
    audit("Запланирована загрузка профиля", файл=src, источник=f"{manifest.get('user')}@{manifest.get('host')}")
    return manifest


def pending_import() -> Path | None:
    data = _read_json(app_dir() / _PENDING)
    return Path(data["path"]) if isinstance(data.get("path"), str) else None


def cancel_pending_import() -> None:
    try:
        (app_dir() / _PENDING).unlink()
    except OSError:
        pass


def apply_pending_import(progress: Callable[[str], None] | None = None) -> Path | None:
    """При запуске, до открытия баз. Возвращает каталог резервной копии.
    Ошибка — TransferError; отложенная загрузка при этом снимается, чтобы
    программа не спотыкалась на каждом запуске."""
    src = pending_import()
    if src is None:
        return None
    cancel_pending_import()
    if not allowed_here():
        raise TransferError("перенос профиля выполняет администратор — загрузка отменена")
    if not src.is_file():
        raise TransferError(f"файл переноса не найден: {src}")
    return import_profile(src, progress=progress)


def import_profile(src: Path, *, progress: Callable[[str], None] | None = None) -> Path:
    """Загружает профиль. Программа не должна быть запущена. Возвращает
    каталог, куда сохранены прежние данные."""
    manifest = read_manifest(src)
    config = app_dir()
    config.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    staging = config / f"import-{stamp}"
    backup = config / f"backup-before-import-{stamp}"
    # 1. Распаковка во временный каталог со сверкой сумм — до этого момента
    #    текущие данные не тронуты.
    try:
        with zipfile.ZipFile(src) as archive:
            for name, info in manifest["files"].items():
                if progress is not None:
                    progress(name)
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with archive.open(name) as source, open(target, "wb") as out:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                        out.write(chunk)
                if digest.hexdigest() != info.get("sha256"):
                    raise TransferError(f"контрольная сумма не сошлась: {name}")
    except (OSError, zipfile.BadZipFile) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise TransferError(f"распаковка не удалась: {exc}") from None
    except TransferError:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    # 2. Прежние данные — в резервную копию.
    new_profile = profile.default_profile_dir()
    old_profile = profile.profile_dir()
    backup.mkdir(parents=True)
    for name in CONFIG_FILES:
        if (config / name).exists():
            shutil.move(str(config / name), str(backup / name))
    if old_profile.exists():
        shutil.move(str(old_profile), str(backup / "profile"))
    if new_profile.exists():  # профиль был в другом месте, а здесь что-то осталось
        shutil.move(str(new_profile), str(backup / "profile-default"))
    brands_dir = branding.user_brands_dir()
    if (staging / "brands").is_dir() and brands_dir.exists():
        shutil.move(str(brands_dir), str(backup / "brands"))

    # 3. Новые данные на место.
    new_profile.mkdir(parents=True)
    for path in (staging / "profile").glob("*") if (staging / "profile").is_dir() else []:
        shutil.move(str(path), str(new_profile / path.name))
    archive_paths: dict[str, str] = {}
    if (staging / "archives").is_dir():
        archives_dir = new_profile / profile.ARCHIVES_DIR
        archives_dir.mkdir(parents=True, exist_ok=True)
        for path in (staging / "archives").glob("*"):
            shutil.move(str(path), str(archives_dir / path.name))
        for original, arcname in (manifest.get("archive_paths") or {}).items():
            archive_paths[original] = str(archives_dir / PurePosixPath(arcname).name)
    for path in (staging / "config").glob("*") if (staging / "config").is_dir() else []:
        shutil.move(str(path), str(config / path.name))
    if (staging / "brands").is_dir():
        brands_dir.mkdir(parents=True, exist_ok=True)
        for path in (staging / "brands").glob("*"):
            shutil.move(str(path), str(brands_dir / path.name))
    shutil.rmtree(staging, ignore_errors=True)
    _relocate_archives(config, new_profile, archive_paths)
    profile.set_profile_dir_override(None)
    _chown_like_parent(config, [config / name for name in CONFIG_FILES] + [new_profile, brands_dir, backup])
    audit(
        "Загрузка профиля", файл=src, источник=f"{manifest.get('user')}@{manifest.get('host')}",
        резервная_копия=backup,
    )
    return backup


def _relocate_archives(config: Path, new_profile: Path, archive_paths: dict[str, str]) -> None:
    settings_path = config / "settings.json"
    data = _read_json(settings_path)
    if data:
        data.pop("profile_dir", None)
        data.pop("archive_storage_dir", None)
        opened = data.get("open_archives")
        if isinstance(opened, list):
            data["open_archives"] = [archive_paths.get(item, item) for item in opened if isinstance(item, str)]
        settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    mail_db = new_profile / profile.MAIL_DB
    if archive_paths and mail_db.is_file():
        with closing(sqlite3.connect(mail_db)) as conn:
            try:
                conn.executemany(
                    "UPDATE messages SET archive_path = ? WHERE archive_path = ?",
                    [(new, old) for old, new in archive_paths.items()],
                )
                conn.commit()
            except sqlite3.OperationalError as exc:  # старая база без столбца архива
                _log.warning("Ссылки на архивы не перенесены: %s", exc)


def _chown_like_parent(config: Path, paths: list[Path]) -> None:
    """Администратор загружает профиль от root — файлы должны остаться у
    владельца каталога настроек, иначе программа пользователя их не откроет."""
    if not is_admin():
        return
    stat = config.parent.stat()  # ~/.config — его создал сам пользователь
    for top in paths:
        if not top.exists():
            continue
        for path in [top, *top.rglob("*")] if top.is_dir() else [top]:
            try:
                os.chown(path, stat.st_uid, stat.st_gid, follow_symlinks=False)
            except OSError as exc:
                _log.warning("Владелец %s не изменён: %s", path, exc)
