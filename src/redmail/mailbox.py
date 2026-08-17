from __future__ import annotations

from redmail import cache_store
from redmail.imap_client import Account, ImapSession, MessageContent, MessageSummary


class CachedMailbox:
    """Читает через ImapSession, но сперва проверяет локальный кэш.

    Список папки: SELECT (дёшево, без сканирования) всегда выполняется, чтобы
    узнать текущее число писем (EXISTS). Если оно совпадает с тем, что было при
    прошлом кэшировании — отдаём сохранённые сводки без обращения к серверу за
    самими письмами. Если число изменилось (пришло новое или что-то удалили) —
    забираем актуальный список и обновляем кэш.

    Текст письма и вложения кэшируются один раз навсегда: после доставки
    письмо не меняется, повторно скачивать нечего.
    """

    def __init__(self, session: ImapSession, account: Account):
        self.session = session
        self._account_key = f"{account.host}:{account.username}"

    def folder_summaries(self, folder: str, limit: int = 50) -> list[MessageSummary]:
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

    def close(self) -> None:
        self.session.close()
