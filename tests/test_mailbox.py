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


def test_folder_summaries_skips_network_when_exists_unchanged(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    session = MagicMock()
    session.folder_message_count.return_value = 5
    session.fetch_summaries.return_value = [_summary(1)]

    with patch("redmail.cache_store._db_path", return_value=db_path):
        mailbox = CachedMailbox(session, _account())
        first = mailbox.folder_summaries("INBOX")
        assert session.fetch_summaries.call_count == 1

        second = mailbox.folder_summaries("INBOX")

    # EXISTS не поменялся (5 оба раза) — второй вызов не должен был снова
    # ходить в сеть за сводками, только за EXISTS (folder_message_count).
    assert session.fetch_summaries.call_count == 1
    assert session.folder_message_count.call_count == 2
    assert [s.uid for s in second] == [s.uid for s in first]


def test_folder_summaries_refetches_when_exists_changes(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    session = MagicMock()
    session.folder_message_count.side_effect = [5, 6]
    session.fetch_summaries.side_effect = [[_summary(1)], [_summary(1), _summary(2)]]

    with patch("redmail.cache_store._db_path", return_value=db_path):
        mailbox = CachedMailbox(session, _account())
        mailbox.folder_summaries("INBOX")
        second = mailbox.folder_summaries("INBOX")

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
