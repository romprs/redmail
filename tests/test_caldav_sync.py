from __future__ import annotations

import urllib.error
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests
from caldav.lib.error import AuthorizationError, NotFoundError

from redmail import itip
from redmail.caldav_sync import CalDavAccount, CalDavSession, CalDavSyncError, probe_auth_schemes
from redmail.calendar_store import Attendee, Event


def _account() -> CalDavAccount:
    return CalDavAccount(url="https://calendar.example.corp/caldav/", username="ivan", password="secret")


def _event(uid: str = "e1@redmail") -> Event:
    start = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
    return Event(
        uid=uid,
        summary="Совещание",
        dtstart=start,
        dtend=start + timedelta(hours=1),
        organizer_email="ivan@example.com",
        organizer_name="Иван",
        is_organizer=True,
        attendees=[Attendee(email="other@example.com", name="Другой", participation="needs-action")],
    )


def _fake_calendar_obj_for(event: Event) -> MagicMock:
    obj = MagicMock()
    obj.data = itip.build_caldav_ics(event, event.organizer_email, event.organizer_name).decode("utf-8")
    return obj


def test_session_creates_client_with_account_credentials() -> None:
    with patch("redmail.caldav_sync.caldav.DAVClient") as client_cls:
        CalDavSession(_account())
    client_cls.assert_called_once_with(
        "https://calendar.example.corp/caldav/", username="ivan", password="secret", timeout=30,
        headers={"User-Agent": "redmail-caldav-client/1.0"},
    )


def test_session_kerberos_uses_spnego_auth_and_plain_requests_session(monkeypatch) -> None:
    # SSO: веб-приложение VK авторизует по Kerberos (keytab на сервере,
    # SPNEGO для клиентов) — CalDAV должен идти по доменному билету, без
    # логина/пароля. requests_gssapi тянет системные библиотеки Kerberos,
    # которых на машине для тестов нет — подменяем модуль целиком.
    import sys

    import requests

    import redmail

    fake_gssapi = MagicMock()
    fake_gssapi.OPTIONAL = 2
    fake_auth = MagicMock()
    fake_gssapi.HTTPSPNEGOAuth.return_value = fake_auth
    monkeypatch.setitem(sys.modules, "requests_gssapi", fake_gssapi)
    # redmail.gssapi_sasl сам импортирует gssapi на уровне модуля —
    # подменяем целиком (см. test_smtp_client.py), keytab не задан → None.
    fake_gssapi_sasl = MagicMock()
    fake_gssapi_sasl.acquire_credentials.return_value = None
    monkeypatch.setattr(redmail, "gssapi_sasl", fake_gssapi_sasl, raising=False)
    monkeypatch.setitem(sys.modules, "redmail.gssapi_sasl", fake_gssapi_sasl)

    fake_client = MagicMock()
    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client) as client_cls:
        session = CalDavSession(
            CalDavAccount(url="https://calendar.corp.local/", username="ivan@corp.local", password="", auth_type="kerberos")
        )

    fake_gssapi_sasl.acquire_credentials.assert_called_once_with("", "")
    fake_gssapi.HTTPSPNEGOAuth.assert_called_once_with(mutual_authentication=2, creds=None)
    client_cls.assert_called_once_with(
        "https://calendar.corp.local/", timeout=30, headers={"User-Agent": "redmail-caldav-client/1.0"}, auth=fake_auth
    )
    assert "password" not in client_cls.call_args.kwargs
    # caldav при наличии niquests берёт его; HTTPSPNEGOAuth написан под
    # настоящий requests — сессия должна быть подменена на requests.Session.
    assert isinstance(session._client.session, requests.Session)


