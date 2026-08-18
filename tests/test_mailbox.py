from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from email.message import EmailMessage

from redmail import archive_store
from redmail.imap_client import Account, MessageContent, MessageSummary
from redmail.mailbox import ArchiveSource, CachedMailbox


def _account() -> Account:
    return Account(host="imap.example.com", username="ivan", password="secret")


def _summary(uid: int) -> MessageSummary:
    return MessageSummary(
        uid=uid, subject="S", sender="Ivan", sender_email="ivan@example.com", date="2026-08-18 10:00",
        message_id="<1@example.com>",
    )


def test_folder_summaries_hits_network_once_when_cache_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    session = MagicMock()
    session.folder_message_count.return_value = 1
    session.fetch_summaries.return_value = [_summary(1)]

    with patch("redmail.cache_store._db_path", return_value=db_path):
        mailbox = CachedMailbox(session, _account())
        summaries = mailbox.folder_summaries("INBOX")

    assert [s.uid for s in summaries] == [1]
    session.folder_message_count.assert_called_once()
    session.fetch_summaries.assert_called_once()


def test_folder_summaries_never_hits_network_once_cached(tmp_path: Path) -> None:
    # Ключевое требование: переключение между уже открытыми папками не должно
    # спрашивать сервер вообще — ни SELECT, ни FETCH.
    db_path = tmp_path / "cache.sqlite3"
    session = MagicMock()
    session.folder_message_count.return_value = 1
    session.fetch_summaries.return_value = [_summary(1)]

    with patch("redmail.cache_store._db_path", return_value=db_path):
        mailbox = CachedMailbox(session, _account())
        mailbox.folder_summaries("INBOX")  # первый раз — сеть
        mailbox.folder_summaries("INBOX")
        mailbox.folder_summaries("INBOX")

    session.folder_message_count.assert_called_once()
    session.fetch_summaries.assert_called_once()


