"""Автоархив по размеру локальной базы (аналог автоархивации Outlook).

Договорённость с пользователем: почтовые ящики ограничены по объёму, их
надо чистить — для этого и нужен локальный архив. Когда база почты
(mail.sqlite3) превышает порог (по умолчанию 500 МБ, настраивается),
самые старые письма переносятся в файл архива .rmarchive и удаляются с
сервера. Порядок строгий: (1) письмо целиком (RFC 822) скачивается с
сервера, (2) записывается в архив, (3) ПРОВЕРЯЕТСЯ чтением из архива,
(4) только потом удаляется на сервере, (5) в основной базе строка не
удаляется, а помечается «в архиве» с указателем на файл и id — единый
индекс: список папки показывает и архивные письма, тело старого письма
читается из архива.

Архивные файлы ротируются по размеру (тот же порог): когда текущий файл
перерос порог, начинается следующий. Папки «Корзина», «Спам»,
«Черновики» не архивируются.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from redmail import archive_store, cache_store
from redmail.applog import get_logger

_log = get_logger("autoarchive")

# После превышения порога архивируем, пока не освободим столько, чтобы
# база стала меньше TARGET_RATIO × порога — иначе автоархив дёргался бы на
# каждом новом письме.
TARGET_RATIO = 0.8
BATCH = 50
# Раунд автоархива — примерно один файл архива (порог, ~500 МБ): пока база
# меньше порога, ничего не делаем; стала больше — переносим самые старые
# письма объёмом в порог, затем сжимаем базу (предложение пользователя:
# "до 500 МБ ничего не делаем, стала больше — переносим 500 МБ в архив,
# а основную базу сжимаем до минимума"). ROUND — страховочный предел по
# числу писем.
ROUND = 20000
_SKIP_HINTS = ("trash", "корзин", "spam", "junk", "спам", "draft", "черновик")

ProgressCallback = Callable[[str, int, int], None]


@dataclass
class ArchivePlan:
    account_key: str
    db_bytes: int
    threshold_bytes: int
    to_free_bytes: int
    candidates: list[tuple[str, int, int, str]] = field(default_factory=list)  # (folder, uid, size, date)

    @property
    def total_bytes(self) -> int:
        return sum(size for _f, _u, size, _d in self.candidates)

    @property
    def count(self) -> int:
        return len(self.candidates)

    @property
    def oldest_date(self) -> str:
        return min((d for *_rest, d in self.candidates), default="")

    @property
    def newest_date(self) -> str:
        return max((d for *_rest, d in self.candidates), default="")


@dataclass
class ArchiveResult:
    archived: int = 0
    bytes_freed: int = 0
    failed: int = 0
    files: list[str] = field(default_factory=list)


def _skip_folder(folder: str, extra_skip: set[str]) -> bool:
    if folder in extra_skip:
        return True
    lowered = folder.lower()
    return any(hint in lowered for hint in _SKIP_HINTS)


def make_plan(account_key: str, threshold_bytes: int, *, skip_folders: set[str] | None = None) -> ArchivePlan:
    """Что архивировать, чтобы база ушла ниже TARGET_RATIO × порога. Пустой
    список — архивировать нечего (база меньше порога или нет кандидатов)."""
    db_bytes = cache_store.storage_stats()["db_bytes"]
    plan = ArchivePlan(account_key=account_key, db_bytes=db_bytes, threshold_bytes=threshold_bytes, to_free_bytes=0)
    if db_bytes <= threshold_bytes:
        return plan
    # Раунд не меньше порога (один файл архива ~500 МБ) и не меньше, чем
    # нужно, чтобы база ушла ниже TARGET_RATIO × порога.
    plan.to_free_bytes = max(threshold_bytes, db_bytes - int(threshold_bytes * TARGET_RATIO))
    skip = skip_folders or set()
    freed = 0
    for folder, uid, size, date in cache_store.oldest_messages(account_key):
        if _skip_folder(folder, skip):
            continue
        plan.candidates.append((folder, uid, size, date))
        freed += max(size, 1)
        if freed >= plan.to_free_bytes or len(plan.candidates) >= ROUND:
            break
    return plan


def _archive_file(archive_dir: Path, account_key: str, threshold_bytes: int) -> Path:
    """Текущий файл автоархива для учётной записи; новый, когда прежний
    перерос порог (ротация по размеру)."""
    safe = "".join(ch if ch.isalnum() or ch in "._-@" else "_" for ch in account_key)
    archive_dir.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        path = archive_dir / f"autoarchive-{safe}-{index:03d}.rmarchive"
        if not path.exists():
            archive_store.create_archive(path)
            return path
        if path.stat().st_size < threshold_bytes:
            return path
        index += 1


def relocate_archives(old_dir: Path, new_dir: Path) -> int:
    """Переносит файлы автоархива из старого каталога в каталог профиля
    (договорённость: архивы живут в профиле рядом с базами; первые сборки
    писали их в «Каталог для новых архивов») и обновляет указатели в
    индексе. Возвращает число перенесённых файлов."""
    try:
        old_dir = Path(old_dir)
        new_dir = Path(new_dir)
        if not old_dir.exists() or old_dir.resolve() == new_dir.resolve():
            return 0
    except OSError:
        return 0
    moved = 0
    for path in sorted(old_dir.glob("autoarchive-*.rmarchive")):
        target = new_dir / path.name
        if target.exists():
            continue
        try:
            new_dir.mkdir(parents=True, exist_ok=True)
            os.replace(path, target) if path.stat().st_dev == new_dir.stat().st_dev else _move_across(path, target)
        except OSError as exc:
            _log.error("Автоархив: не удалось перенести %s в %s: %s", path, target, exc)
            continue
        rows = cache_store.relocate_archive_path(str(path), str(target))
        _log.info("Автоархив: %s перенесён в %s (обновлено ссылок: %d)", path, target, rows)
        moved += 1
    return moved


def _move_across(src: Path, dst: Path) -> None:
    import shutil

    shutil.copy2(src, dst)
    src.unlink()


def run(
    mailbox,
    plan: ArchivePlan,
    archive_dir: Path,
    *,
    progress: ProgressCallback | None = None,
    stop: threading.Event | None = None,
    delete_on_server: bool = False,
    full_vacuum: bool = False,
) -> ArchiveResult:
    """Выполняет план: по одному письму — скачать целиком, записать в
    архив, проверить, пометить в индексе; удалить на сервере — только если
    delete_on_server (отдельная галочка в настройках, по умолчанию
    выключена: пользователь усомнился — «что значит удаляет?»). Без неё
    автоархив лишь освобождает локальную базу, сервер не трогает."""
    result = ArchiveResult()
    if not plan.candidates:
        return result
    current = _archive_file(archive_dir, plan.account_key, plan.threshold_bytes)
    result.files.append(str(current))
    written_to_current = 0
    for index, (folder, uid, size, _date) in enumerate(plan.candidates, 1):
        if stop is not None and stop.is_set():
            break
        try:
            raw = mailbox.message_raw(folder, uid, background=True)
            if not raw:
                raise ValueError("сервер вернул пустое письмо")
            if written_to_current and current.stat().st_size >= plan.threshold_bytes:
                # Ротация по размеру — только после того, как в текущий файл
                # что-то записано (свежий пустой файл сам по себе больше нуля).
                current = _archive_file(archive_dir, plan.account_key, plan.threshold_bytes)
                result.files.append(str(current))
                written_to_current = 0
            archive_uid = archive_store.append_raw_message(current, folder, raw)
            written_to_current += 1
            # Проверка: письмо читается из архива и совпадает по размеру.
            stored = archive_store.get_message_raw(current, archive_uid)
            if len(stored) != len(raw):
                archive_store.delete_messages(current, [archive_uid])
                raise ValueError("письмо в архиве не совпадает с исходным")
            if delete_on_server:
                mailbox.delete_on_server(folder, [uid])
            cache_store.mark_archived(plan.account_key, folder, uid, str(current), archive_uid)
            result.archived += 1
            result.bytes_freed += size
            if result.archived % 50 == 0 and not full_vacuum:
                try:
                    cache_store.vacuum(stop)
                except Exception as exc:
                    _log.warning("Ужатие базы по ходу автоархива не удалось: %s", exc)
        except Exception as exc:
            result.failed += 1
            _log.error("Автоархив %s/%d: %s", folder, uid, exc)
            if result.failed >= 20 and result.archived == 0:
                _log.error("Автоархив остановлен: подряд не удались 20 писем")
                break
        if progress is not None and (index % 10 == 0 or index == len(plan.candidates)):
            try:
                progress(f"Автоархив: {index}/{len(plan.candidates)}", index, len(plan.candidates))
            except Exception:
                pass
    if result.archived:
        try:
            if full_vacuum:
                freed_bytes = cache_store.full_vacuum()
                _log.info("Автоархив: база сжата полностью, освобождено ~%.0f МБ", freed_bytes / (1024 * 1024))
            else:
                freed_pages = cache_store.vacuum(stop)
                if freed_pages:
                    _log.info("Автоархив: база ужата на %d страниц (~%.0f МБ)", freed_pages, freed_pages * 4096 / (1024 * 1024))
        except Exception as exc:
            _log.warning("Ужатие базы после автоархива не удалось: %s", exc)
        _log.info(
            "Автоархив %s: перенесено %d писем (%.1f МБ), с сервера %s, ошибок %d, файлы: %s",
            plan.account_key, result.archived, result.bytes_freed / (1024 * 1024),
            "удалены" if delete_on_server else "НЕ удалялись", result.failed, ", ".join(result.files),
        )
    return result
