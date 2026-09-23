"""Загрузка адресных книг с серверов в книгу контактов.

Связывает транспорт (CardDAV — carddav_sync, Exchange — ews_client) с
хранилищем (contact_store): книга с сервера обновляется целиком и живёт
отдельно от своих контактов, которые человек завёл сам или импортировал
файлом (пожелание: «почему бы не качать книгу с серверов — на Exchange
тоже есть книга»).
"""
from __future__ import annotations

from pathlib import Path

from redmail import carddav_sync, contact_store
from redmail.applog import get_logger

_log = get_logger("books")

KIND_CARDDAV = "carddav"
KIND_EWS = "ews"


def source_key(book: dict) -> str:
    """Ключ книги в хранилище контактов: по нему её контакты заменяются
    при следующей загрузке и по нему же видно, откуда контакт."""
    if book.get("kind") == KIND_EWS:
        return f"ews:{book.get('account') or ''}"
    return f"carddav:{(book.get('url') or '').rstrip('/')}"


def book_title(book: dict) -> str:
    return str(book.get("name") or ("Контакты Exchange" if book.get("kind") == KIND_EWS else "Адресная книга"))


def sync_carddav(contacts_path: Path, book: dict, account: carddav_sync.CardDavAccount) -> int:
    """Скачать книгу CardDAV и заменить ею прежний снимок. Возвращает
    количество контактов."""
    vcards = carddav_sync.fetch_book(account)
    source = source_key(book)
    contacts = contact_store.contacts_from_vcards(vcards, source)
    count = contact_store.replace_source_contacts(contacts_path, source, contacts)
    _log.info("Книга «%s»: карточек %d, контактов %d", book_title(book), len(vcards), count)
    return count


def contacts_from_ews(entries: list[dict], source: str) -> list[contact_store.Contact]:
    result = []
    for entry in entries:
        emails = [str(e).lower() for e in entry.get("emails") or [] if e]
        name = str(entry.get("display_name") or "").strip()
        if not emails and not name:
            continue
        uid = str(entry.get("uid") or "")
        result.append(contact_store.Contact(
            uid=f"{source}|{uid}" if uid else "",
            display_name=name or emails[0],
            emails=emails,
            phone=str(entry.get("phone") or ""),
            organization=str(entry.get("organization") or ""),
            title=str(entry.get("title") or ""),
            department=str(entry.get("department") or ""),
            source=source,
        ))
    return result


def sync_ews(contacts_path: Path, book: dict, entries: list[dict]) -> int:
    """Контакты ящика Exchange (их читает ews_client.fetch_contacts) —
    в книгу."""
    source = source_key(book)
    contacts = contacts_from_ews(entries, source)
    count = contact_store.replace_source_contacts(contacts_path, source, contacts)
    _log.info("Книга «%s»: контактов %d", book_title(book), count)
    return count
