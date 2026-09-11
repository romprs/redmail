"""Полная синхронизация ящика с локальной базой (аналог OST в Outlook).

Договорённость с пользователем: основная база — ПОЛНАЯ копия ящика.
Заголовки всех папок забираются сразу, тела писем докачиваются в фоне от
новых к старым, письма больше порога (по умолчанию 25 МБ) — только по
запросу при открытии. Удалённое на сервере удаляется локально (зеркало),
удалённое локально удаляется на сервере (это уже делает CachedMailbox).

Всё работает порциями (chunk), чтобы не держать IMAP-сессию (она
выполняет команды по одной, см. ImapSession) занятой надолго: между
порциями успевают пройти действия пользователя — открыть письмо, отметить
прочитанным. Прогресс сообщается через колбэк, остановка — через
threading.Event.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from redmail import cache_store
from redmail.applog import get_logger

_log = get_logger("sync")

HEADER_CHUNK = 250
FLAGS_CHUNK = 2000
BODY_BATCH = 20
DEFAULT_BODY_MAX_BYTES = 25 * 1024 * 1024

ProgressCallback = Callable[[str, int, int], None]  # (текст, сделано, всего); всего=0 — неизвестно


@dataclass
class FolderSyncResult:
    folder: str
    total: int = 0
    added: int = 0
    deleted: int = 0
    flags_updated: int = 0
    reset: bool = False  # UIDVALIDITY сменился — локальная копия папки перестроена


@dataclass
class SyncStats:
    folders: list[FolderSyncResult] = field(default_factory=list)
    bodies_downloaded: int = 0

    @property
    def added(self) -> int:
        return sum(f.added for f in self.folders)

    @property
    def deleted(self) -> int:
        return sum(f.deleted for f in self.folders)


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _stopped(stop: threading.Event | None) -> bool:
    return stop is not None and stop.is_set()


def _report(progress: ProgressCallback | None, text: str, done: int, total: int) -> None:
    if progress is not None:
        try:
            progress(text, done, total)
        except Exception:
            pass  # индикатор не должен ронять синхронизацию


def sync_folder_headers(
    session,
    account_key: str,
    folder: str,
    *,
    progress: ProgressCallback | None = None,
    stop: threading.Event | None = None,
    chunk: int = HEADER_CHUNK,
) -> FolderSyncResult:
    """Приводит локальный список писем папки к серверному: новые
    заголовки скачиваются (от новых к старым), пропавшие на сервере
    удаляются локально, флаги (прочитано/отвечено/маркер) у остальных
    обновляются. Тела не трогает."""
    result = FolderSyncResult(folder=folder)
    uidvalidity, exists = session.folder_status(folder)
    local_validity = cache_store.get_folder_uidvalidity(account_key, folder)
    if local_validity and uidvalidity and local_validity != uidvalidity:
        # Сервер перевыдал UID (пересоздание папки, миграция) — старые
        # локальные UID больше ничего не значат.
        _log.warning("Папка %s: UIDVALIDITY %s → %s, локальная копия перестраивается", folder, local_validity, uidvalidity)
        cache_store.delete_folder(account_key, folder)
        result.reset = True

    server_uids = set(session.search_uids(folder))
    local_uids = cache_store.get_folder_uids(account_key, folder)
    result.total = len(server_uids)

    missing_on_server = sorted(local_uids - server_uids)
    if missing_on_server:
        cache_store.delete_messages(account_key, folder, missing_on_server)
        result.deleted = len(missing_on_server)

    new_uids = sorted(server_uids - local_uids, reverse=True)
    done = 0
    for part in _chunks(new_uids, chunk):
        if _stopped(stop):
            return result
        summaries = session.fetch_summaries_by_uids(folder, part)
        cache_store.upsert_summaries(account_key, folder, summaries)
        done += len(part)
        result.added += len(summaries)
        _report(progress, f"{folder}: заголовки {done}/{len(new_uids)}", done, len(new_uids))

    existing = sorted(server_uids & local_uids)
    if existing:
        local_flags = cache_store.get_folder_flags(account_key, folder)
        changes: list[tuple[int, bool, bool, str | None]] = []
        for part in _chunks(existing, FLAGS_CHUNK):
            if _stopped(stop):
                break
            for uid, (is_read, is_answered, server_marker) in session.fetch_flags(folder, part).items():
                old = local_flags.get(uid)
                if old is None:
                    continue
                old_read, old_answered, old_marker = old
                # Сервер знает, ЕСТЬ ли маркер (\Flagged); цвет помнит
                # локальная база (VK не хранит keyword-флаги).
                if server_marker is None:
                    marker = None
                elif old_marker:
                    marker = old_marker
                else:
                    marker = server_marker
                if (is_read, is_answered, marker) != (old_read, old_answered, old_marker):
                    changes.append((uid, is_read, is_answered, marker))
        if changes:
            cache_store.update_flags(account_key, folder, changes)
            result.flags_updated = len(changes)

    cache_store.set_folder_state(account_key, folder, uidvalidity=uidvalidity, exists_count=exists, headers_complete=not _stopped(stop))
    _log.info(
        "Папка %s: на сервере %d, новых %d, удалено %d, флагов обновлено %d",
        folder, result.total, result.added, result.deleted, result.flags_updated,
    )
    return result


def sync_all_folders(
    session,
    account_key: str,
    folders: list[str],
    *,
    progress: ProgressCallback | None = None,
    stop: threading.Event | None = None,
) -> SyncStats:
    stats = SyncStats()
    for index, folder in enumerate(folders):
        if _stopped(stop):
            break
        _report(progress, f"Папка {folder} ({index + 1}/{len(folders)})", index, len(folders))
        try:
            stats.folders.append(sync_folder_headers(session, account_key, folder, progress=progress, stop=stop))
        except Exception as exc:
            _log.error("Папка %s: синхронизация заголовков не удалась: %s", folder, exc)
    return stats


def download_bodies(
    session,
    account_key: str,
    *,
    max_bytes: int = DEFAULT_BODY_MAX_BYTES,
    batch: int = BODY_BATCH,
    progress: ProgressCallback | None = None,
    stop: threading.Event | None = None,
    limit: int | None = None,
) -> int:
    """Докачивает тела писем, которых ещё нет локально, от новых к старым.
    Письма больше max_bytes помечаются как отложенные (скачаются при
    открытии). Возвращает число скачанных."""
    downloaded = 0
    # Большие письма сразу помечаются отложенными — независимо от того,
    # дойдёт ли этот проход до конца (limit/stop).
    cache_store.defer_large_messages(account_key, max_bytes)
    total_pending = cache_store.count_messages_without_body(account_key, max_bytes)
    while not _stopped(stop):
        pending = cache_store.messages_without_body(account_key, max_bytes, limit=batch)
        if not pending:
            break
        for folder, uid, size in pending:
            if _stopped(stop):
                break
            try:
                content = session.fetch_message_content(folder, uid)
            except Exception as exc:
                _log.warning("Письмо %s/%d: тело не скачано (%s), отложено", folder, uid, exc)
                cache_store.set_body_state(account_key, folder, uid, "deferred")
                continue
            cache_store.save_message_content(account_key, folder, uid, content)
            downloaded += 1
            if limit is not None and downloaded >= limit:
                _report(progress, f"Загрузка писем: {downloaded}/{total_pending}", downloaded, total_pending)
                return downloaded
        _report(progress, f"Загрузка писем: {downloaded}/{total_pending}", downloaded, total_pending)
    if downloaded:
        _log.info("Скачано тел писем: %d", downloaded)
    return downloaded
