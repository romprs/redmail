from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from redmail.imap_client import Attachment  # noqa: E402
from redmail.ui import main_window as mw  # noqa: E402

NESTED = (
    b"From: =?utf-8?b?0JjQstCw0L3QvtCy?= <ivanov@example.com>\r\n"
    b"To: petrov@example.com\r\n"
    b"Subject: =?utf-8?b?0J/QtdGA0LXRgdC70LDQvdC90L7QtQ==?=\r\n"
    b"Date: Tue, 15 Sep 2026 10:00:00 +0300\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"\xd0\xa2\xd0\xb5\xd0\xba\xd1\x81\xd1\x82 \xd0\xbf\xd0\xb8\xd1\x81\xd1\x8c\xd0\xbc\xd0\xb0\r\n"
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_eml_attachment_is_recognised_by_type_and_extension() -> None:
    """Вложенное письмо приходит то с типом message/rfc822, то просто
    файлом .eml — узнаём оба случая, иначе двойной щелчок уходил в систему,
    где для .eml обработчика обычно нет."""
    assert mw.is_eml_attachment("письмо.eml")
    assert mw.is_eml_attachment("без расширения", "message/rfc822")
    assert mw.is_eml_attachment("письмо.EML")
    assert not mw.is_eml_attachment("отчёт.pdf", "application/pdf")
    assert not mw.is_eml_attachment("таблица.xlsx")


def test_eml_window_shows_headers_and_body() -> None:
    _app()
    window = mw.EmlAttachmentWindow(NESTED, "письмо.eml")
    try:
        assert window.windowTitle() == "Пересланное"
        assert "ivanov@example.com" in window._content.from_
        assert "Текст письма" in window._content.text
    finally:
        window.close()


def test_eml_window_survives_broken_message() -> None:
    """Битое вложение не должно ронять программу — показываем, что письмо
    не разобралось."""
    _app()
    window = mw.EmlAttachmentWindow(b"\xff\xfe not a message at all", "битое.eml")
    try:
        assert window.isVisible() is False  # окно создано, показывать его — дело вызывающего
    finally:
        window.close()


def test_open_attachment_payload_opens_window_for_eml(monkeypatch) -> None:
    _app()
    opened: list[str] = []
    monkeypatch.setattr(mw.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    holder = mw.QWidget()
    holder._temp_attachment_dirs = []
    try:
        mw.open_attachment_payload(
            holder, Attachment(filename="письмо.eml", content_type="message/rfc822", payload=NESTED)
        )
        assert opened == []  # письмо показано своим окном, наружу ничего не отдавали

        mw.open_attachment_payload(
            holder, Attachment(filename="отчёт.txt", content_type="text/plain", payload=b"abc")
        )
        assert len(opened) == 1 and opened[0].endswith(".txt")
    finally:
        holder.close()
