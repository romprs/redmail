from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from redmail.imap_client import Account, MessageContent, MessageSummary
from redmail.mailbox import CachedMailbox


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


def test_toggle_flag_updates_session_and_cache(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    session = MagicMock()
    session.folder_message_count.return_value = 1
    session.fetch_summaries.return_value = [_summary(1)]

    with patch("redmail.cache_store._db_path", return_value=db_path):
        mailbox = CachedMailbox(session, _account())
        mailbox.folder_summaries("INBOX")
        mailbox.toggle_flag("INBOX", 1, True)
        cached = mailbox.folder_summaries("INBOX")

    session.set_flagged.assert_called_once_with("INBOX", 1, True)
    assert cached[0].flagged is True


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


def test_close_delegates_to_session() -> None:
    session = MagicMock()
    mailbox = CachedMailbox(session, _account())
    mailbox.close()
    session.close.assert_called_once()