def test_fetch_events_parses_server_objects_into_events() -> None:
    fake_client = MagicMock()
    fake_calendar = MagicMock()
    fake_calendar.date_search.return_value = [_fake_calendar_obj_for(_event())]

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client), \
         patch("redmail.caldav_sync.caldav.Calendar", return_value=fake_calendar):
        session = CalDavSession(_account())
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=7)
        events = session.fetch_events(start, end, my_email="ivan@example.com")

    assert len(events) == 1
    assert events[0].uid == "e1@redmail"
    assert events[0].summary == "Совещание"
    fake_calendar.date_search.assert_called_once_with(start, end)


def test_fetch_events_skips_broken_object_without_failing_whole_sync() -> None:
    fake_client = MagicMock()
    fake_calendar = MagicMock()
    broken = MagicMock()
    broken.data = "not a valid ics at all"
    fake_calendar.date_search.return_value = [broken, _fake_calendar_obj_for(_event())]

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client), \
         patch("redmail.caldav_sync.caldav.Calendar", return_value=fake_calendar):
        session = CalDavSession(_account())
        events = session.fetch_events(
            datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 9, 8, tzinfo=timezone.utc), "ivan@example.com"
        )

    assert len(events) == 1
    assert events[0].uid == "e1@redmail"


def test_push_event_creates_new_when_not_found_on_server() -> None:
    fake_client = MagicMock()
    fake_calendar = MagicMock()
    fake_calendar.get_event_by_uid.side_effect = NotFoundError("nope")

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client), \
         patch("redmail.caldav_sync.caldav.Calendar", return_value=fake_calendar):
        session = CalDavSession(_account())
        session.push_event(_event(), "ivan@example.com", "Иван")

    fake_calendar.save_event.assert_called_once()
    ics_text = fake_calendar.save_event.call_args[0][0]
    assert "UID:e1@redmail" in ics_text
    assert "METHOD" not in ics_text


def test_push_event_updates_existing_when_found_on_server() -> None:
    fake_client = MagicMock()
    fake_calendar = MagicMock()
    existing_obj = MagicMock()
    fake_calendar.get_event_by_uid.return_value = existing_obj

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client), \
         patch("redmail.caldav_sync.caldav.Calendar", return_value=fake_calendar):
        session = CalDavSession(_account())
        session.push_event(_event(), "ivan@example.com", "Иван")

    fake_calendar.save_event.assert_not_called()
    existing_obj.save.assert_called_once()
    assert "UID:e1@redmail" in existing_obj.data


def test_push_event_retries_once_on_connection_error() -> None:
    # Жалоба: "не синхронизируется календарь, при этом проверка подключения
    # проходит" — "Remote end closed connection without response" на
    # реальном сервере при push, хотя более лёгкий PROPFIND (проверка
    # подключения) при этом отвечал нормально. Один повтор на свежем
    # соединении — стандартное лечение для устаревшего keep-alive.
    fake_client = MagicMock()
    fake_calendar = MagicMock()
    fake_calendar.get_event_by_uid.side_effect = NotFoundError("nope")
    fake_calendar.save_event.side_effect = [
        requests.exceptions.ConnectionError("Remote end closed connection without response"),
        None,
    ]

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client), \
         patch("redmail.caldav_sync.caldav.Calendar", return_value=fake_calendar):
        session = CalDavSession(_account())
        session.push_event(_event(), "ivan@example.com", "Иван")

    assert fake_calendar.save_event.call_count == 2


def test_push_event_raises_clear_error_when_connection_fails_twice() -> None:
    fake_client = MagicMock()
    fake_calendar = MagicMock()
    fake_calendar.get_event_by_uid.side_effect = NotFoundError("nope")
    fake_calendar.save_event.side_effect = requests.exceptions.ConnectionError("still closed")

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client), \
         patch("redmail.caldav_sync.caldav.Calendar", return_value=fake_calendar):
        session = CalDavSession(_account())
        with pytest.raises(CalDavSyncError):
            session.push_event(_event(), "ivan@example.com", "Иван")

    assert fake_calendar.save_event.call_count == 2


