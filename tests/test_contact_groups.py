from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from redmail import contact_store
from redmail.contact_store import Contact, normalize_emails


def test_normalize_emails_detects_distribution_list_with_rfc2047_names() -> None:
    # Экспорт списка рассылки Outlook: участники «Имя <адрес>», имена в koi8-r.
    values = [
        "0msk1-ksc-01 <0msk1-ksc-01@amurgpz.ru>",
        "=?koi8-r?q?=e1=c2=c4=d5=cc=cc=c9=ce_?= =?koi8-r?q?=e9=cc=d8=c6=c1=d4?= <igabdullin@amurgpz.ru>",
        "0msk1-ksc-01 <0MSK1-KSC-01@amurgpz.ru>",  # повтор другим регистром
    ]
    addresses, is_group, labels = normalize_emails(values)
    assert is_group is True
    assert addresses == ["0msk1-ksc-01@amurgpz.ru", "igabdullin@amurgpz.ru"]
    assert labels[1] == "Абдуллин Ильфат <igabdullin@amurgpz.ru>"


def test_normalize_emails_plain_addresses_are_not_a_group() -> None:
    addresses, is_group, labels = normalize_emails(["Ivan@Example.com", "ivan.home@example.com"])
    assert is_group is False and addresses == ["ivan@example.com", "ivan.home@example.com"]
    assert labels == addresses


def test_import_vcard_group_gets_flag_members_and_person_single_address(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcontacts"
    vcf = (
        "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:g1\r\nFN:АмурГПЗ Все получатели\r\n"
        "EMAIL:0msk1-ksc-01 <0msk1-ksc-01@amurgpz.ru>\r\n"
        "EMAIL:=?koi8-r?q?=e1=c2=c4=d5=cc=cc=c9=ce?= <igabdullin@amurgpz.ru>\r\n"
        "END:VCARD\r\n"
        "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:p1\r\nN:Архипова;Татьяна;;;\r\n"
        "EMAIL:taarkhipova@amurgpz.ru\r\nEMAIL:taradchina@amurgpz.ru\r\nEND:VCARD\r\n"
    ).encode("utf-8")
    assert contact_store.import_vcard(path, vcf) == 2
    by_name = {c.display_name: c for c in contact_store.list_contacts(path)}
    group = by_name["АмурГПЗ Все получатели"]
    assert group.is_group and group.emails == ["0msk1-ksc-01@amurgpz.ru", "igabdullin@amurgpz.ru"]
    assert "Абдуллин <igabdullin@amurgpz.ru>" in group.notes
    person = by_name["Архипова Татьяна"]
    assert not person.is_group and person.emails == ["taarkhipova@amurgpz.ru"]
    assert "taradchina@amurgpz.ru" in person.notes


def test_import_updates_existing_contact_with_same_address_instead_of_duplicating(tmp_path: Path) -> None:
    # Пожелание: "нет проверки на существование адреса" — тот же адрес из
    # другого источника обновляет контакт, а не создаёт второго.
    path = tmp_path / "test.rmcontacts"
    contact_store.save_contact(path, Contact(uid="manual-1", display_name="Иванов Иван", emails=["ivan@example.com"]))
    csv_bytes = "Display Name,E-mail Address\r\nИван Иванов,IVAN@example.com\r\n".encode("utf-8-sig")
    assert contact_store.import_csv(path, csv_bytes) == 1
    contacts = contact_store.list_contacts(path)
    assert len(contacts) == 1 and contacts[0].uid == "manual-1"
    assert contacts[0].display_name == "Иван Иванов"


def test_migration_adds_group_flag_and_reparses_old_lists(tmp_path: Path) -> None:
    # Книга прежней сборки: без колонки is_group, список рассылки лежит как
    # «Имя <адрес>» в адресах контакта.
    path = tmp_path / "old.rmcontacts"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO meta VALUES ('format_version', '1');"
            "CREATE TABLE contacts (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL UNIQUE,"
            " display_name TEXT NOT NULL DEFAULT '', emails TEXT NOT NULL DEFAULT '[]', phone TEXT NOT NULL DEFAULT '',"
            " organization TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '');"
        )
        conn.execute(
            "INSERT INTO contacts (uid, display_name, emails) VALUES (?, ?, ?)",
            ("g1", "Все получатели", json.dumps(["a <a@x.ru>", "=?koi8-r?q?=e2?= <b@x.ru>"])),
        )
        conn.execute("INSERT INTO contacts (uid, display_name, emails) VALUES (?, ?, ?)", ("p1", "Человек", json.dumps(["p@x.ru"])))
        conn.commit()
    contacts = {c.uid: c for c in contact_store.list_contacts(path)}
    assert contacts["g1"].is_group and contacts["g1"].emails == ["a@x.ru", "b@x.ru"]
    assert not contacts["p1"].is_group and contacts["p1"].emails == ["p@x.ru"]
    # Повторное открытие — миграция не повторяется и ничего не ломает.
    assert {c.uid: c.is_group for c in contact_store.list_contacts(path)} == {"g1": True, "p1": False}
