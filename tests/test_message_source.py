from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: E402

from redmail import archive_store  # noqa: E402
from redmail.imap_client import MessageContent, MessageSummary  # noqa: E402
from redmail.mailbox import ArchiveSource  # noqa: E402
from redmail.ui import main_window as mw  # noqa: E402
from redmail.ui.message_source import MAX_SHOWN_BYTES, MessageSourceWindow, decoded_headers, split_headers  # noqa: E402

RAW = (
    b"Received: from mx.example.com by mail.example.com\r\n"
    b"From: =?utf-8?b?0J/QvtC90L7QvNCw0YDQtdCyINCgLtChLg==?= <rs@example.com>\r\n"
    b"Subject: =?utf-8?b?0J7QsdC90L7QstC70LXQvdC40LUg0KPQpQ==?=\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"\xd0\x9f\xd1\x80\xd0\xb8\xd0\xb2\xd0\xb5\xd1\x82\r\n"
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_split_and_decode_headers() -> None:
    headers, body = split_headers(RAW)
    assert headers.startswith("Received:") and body.startswith("Привет")
    decoded = dict(decoded_headers(RAW))
    assert decoded["From"] == '"Пономарев Р.С." <rs@example.com>'
    assert decoded["Subject"] == "Обновление УХ"
    # кривой заголовок показывается как есть, а не роняет окно
    assert dict(decoded_headers(b"To: \xd0\x9f.\xd0\xa0.<x@y>\r\n\r\n"))["To"]


def test_archive_source_returns_original_bytes(tmp_path: Path) -> None:
    archive = tmp_path / "a.rmarchive"
    archive_store.create_archive(archive)
    uid = archive_store.append_raw_message(archive, "INBOX", RAW)
    assert ArchiveSource(archive).original_message("INBOX", uid) == RAW


def test_window_shows_source_headers_and_finds_text() -> None:
    _app()
    window = MessageSourceWindow("Тема", RAW)
    assert "Received: from mx.example.com" in window.source_edit.toPlainText()
    assert window.headers_table.rowCount() == 4
    assert window.note_label.isHidden()
    window.find_edit.setText("Content-Type")
    assert window.find_next(backward=False)
    assert window.source_edit.textCursor().selectedText() == "Content-Type"
    window.find_edit.setText("нет такого")
    assert not window.find_next(backward=False)


def test_window_marks_rebuilt_and_truncated_messages() -> None:
    _app()
    rebuilt = MessageSourceWindow("Тема", RAW, original=False)
    assert not rebuilt.note_label.isHidden() and "не оригинал" in rebuilt.note_label.text()
    big = MessageSourceWindow("Тема", RAW + b"x" * (MAX_SHOWN_BYTES + 10))
    assert "Показано" in big.note_label.text()
    assert len(big.source_edit.toPlainText()) <= MAX_SHOWN_BYTES


class _SyncWorker:
    """Вместо QThread: выполняет сразу при start() — тест проверяет логику
    окна, а не планировщик потоков Qt."""

    def __init__(self, fn, *args, parent=None, **kwargs):
        self._call = lambda: fn(*args, **kwargs)
        self._done, self._failed = [], []
        self.succeeded = SimpleNamespace(connect=self._done.append)
        self.failed = SimpleNamespace(connect=self._failed.append)

    def start(self):
        try:
            result = self._call()
        except Exception as exc:
            for callback in self._failed:
                callback(str(exc))
        else:
            for callback in self._done:
                callback(result)


def _stub_window(source, monkeypatch):
    errors: list[str] = []
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda _parent, _title, text: errors.append(text))
    monkeypatch.setattr(mw, "_CallableWorker", _SyncWorker)
    host = QMainWindow()
    host.errors = errors
    host.active_source = source
    host.current_folder = "INBOX"
    host.current_content = None
    host.selected_summary = None
    host._background_workers = []
    host._message_windows = []
    return host


def test_show_source_falls_back_to_local_copy_when_server_fails(monkeypatch) -> None:
    _app()
    summary = MessageSummary(uid=7, subject="Тема", sender="Пономарев", sender_email="rs@example.com",
                             date="2026-09-16 09:02", message_id="<m@example.com>")

    def offline(_folder, _uid):
        raise OSError("нет сети")

    source = SimpleNamespace(
        original_message=offline,
        message_content=lambda _f, _u: MessageContent(text="Привет", html="<p>Привет</p>", from_="Пономарев Р.С.<rs@example.com>"),
    )
    host = _stub_window(source, monkeypatch)
    mw.MainWindow._show_message_source(host, summary)
    assert host.errors == []
    assert len(host._message_windows) == 1
    window = host._message_windows[0]
    assert "не оригинал" in window.note_label.text()
    assert "rs@example.com" in window.source_edit.toPlainText()


def test_show_source_uses_server_original(monkeypatch) -> None:
    _app()
    summary = MessageSummary(uid=7, subject="Тема", sender="", sender_email="", date="", message_id="")
    host = _stub_window(SimpleNamespace(original_message=lambda _f, _u: RAW), monkeypatch)
    mw.MainWindow._show_message_source(host, summary)
    assert host.errors == []
    window = host._message_windows[0]
    assert window.note_label.isHidden()
    assert window.source_edit.toPlainText().startswith("Received:")
