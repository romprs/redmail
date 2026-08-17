from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from redmail import cache_store
from redmail.imap_client import Attachment, MessageContent, MessageSummary


def _summary(uid: int, subject: str) -> MessageSummary:
    return MessageSummary(
        uid=uid, subject=subject, sender="Ivan", sender_email="ivan@example.com", date="2026-08-18 10:00",
        message_id=f"<{uid}@example.com>",
    )


def test_folder_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    with patch("redmail.cache_store._db_path", return_value=db_path):
        assert cache_store.get_folder_exists("acc", "INBOX") is None

        summaries = [_summary(2, "Newer"), _summary(1, "Older")]
        cache_store.save_folder_summaries("acc", "INBOX", exists_count=5, summaries=summaries)

        assert cache_store.get_folder_exists("acc", "INBOX") == 5
        cached = cache_store.get_folder_summaries("acc", "INBOX")
        assert [s.uid for s in cached] == [2, 1]
        assert cached[0].subject == "Newer"


def test_save_folder_summaries_drops_removed_messages_but_keeps_cached_body(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    with patch("redmail.cache_store._db_path", return_value=db_path):
        cache_store.save_folder_summaries("acc", "INBOX", 2, [_summary(1, "A"), _summary(2, "B")])
        cache_store.save_message_content("acc", "INBOX", 1, MessageContent(text="body of A"))

        # Письмо 2 пропало (удалено на сервере), письмо 1 осталось — обновляем список.
        cache_store.save_folder_summaries("acc", "INBOX", 1, [_summary(1, "A")])

        cached = cache_store.get_folder_summaries("acc", "INBOX")
        assert [s.uid for s in cached] == [1]
        # Тело письма 1, закэшированное раньше, не должно было потереться повторным сохранением списка.
        content = cache_store.get_message_content("acc", "INBOX", 1)
        assert content is not None
        assert content.text == "body of A"


def test_message_content_round_trip_with_attachment(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    with patch("redmail.cache_store._db_path", return_value=db_path):
        assert cache_store.get_message_content("acc", "INBOX", 42) is None

        content = MessageContent(
            text="Привет!",
            attachments=[Attachment(filename="notes.txt", content_type="text/plain", payload=b"hello")],
        )
        cache_store.save_message_content("acc", "INBOX", 42, content)

        cached = cache_store.get_message_content("acc", "INBOX", 42)
        assert cached is not None
        assert cached.text == "Привет!"
        assert len(cached.attachments) == 1
        assert cached.attachments[0].filename == "notes.txt"
        assert cached.attachments[0].payload == b"hello"


def test_message_content_can_be_cached_before_folder_is_listed(tmp_path: Path) -> None:
    # Например: письмо только что отправлено и сразу открыто, до того как
    # список папки вообще был закэширован.
    db_path = tmp_path / "cache.sqlite3"
    with patch("redmail.cache_store._db_path", return_value=db_path):
        cache_store.save_message_content("acc", "Sent", 7, MessageContent(text="hi"))
        cached = cache_store.get_message_content("acc", "Sent", 7)
        assert cached is not None
        assert cached.text == "hi"
