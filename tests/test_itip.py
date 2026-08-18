from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import icalendar
import pytest

from redmail import itip
from redmail.calendar_store import Attendee, Event


def _event() -> Event:
    start = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
    return Event(
        uid="e1@redmail",
        summary="Совещание по проекту",
        description="Обсуждаем сроки",
        location="Переговорная 3",
        dtstart=start,
        dtend=start + timedelta(hours=1),
        organizer_email="organizer@example.com",
        organizer_name="Организатор",
        attendees=[
            Attendee(email="me@example.com", name="Я", participation="needs-action"),
            Attendee(email="other@example.com", name="Другой участник", participation="needs-action"),
        ],
    )


def test_build_request_and_parse_round_trip() -> None:
    ics = itip.build_request_ics(_event(), "organizer@example.com", "Организатор")
    invite = itip.parse_invite(ics, my_email="me@example.com")

    assert invite.method == "REQUEST"
    assert invite.event.uid == "e1@redmail"
    assert invite.event.summary == "Совещание по проекту"
    assert invite.event.description == "Обсуждаем сроки"
    assert invite.event.location == "Переговорная 3"
    assert invite.event.organizer_email == "organizer@example.com"
    assert invite.event.is_organizer is False
    assert invite.event.dtstart == datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
    assert {a.email for a in invite.event.attendees} == {"me@example.com", "other@example.com"}


def test_parse_invite_detects_organizer_is_me() -> None:
    ics = itip.build_request_ics(_event(), "organizer@example.com", "Организатор")
    invite = itip.parse_invite(ics, my_email="organizer@example.com")
    assert invite.event.is_organizer is True


def test_build_cancel_sets_method_and_status() -> None:
    ics = itip.build_cancel_ics(_event(), "organizer@example.com", "Организатор")
    invite = itip.parse_invite(ics, my_email="me@example.com")
    assert invite.method == "CANCEL"
    assert invite.event.status == "cancelled"


def test_build_reply_carries_partstat() -> None:
    ics = itip.build_reply_ics(_event(), "me@example.com", "Я", "accepted")
    cal = icalendar.Calendar.from_ical(ics)
    assert str(cal.get("method")).upper() == "REPLY"
    vevent = next(c for c in cal.walk() if c.name == "VEVENT")
    attendee = vevent["ATTENDEE"]
    assert str(attendee.params["PARTSTAT"]) == "ACCEPTED"

    invite = itip.parse_invite(ics, my_email="organizer@example.com")
    assert invite.method == "REPLY"
    assert invite.replying_attendee_email == "me@example.com"


def test_all_day_event_uses_date_only_values() -> None:
    event = _event()
    event.all_day = True
    ics = itip.build_request_ics(event, "organizer@example.com", "Организатор")
    invite = itip.parse_invite(ics, my_email="me@example.com")
    assert invite.event.all_day is True
    assert invite.event.dtstart == datetime(2026, 9, 1, tzinfo=timezone.utc)


def test_find_calendar_part_locates_ics_attachment() -> None:
    message = EmailMessage()
    message["Subject"] = "Приглашение"
    message["From"] = "organizer@example.com"
    message.set_content("Вас приглашают на встречу.")
    ics = itip.build_request_ics(_event(), "organizer@example.com", "Организатор")
    message.add_attachment(ics, maintype="text", subtype="calendar", filename="invite.ics")

    found = itip.find_calendar_part(message)
    assert found is not None
    invite = itip.parse_invite(found, my_email="me@example.com")
    assert invite.event.uid == "e1@redmail"


def test_find_calendar_part_returns_none_without_ics() -> None:
    message = EmailMessage()
    message.set_content("Просто письмо без приглашения")
    assert itip.find_calendar_part(message) is None
