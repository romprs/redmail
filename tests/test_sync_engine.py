from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

from redmail import cache_store, sync_engine
from redmail.imap_client import MessageContent, MessageSummary


def _summary(uid: int, **kwargs) -> MessageSummary:
    base = dict(uid=uid, subject=f"S{uid}", sender="A", sender_email="a@x.ru", date="2026-09-11 10:00", message_id=f"<{uid}@x>")
    base.update(kwargs)
    return MessageSummary(**base)


class Server:
    def __init__(self, n: int) -> None:
        self.messages = {uid: _summary(uid, size=100) for uid in range(1, n + 1)}
        self.summary_calls: list[list[int]] = []
        self.flag_calls: list[list[int]] = []
        self.content_calls: list[tuple[str, int]] = []

    def folder_status(self, folder):
        return 42, len(self.messages)

    def search_uids(self, folder, *, before=None):
        return sorted(self.messages)

    def fetch_summaries_by_uids(self, folder, uids):
        self.summary_calls.append(list(uids))
        return [self.messages[u] for u in uids]

    def fetch_flags(self, folder, uids):
        self.flag_calls.append(list(uids))
        return {u: (self.messages[u].is_read, False, None) for u in uids}

    def fetch_message_content(self, folder, uid):
        self.content_calls.append((folder, uid))
        return MessageContent(text=f"body {uid}")


def test_headers_are_fetched_in_chunks_newest_first_with_progress(tmp_path: Path) -> None:
    server = Server(1000)
    progress: list[tuple[str, int, int]] = []
    with patch("redmail.cache_store._db_path", return_value=tmp_path / "mail.sqlite3"):
        result = sync_engine.sync_folder_headers(server, "acc", "INBOX", progress=lambda *args: progress.append(args), chunk=300)
        assert cache_store.count_folder_summaries("acc", "INBOX") == 1000
        assert cache_store.is_folder_synced("acc", "INBOX")
        assert cache_store.get_folder_uidvalidity("acc", "INBOX") == 42

    assert result.total == 1000 and result.added == 1000 and result.deleted == 0
    assert [len(c) for c in server.summary_calls] == [300, 300, 300, 100]
    assert server.summary_calls[0][0] == 1000  # сперва самые новые
    assert progress[-1][1:] == (1000, 1000)


def test_stop_event_interrupts_and_leaves_folder_marked_incomplete(tmp_path: Path) -> None:
    server = Server(500)
    stop = threading.Event()

    def stop_after_first(text, done, total):
        stop.set()

    with patch("redmail.cache_store._db_path", return_value=tmp_path / "mail.sqlite3"):
        sync_engine.sync_folder_headers(server, "acc", "INBOX", progress=stop_after_first, stop=stop, chunk=100)
        assert cache_store.count_folder_summaries("acc", "INBOX") == 100
        assert not cache_store.is_folder_synced("acc", "INBOX")
        # Следующий запуск докачивает остальное, не перекачивая уже полученное.
        server.summary_calls.clear()
        sync_engine.sync_folder_headers(server, "acc", "INBOX", chunk=100)
        assert cache_store.count_folder_summaries("acc", "INBOX") == 500
        assert cache_store.is_folder_synced("acc", "INBOX")
    assert sum(len(c) for c in server.summary_calls) == 400


def test_sync_all_folders_continues_after_a_failing_folder(tmp_path: Path) -> None:
    server = Server(3)
    original = server.search_uids

    def failing_search(folder, *, before=None):
        if folder == "Broken":
            raise OSError("boom")
        return original(folder, before=before)

    server.search_uids = failing_search
    with patch("redmail.cache_store._db_path", return_value=tmp_path / "mail.sqlite3"):
        stats = sync_engine.sync_all_folders(server, "acc", ["INBOX", "Broken", "Sent"])
        assert cache_store.count_folder_summaries("acc", "Sent") == 3
    assert [f.folder for f in stats.folders] == ["INBOX", "Sent"]
    assert stats.added == 6


def test_download_bodies_marks_failed_message_deferred_and_moves_on(tmp_path: Path) -> None:
    server = Server(3)
    original = server.fetch_message_content

    def flaky(folder, uid):
        if uid == 2:
            raise OSError("timeout")
        return original(folder, uid)

    server.fetch_message_content = flaky
    with patch("redmail.cache_store._db_path", return_value=tmp_path / "mail.sqlite3"):
        sync_engine.sync_folder_headers(server, "acc", "INBOX")
        downloaded = sync_engine.download_bodies(server, "acc", max_bytes=10_000)
        assert downloaded == 2
        assert cache_store.count_messages_without_body("acc", 10_000) == 0
        assert cache_store.get_message_content("acc", "INBOX", 2) is None
        stats = cache_store.storage_stats("acc")
    assert stats["messages"] == 3 and stats["with_body"] == 2
