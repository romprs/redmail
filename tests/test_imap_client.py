from __future__ import annotations

from datetime import datetime
from email.header import Header
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from redmail.imap_client import Account, fetch_inbox_summaries


def _address(name: bytes | None, mailbox: bytes, host: bytes) -> SimpleNamespace:
    return SimpleNamespace(name=name, mailbox=mailbox, host=host)


def test_fetch_inbox_summaries_parses_envelope() -> None:
    encoded_subject = Header("Привет из РЕД ОС", "utf-8").encode().encode("ascii")
    envelope = SimpleNamespace(
        subject=encoded_subject,
        from_=[_address(b"Ivan Petrov", b"ivan", b"example.com")],
        date=datetime(2026, 8, 17, 10, 30),
    )

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.search.return_value = [1, 2, 3]
    fake_client.fetch.return_value = {3: {b"ENVELOPE": envelope}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        account = Account(host="imap.example.com", username="ivan", password="secret")
        summaries = fetch_inbox_summaries(account, limit=1)

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.uid == 3
    assert summary.subject == "Привет из РЕД ОС"
    assert summary.sender == "Ivan Petrov"
    assert summary.date == "2026-08-17 10:30"
    fake_client.login.assert_called_once_with("ivan", "secret")
    fake_client.select_folder.assert_called_once_with("INBOX", readonly=True)


def test_fetch_inbox_summaries_empty_mailbox() -> None:
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.search.return_value = []

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        account = Account(host="imap.example.com", username="ivan", password="secret")
        summaries = fetch_inbox_summaries(account)

    assert summaries == []
    fake_client.fetch.assert_not_called()


def test_format_address_decodes_rfc2047_display_name() -> None:
    # Реальный кейс с боевого ящика: некоторые отправители (например, Авито)
    # присылают имя в ENVELOPE закодированным словом, а не сырым UTF-8.
    encoded_name = Header("Авито", "utf-8").encode().encode("ascii")
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.search.return_value = [1]
    envelope = SimpleNamespace(
        subject=None,
        from_=[_address(encoded_name, b"noreply", b"avito.ru")],
        date=None,
    )
    fake_client.fetch.return_value = {1: {b"ENVELOPE": envelope}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        account = Account(host="imap.example.com", username="ivan", password="secret")
        summaries = fetch_inbox_summaries(account)

    assert summaries[0].sender == "Авито"


def test_format_address_without_display_name() -> None:
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.search.return_value = [1]
    envelope = SimpleNamespace(
        subject=None,
        from_=[_address(None, b"ivan", b"example.com")],
        date=None,
    )
    fake_client.fetch.return_value = {1: {b"ENVELOPE": envelope}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        account = Account(host="imap.example.com", username="ivan", password="secret")
        summaries = fetch_inbox_summaries(account)

    assert summaries[0].sender == "ivan@example.com"
    assert summaries[0].subject == "(без темы)"
    assert summaries[0].date == ""
