from __future__ import annotations

from datetime import datetime
from email.header import Header
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from imapclient.exceptions import IMAPClientError
from imapclient.response_types import BodyData

from redmail.imap_client import Account, FolderInfo, ImapSession

_FETCH_FIELDS = ["ENVELOPE", "UID", "FLAGS", "BODYSTRUCTURE", "BODY.PEEK[HEADER.FIELDS (IMPORTANCE X-PRIORITY)]"]


def _address(name: bytes | None, mailbox: bytes, host: bytes) -> SimpleNamespace:
    return SimpleNamespace(name=name, mailbox=mailbox, host=host)


def _account() -> Account:
    return Account(host="imap.example.com", username="ivan", password="secret")


def _client(exists: int = 0) -> MagicMock:
    client = MagicMock()
    client.use_uid = True
    client.select_folder.return_value = {b"EXISTS": exists}
    return client


def test_session_logs_in_once_on_construction() -> None:
    fake_client = _client(exists=0)

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(_account())
        session.fetch_folder_summaries("INBOX")
        session.fetch_folder_summaries("Archive")

    # Логин — один раз при подключении, а не при каждом переключении папки/письма.
    fake_client.login.assert_called_once_with("ivan", "secret")


def test_fetch_folder_summaries_always_reselects_for_fresh_exists() -> None:
    # Намеренно НЕ пропускаем повторный SELECT для той же папки: иначе, раз мы
    # больше не делаем отдельный SEARCH ALL, можно не заметить письма, пришедшие
    # после прошлого захода в эту же папку.
    fake_client = _client(exists=0)

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(_account())
        session.fetch_folder_summaries("INBOX")
        session.fetch_folder_summaries("INBOX")

    assert fake_client.select_folder.call_count == 2


def test_fetch_message_body_skips_reselect_of_same_folder() -> None:
    fake_client = _client(exists=0)
    fake_client.fetch.return_value = {
        5: {b"BODY[]": b"From: a@example.com\r\nContent-Type: text/plain\r\n\r\nhi"}
    }

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(_account())
        session.fetch_folder_summaries("INBOX")
        session.fetch_message_content("INBOX", 5)

    assert fake_client.select_folder.call_count == 1


def test_list_folders_excludes_noselect_folders() -> None:
    # Реальный кейс: у Gmail папка [Gmail] — контейнер для Отправленных/Корзины
    # и т.п., сама по себе не открывается (флаг \Noselect), но раньше мы её
    # всё равно показывали в списке, и клик по ней падал с ошибкой сервера.
    fake_client = MagicMock()
    fake_client.list_folders.return_value = [
        ((b"\\HasNoChildren",), b"/", "INBOX"),
        ((b"\\Noselect", b"\\HasChildren"), b"/", "[Gmail]"),
        ((b"\\HasNoChildren", b"\\Trash"), b"/", "[Gmail]/Trash"),
    ]

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(_account())
        folders = session.list_folders()

    assert folders == [
        FolderInfo(name="INBOX", delimiter="/"),
        FolderInfo(name="[Gmail]/Trash", delimiter="/"),
    ]


def test_trash_folder_found_by_flag() -> None:
    fake_client = MagicMock()
    fake_client.list_folders.return_value = [
        ((b"\\HasNoChildren",), b"/", "INBOX"),
        ((b"\\Noselect", b"\\HasChildren"), b"/", "[Gmail]"),
        ((b"\\HasNoChildren", b"\\Trash"), b"/", "[Gmail]/Trash"),
    ]

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(_account())
        session.list_folders()
        trash = session.trash_folder()

    assert trash == "[Gmail]/Trash"


def test_trash_folder_none_when_not_found() -> None:
    fake_client = MagicMock()
    fake_client.list_folders.return_value = [((b"\\HasNoChildren",), b"/", "INBOX")]

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(_account())
        session.list_folders()
        trash = session.trash_folder()

    assert trash is None