def test_refresh_folder_skips_fetch_when_exists_unchanged(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    session = MagicMock()
    session.folder_message_count.return_value = 5
    session.fetch_summaries.return_value = [_summary(1)]

    with patch("redmail.cache_store._db_path", return_value=db_path):
        mailbox = CachedMailbox(session, _account())
        mailbox.refresh_folder("INBOX")
        mailbox.refresh_folder("INBOX")

    # EXISTS не поменялся (5 оба раза) — второй refresh не должен снова
    # ходить в сеть за сводками, только за EXISTS (folder_message_count).
    assert session.fetch_summaries.call_count == 1
    assert session.folder_message_count.call_count == 2


def test_refresh_folder_refetches_when_exists_changes(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    session = MagicMock()
    session.folder_message_count.side_effect = [5, 6]
    session.fetch_summaries.side_effect = [[_summary(1)], [_summary(1), _summary(2)]]

    with patch("redmail.cache_store._db_path", return_value=db_path):
        mailbox = CachedMailbox(session, _account())
        mailbox.refresh_folder("INBOX")
        second = mailbox.refresh_folder("INBOX")

    assert session.fetch_summaries.call_count == 2
    assert len(second) == 2


def test_message_content_cached_after_first_fetch(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    session = MagicMock()
    session.fetch_message_content.return_value = MessageContent(text="hello")

    with patch("redmail.cache_store._db_path", return_value=db_path):
        mailbox = CachedMailbox(session, _account())
        first = mailbox.message_content("INBOX", 1)
        second = mailbox.message_content("INBOX", 1)

    assert session.fetch_message_content.call_count == 1
    assert first.text == second.text == "hello"


def test_set_marker_updates_session_and_cache(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    session = MagicMock()
    session.folder_message_count.return_value = 1
    session.fetch_summaries.return_value = [_summary(1)]

    with patch("redmail.cache_store._db_path", return_value=db_path):
        mailbox = CachedMailbox(session, _account())
        mailbox.folder_summaries("INBOX")
        mailbox.set_marker("INBOX", 1, "green")
        cached = mailbox.folder_summaries("INBOX")

    session.set_marker.assert_called_once_with("INBOX", 1, "green")
    assert cached[0].marker_color == "green"


def test_delete_messages_updates_session_and_cache(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    session = MagicMock()
    session.folder_message_count.return_value = 2
    session.fetch_summaries.return_value = [_summary(1), _summary(2)]

    with patch("redmail.cache_store._db_path", return_value=db_path):
        mailbox = CachedMailbox(session, _account())
        mailbox.folder_summaries("INBOX")
        mailbox.delete_messages("INBOX", [1])
        cached = mailbox.folder_summaries("INBOX")

    session.delete_messages.assert_called_once_with("INBOX", [1])
    assert [s.uid for s in cached] == [2]


def test_move_to_trash_updates_session_and_cache(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    session = MagicMock()
    session.folder_message_count.return_value = 2
    session.fetch_summaries.return_value = [_summary(1), _summary(2)]

    with patch("redmail.cache_store._db_path", return_value=db_path):
        mailbox = CachedMailbox(session, _account())
        mailbox.folder_summaries("INBOX")
        mailbox.move_to_trash("INBOX", [1], "Trash")
        cached = mailbox.folder_summaries("INBOX")

    session.move_messages.assert_called_once_with("INBOX", [1], "Trash")
    assert [s.uid for s in cached] == [2]


def test_different_accounts_do_not_share_cache(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    session_a = MagicMock()
    session_a.folder_message_count.return_value = 1
    session_a.fetch_summaries.return_value = [_summary(1)]

    session_b = MagicMock()
    session_b.folder_message_count.return_value = 1
    session_b.fetch_summaries.return_value = [_summary(1)]

    account_a = Account(host="imap.example.com", username="ivan", password="secret")
    account_b = Account(host="imap.example.com", username="petr", password="secret")

    with patch("redmail.cache_store._db_path", return_value=db_path):
        CachedMailbox(session_a, account_a).folder_summaries("INBOX")
        CachedMailbox(session_b, account_b).folder_summaries("INBOX")

    # У обоих аккаунтов должен был быть реальный запрос сводок — не подхватили чужой кэш.
    assert session_a.fetch_summaries.call_count == 1
    assert session_b.fetch_summaries.call_count == 1


def test_message_raw_delegates_to_session_uncached(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    session = MagicMock()
    session.fetch_message_raw.return_value = b"raw bytes"

    with patch("redmail.cache_store._db_path", return_value=db_path):
        mailbox = CachedMailbox(session, _account())
        first = mailbox.message_raw("INBOX", 1)
        second = mailbox.message_raw("INBOX", 1)

    assert first == second == b"raw bytes"
    assert session.fetch_message_raw.call_count == 2


def test_close_delegates_to_session() -> None:
    session = MagicMock()
    mailbox = CachedMailbox(session, _account())
    mailbox.close()
    session.close.assert_called_once()


def _archive_message(subject: str) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "a@example.com"
    msg.set_content("тело")
    return msg.as_bytes()


def test_archive_source_same_protocol_as_cached_mailbox(tmp_path: Path) -> None:
    archive_path = tmp_path / "test.rmarchive"
    archive_store.create_archive(archive_path)
    archive_store.append_raw_message(archive_path, "F", _archive_message("A"))

    source = ArchiveSource(archive_path)
    summaries = source.folder_summaries("F")
    assert len(summaries) == 1
    assert summaries[0].subject == "A"

    # refresh_folder — тот же результат, архив не ходит в сеть
    assert source.refresh_folder("F")[0].subject == "A"

    content = source.message_content("F", summaries[0].uid)
    assert content.text.strip() == "тело"


def test_archive_source_set_marker_and_delete(tmp_path: Path) -> None:
    archive_path = tmp_path / "test.rmarchive"
    archive_store.create_archive(archive_path)
    msg_id = archive_store.append_raw_message(archive_path, "F", _archive_message("A"))

    source = ArchiveSource(archive_path)
    source.set_marker("F", msg_id, "green")
    assert source.folder_summaries("F")[0].marker_color == "green"

    source.delete_messages("F", [msg_id])
    assert source.folder_summaries("F") == []


def test_archive_source_close_is_noop(tmp_path: Path) -> None:
    ArchiveSource(tmp_path / "test.rmarchive").close()  # не должно падать
