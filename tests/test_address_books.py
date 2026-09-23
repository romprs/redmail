"""Адресная книга с сервера: CardDAV (общая книга VK приходит ссылкой) и
контакты Exchange. Книга обновляется целиком, свои контакты не трогает."""
from __future__ import annotations

from pathlib import Path

import pytest

from redmail import address_books, carddav_sync, config_store, contact_store

VCARD = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:card-1\r\nFN:Захаров Никита Андреевич\r\n"
    "EMAIL:NAZaharov@amurgpz.ru\r\nTITLE:Начальник отдела\r\nORG:ГПБ;ОСТИ\r\n"
    "TEL;TYPE=WORK:+7 (4164) 321-500\r\nEND:VCARD\r\n"
)
VCARD2 = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:card-2\r\nFN:Максаков Александр\r\n"
    "EMAIL:avmaksakov@amurgpz.ru\r\nEND:VCARD\r\n"
)

MULTISTATUS = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">'
    "<d:response><d:href>/carddav/principal/addressbook/common/1.vcf</d:href>"
    "<d:propstat><d:prop><d:getetag>\"1\"</d:getetag>"
    f"<c:address-data>{VCARD}</c:address-data></d:prop>"
    "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    "<d:response><d:href>/carddav/principal/addressbook/common/2.vcf</d:href>"
    "<d:propstat><d:prop><d:getetag>\"2\"</d:getetag>"
    f"<c:address-data>{VCARD2}</c:address-data></d:prop>"
    "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    "</d:multistatus>"
)

BOOKS_MULTISTATUS = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">'
    "<d:response><d:href>/carddav/principal/addressbook/common/</d:href>"
    "<d:propstat><d:prop><d:resourcetype><d:collection/><c:addressbook/></d:resourcetype>"
    "<d:displayname>Общая книга</d:displayname></d:prop>"
    "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    "<d:response><d:href>https://evil.example.com/carddav/stolen/</d:href>"
    "<d:propstat><d:prop><d:resourcetype><d:collection/><c:addressbook/></d:resourcetype>"
    "<d:displayname>Чужой сервер</d:displayname></d:prop>"
    "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    "</d:multistatus>"
)

URL = "https://e.vkm.corp.amurgpz.ru/carddav/principal/addressbook/common/"


class _Response:
    def __init__(self, content: str, status: int = 207) -> None:
        self.content = content.encode("utf-8")
        self.status_code = status
        self.reason = "OK"


class _FakeSession:
    def __init__(self, replies: dict) -> None:
        self.replies = replies
        self.calls: list[tuple[str, str]] = []
        self.headers: dict = {}
        self.auth = None

    def request(self, method, url, data=None, headers=None, timeout=None):
        self.calls.append((method, url))
        reply = self.replies.get(method)
        if reply is None:
            raise AssertionError(f"неожиданный запрос {method} {url}")
        return reply

    def close(self) -> None:
        pass


def _session(monkeypatch, replies: dict) -> carddav_sync.CardDavSession:
    fake = _FakeSession(replies)
    monkeypatch.setattr(carddav_sync.requests, "Session", lambda: fake)
    session = carddav_sync.CardDavSession(carddav_sync.CardDavAccount(url=URL, username="u", password="p"))
    session.fake = fake
    return session


def test_fetch_vcards_reads_address_data(monkeypatch) -> None:
    session = _session(monkeypatch, {"REPORT": _Response(MULTISTATUS)})
    result = session.fetch_vcards()
    assert len(result.vcards) == 2
    assert b"NAZaharov@amurgpz.ru" in result.vcards[0]


def test_books_on_other_servers_are_skipped(monkeypatch) -> None:
    """Сервер вернул ссылку на чужой узел — ходить туда с нашим паролем нельзя."""
    session = _session(monkeypatch, {"PROPFIND": _Response(BOOKS_MULTISTATUS)})
    books = session.list_address_books()
    assert [book.name for book in books] == ["Общая книга"]


def test_auth_failure_is_explained(monkeypatch) -> None:
    session = _session(monkeypatch, {"REPORT": _Response("", 401)})
    with pytest.raises(carddav_sync.CardDavError) as exc:
        session.fetch_vcards()
    assert "логин" in str(exc.value)


