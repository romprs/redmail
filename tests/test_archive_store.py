from __future__ import annotations

import mailbox
import sqlite3
import sys
from email.message import EmailMessage
from pathlib import Path

import pytest

from redmail import archive_store


def _build_message(
    subject: str, sender: str, body: str = "Текст письма", with_attachment: bool = False
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = "someone@example.com"
    msg["Date"] = "Tue, 18 Aug 2026 10:00:00 +0300"
    msg.set_content(body)
    if with_attachment:
        msg.add_attachment(b"file-bytes", maintype="text", subtype="plain", filename="note.txt")
    return msg


def test_create_and_is_archive_file(tmp_path: Path) -> None:
    archive_path = tmp_path / "test.rmarchive"
    assert archive_store.is_archive_file(archive_path) is False
    archive_store.create_archive(archive_path)
    assert archive_store.is_archive_file(archive_path) is True


def test_is_archive_file_rejects_unrelated_sqlite(tmp_path: Path) -> None:
    other = tmp_path / "other.sqlite3"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()
    assert archive_store.is_archive_file(other) is False


def test_is_archive_file_false_for_missing_file(tmp_path: Path) -> None:
    assert archive_store.is_archive_file(tmp_path / "missing.rmarchive") is False


def test_append_and_list_messages(tmp_path: Path) -> None:
    archive_path = tmp_path / "test.rmarchive"
    archive_store.create_archive(archive_path)

    msg = _build_message("Привет", "Иван <ivan@example.com>", with_attachment=True)
    new_id = archive_store.append_raw_message(archive_path, "Входящие", msg.as_bytes())

    assert archive_store.list_folders(archive_path) == ["Входящие"]

    summaries = archive_store.list_messages(archive_path, "Входящие")
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.uid == new_id
    assert summary.subject == "Привет"
    assert summary.sender == "Иван"
    assert summary.sender_email == "ivan@example.com"
    assert summary.has_attachments is True
    assert summary.is_read is True  # архив — уже разобранная почта

    content = archive_store.get_message_content(archive_path, new_id)
    assert content.text.strip() == "Текст письма"
    assert len(content.attachments) == 1
    assert content.attachments[0].filename == "note.txt"


def test_rename_folder(tmp_path: Path) -> None:
    archive_path = tmp_path / "test.rmarchive"
    archive_store.create_archive(archive_path)
    msg = _build_message("Привет", "Иван <ivan@example.com>")
    archive_store.append_raw_message(archive_path, "Импорт", msg.as_bytes())

    archive_store.rename_folder(archive_path, "Импорт", "Из Outlook")

    assert archive_store.list_folders(archive_path) == ["Из Outlook"]
    assert len(archive_store.list_messages(archive_path, "Из Outlook")) == 1
    assert archive_store.list_messages(archive_path, "Импорт") == []


def test_append_raw_message_auto_creates_archive(tmp_path: Path) -> None:
    # append_raw_message используется и для "выгрузить в архив" из живого
    # ящика — не должен требовать отдельного шага "сначала создайте файл".
    archive_path = tmp_path / "auto.rmarchive"
    msg = _build_message("Тест", "a@x.com")
    archive_store.create_archive(archive_path)
    archive_store.append_raw_message(archive_path, "F", msg.as_bytes())
    assert len(archive_store.list_messages(archive_path, "F")) == 1


def test_message_without_display_name_falls_back_to_address(tmp_path: Path) -> None:
    archive_path = tmp_path / "test.rmarchive"
    archive_store.create_archive(archive_path)
    msg = _build_message("Тест", "ivan@example.com")
    archive_store.append_raw_message(archive_path, "F", msg.as_bytes())
    summary = archive_store.list_messages(archive_path, "F")[0]
    assert summary.sender == "ivan@example.com"
    assert summary.sender_email == "ivan@example.com"


def test_encoded_sender_name_without_angle_brackets_is_decoded(tmp_path: Path) -> None:
    # PST-письма иногда дают From без реального e-mail в <угловых
    # скобках> — только закодированное имя целиком. parseaddr() в этом
    # случае кладёт всю строку в "адрес", а не в "имя" (RFC 822 не может
    # разделить их без <>) — реальный баг: имя оставалось нерасшифрованным
    # как "=?utf-8?b?...?=" в списке писем.
    archive_path = tmp_path / "test.rmarchive"
    archive_store.create_archive(archive_path)
    msg = _build_message("Тест", "=?utf-8?B?0JDQu9C10LrRgdCw0L3QtNGA0L7QstCw?=")
    archive_store.append_raw_message(archive_path, "F", msg.as_bytes())
    summary = archive_store.list_messages(archive_path, "F")[0]
    assert summary.sender == "Александрова"
    assert summary.sender_email == ""


def test_delete_messages(tmp_path: Path) -> None:
    archive_path = tmp_path / "test.rmarchive"
    archive_store.create_archive(archive_path)
    msg_id = archive_store.append_raw_message(archive_path, "F", _build_message("A", "a@x.com").as_bytes())
    archive_store.delete_messages(archive_path, [msg_id])
    assert archive_store.list_messages(archive_path, "F") == []


def test_delete_messages_noop_for_empty_list(tmp_path: Path) -> None:
    archive_path = tmp_path / "test.rmarchive"
    archive_store.create_archive(archive_path)
    archive_store.append_raw_message(archive_path, "F", _build_message("A", "a@x.com").as_bytes())
    archive_store.delete_messages(archive_path, [])
    assert len(archive_store.list_messages(archive_path, "F")) == 1


def test_get_message_raw_missing_id_raises(tmp_path: Path) -> None:
    archive_path = tmp_path / "test.rmarchive"
    archive_store.create_archive(archive_path)
    with pytest.raises(KeyError):
        archive_store.get_message_raw(archive_path, 999)


def test_import_mbox(tmp_path: Path) -> None:
    mbox_path = tmp_path / "source.mbox"
    box = mailbox.mbox(str(mbox_path))
    box.add(_build_message("Письмо 1", "a@x.com"))
    box.add(_build_message("Письмо 2", "b@x.com", with_attachment=True))
    box.flush()
    box.close()

    archive_path = tmp_path / "test.rmarchive"
    count = archive_store.import_mbox(archive_path, mbox_path, "Импорт")

    assert count == 2
    summaries = archive_store.list_messages(archive_path, "Импорт")
    assert {s.subject for s in summaries} == {"Письмо 1", "Письмо 2"}
    with_attachment = next(s for s in summaries if s.subject == "Письмо 2")
    assert with_attachment.has_attachments is True


def test_import_maildir(tmp_path: Path) -> None:
    maildir_path = tmp_path / "source_maildir"
    box = mailbox.Maildir(str(maildir_path), create=True)
    box.add(_build_message("Из мейлдира", "c@x.com"))
    box.close()

    archive_path = tmp_path / "test.rmarchive"
    count = archive_store.import_maildir(archive_path, maildir_path, "Импорт")

    assert count == 1
    summaries = archive_store.list_messages(archive_path, "Импорт")
    assert summaries[0].subject == "Из мейлдира"


def test_decode_mime_words_falls_back_on_malformed_input() -> None:
    # Реальная жалоба: "загрузка из pst все ещё некорректно загружает...
    # авторов" — decode_header() поднимает HeaderParseError на
    # действительно повреждённом base64 (не просто пустую строку не
    # обрабатывает), и раньше это роняло весь импорт .pst.
    malformed = "=?utf-8?B?!!!invalid-base64!!!?="
    assert archive_store._decode_mime_words(malformed) == malformed


def test_import_pst_folder_skips_broken_message_and_keeps_the_rest(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    archive_path = tmp_path / "test.rmarchive"
    archive_store.create_archive(archive_path)

    good_message = MagicMock()
    good_message.get_subject.return_value = "Хорошее письмо"
    good_message.get_sender_name.return_value = "Иван Иванов"
    good_message.get_delivery_time.return_value = None
    good_message.get_transport_headers.return_value = ""
    good_message.get_plain_text_body.return_value = "Текст"
    good_message.get_html_body.return_value = None
    good_message.get_number_of_attachments.return_value = 0

    broken_message = MagicMock()
    broken_message.get_subject.side_effect = RuntimeError("повреждённое MAPI-свойство")

    fake_folder = SimpleNamespace(
        get_name=lambda: "Входящие",
        sub_messages=[broken_message, good_message],
        sub_folders=[],
    )

    with sqlite3.connect(archive_path) as conn:
        count = archive_store._import_pst_folder(conn, fake_folder, "")
        conn.commit()

    assert count == 1  # только хорошее письмо — сломанное пропущено, а не уронило всё
    summaries = archive_store.list_messages(archive_path, "Входящие")
    assert len(summaries) == 1
    assert summaries[0].sender == "Иван Иванов"


def _fake_pst_message(subject: str) -> object:
    from unittest.mock import MagicMock

    message = MagicMock()
    message.get_subject.return_value = subject
    message.get_sender_name.return_value = "Отправитель"
    message.get_delivery_time.return_value = None
    message.get_transport_headers.return_value = ""
    message.get_plain_text_body.return_value = "Текст"
    message.get_html_body.return_value = None
    message.get_number_of_attachments.return_value = 0
    return message


def test_import_pst_skips_root_and_top_of_personal_folders_wrapper_levels(tmp_path: Path) -> None:
    # Реальный .pst: корень хранилища безымянный, под ним ровно одна папка
    # "Top of Personal Folders" — оба уровня служебные, Outlook их не
    # показывает. Раньше архив показывал письма под путём
    # "(без имени)/Top of Personal Folders/Входящие" вместо "Входящие"
    # (жалоба: "зачем эта структура?").
    from types import SimpleNamespace

    archive_path = tmp_path / "test.rmarchive"
    archive_store.create_archive(archive_path)

    inbox = SimpleNamespace(get_name=lambda: "Входящие", sub_messages=[_fake_pst_message("Привет")], sub_folders=[])
    top_of_personal = SimpleNamespace(get_name=lambda: "Top of Personal Folders", sub_messages=[], sub_folders=[inbox])
    root = SimpleNamespace(get_name=lambda: "", sub_messages=[], sub_folders=[top_of_personal])

    with sqlite3.connect(archive_path) as conn:
        count = archive_store._import_pst_folder(conn, root, "")
        conn.commit()

    assert count == 1
    assert archive_store.list_folders(archive_path) == ["Входящие"]


def test_import_pst_skips_calendar_and_contacts_folders() -> None:
    # Папки "Календарь"/"Контакты" содержат не почту, а события/карточки —
    # get_plain_text_body() и т.п. на таких элементах бессмысленны, раньше
    # они всё равно попадали в архив как мусорные "письма" (жалоба:
    # "календарь загрузился как письма, контакты тоже как письма").
    from types import SimpleNamespace

    calendar_item = _fake_pst_message("не должно попасть в архив")
    calendar_folder = SimpleNamespace(get_name=lambda: "Календарь", sub_messages=[calendar_item], sub_folders=[])
    contacts_folder = SimpleNamespace(get_name=lambda: "Contacts", sub_messages=[_fake_pst_message("тоже нет")], sub_folders=[])
    root = SimpleNamespace(get_name=lambda: "", sub_messages=[], sub_folders=[calendar_folder, contacts_folder])

    conn = sqlite3.connect(":memory:")
    conn.executescript(archive_store._SCHEMA)
    count = archive_store._import_pst_folder(conn, root, "")

    assert count == 0
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_rename_folder_renames_nested_subfolders_too(tmp_path: Path) -> None:
    archive_path = tmp_path / "test.rmarchive"
    archive_store.create_archive(archive_path)
    msg = _build_message("Отчёт", "a@x.com")
    archive_store.append_raw_message(archive_path, "Работа/Проекты", msg.as_bytes())
    archive_store.append_raw_message(archive_path, "Работа", msg.as_bytes())
    archive_store.append_raw_message(archive_path, "Личное", msg.as_bytes())  # не должно затронуться

    archive_store.rename_folder(archive_path, "Работа", "Дела")

    folders = set(archive_store.list_folders(archive_path))
    assert folders == {"Дела", "Дела/Проекты", "Личное"}


def test_rename_folder_escapes_sql_like_wildcards_in_old_name(tmp_path: Path) -> None:
    # "%"/"_" в имени папки — это спецсимволы LIKE, не должны трактоваться
    # как wildcard при поиске вложенных подпапок для переименования.
    archive_path = tmp_path / "test.rmarchive"
    archive_store.create_archive(archive_path)
    msg = _build_message("Отчёт", "a@x.com")
    archive_store.append_raw_message(archive_path, "100%_Готово", msg.as_bytes())
    archive_store.append_raw_message(archive_path, "100X_ГотовоНЕТ/Sub", msg.as_bytes())  # "%" != "X" буквально

    archive_store.rename_folder(archive_path, "100%_Готово", "Сделано")

    folders = set(archive_store.list_folders(archive_path))
    assert "Сделано" in folders
    assert "100X_ГотовоНЕТ/Sub" in folders  # не затронуто


def test_import_pst_without_pypff_raises_clear_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "pypff", None)  # имитируем отсутствие пакета
    archive_path = tmp_path / "test.rmarchive"
    with pytest.raises(RuntimeError, match="libpff-python"):
        archive_store.import_pst(archive_path, tmp_path / "doesnotmatter.pst")
