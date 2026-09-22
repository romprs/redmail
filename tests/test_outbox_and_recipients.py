"""Отправка: адрес без почты не уходит; письмо видно в «Исходящих» до
отправки и остаётся там при ошибке; ссылки target=_blank в письме
открываются; журнал после ротации не пустеет."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from redmail import applog, contact_store, outbox  # noqa: E402
from redmail.smtp_client import OutgoingAttachment, OutgoingMessage  # noqa: E402
from redmail.ui import main_window  # noqa: E402

@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


PATRIEVSKAYA = contact_store.Contact(
    display_name="Патриевская Ольга Павловна", emails=["o.patrievskaya@diagnostika.gazprom.ru"]
)
PETROV_A = contact_store.Contact(display_name="Петров Андрей", emails=["a.petrov@x.ru"])
PETROV_B = contact_store.Contact(display_name="Петров Борис", emails=["b.petrov@x.ru"])


def test_bare_name_is_not_an_address() -> None:
    """Письмо на «Патриевская» ушло и вернулось «550 invalid recipient»."""
    assert main_window._parse_recipient_list("Патриевская") == []
    assert main_window._parse_recipient_list("Патриевская <Патриевская>") == []
    assert main_window._parse_recipient_list("Ольга <o.p@diagnostika.gazprom.ru>") == ["o.p@diagnostika.gazprom.ru"]
    assert not main_window._looks_like_email("user@localhost")
    assert main_window._looks_like_email("RSPonomarev@amurgpz.ru")


def test_name_is_resolved_by_address_book() -> None:
    text, unresolved = main_window._resolve_recipient_names("патриевская", [PATRIEVSKAYA, PETROV_A])
    assert unresolved == []
    assert text == "Патриевская Ольга Павловна <o.patrievskaya@diagnostika.gazprom.ru>"


def test_ambiguous_or_unknown_name_blocks_sending() -> None:
    text, unresolved = main_window._resolve_recipient_names("Петров, boss@x.ru, Сидоров", [PETROV_A, PETROV_B])
    assert unresolved == ["Петров", "Сидоров"]
    assert text == "Петров, boss@x.ru, Сидоров"


def test_compose_dialog_refuses_unresolved_recipient(qapp, monkeypatch) -> None:
    warnings = []
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    dialog = main_window.ComposeDialog(None, to="Сидоров", contacts=[PATRIEVSKAYA])
    dialog.accept()
    assert dialog.result() != main_window.QDialog.DialogCode.Accepted
    assert warnings and "Сидоров" in warnings[0]

    dialog = main_window.ComposeDialog(None, to="Патриевская", contacts=[PATRIEVSKAYA])
    dialog.accept()
    assert dialog.result() == main_window.QDialog.DialogCode.Accepted
    assert dialog.recipients() == ["o.patrievskaya@diagnostika.gazprom.ru"]


def _message() -> OutgoingMessage:
    return OutgoingMessage(
        sender="me@x.ru", to=["a@x.ru"], cc=["b@x.ru"], bcc=["c@x.ru"], subject="Иерархия",
        body="текст", html_body="<p>текст</p>", in_reply_to="<id@x>",
        attachments=[OutgoingAttachment("a.xlsx", "application/vnd.ms-excel", b"\x00\x01", {"name": "a"})],
        inline_images={"img1": ("image/png", b"\x89PNG")},
    )


def test_outbox_keeps_failed_message_across_restart(tmp_path: Path) -> None:
    box = outbox.Outbox(tmp_path / "outbox")
    item = box.add("acc", _message(), source_draft=("Черновики", 7), edit_html="<p>без колонтитула</p>")
    box.mark_failed(item, "timed out")
    again = outbox.Outbox(tmp_path / "outbox").items()
    assert len(again) == 1
    restored = again[0]
    assert restored.status == outbox.FAILED and restored.error == "timed out"
    assert restored.message == item.message
    assert restored.source_draft == ("Черновики", 7) and restored.edit_html == "<p>без колонтитула</p>"
    box.remove(restored)
    assert box.items() == []


def test_interrupted_sending_becomes_failed(tmp_path: Path) -> None:
    box = outbox.Outbox(tmp_path / "outbox")
    box.add("acc", _message())
    items = outbox.Outbox(tmp_path / "outbox").recover_interrupted()
    assert items[0].status == outbox.FAILED and "прервана" in items[0].error


def test_outbox_ignores_foreign_and_broken_files(tmp_path: Path) -> None:
    folder = tmp_path / "outbox"
    folder.mkdir()
    (folder / "notes.json").write_text("{}", encoding="utf-8")
    (folder / ("a" * 32 + ".json")).write_text("не json", encoding="utf-8")
    assert outbox.Outbox(folder).items() == []


@pytest.mark.skipif(os.name == "nt", reason="права файлов unix")
def test_outbox_file_is_private(tmp_path: Path) -> None:
    box = outbox.Outbox(tmp_path / "outbox")
    item = box.add("acc", _message())
    assert (tmp_path / "outbox" / f"{item.id}.json").stat().st_mode & 0o077 == 0


def test_blank_target_link_opens_in_browser(qapp, monkeypatch) -> None:
    """Кнопка «Перейти к календарям» в письме VK (target=_blank) молчала."""
    opened = []
    monkeypatch.setattr(main_window.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    page = main_window._ExternalLinkPage(None)
    link = main_window.QWebEnginePage.NavigationType.NavigationTypeLinkClicked
    assert page.acceptNavigationRequest(main_window.QUrl("https://calendar.vkm.corp.amurgpz.ru/calendars/"), link, True) is False
    assert opened == ["https://calendar.vkm.corp.amurgpz.ru/calendars/"]
    assert main_window._open_mail_link(main_window.QUrl("file:///etc/passwd")) is False
    assert opened == ["https://calendar.vkm.corp.amurgpz.ru/calendars/"]


def test_log_tail_includes_previous_file_after_rotation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(applog, "log_dir", lambda: tmp_path)
    (tmp_path / "redmail.log.1").write_bytes("старая строка почты\n".encode("utf-8"))
    (tmp_path / "redmail.log").write_bytes("новая строка\n".encode("utf-8"))
    assert applog.tail_text() == "старая строка почты\nновая строка\n"
