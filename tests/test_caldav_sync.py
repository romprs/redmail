from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from caldav.lib.error import NotFoundError

from redmail import itip
from redmail.caldav_sync import CalDavAccount, CalDavSession, CalDavSyncError
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
        "https://calendar.example.corp/caldav/", username="ivan", password="secret", timeout=30
    )


def test_fetch_events_parses_server_objects_into_events() -> None:
    fake_client = MagicMock()
    fake_calendar = MagicMock()
    fake_client.principal.return_value.calendars.return_value = [fake_calendar]
    fake_calendar.date_search.return_value = [_fake_calendar_obj_for(_event())]

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client):
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
    fake_client.principal.return_value.calendars.return_value = [fake_calendar]
    broken = MagicMock()
    broken.data = "not a valid ics at all"
    fake_calendar.date_search.return_value = [broken, _fake_calendar_obj_for(_event())]

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client):
        session = CalDavSession(_account())
        events = session.fetch_events(
            datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 9, 8, tzinfo=timezone.utc), "ivan@example.com"
        )

    assert len(events) == 1
    assert events[0].uid == "e1@redmail"


def test_no_calendars_raises_clear_error() -> None:
    fake_client = MagicMock()
    fake_client.principal.return_value.calendars.return_value = []

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client):
        session = CalDavSession(_account())
        with pytest.raises(CalDavSyncError):
            session.fetch_events(
                datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 9, 8, tzinfo=timezone.utc), "ivan@example.com"
            )


def test_push_event_creates_new_when_not_found_on_server() -> None:
    fake_client = MagicMock()
    fake_calendar = MagicMock()
    fake_client.principal.return_value.calendars.return_value = [fake_calendar]
    fake_calendar.get_event_by_uid.side_effect = NotFoundError("nope")

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client):
        session = CalDavSession(_account())
        session.push_event(_event(), "ivan@example.com", "Иван")

    fake_calendar.save_event.assert_called_once()
    ics_text = fake_calendar.save_event.call_args[0][0]
    assert "UID:e1@redmail" in ics_text
    assert "METHOD" not in ics_text


def test_push_event_updates_existing_when_found_on_server() -> None:
    fake_client = MagicMock()
    fake_calendar = MagicMock()
    fake_client.principal.return_value.calendars.return_value = [fake_calendar]
    existing_obj = MagicMock()
    fake_calendar.get_event_by_uid.return_value = existing_obj

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client):
        session = CalDavSession(_account())
        session.push_event(_event(), "ivan@example.com", "Иван")

    fake_calendar.save_event.assert_not_called()
    existing_obj.save.assert_called_once()
    assert "UID:e1@redmail" in existing_obj.data


def test_delete_event_deletes_when_found() -> None:
    fake_client = MagicMock()
    fake_calendar = MagicMock()
    fake_client.principal.return_value.calendars.return_value = [fake_calendar]
    existing_obj = MagicMock()
    fake_calendar.get_event_by_uid.return_value = existing_obj

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client):
        session = CalDavSession(_account())
        session.delete_event("e1@redmail")

    existing_obj.delete.assert_called_once()


def test_delete_event_is_noop_when_not_found() -> None:
    fake_client = MagicMock()
    fake_calendar = MagicMock()
    fake_client.principal.return_value.calendars.return_value = [fake_calendar]
    fake_calendar.get_event_by_uid.side_effect = NotFoundError("nope")

    with patch("redmail.caldav_sync.caldav.DAVClient", return_value=fake_client):
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