def test_write_access_saves_and_deletes_test_event() -> None:
    fake_client = MagicMock()
    fake_calendar = MagicMock()
    cleanup_obj = MagicMock()
    fake_calendar.get_event_by_uid.return_value = cleanup_obj

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client), \
         patch("redmail.caldav_sync.caldav.Calendar", return_value=fake_calendar):
        session = CalDavSession(_account())
        session.test_write_access()

    fake_calendar.save_event.assert_called_once()
    ics_text = fake_calendar.save_event.call_args[0][0]
    assert "redmail-conntest-" in ics_text
    cleanup_obj.delete.assert_called_once()


def test_write_access_raises_clear_error_when_save_fails() -> None:
    # Настоящая жалоба: чтение (проверка подключения) проходит, а запись
    # (реальная синхронизация) — нет. Проверка подключения должна ловить это
    # заранее, а не только чтение.
    fake_client = MagicMock()
    fake_calendar = MagicMock()
    fake_calendar.save_event.side_effect = requests.exceptions.ConnectionError("Remote end closed connection without response")

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client), \
         patch("redmail.caldav_sync.caldav.Calendar", return_value=fake_calendar):
        session = CalDavSession(_account())
        with pytest.raises(CalDavSyncError, match="Запись на сервер не удалась"):
            session.test_write_access()


def test_delete_event_deletes_when_found() -> None:
    fake_client = MagicMock()
    fake_calendar = MagicMock()
    existing_obj = MagicMock()
    fake_calendar.get_event_by_uid.return_value = existing_obj

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client), \
         patch("redmail.caldav_sync.caldav.Calendar", return_value=fake_calendar):
        session = CalDavSession(_account())
        session.delete_event("e1@redmail")

    existing_obj.delete.assert_called_once()


def test_delete_event_is_noop_when_not_found() -> None:
    fake_client = MagicMock()
    fake_calendar = MagicMock()
    fake_calendar.get_event_by_uid.side_effect = NotFoundError("nope")

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client), \
         patch("redmail.caldav_sync.caldav.Calendar", return_value=fake_calendar):
        session = CalDavSession(_account())
        session.delete_event("missing@redmail")  # не должно бросить исключение


def test_list_calendar_names() -> None:
    fake_client = MagicMock()
    cal_a = MagicMock()
    cal_a.get_display_name.return_value = "Основной"
    cal_b = MagicMock()
    cal_b.get_display_name.return_value = None
    cal_b.url = "https://calendar.example.corp/caldav/other/"
    fake_client.principal.return_value.calendars.return_value = [cal_a, cal_b]

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client):
        session = CalDavSession(_account())
        names = session.list_calendar_names()

    assert names == ["Основной", "https://calendar.example.corp/caldav/other/"]


def _propfind_response(xml: str):
    """Ответ PROPFIND как его отдаёт caldav любой версии: объект с сырым
    lxml-деревом multistatus (tree) и статусом. Разбор свойств — наш
    собственный (_parse_multistatus), поэтому от внутренностей библиотеки
    (в 3.x был parse_propfind/results, в 2.x их нет) тест не зависит."""
    import lxml.etree as etree

    return SimpleNamespace(tree=etree.XML(xml.encode("utf-8")), status=207)


_SHARED_CALENDARS_XML = '''<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/calendars/ivan@corp.ru/personal/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
        <d:displayname>Личный</d:displayname>
        <d:owner><d:href>/principals/ivan@corp.ru/</d:href></d:owner>
        <d:current-user-privilege-set>
          <d:privilege><d:write/></d:privilege>
          <d:privilege><d:read/></d:privilege>
        </d:current-user-privilege-set>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/calendars/ivan@corp.ru/shared-by-coworker/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
        <d:displayname>Отдел продаж</d:displayname>
        <d:owner><d:href>/principals/coworker@corp.ru/</d:href></d:owner>
        <d:current-user-privilege-set>
          <d:privilege><d:read/></d:privilege>
        </d:current-user-privilege-set>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/calendars/ivan@corp.ru/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/></d:resourcetype>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>'''