def test_fetch_folder_summaries_parses_envelope() -> None:
    encoded_subject = Header("Привет из РЕД ОС", "utf-8").encode().encode("ascii")
    envelope = SimpleNamespace(
        subject=encoded_subject,
        from_=[_address(b"Ivan Petrov", b"ivan", b"example.com")],
        date=datetime(2026, 8, 17, 10, 30),
        message_id=b"<abc123@example.com>",
    )

    fake_client = _client(exists=3)
    fake_client.fetch.return_value = {3: {b"ENVELOPE": envelope, b"UID": 42}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        summaries = ImapSession(_account()).fetch_folder_summaries(limit=1)

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.uid == 42
    assert summary.subject == "Привет из РЕД ОС"
    assert summary.sender == "Ivan Petrov"
    assert summary.sender_email == "ivan@example.com"
    assert summary.date == "2026-08-17 10:30"
    assert summary.message_id == "<abc123@example.com>"
    assert summary.has_attachments is False
    assert summary.marker_color is None
    assert summary.importance == "normal"
    fake_client.select_folder.assert_called_once_with("INBOX", readonly=False)
    # 3 письма в папке, лимит 1 — просим только последнее по номеру, без SEARCH.
    fake_client.fetch.assert_called_once_with("3:*", _FETCH_FIELDS)
    # Временный sequence-режим не должен просочиться в следующие вызовы.
    assert fake_client.use_uid is True


def test_fetch_folder_summaries_orders_newest_first() -> None:
    def envelope(subject: bytes) -> SimpleNamespace:
        return SimpleNamespace(
            subject=subject, from_=[_address(None, b"a", b"example.com")], date=None, message_id=None
        )

    fake_client = _client(exists=5)
    fake_client.fetch.return_value = {
        4: {b"ENVELOPE": envelope(b"older"), b"UID": 104},
        5: {b"ENVELOPE": envelope(b"newer"), b"UID": 105},
    }

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        summaries = ImapSession(_account()).fetch_folder_summaries(limit=2)

    assert [s.uid for s in summaries] == [105, 104]
    fake_client.fetch.assert_called_once_with("4:*", _FETCH_FIELDS)


def test_fetch_folder_summaries_reads_flags_attachment_and_importance() -> None:
    envelope = SimpleNamespace(
        subject=None, from_=[_address(None, b"a", b"example.com")], date=None, message_id=None
    )
    # Форма — как в реальном ответе Gmail для multipart/mixed с вложением
    # (alternative-часть + вложение с Content-Disposition: attachment),
    # построена напрямую в уже "разобранном" виде (как после BodyData.create).
    plain_part = BodyData((b"TEXT", b"PLAIN", (b"CHARSET", b"UTF-8"), None, None, b"BASE64", 114, 3, None, None, None))
    html_part = BodyData(
        (b"TEXT", b"HTML", (b"CHARSET", b"UTF-8"), None, None, b"QUOTED-PRINTABLE", 455, 10, None, None, None)
    )
    alternative = BodyData(([plain_part, html_part], b"ALTERNATIVE", (b"BOUNDARY", b"xyz"), None, None))
    attachment_part = BodyData(
        (
            b"APPLICATION",
            b"PDF",
            (b"NAME", b"report.pdf"),
            None,
            None,
            b"BASE64",
            2048,
            None,
            (b"ATTACHMENT", (b"FILENAME", b"report.pdf")),
            None,
        )
    )
    bodystructure = BodyData(([alternative, attachment_part], b"MIXED", (b"BOUNDARY", b"abc"), None, None))

    fake_client = _client(exists=1)
    fake_client.fetch.return_value = {
        1: {
            b"ENVELOPE": envelope,
            b"UID": 9,
            b"FLAGS": (b"\\Flagged", b"\\Seen", b"$RedMailRed"),
            b"BODYSTRUCTURE": bodystructure,
            b"BODY[HEADER.FIELDS (IMPORTANCE X-PRIORITY)]": b"Importance: high\r\n\r\n",
        }
    }

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        summary = ImapSession(_account()).fetch_folder_summaries()[0]

    assert summary.has_attachments is True
    assert summary.marker_color == "red"
    assert summary.importance == "high"
    assert summary.is_read is True


def test_fetch_folder_summaries_no_seen_flag_is_unread() -> None:
    envelope = SimpleNamespace(
        subject=None, from_=[_address(None, b"a", b"example.com")], date=None, message_id=None
    )
    fake_client = _client(exists=1)
    fake_client.fetch.return_value = {1: {b"ENVELOPE": envelope, b"UID": 9, b"FLAGS": (b"\\Flagged",)}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        summary = ImapSession(_account()).fetch_folder_summaries()[0]

    assert summary.is_read is False


def test_set_read_adds_seen_flag() -> None:
    fake_client = _client()
    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        ImapSession(_account()).set_read("INBOX", 7, True)
    fake_client.add_flags.assert_called_once_with([7], [b"\\Seen"])
    fake_client.remove_flags.assert_not_called()


def test_set_read_removes_seen_flag() -> None:
    fake_client = _client()
    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        ImapSession(_account()).set_read("INBOX", 7, False)
    fake_client.remove_flags.assert_called_once_with([7], [b"\\Seen"])
    fake_client.add_flags.assert_not_called()


def test_fetch_folder_summaries_alternative_only_has_no_attachment() -> None:
    # multipart/alternative (обычное HTML-письмо с текстовой версией) — без
    # вложений. is_multipart=True само по себе не должно давать скрепку.
    envelope = SimpleNamespace(
        subject=None, from_=[_address(None, b"a", b"example.com")], date=None, message_id=None
    )
    plain_part = BodyData((b"TEXT", b"PLAIN", (b"CHARSET", b"UTF-8"), None, None, b"BASE64", 114, 3, None, None, None))
    html_part = BodyData(
        (b"TEXT", b"HTML", (b"CHARSET", b"UTF-8"), None, None, b"QUOTED-PRINTABLE", 455, 10, None, None, None)
    )
    bodystructure = BodyData(([plain_part, html_part], b"ALTERNATIVE", (b"BOUNDARY", b"xyz"), None, None))

    fake_client = _client(exists=1)
    fake_client.fetch.return_value = {
        1: {b"ENVELOPE": envelope, b"UID": 9, b"BODYSTRUCTURE": bodystructure}
    }

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        summary = ImapSession(_account()).fetch_folder_summaries()[0]

    assert summary.has_attachments is False


def test_set_marker_sets_color_and_flagged() -> None:
    fake_client = _client()

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(_account())
        session.set_marker("INBOX", 7, "red")

    # Убираем другие цветные keyword'ы (на случай смены цвета) по одному —
    # см. test_set_marker_tolerates_server_rejecting_one_keyword ниже, почему
    # не разом — и ставим \Flagged + нужный.
    removed_flags = [call.args[1][0] for call in fake_client.remove_flags.call_args_list]
    assert b"$RedMailRed" not in removed_flags
    assert b"$RedMailBlue" in removed_flags
    assert len(removed_flags) == 5  # все цвета, кроме red
    fake_client.add_flags.assert_called_once_with([7], [b"\\Flagged", b"$RedMailRed"])
    # Папка одна и та же — второй раз переселектить не должны.
    assert fake_client.select_folder.call_count == 1


def test_set_marker_none_clears_flag_and_all_colors() -> None:
    fake_client = _client()

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(_account())
        session.set_marker("INBOX", 7, None)

    fake_client.add_flags.assert_not_called()
    removed_flags = [call.args[1][0] for call in fake_client.remove_flags.call_args_list]
    assert b"\\Flagged" in removed_flags
    assert b"$RedMailRed" in removed_flags
    assert b"$RedMailBlue" in removed_flags


def test_set_marker_tolerates_server_rejecting_one_keyword() -> None:
    # Реальный корпоративный сервер (VK Mail) отвечает "BAD [PARSE] Unable
    # to parse flag" на STORE с несколькими нашими keyword-флагами разом —
    # похоже, произвольные keyword'ы там вообще не разрешены. Один
    # отклонённый remove_flags не должен мешать остальным.
    fake_client = _client()
    fake_client.remove_flags.side_effect = [
        IMAPClientError("BAD [PARSE] Unable to parse flag"),
        None,
        None,
        None,
        None,
    ]

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(_account())
        session.set_marker("INBOX", 7, "red")  # не должно бросить исключение

    assert fake_client.remove_flags.call_count == 5
    fake_client.add_flags.assert_called_once_with([7], [b"\\Flagged", b"$RedMailRed"])


def test_set_marker_falls_back_to_plain_flagged_when_server_rejects_keyword() -> None:
    # Если сервер вообще не поддерживает произвольные keyword-флаги (не
    # только remove, но и add) — хотя бы стандартный \Flagged должен
    # выставиться, а не упасть ошибкой на весь маркер целиком.
    fake_client = _client()
    fake_client.add_flags.side_effect = [IMAPClientError("BAD [PARSE] Unable to parse flag"), None]

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(_account())
        session.set_marker("INBOX", 7, "red")  # не должно бросить исключение

    assert fake_client.add_flags.call_count == 2
    fake_client.add_flags.assert_any_call([7], [b"\\Flagged", b"$RedMailRed"])
    fake_client.add_flags.assert_any_call([7], [b"\\Flagged"])


def test_move_messages_uses_move_when_supported() -> None:
    fake_client = _client()
    fake_client.has_capability.return_value = True

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(_account())
        session.move_messages("INBOX", [1, 2], "Trash")

    fake_client.move.assert_called_once_with([1, 2], "Trash")
    fake_client.copy.assert_not_called()
    fake_client.delete_messages.assert_not_called()


def test_move_messages_falls_back_without_move_capability() -> None:
    fake_client = _client()
    fake_client.has_capability.return_value = False

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(_account())
        session.move_messages("INBOX", [1, 2], "Trash")

    fake_client.move.assert_not_called()
    fake_client.copy.assert_called_once_with([1, 2], "Trash")
    fake_client.delete_messages.assert_called_once_with([1, 2])
    fake_client.expunge.assert_called_once()


def test_move_messages_noop_for_empty_list() -> None:
    fake_client = _client()

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(_account())
        session.move_messages("INBOX", [], "Trash")

    fake_client.move.assert_not_called()
    fake_client.select_folder.assert_not_called()


def test_delete_messages_flags_and_expunges() -> None:
    fake_client = _client()

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(_account())
        session.delete_messages("INBOX", [1, 2, 3])

    fake_client.delete_messages.assert_called_once_with([1, 2, 3])
    fake_client.expunge.assert_called_once()


def test_delete_messages_noop_for_empty_list() -> None:
    fake_client = _client()

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(_account())
        session.delete_messages("INBOX", [])

    fake_client.delete_messages.assert_not_called()
    fake_client.expunge.assert_not_called()


def test_fetch_folder_summaries_uses_requested_folder() -> None:
    fake_client = _client(exists=0)

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        ImapSession(_account()).fetch_folder_summaries(folder="Archive")

    fake_client.select_folder.assert_called_once_with("Archive", readonly=False)


def test_fetch_folder_summaries_empty_mailbox() -> None:
    fake_client = _client(exists=0)

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        summaries = ImapSession(_account()).fetch_folder_summaries()

    assert summaries == []
    fake_client.fetch.assert_not_called()


def test_format_address_decodes_rfc2047_display_name() -> None:
    # Реальный кейс с боевого ящика: некоторые отправители (например, Авито)
    # присылают имя в ENVELOPE закодированным словом, а не сырым UTF-8.
    encoded_name = Header("Авито", "utf-8").encode().encode("ascii")
    fake_client = _client(exists=1)
    envelope = SimpleNamespace(
        subject=None,
        from_=[_address(encoded_name, b"noreply", b"avito.ru")],
        date=None,
        message_id=None,
    )
    fake_client.fetch.return_value = {1: {b"ENVELOPE": envelope, b"UID": 1}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        summaries = ImapSession(_account()).fetch_folder_summaries()

    assert summaries[0].sender == "Авито"
    assert summaries[0].sender_email == "noreply@avito.ru"


def test_format_address_without_display_name() -> None:
    fake_client = _client(exists=1)
    envelope = SimpleNamespace(
        subject=None,
        from_=[_address(None, b"ivan", b"example.com")],
        date=None,
        message_id=None,
    )
    fake_client.fetch.return_value = {1: {b"ENVELOPE": envelope, b"UID": 1}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        summaries = ImapSession(_account()).fetch_folder_summaries()

    assert summaries[0].sender == "ivan@example.com"
    assert summaries[0].sender_email == "ivan@example.com"
    assert summaries[0].subject == "(без темы)"
    assert summaries[0].date == ""
    assert summaries[0].message_id == ""


def test_fetch_message_content_plain_text() -> None:
    fake_client = _client()
    raw = (
        b"From: ivan@example.com\r\n"
        b"To: test@example.com\r\n"
        b"Subject: Test\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
    ) + "Привет!".encode("utf-8")
    fake_client.fetch.return_value = {5: {b"BODY[]": raw}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        content = ImapSession(_account()).fetch_message_content("INBOX", 5)

    assert content.text == "Привет!"
    assert content.attachments == []
    fake_client.select_folder.assert_called_once_with("INBOX", readonly=False)


def test_fetch_message_content_html_only_shows_placeholder() -> None:
    fake_client = _client()
    raw = (
        b"From: ivan@example.com\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<p>hello</p>"
    )
    fake_client.fetch.return_value = {5: {b"BODY[]": raw}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        content = ImapSession(_account()).fetch_message_content("INBOX", 5)

    assert "HTML" in content.text


def test_fetch_message_content_extracts_attachment() -> None:
    from email.message import EmailMessage

    built = EmailMessage()
    built["From"] = "ivan@example.com"
    built["Subject"] = "With attachment"
    built.set_content("Смотри файл во вложении.")
    built.add_attachment(b"file-bytes-here", maintype="text", subtype="plain", filename="notes.txt")
    raw = built.as_bytes()

    fake_client = _client()
    fake_client.fetch.return_value = {5: {b"BODY[]": raw}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        content = ImapSession(_account()).fetch_message_content("INBOX", 5)

    assert content.text.strip() == "Смотри файл во вложении."
    assert len(content.attachments) == 1
    attachment = content.attachments[0]
    assert attachment.filename == "notes.txt"
    assert attachment.payload == b"file-bytes-here"
    assert attachment.size == len(b"file-bytes-here")


def test_search_uids_all() -> None:
    fake_client = _client()
    fake_client.search.return_value = [1, 2, 3]

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        uids = ImapSession(_account()).search_uids("INBOX")

    assert uids == [1, 2, 3]
    fake_client.search.assert_called_once_with("ALL")


def test_search_uids_before_date() -> None:
    from datetime import date

    fake_client = _client()
    fake_client.search.return_value = [5]

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        uids = ImapSession(_account()).search_uids("INBOX", before=date(2026, 1, 15))

    assert uids == [5]
    fake_client.search.assert_called_once_with(["BEFORE", "15-Jan-2026"])


def test_fetch_message_content_html_alternative_populates_html_field() -> None:
    from email.message import EmailMessage

    built = EmailMessage()
    built["From"] = "ivan@example.com"
    built["Subject"] = "HTML letter"
    built.set_content("plain fallback")
    built.add_alternative("<p>Hello <b>world</b></p>", subtype="html")
    raw = built.as_bytes()

    fake_client = _client()
    fake_client.fetch.return_value = {5: {b"BODY[]": raw}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        content = ImapSession(_account()).fetch_message_content("INBOX", 5)

    assert content.text.strip() == "plain fallback"
    assert "<b>world</b>" in content.html


def test_fetch_message_content_extracts_inline_cid_image_not_as_attachment() -> None:
    from email.message import EmailMessage

    built = EmailMessage()
    built["From"] = "ivan@example.com"
    built["Subject"] = "With inline image"
    built.set_content("plain fallback")
    built.add_alternative('<p>hi</p><img src="cid:img1">', subtype="html")
    html_part = built.get_payload()[1]
    html_part.add_related(b"fake-png-bytes", maintype="image", subtype="png", cid="<img1>")
    raw = built.as_bytes()

    fake_client = _client()
    fake_client.fetch.return_value = {5: {b"BODY[]": raw}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        content = ImapSession(_account()).fetch_message_content("INBOX", 5)

    assert content.attachments == []  # встроенная картинка — не отдельное вложение
    assert "img1" in content.inline_images
    content_type, payload = content.inline_images["img1"]
    assert content_type == "image/png"
    assert payload == b"fake-png-bytes"


def test_fetch_message_content_decodes_rfc2047_attachment_filename() -> None:
    # Некоторые сервера (встречалось от Exchange) кодируют имя файла в
    # заголовке Content-Disposition через RFC 2047, а не RFC 2231 —
    # email.message.get_filename() такое не декодирует сам.
    raw = (
        b"From: ivan@example.com\r\n"
        b"Content-Type: multipart/mixed; boundary=XYZ\r\n"
        b"\r\n"
        b"--XYZ\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body\r\n"
        b"--XYZ\r\n"
        b"Content-Type: application/octet-stream\r\n"
        b'Content-Disposition: attachment; filename="=?UTF-8?B?0YLQtdGB0YIudHh0?="\r\n'
        b"\r\n"
        b"filedata\r\n"
        b"--XYZ--\r\n"
    )
    fake_client = _client()
    fake_client.fetch.return_value = {5: {b"BODY[]": raw}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        content = ImapSession(_account()).fetch_message_content("INBOX", 5)

    assert content.attachments[0].filename == "тест.txt"


def test_fetch_message_content_keeps_inline_calendar_part_without_disposition() -> None:
    # Не все серверы ставят Content-Disposition: attachment/filename на
    # text/calendar-часть приглашения (RFC 5546 этого не требует) — раньше
    # такая часть тихо терялась (не текст, не HTML, без имени файла).
    raw = (
        b"From: organizer@example.com\r\n"
        b"Subject: Invite\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n'
        b"\r\n"
        b"--B\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"You are invited.\r\n"
        b"--B\r\n"
        b"Content-Type: text/calendar; method=REQUEST; charset=utf-8\r\n"
        b"\r\n"
        b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
        b"--B--\r\n"
    )
    fake_client = _client()
    fake_client.fetch.return_value = {5: {b"BODY[]": raw}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        content = ImapSession(_account()).fetch_message_content("INBOX", 5)

    assert len(content.attachments) == 1
    assert content.attachments[0].content_type == "text/calendar"
    assert content.attachments[0].payload == b"BEGIN:VCALENDAR\r\nEND:VCALENDAR"


def test_fetch_message_content_bare_calendar_message() -> None:
    # Редкий случай: всё письмо целиком — один text/calendar без обёртки
    # multipart (не приходит от Gmail/Exchange, но валидно по MIME).
    raw = (
        b"From: organizer@example.com\r\n"
        b"Subject: Invite\r\n"
        b"Content-Type: text/calendar; method=REQUEST; charset=utf-8\r\n"
        b"\r\n"
        b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
    )
    fake_client = _client()
    fake_client.fetch.return_value = {5: {b"BODY[]": raw}}

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        content = ImapSession(_account()).fetch_message_content("INBOX", 5)

    assert len(content.attachments) == 1
    assert content.attachments[0].content_type == "text/calendar"


def test_close_logs_out() -> None:
    fake_client = MagicMock()

    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(_account())
        session.close()

    fake_client.logout.assert_called_once()
