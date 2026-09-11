from __future__ import annotations

import threading
from pathlib import Path

from redmail import archive_store, cache_store, sync_engine
from redmail.applog import get_logger
from redmail.imap_client import UNKNOWN_MARKER, Account, ImapSession, MessageContent, MessageSummary

_log = get_logger("mailbox")


class CachedMailbox:
    """Ящик поверх локальной копии (аналог OST): интерфейс читает только
    из базы, сервер опрашивается синхронизацией.

    folder_summaries() — чистое чтение из базы, сети не касается.
    refresh_folder() — синхронизация заголовков папки с сервером
    (sync_engine.sync_folder_headers): новые письма добавляются, удалённые
    на сервере удаляются локально, флаги обновляются; тела при этом не
    скачиваются. Первое открытие ещё не синхронизированной папки тоже
    идёт через refresh_folder.

    message_content() — тело из базы; если его ещё нет (фон не успел или
    письмо большое и отложено) — скачивается сейчас и сохраняется.

    sync_all()/download_bodies() — полная фоновая синхронизация (все папки,
    затем тела от новых к старым), см. sync_engine.
    """

    def __init__(self, session: ImapSession, account: Account, *, body_max_bytes: int = sync_engine.DEFAULT_BODY_MAX_BYTES):
        self.session = session
        self._account_key = f"{account.host}:{account.username}"
        self.body_max_bytes = body_max_bytes
        # Одна синхронизация за раз на ящик: обновление по кнопке/таймеру и
        # полный фоновый проход могли стартовать одновременно и оба качать
        # одни и те же заголовки (в журнале на реальном ящике: две строки
        # «новых 3096» подряд). Второй ждёт первого и находит папку уже
        # синхронизированной.
        self._sync_lock = threading.Lock()

    @property
    def account_key(self) -> str:
        return self._account_key

    def folder_summaries(self, folder: str, limit: int | None = None) -> list[MessageSummary]:
        if not cache_store.is_folder_synced(self._account_key, folder):
            cached = cache_store.get_folder_summaries(self._account_key, folder, limit)
            if cached:
                return cached
            return self.refresh_folder(folder, limit)
        return cache_store.get_folder_summaries(self._account_key, folder, limit)

    def folder_message_total(self, folder: str) -> int:
        return cache_store.count_folder_summaries(self._account_key, folder)

    def refresh_folder(self, folder: str, limit: int | None = None, *, progress=None, stop: threading.Event | None = None) -> list[MessageSummary]:
        with self._sync_lock:
            sync_engine.sync_folder_headers(self.session, self._account_key, folder, progress=progress, stop=stop)
        return cache_store.get_folder_summaries(self._account_key, folder, limit)

    def sync_all(self, folders: list[str], *, progress=None, stop: threading.Event | None = None) -> sync_engine.SyncStats:
        with self._sync_lock:
            return sync_engine.sync_all_folders(self.session, self._account_key, folders, progress=progress, stop=stop)

    def download_bodies(self, *, progress=None, stop: threading.Event | None = None, limit: int | None = None) -> int:
        with self._sync_lock:
            return sync_engine.download_bodies(
                self.session, self._account_key, max_bytes=self.body_max_bytes, progress=progress, stop=stop, limit=limit
            )

    def message_content(self, folder: str, uid: int) -> MessageContent:
        cached = cache_store.get_message_content(self._account_key, folder, uid)
        if cached is not None:
            return cached
        content = self.session.fetch_message_content(folder, uid)
        cache_store.save_message_content(self._account_key, folder, uid, content)
        return content

    def message_raw(self, folder: str, uid: int) -> bytes:
        """Не кэшируется — нужен только для разового экспорта в архив."""
        return self.session.fetch_message_raw(folder, uid)

    def search_uids(self, folder: str, *, before=None) -> list[int]:
        """Не кэшируется — используется только для массовой выгрузки папки
        в архив (вся папка / всё до даты), где важна полная папка на
        сервере, а не то, что сейчас в локальном кэше сводок."""
        return self.session.search_uids(folder, before=before)

    def set_marker(self, folder: str, uid: int, color: str | None, *, previous_color=UNKNOWN_MARKER) -> None:
        self.session.set_marker(folder, uid, color, previous_color=previous_color)
        cache_store.set_marker(self._account_key, folder, uid, color)

    def set_read(self, folder: str, uid: int, read: bool) -> None:
        self.session.set_read(folder, uid, read)
        cache_store.set_read(self._account_key, folder, uid, read)

    def set_answered(self, folder: str, uid: int) -> None:
        self.session.set_answered(folder, uid)
        cache_store.set_answered(self._account_key, folder, uid)

    def move_to_trash(self, folder: str, uids: list[int], trash_folder: str) -> None:
        self.move_to_folder(folder, uids, trash_folder)

    def move_to_folder(self, folder: str, uids: list[int], target_folder: str) -> None:
        """Общий переезд писем в любую папку (не только корзину) — так же
        используется при ручном применении правил сортировки почты."""
        self.session.move_messages(folder, uids, target_folder)
        cache_store.delete_messages(self._account_key, folder, uids)

    def delete_messages(self, folder: str, uids: list[int]) -> None:
        # Удаление локально = удаление на сервере (договорённость по
        # хранилищу): сначала сервер, затем локальная копия.
        self.session.delete_messages(folder, uids)
        cache_store.delete_messages(self._account_key, folder, uids)

    def close(self) -> None:
        self.session.close()

    def append_message(self, folder: str, raw: bytes, *, flags: tuple = ()) -> None:
        # Новое письмо подхватит следующая синхронизация папки (появится
        # новый UID на сервере).
        self.session.append_message(folder, raw, flags=flags)


class ArchiveSource:
    """Тот же протокол, что у CachedMailbox (folder_summaries/refresh_folder/
    message_content/set_marker/delete_messages/close), но поверх локального
    файла архива — чтобы UI не знал разницы между «живой ящик» и «архив»."""

    def __init__(self, path: Path):
        self.path = path

    def folder_summaries(self, folder: str, limit: int | None = None) -> list[MessageSummary]:
        messages = archive_store.list_messages(self.path, folder)
        return messages[:limit] if limit is not None else messages

    def folder_message_total(self, folder: str) -> int:
        return len(archive_store.list_messages(self.path, folder))

    def refresh_folder(self, folder: str, limit: int | None = None, **_kwargs) -> list[MessageSummary]:
        # Архив не меняется извне сам по себе — «обновить» просто перечитывает файл.
        return self.folder_summaries(folder, limit)

    def message_content(self, folder: str, uid: int) -> MessageContent:
        return archive_store.get_message_content(self.path, uid)

    def set_marker(self, folder: str, uid: int, color: str | None, *, previous_color=UNKNOWN_MARKER) -> None:
        archive_store.set_marker(self.path, uid, color)

    def set_read(self, folder: str, uid: int, read: bool) -> None:
        pass  # архив всегда "прочитан" (см. folder_summaries) — переключать нечего

    def set_answered(self, folder: str, uid: int) -> None:
        pass  # архив — статичный снимок, отвечать "заново" на его письма нельзя

    def delete_messages(self, folder: str, uids: list[int]) -> None:
        archive_store.delete_messages(self.path, uids)

    def close(self) -> None:
        pass
