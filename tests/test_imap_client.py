from __future__ import annotations

from datetime import datetime
from email.header import Header
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from redmail.imap_client import Account, ImapSession


def _address(name: bytes | None, mailbox: bytes, host: bytes) -> SimpleNamespace:
    return SimpleNamespace(name=name, mailbox=mailbox, host=host)


def _account() -> Account:
    return Account(host="imap.example.com", username="ivan", password="secret")


def test_session_logs_in_once_on_construction() -> None:
    fake_client = MagicMock()
    fake_client.search.return_value = []

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(_account())
        session.fetch_folder_summaries("INBOX")
        session.fetch_folder_summaries("Archive")

    # Логин — один раз при подключении, а не при каждом переключении папки/письма.
    fake_client.login.assert_called_once_with("ivan", "secret")


def test_session_skips_reselect_of_same_folder() -> None:
    fake_client = MagicMock()
    fake_client.search.return_value = []

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(_account())
        session.fetch_folder_summaries("INBOX")
        session.fetch_folder_summaries("INBOX")
        session.fetch_folder_summaries("Archive")

    assert fake_client.select_folder.call_count == 2
    fake_client.select_folder.assert_any_call("INBOX", readonly=True)
    fake_client.select_folder.assert_any_call("Archive", readonly=True)


def test_list_folders_excludes_noselect_folders() -> None:
    # Реальный кейс: у Gmail папка [Gmail] — контейнер для Отправленных/Корзины
    # и т.п., сама по себе не открывается (флаг \Noselect), но раньше мы её
    # всё равно показывали в списке, и клик по ней падал с ошибкой сервера.
    fake_client = MagicMock()
    fake_client.list_folders.return_value = [
        ((b"\\HasNoChildren",), b"/", "INBOX"),
        ((b"\\Noselect", b"\\HasChildren"), b"/", "[Gmail]"),
        ((b"\\HasNoChildren",), b"/", "[Gmail]/Sent Mail"),
    ]

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        folders = ImapSession(_account()).list_folders()

    assert folders == ["INBOX", "[Gmail]/Sent Mail"]


def test_fetch_folder_summaries_parses_envelope() -> None:
    encoded_subject = Header("Привет из РЕД ОС", "utf-8").encode().encode("ascii")
    envelope = SimpleNamespace(
        subject=encoded_subject,
        from_=[_address(b"Ivan Petrov", b"ivan", b"example.com")],
        date=datetime(2026, 8, 17, 10, 30),
        message_id=b"<abc123@example.com>",
    )

    fake_client = MagicMock()
    fake_client.search.return_value = [1, 2, 3]
    fake_client.fetch.return_value = {3: {b"ENVELOPE": envelope}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        summaries = ImapSession(_account()).fetch_folder_summaries(limit=1)

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.uid == 3
    assert summary.subject == "Привет из РЕД ОС"
    assert summary.sender == "Ivan Petrov"
    assert summary.sender_email == "ivan@example.com"
    assert summary.date == "2026-08-17 10:30"
    assert summary.message_id == "<abc123@example.com>"
    fake_client.select_folder.assert_called_once_with("INBOX", readonly=True)


def test_fetch_folder_summaries_uses_requested_folder() -> None:
    fake_client = MagicMock()
    fake_client.search.return_value = []

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        ImapSession(_account()).fetch_folder_summaries(folder="Archive")

    fake_client.select_folder.assert_called_once_with("Archive", readonly=True)


def test_fetch_folder_summaries_empty_mailbox() -> None:
    fake_client = MagicMock()
    fake_client.search.return_value = []

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        summaries = ImapSession(_account()).fetch_folder_summaries()

    assert summaries == []
    fake_client.fetch.assert_not_called()


def test_format_address_decodes_rfc2047_display_name() -> None:
    # Реальный кейс с боевого ящика: некоторые отправители (например, Авито)
    # присылают имя в ENVELOPE закодированным словом, а не сырым UTF-8.
    encoded_name = Header("Авито", "utf-8").encode().encode("ascii")
    fake_client = MagicMock()
    fake_client.search.return_value = [1]
    envelope = SimpleNamespace(
        subject=None,
        from_=[_address(encoded_name, b"noreply", b"avito.ru")],
        date=None,
        message_id=None,
    )
    fake_client.fetch.return_value = {1: {b"ENVELOPE": envelope}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        summaries = ImapSession(_account()).fetch_folder_summaries()

    assert summaries[0].sender == "Авито"
    assert summaries[0].sender_email == "noreply@avito.ru"


def test_format_address_without_display_name() -> None:
    fake_client = MagicMock()
    fake_client.search.return_value = [1]
    envelope = SimpleNamespace(
        subject=None,
        from_=[_address(None, b"ivan", b"example.com")],
        date=None,
        message_id=None,
    )
    fake_client.fetch.return_value = {1: {b"ENVELOPE": envelope}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        summaries = ImapSession(_account()).fetch_folder_summaries()

    assert summaries[0].sender == "ivan@example.com"
    assert summaries[0].sender_email == "ivan@example.com"
    assert summaries[0].subject == "(без темы)"
    assert summaries[0].date == ""
    assert summaries[0].message_id == ""


def test_fetch_message_body_plain_text() -> None:
    fake_client = MagicMock()
    raw = (
        b"From: ivan@example.com\r\n"
        b"To: test@example.com\r\n"
        b"Subject: Test\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
    ) + "Привет!".encode("utf-8")
    fake_client.fetch.return_value = {5: {b"BODY[]": raw}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        body = ImapSession(_account()).fetch_message_body("INBOX", 5)

    assert body == "Привет!"
    fake_client.select_folder.assert_called_once_with("INBOX", readonly=True)


def test_fetch_message_body_html_only_shows_placeholder() -> None:
    fake_client = MagicMock()
    raw = (
        b"From: ivan@example.com\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<p>hello</p>"
    )
    fake_client.fetch.return_value = {5: {b"BODY[]": raw}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        body = ImapSession(_account()).fetch_message_body("INBOX", 5)

    assert "HTML" in body


def test_close_logs_out() -> None:
    fake_client = MagicMock()

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(_account())
        session.close()

    fake_client.logout.assert_called_once()