def test_list_calendars_detailed_marks_shared_calendar_and_privileges() -> None:
    fake_client = MagicMock()
    fake_client.url = "https://calendar.example.corp/"
    fake_client.principal.return_value.calendar_home_set.url = "https://calendar.example.corp/calendars/ivan@corp.ru/"
    fake_client.principal.return_value.url = "https://calendar.example.corp/principals/ivan@corp.ru/"
    fake_client.propfind.return_value = _propfind_response(_SHARED_CALENDARS_XML)

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client):
        session = CalDavSession(_account())
        calendars = session.list_calendars_detailed()

    assert len(calendars) == 2  # calendar-home-set сам по себе (без c:calendar в resourcetype) отфильтрован
    own, shared = calendars
    assert own.name == "Личный"
    assert own.is_shared is False
    assert own.read_only is False
    assert shared.name == "Отдел продаж"
    assert shared.owner == "/principals/coworker@corp.ru/"
    assert shared.is_shared is True
    assert shared.read_only is True


def _http_401(schemes: list[str]) -> urllib.error.HTTPError:
    headers = MagicMock()
    headers.get_all.return_value = schemes
    return urllib.error.HTTPError("https://calendar.example.corp/caldav/", 401, "Unauthorized", headers, None)


def test_probe_auth_schemes_reads_www_authenticate_header() -> None:
    with patch("redmail.caldav_sync.urllib.request.urlopen", side_effect=_http_401(["Negotiate", 'Basic realm="CalDAV"'])):
        schemes = probe_auth_schemes("https://calendar.example.corp/caldav/")
    assert schemes == ["Negotiate", "Basic"]


def test_probe_auth_schemes_returns_empty_on_non_401() -> None:
    error = urllib.error.HTTPError("https://calendar.example.corp/caldav/", 500, "Server Error", MagicMock(), None)
    with patch("redmail.caldav_sync.urllib.request.urlopen", side_effect=error):
        assert probe_auth_schemes("https://calendar.example.corp/caldav/") == []


def test_probe_auth_schemes_returns_empty_on_network_error_instead_of_raising() -> None:
    # Диагностика — best-effort: сетевая ошибка тут не должна маскировать
    # или заменять собой исходную ошибку вызывающего кода.
    with patch("redmail.caldav_sync.urllib.request.urlopen", side_effect=urllib.error.URLError("нет маршрута")):
        assert probe_auth_schemes("https://calendar.example.corp/caldav/") == []


def test_list_calendar_names_authorization_error_names_negotiate_if_offered() -> None:
    # Реальный сценарий: тонкий веб-клиент (аналог OWA) для той же почты
    # заходит через SSO — организация имеет Kerberos-инфраструктуру, но
    # неясно, предлагает ли конкретно CalDAV-сервер Negotiate, пока не
    # проверишь напрямую по заголовку ответа.
    fake_client = MagicMock()
    fake_client.principal.side_effect = AuthorizationError(url="https://calendar.example.corp/caldav/", reason="Unauthorized")

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client), \
         patch("redmail.caldav_sync.urllib.request.urlopen", side_effect=_http_401(["Negotiate", "Basic"])):
        session = CalDavSession(_account())
        with pytest.raises(CalDavSyncError, match="Negotiate"):
            session.list_calendar_names()


def test_list_calendar_names_authorization_error_without_negotiate_says_sso_unavailable() -> None:
    fake_client = MagicMock()
    fake_client.principal.side_effect = AuthorizationError(url="https://calendar.example.corp/caldav/", reason="Unauthorized")

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client), \
         patch("redmail.caldav_sync.urllib.request.urlopen", side_effect=_http_401(["Basic"])):
        session = CalDavSession(_account())
        with pytest.raises(CalDavSyncError, match="SSO .* недоступен"):
            session.list_calendar_names()
