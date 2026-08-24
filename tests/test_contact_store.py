from __future__ import annotations

from pathlib import Path

from redmail import contact_store
from redmail.contact_store import Contact


def _contact(display_name: str = "Иван Иванов", emails: list[str] | None = None) -> Contact:
    return Contact(display_name=display_name, emails=emails if emails is not None else ["ivan@example.com"])


def test_create_and_is_contacts_file(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcontacts"
    assert contact_store.is_contacts_file(path) is False
    contact_store.create_contacts_book(path)
    assert contact_store.is_contacts_file(path) is True


def test_save_and_list_contacts(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcontacts"
    saved = contact_store.save_contact(path, _contact())
    assert saved.id is not None
    assert saved.uid  # автосгенерирован

    contacts = contact_store.list_contacts(path)
    assert len(contacts) == 1
    assert contacts[0].display_name == "Иван Иванов"
    assert contacts[0].emails == ["ivan@example.com"]


def test_list_contacts_sorted_by_name(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcontacts"
    contact_store.save_contact(path, _contact("Пётр Петров", ["petr@example.com"]))
    contact_store.save_contact(path, _contact("Анна Смирнова", ["anna@example.com"]))
    names = [c.display_name for c in contact_store.list_contacts(path)]
    assert names == ["Анна Смирнова", "Пётр Петров"]


def test_save_contact_upserts_by_uid(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcontacts"
    saved = contact_store.save_contact(path, _contact())
    saved.phone = "+79001234567"
    contact_store.save_contact(path, saved)

    contacts = contact_store.list_contacts(path)
    assert len(contacts) == 1
    assert contacts[0].phone == "+79001234567"


def test_get_contact_by_id(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcontacts"
    saved = contact_store.save_contact(path, _contact())
    fetched = contact_store.get_contact(path, saved.id)
    assert fetched is not None
    assert fetched.display_name == "Иван Иванов"


def test_get_contact_missing_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcontacts"
    contact_store.create_contacts_book(path)
    assert contact_store.get_contact(path, 999) is None


def test_find_by_email_case_insensitive(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcontacts"
    contact_store.save_contact(path, _contact(emails=["Ivan@Example.com", "ivan2@example.com"]))
    found = contact_store.find_by_email(path, "ivan@example.com")
    assert found is not None
    assert found.display_name == "Иван Иванов"
    assert contact_store.find_by_email(path, "unknown@example.com") is None


def test_delete_contact(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcontacts"
    saved = contact_store.save_contact(path, _contact())
    contact_store.delete_contact(path, saved.id)
    assert contact_store.list_contacts(path) == []


def test_delete_all_contacts(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcontacts"
    contact_store.save_contact(path, _contact())
    contact_store.save_contact(path, contact_store.Contact(display_name="Пётр Петров", emails=["petr@example.com"]))
    assert len(contact_store.list_contacts(path)) == 2

    contact_store.delete_all_contacts(path)

    assert contact_store.list_contacts(path) == []


def test_import_vcard_multiple_cards(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcontacts"
    vcf = (
        "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:abc-1\r\nFN:Иван Иванов\r\n"
        "EMAIL;TYPE=WORK:ivan@example.com\r\nEMAIL;TYPE=HOME:ivan.home@example.com\r\n"
        "TEL;TYPE=CELL:+79001234567\r\nORG:ООО Ромашка\r\nNOTE:Коллега\r\nEND:VCARD\r\n"
        "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Пётр Петров\r\nEMAIL:petr@example.com\r\nEND:VCARD\r\n"
    ).encode("utf-8")

    count = contact_store.import_vcard(path, vcf)
    assert count == 2

    contacts = contact_store.list_contacts(path)
    ivan = next(c for c in contacts if c.display_name == "Иван Иванов")
    assert ivan.uid == "abc-1"
    assert ivan.emails == ["ivan@example.com", "ivan.home@example.com"]
    assert ivan.phone == "+79001234567"
    assert ivan.organization == "ООО Ромашка"
    assert ivan.notes == "Коллега"

    petr = next(c for c in contacts if c.display_name == "Пётр Петров")
    assert petr.emails == ["petr@example.com"]


def test_import_vcard_reimport_updates_not_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcontacts"
    vcf = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:abc-1\r\nFN:Иван\r\nEMAIL:ivan@example.com\r\nEND:VCARD\r\n".encode()
    contact_store.import_vcard(path, vcf)
    vcf2 = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:abc-1\r\nFN:Иван Иванович\r\nEMAIL:ivan@example.com\r\nEND:VCARD\r\n".encode()
    contact_store.import_vcard(path, vcf2)

    contacts = contact_store.list_contacts(path)
    assert len(contacts) == 1
    assert contacts[0].display_name == "Иван Иванович"


def test_import_vcard_card_without_uid_dedupes_by_email(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcontacts"
    vcf = "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Иван\r\nEMAIL:ivan@example.com\r\nEND:VCARD\r\n".encode()
    contact_store.import_vcard(path, vcf)
    contact_store.import_vcard(path, vcf)
    assert len(contact_store.list_contacts(path)) == 1


def test_import_vcard_empty_card_skipped(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcontacts"
    vcf = "BEGIN:VCARD\r\nVERSION:3.0\r\nEND:VCARD\r\n".encode()
    count = contact_store.import_vcard(path, vcf)
    assert count == 0
    assert contact_store.list_contacts(path) == []


def test_import_csv_with_display_name_column(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcontacts"
    csv_bytes = (
        "Display Name,E-mail Address,Business Phone,Company\r\n"
        "Иван Иванов,ivan@example.com,+79001234567,ООО Ромашка\r\n"
    ).encode("utf-8-sig")

    count = contact_store.import_csv(path, csv_bytes)
    assert count == 1
    contact = contact_store.list_contacts(path)[0]
    assert contact.display_name == "Иван Иванов"
    assert contact.emails == ["ivan@example.com"]
    assert contact.phone == "+79001234567"
    assert contact.organization == "ООО Ромашка"


def test_import_csv_with_first_last_name_columns(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcontacts"
    csv_bytes = (
        "First Name,Last Name,E-mail Address\r\nПётр,Петров,petr@example.com\r\n"
    ).encode("utf-8-sig")
    count = contact_store.import_csv(path, csv_bytes)
    assert count == 1
    assert contact_store.list_contacts(path)[0].display_name == "Пётр Петров"


def test_import_csv_skips_blank_rows(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcontacts"
    csv_bytes = "Display Name,E-mail Address\r\n,\r\nИван,ivan@example.com\r\n".encode("utf-8-sig")
    count = contact_store.import_csv(path, csv_bytes)
    assert count == 1


def test_import_csv_empty_file_returns_zero(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcontacts"
    assert contact_store.import_csv(path, b"") == 0
