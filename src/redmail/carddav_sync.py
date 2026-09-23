"""Адресная книга с сервера (CardDAV).

Зачем: адреса коллег до сих пор попадали в программу только файлом —
человек выгружал .vcf из веб-почты и импортировал руками (пожелание:
«почему бы не качать книгу с серверов»). У VK общая книга организации
отдаётся по CardDAV, например
https://e.vkm.corp.amurgpz.ru/carddav/principal/addressbook/common/ —
её и забираем целиком, а разбор vCard переиспользуем тот же, что у
импорта файла (contact_store), чтобы поля (должность, подразделение,
фотография) разбирались одинаково.

Протокол: PROPFIND для поиска книг и REPORT addressbook-query для
выгрузки карточек одним запросом; если сервер не умеет REPORT —
PROPFIND Depth:1 и GET каждой карточки.

Логин и пароль — те же, что у почты (как у CalDAV), либо SSO по
доменному билету.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import requests

from redmail.applog import get_logger
from redmail.caldav_sync import (
    _href_path,
    _parse_multistatus,
    _propfind_body,
    _with_server_retry,
    same_server,
)

_log = get_logger("carddav")

_USER_AGENT = "redmail/1.0 (CardDAV)"
_TIMEOUT = 30

_ADDRESSBOOK_TAG = "{urn:ietf:params:xml:ns:carddav}addressbook"
_HOME_SET_PROP = "{urn:ietf:params:xml:ns:carddav}addressbook-home-set"
_CURRENT_USER_PRINCIPAL_PROP = "{DAV:}current-user-principal"
_DISCOVERY_PROPS = ["{DAV:}resourcetype", "{DAV:}displayname"]

_ADDRESSBOOK_QUERY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<c:addressbook-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">'
    "<d:prop><d:getetag/><c:address-data/></d:prop>"
    "</c:addressbook-query>"
)


class CardDavError(Exception):
    """Ошибка подключения к книге — показывается пользователю как есть."""


class CardDavAuthError(CardDavError):
    """Сервер не пустил: логин, пароль или права. Обходные пути пробовать
    бессмысленно — сразу говорим человеку."""


@dataclass
class CardDavAccount:
    url: str
    username: str = ""
    password: str = ""
    #: "password" — Basic, "kerberos" — SSO доменным билетом (как у CalDAV).
    auth_type: str = "password"
    keytab_path: str = ""
    principal: str = ""


@dataclass
class AddressBookInfo:
    url: str
    name: str


@dataclass
class FetchResult:
    """Карточки книги: vcards — сырые vCard, их разбирает contact_store."""

    vcards: list[bytes] = field(default_factory=list)
    skipped: int = 0


def _auth_for(account: CardDavAccount):
    if account.auth_type == "kerberos":
        # Импорт внутри — requests_gssapi тянет системные библиотеки
        # Kerberos, которых нет там, где SSO не используется (см.
        # caldav_sync.CalDavSession).
        import requests_gssapi

        from redmail import gssapi_sasl

        return requests_gssapi.HTTPSPNEGOAuth(
            mutual_authentication=requests_gssapi.OPTIONAL,
            creds=gssapi_sasl.acquire_credentials(account.keytab_path, account.principal),
        )
    if account.username:
        return (account.username, account.password)
    return None


class CardDavSession:
    def __init__(self, account: CardDavAccount) -> None:
        self.account = account
        self._session = requests.Session()
        self._session.headers["User-Agent"] = _USER_AGENT
        auth = _auth_for(account)
        if auth is not None:
            self._session.auth = auth

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "CardDavSession":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _request(self, method: str, url: str, body: str | None = None, depth: str = "0"):
        headers = {"Depth": depth}
        if body is not None:
            headers["Content-Type"] = 'application/xml; charset="utf-8"'
        try:
            response = self._session.request(
                method, url, data=body.encode("utf-8") if body else None,
                headers=headers, timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise CardDavError(f"Сервер книги не ответил: {exc}") from exc
        if response.status_code in (401, 403):
            raise CardDavAuthError(
                f"Сервер книги отказал в доступе ({response.status_code}): проверьте логин и пароль."
            )
        if response.status_code >= 400:
            raise CardDavError(f"Сервер книги ответил {response.status_code} {response.reason}")
        return response

    def _multistatus(self, method: str, url: str, body: str, depth: str):
        from lxml import etree

        response = _with_server_retry(
            f"{method} {url}", self._request, method, url, body, depth
        )
        try:
            tree = etree.fromstring(response.content)
        except Exception as exc:
            raise CardDavError(f"Непонятный ответ сервера книги: {exc}") from exc
        return tree

    # ------------------------------------------------------------------
    # Поиск книг
    # ------------------------------------------------------------------

    def list_address_books(self) -> list[AddressBookInfo]:
        """Книги на сервере. Указанный адрес может быть и самой книгой (её
        прислали ссылкой в письме), и адресом сервера — тогда идём от
        принципала к дому книг."""
        books: list[AddressBookInfo] = []
        seen: set[str] = set()
        self._collect(self.account.url, books, seen, depth_left=2)
        if books:
            return books
        for home in self._home_urls():
            self._collect(home, books, seen, depth_left=2)
        return books

    def _home_urls(self) -> list[str]:
        tree = self._multistatus(
            "PROPFIND", self.account.url,
            _propfind_body([_CURRENT_USER_PRINCIPAL_PROP, _HOME_SET_PROP]), "0",
        )
        homes: list[str] = []
        principals: list[str] = []
        for result in _parse_multistatus(tree):
            for prop, target in ((_HOME_SET_PROP, homes), (_CURRENT_USER_PRINCIPAL_PROP, principals)):
                value = result.properties.get(prop)
                for href in value if isinstance(value, list) else []:
                    if not isinstance(href, str) or not href:
                        continue
                    full = requests.compat.urljoin(self.account.url, href)
                    if not same_server(self.account.url, full):
                        _log.warning("CardDAV: ссылка на чужой сервер пропущена: %s", full)
                        continue
                    target.append(full)
        if homes:
            return homes
        # Принципал без дома книг: спросим дом у него самого.
        for principal in principals:
            tree = self._multistatus("PROPFIND", principal, _propfind_body([_HOME_SET_PROP]), "0")
            for result in _parse_multistatus(tree):
                value = result.properties.get(_HOME_SET_PROP)
                for href in value if isinstance(value, list) else []:
                    full = requests.compat.urljoin(principal, href)
                    if same_server(self.account.url, full):
                        homes.append(full)
        return homes

    def _collect(self, url: str, books: list[AddressBookInfo], seen: set[str], depth_left: int) -> None:
        try:
            tree = self._multistatus("PROPFIND", url, _propfind_body(_DISCOVERY_PROPS), "1")
        except CardDavError as exc:
            _log.info("CardDAV: коллекция %s не отвечает: %s", url, exc)
            return
        for result in _parse_multistatus(tree):
            if result.status not in (200, 207):
                continue
            full = requests.compat.urljoin(url, result.href)
            if not same_server(self.account.url, full):
                _log.warning("CardDAV: ответ со ссылкой на чужой сервер пропущен: %s", full)
                continue
            path = _href_path(full)
            tags = result.properties.get("{DAV:}resourcetype")
            tags = tags if isinstance(tags, list) else []
            name = result.properties.get("{DAV:}displayname")
            name = name if isinstance(name, str) and name else path.rstrip("/").rsplit("/", 1)[-1]
            if _ADDRESSBOOK_TAG in tags:
                if path not in seen:
                    seen.add(path)
                    books.append(AddressBookInfo(url=full, name=name))
            elif any(t.endswith("}collection") for t in tags) and depth_left > 1 and path != _href_path(url):
                self._collect(full, books, seen, depth_left - 1)

    # ------------------------------------------------------------------
    # Выгрузка карточек
    # ------------------------------------------------------------------

    def fetch_vcards(self, url: str | None = None) -> FetchResult:
        """Все карточки книги. Сначала REPORT одним запросом; сервер его не
        умеет — обходим список и забираем карточки поштучно."""
        book_url = url or self.account.url
        result = FetchResult()
        try:
            tree = self._multistatus("REPORT", book_url, _ADDRESSBOOK_QUERY, "1")
        except CardDavAuthError:
            raise
        except CardDavError as exc:
            _log.info("CardDAV: REPORT не удался (%s), забираю карточки по одной", exc)
            return self._fetch_one_by_one(book_url)
        for response in _parse_multistatus(tree):
            data = response.properties.get("{urn:ietf:params:xml:ns:carddav}address-data")
            text = data if isinstance(data, str) else ""
            if "BEGIN:VCARD" in text.upper():
                result.vcards.append(text.encode("utf-8"))
            elif response.href and not response.href.rstrip("/").endswith("/"):
                result.skipped += 1
        if not result.vcards:
            return self._fetch_one_by_one(book_url)
        _log.info("CardDAV %s: получено карточек %d", book_url, len(result.vcards))
        return result

    def _fetch_one_by_one(self, book_url: str) -> FetchResult:
        result = FetchResult()
        tree = self._multistatus("PROPFIND", book_url, _propfind_body(["{DAV:}getetag"]), "1")
        own_path = _href_path(book_url)
        for response in _parse_multistatus(tree):
            full = requests.compat.urljoin(book_url, response.href)
            if _href_path(full) == own_path or not same_server(self.account.url, full):
                continue
            try:
                card = _with_server_retry(f"GET {full}", self._request, "GET", full)
            except CardDavError as exc:
                _log.info("CardDAV: карточка %s не получена: %s", full, exc)
                result.skipped += 1
                continue
            if b"BEGIN:VCARD" in card.content.upper():
                result.vcards.append(card.content)
            else:
                result.skipped += 1
        _log.info("CardDAV %s: получено карточек %d, пропущено %d", book_url, len(result.vcards), result.skipped)
        return result


def fetch_book(account: CardDavAccount) -> list[bytes]:
    """Карточки книги по её адресу — то, что нужно синхронизации."""
    with CardDavSession(account) as session:
        return session.fetch_vcards().vcards


def test_connection(account: CardDavAccount) -> int:
    """Проверка подключения: сколько книг видно по этому адресу."""
    with CardDavSession(account) as session:
        return len(session.list_address_books())