def test_carddav_book_replaces_only_its_own_contacts(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "contacts.rmab"
    mine = contact_store.save_contact(path, contact_store.Contact(display_name="Мой контакт", emails=["me@x.ru"]))
    book = {"kind": "carddav", "name": "Книга организации", "url": URL}

    monkeypatch.setattr(carddav_sync, "fetch_book", lambda _account: [VCARD.encode("utf-8"), VCARD2.encode("utf-8")])
    account = carddav_sync.CardDavAccount(url=URL)
    assert address_books.sync_carddav(path, book, account) == 2

    names = {c.display_name for c in contact_store.list_contacts(path)}
    assert names == {"Мой контакт", "Захаров Никита Андреевич", "Максаков Александр"}
    zaharov = next(c for c in contact_store.list_contacts(path) if c.display_name.startswith("Захаров"))
    assert zaharov.emails == ["nazaharov@amurgpz.ru"]  # адрес приводится к нижнему регистру
    assert zaharov.title == "Начальник отдела" and zaharov.source == f"carddav:{URL.rstrip('/')}"

    # Захарова убрали из книги на сервере — он пропадает и здесь, свой остаётся
    monkeypatch.setattr(carddav_sync, "fetch_book", lambda _account: [VCARD2.encode("utf-8")])
    assert address_books.sync_carddav(path, book, account) == 1
    names = {c.display_name for c in contact_store.list_contacts(path)}
    assert names == {"Мой контакт", "Максаков Александр"}
    assert contact_store.get_contact(path, mine.id) is not None


def test_ews_contacts_go_to_their_own_book(tmp_path: Path) -> None:
    path = tmp_path / "contacts.rmab"
    book = {"kind": "ews", "name": "Контакты Exchange", "account": "rsponomarev@amurgpz.ru"}
    entries = [{
        "uid": "AAA", "display_name": "Патриевская Ольга Павловна",
        "emails": ["O.Patrievskaya@diagnostika.gazprom.ru"], "phone": "+7", "organization": "Диагностика",
        "title": "Специалист", "department": "Отдел",
    }]
    assert address_books.sync_ews(path, book, entries) == 1
    contact = contact_store.list_contacts(path)[0]
    assert contact.emails == ["o.patrievskaya@diagnostika.gazprom.ru"]
    assert contact.source == "ews:rsponomarev@amurgpz.ru" and contact.department == "Отдел"
    assert contact_store.list_sources(path) == {"ews:rsponomarev@amurgpz.ru": 1}


def test_books_survive_settings_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config_store, "_settings_path", lambda: tmp_path / "settings.json", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    books = [
        {"kind": "carddav", "name": "Книга организации", "url": URL, "account": "", "enabled": True},
        {"kind": "ews", "name": "Контакты Exchange", "url": "", "account": "me@x.ru", "enabled": False},
        {"kind": "мусор", "name": "не книга"},
    ]
    config_store.save_address_books(books)
    loaded = config_store.load_address_books()
    assert [book["kind"] for book in loaded] == ["carddav", "ews"]
    assert loaded[0]["url"] == URL and loaded[1]["enabled"] is False


def test_server_card_cannot_read_local_files(tmp_path: Path) -> None:
    """В карточке с сервера PHOTO:file:///… заставил бы наш клиент прочитать
    чужой файл с диска — такие ссылки из серверных книг не выполняем."""
    secret = tmp_path / "secret.bin"
    secret.write_bytes(b"\x89PNG\r\n\x1a\n" + b"s" * 100)
    card = (
        "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:card-3\r\nFN:Чужой\r\nEMAIL:x@x.ru\r\n"
        f"PHOTO;VALUE=URI:file:///{secret.as_posix()}\r\nEND:VCARD\r\n"
    )
    contacts = contact_store.contacts_from_vcards([card.encode("utf-8")], "carddav:https://server/book")
    assert contacts and contacts[0].photo == b""

    # свой файл, импортированный вручную, по-прежнему разбирается как раньше
    path = tmp_path / "contacts.rmab"
    assert contact_store.import_vcard(path, card.encode("utf-8")) == 1
    assert contact_store.list_contacts(path)[0].photo.startswith(b"\x89PNG")
