from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import icalendar
import pytest

from redmail import itip
from redmail.calendar_store import Attendee, Event
from redmail.imap_client import Attachment

_FIXTURES = Path(__file__).parent / "fixtures"


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


def test_recurring_event_round_trips_rrule() -> None:
    event = _event()
    event.recurrence_rule = "FREQ=DAILY"
    ics = itip.build_request_ics(event, "organizer@example.com", "Организатор")
    invite = itip.parse_invite(ics, my_email="me@example.com")
    assert invite.event.recurrence_rule == "FREQ=DAILY"


def test_non_recurring_event_has_no_rrule() -> None:
    ics = itip.build_request_ics(_event(), "organizer@example.com", "Организатор")
    invite = itip.parse_invite(ics, my_email="me@example.com")
    assert invite.event.recurrence_rule is None


def test_request_round_trips_attachments() -> None:
    event = _event()
    event.attachments = [
        Attachment(filename="agenda.pdf", content_type="application/pdf", payload=b"%PDF-fake-bytes"),
    ]
    ics = itip.build_request_ics(event, "organizer@example.com", "Организатор")
    invite = itip.parse_invite(ics, my_email="me@example.com")

    assert len(invite.event.attachments) == 1
    attachment = invite.event.attachments[0]
    assert attachment.filename == "agenda.pdf"
    assert attachment.content_type == "application/pdf"
    assert attachment.payload == b"%PDF-fake-bytes"


def test_event_without_attachments_parses_empty_list() -> None:
    ics = itip.build_request_ics(_event(), "organizer@example.com", "Организатор")
    invite = itip.parse_invite(ics, my_email="me@example.com")
    assert invite.event.attachments == []


def test_uri_attach_is_skipped_not_crashed_on() -> None:
    # Внешняя ссылка вместо встроенного файла (ATTACH:https://...) —
    # реальный, RFC-валидный случай; не должен падать, просто не вложение.
    ics = (
        b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:REQUEST\r\n"
        b"BEGIN:VEVENT\r\nUID:x@example.com\r\nSUMMARY:Test\r\n"
        b"DTSTART:20260101T100000Z\r\nATTACH:https://example.com/file.pdf\r\n"
        b"END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    invite = itip.parse_invite(ics, my_email="me@example.com")
    assert invite.event.attachments == []


def test_find_calendar_part_returns_none_without_ics() -> None:
    message = EmailMessage()
    message.set_content("Просто письмо без приглашения")
    assert itip.find_calendar_part(message) is None


def test_parses_real_google_calendar_invite() -> None:
    # Настоящее приглашение, присланное Google Calendar (не нашим build_*)
    # romprs@gmail.com -> romprs1@gmail.com, захвачено при живой проверке
    # 2026-08-19. TZID=America/Los_Angeles с отдельным VTIMEZONE-блоком
    # (не голый Z-UTC) плюс RRULE:FREQ=DAILY — оба реальных источника риска,
    # которые синтетические тесты сборки/разбора могли не поймать.
    ics = (_FIXTURES / "google_calendar_invite.ics").read_bytes()
    invite = itip.parse_invite(ics, my_email="romprs1@gmail.com")

    assert invite.method == "REQUEST"
    assert invite.event.uid == "5g4sfsprqgihngf95j0l646rar@google.com"
    assert invite.event.organizer_email == "romprs@gmail.com"
    assert invite.event.recurrence_rule == "FREQ=DAILY"
    # 05:30 America/Los_Angeles 21 авг 2026 = 12:30 UTC (PDT, UTC-7).
    assert invite.event.dtstart == datetime(2026, 8, 21, 12, 30, tzinfo=timezone.utc)
    emails = {a.email for a in invite.event.attendees}
    assert emails == {"romprs1@gmail.com", "romprs@gmail.com"}
