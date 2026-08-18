from __future__ import annotations

from redmail import cache_store
from redmail.imap_client import Account, ImapSession, MessageContent, MessageSummary


class CachedMailbox:
    """Читает через ImapSession, но сперва проверяет локальный кэш.

    folder_summaries() — чистое чтение из кэша, без обращения к серверу,
    КРОМЕ самого первого раза, когда для папки ещё нет вообще ничего
    закэшированного (переключение между уже открытыми папками сети не
    трогает — не нужно спрашивать сервер на каждый клик).

    refresh_folder() — явная проверка сервера: при первом открытии папки,
    по периодическому таймеру и по кнопке «Обновить» в UI. SELECT (дёшево,
    без сканирования) выполняется всегда, чтобы узнать текущее число писем;
    если оно не изменилось — обходимся без похода за самими письмами.

    Текст письма и вложения кэшируются один раз навсегда: после доставки
    письмо не меняется, повторно скачивать нечего.
    """

    def __init__(self, session: ImapSession, account: Account):
        self.session = session
        self._account_key = f"{account.host}:{account.username}"

    def folder_summaries(self, folder: str, limit: int = 50) -> list[MessageSummary]:
        cached = cache_store.get_folder_summaries(self._account_key, folder)
        if cached:
            return cached[:limit]
        return self.refresh_folder(folder, limit)

    def refresh_folder(self, folder: str, limit: int = 50) -> list[MessageSummary]:
        total = self.session.folder_message_count(folder)
        cached_total = cache_store.get_folder_exists(self._account_key, folder)
        if cached_total == total:
            cached = cache_store.get_folder_summaries(self._account_key, folder)
            if cached:
                return cached[:limit]
        summaries = self.session.fetch_summaries(limit)
        cache_store.save_folder_summaries(self._account_key, folder, total, summaries)
        return summaries

    def message_content(self, folder: str, uid: int) -> MessageContent:
        cached = cache_store.get_message_content(self._account_key, folder, uid)
        if cached is not None:
            return cached
        content = self.session.fetch_message_content(folder, uid)
        cache_store.save_message_content(self._account_key, folder, uid, content)
        return content

    def set_marker(self, folder: str, uid: int, color: str | None) -> None:
        self.session.set_marker(folder, uid, color)
        cache_store.set_marker(self._account_key, folder, uid, color)

    def move_to_trash(self, folder: str, uids: list[int], trash_folder: str) -> None:
        self.session.move_messages(folder, uids, trash_folder)
        cache_store.delete_messages(self._account_key, folder, uids)

    def delete_messages(self, folder: str, uids: list[int]) -> None:
        self.session.delete_messages(folder, uids)
        cache_store.delete_messages(self._account_key, folder, uids)

    def close(self) -> None:
        self.session.close()
