from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from redmail import calendar_mail, calendar_store
from redmail.imap_client import Attachment, MessageContent

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def _ics(method: str | None, *, uid: str = "meet-1@example.com", start: datetime | None = None,
         attendee_status: str = "NEEDS-ACTION", summary: str = "Планёрка") -> bytes:
    start = start or NOW + timedelta(days=2)
    end = start + timedelta(hours=1)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//test//RU"]
    if method:
        lines.append(f"METHOD:{method}")
    lines += [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{NOW.strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
        f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}",
        f"SUMMARY:{summary}",
        "ORGANIZER;CN=Организатор:mailto:boss@example.com",
        f"ATTENDEE;PARTSTAT={attendee_status}:mailto:me@example.com",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def _content(*attachments: Attachment, sender: str = "Организатор <boss@example.com>") -> MessageContent:
    return MessageContent(text="письмо", attachments=list(attachments), from_=sender)


def test_invitation_in_plain_ics_attachment_reaches_calendar(tmp_path: Path) -> None:
    """Раньше учитывалась только часть text/calendar; файл «meeting.ics»
    с типом application/octet-stream в календарь не попадал."""
    path = tmp_path / "calendar.rmcal"
    content = _content(Attachment("meeting.ics", "application/octet-stream", _ics("REQUEST")))

    results = calendar_mail.apply_calendar_parts(path, content, "me@example.com", now=NOW)

    assert [r.method for r in results] == ["REQUEST"]
    stored = calendar_store.get_event(path, "meet-1@example.com")
    assert stored is not None and stored.summary == "Планёрка"
    assert stored.my_participation == "needs-action"


def test_ics_file_without_method_is_added_as_is(tmp_path: Path) -> None:
    path = tmp_path / "calendar.rmcal"
    content = _content(Attachment("Совещание.ics", "text/calendar", _ics(None, uid="pub-1", summary="Совещание")))

    results = calendar_mail.apply_calendar_parts(path, content, "me@example.com", now=NOW)

    assert results[0].method == "PUBLISH"
    assert calendar_store.get_event(path, "pub-1").summary == "Совещание"


def test_cancellation_marks_meeting_cancelled(tmp_path: Path) -> None:
    path = tmp_path / "calendar.rmcal"
    calendar_mail.apply_calendar_parts(path, _content(Attachment("i.ics", "text/calendar", _ics("REQUEST"))), "me@example.com", now=NOW)

    calendar_mail.apply_calendar_parts(path, _content(Attachment("c.ics", "text/calendar", _ics("CANCEL"))), "me@example.com", now=NOW)

    assert calendar_store.get_event(path, "meet-1@example.com").status == "cancelled"


def test_repeated_invitation_keeps_answer_and_calendar(tmp_path: Path) -> None:
    """Exchange кладёт приглашение в свой календарь, письмо приходит следом —
    встреча не должна перескакивать в «Мои встречи» и терять ответ."""
    path = tmp_path / "calendar.rmcal"
    content = _content(Attachment("i.ics", "text/calendar", _ics("REQUEST")))
    calendar_mail.apply_calendar_parts(path, content, "me@example.com", now=NOW)
    stored = calendar_store.get_event(path, "meet-1@example.com")
    stored.calendar_id = "exchange"
    stored.my_participation = "accepted"
    calendar_store.save_event(path, stored)

    calendar_mail.apply_calendar_parts(path, content, "me@example.com", now=NOW)

    again = calendar_store.get_event(path, "meet-1@example.com")
    assert again.calendar_id == "exchange"
    assert again.my_participation == "accepted"


def test_long_past_meetings_are_not_added(tmp_path: Path) -> None:
    path = tmp_path / "calendar.rmcal"
    old = _ics("REQUEST", uid="old-1", start=NOW - timedelta(days=400))

    results = calendar_mail.apply_calendar_parts(path, _content(Attachment("i.ics", "text/calendar", old)), "me@example.com", now=NOW)

    assert results == []
    assert calendar_store.get_event(path, "old-1") is None


def test_broken_or_foreign_attachments_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "calendar.rmcal"
    content = _content(
        Attachment("report.pdf", "application/pdf", b"%PDF-1.4"),
        Attachment("broken.ics", "text/calendar", "BEGIN:VCALENDAR\r\nнеразбираемое".encode("utf-8")),
    )

    assert calendar_mail.apply_calendar_parts(path, content, "me@example.com", now=NOW) == []
    assert calendar_mail.has_calendar_data(_content(Attachment("report.pdf", "application/pdf", b"%PDF"))) is False


def test_cancellation_from_someone_else_is_not_applied(tmp_path: Path) -> None:
    """Отмену может прислать только организатор: UID встречи знает любой
    приглашённый, а письма теперь разбираются и без открытия."""
    path = tmp_path / "calendar.rmcal"
    calendar_mail.apply_calendar_parts(path, _content(Attachment("i.ics", "text/calendar", _ics("REQUEST"))), "me@example.com", now=NOW)

    results = calendar_mail.apply_calendar_parts(
        path, _content(Attachment("c.ics", "text/calendar", _ics("CANCEL")), sender="Шутник <joker@example.com>"),
        "me@example.com", now=NOW,
    )

    assert results[0].rejected_sender == "joker@example.com"
    assert calendar_store.get_event(path, "meet-1@example.com").status != "cancelled"


def test_changed_invitation_from_someone_else_does_not_overwrite_meeting(tmp_path: Path) -> None:
    path = tmp_path / "calendar.rmcal"
    calendar_mail.apply_calendar_parts(path, _content(Attachment("i.ics", "text/calendar", _ics("REQUEST"))), "me@example.com", now=NOW)

    calendar_mail.apply_calendar_parts(
        path,
        _content(Attachment("i.ics", "text/calendar", _ics("REQUEST", summary="Подменённая")), sender="joker@example.com"),
        "me@example.com", now=NOW,
    )

    assert calendar_store.get_event(path, "meet-1@example.com").summary == "Планёрка"


def test_reply_is_accepted_only_from_the_attendee_himself(tmp_path: Path) -> None:
    path = tmp_path / "calendar.rmcal"
    start = NOW + timedelta(days=2)
    mine = calendar_store.Event(
        uid="meet-1@example.com", summary="Моя встреча", dtstart=start, dtend=start + timedelta(hours=1),
        organizer_email="boss@example.com", is_organizer=True,
        attendees=[calendar_store.Attendee(email="me@example.com")],
    )
    calendar_store.save_event(path, mine)
    reply = _ics("REPLY", attendee_status="DECLINED")

    calendar_mail.apply_calendar_parts(path, _content(Attachment("r.ics", "text/calendar", reply), sender="joker@example.com"), "boss@example.com", now=NOW)
    before = calendar_store.get_event(path, "meet-1@example.com").attendees[0].participation
    calendar_mail.apply_calendar_parts(path, _content(Attachment("r.ics", "text/calendar", reply), sender="me@example.com"), "boss@example.com", now=NOW)
    after = calendar_store.get_event(path, "meet-1@example.com").attendees[0].participation

    assert before == "needs-action"
    assert after == "declined"


def test_ics_file_does_not_overwrite_known_meeting(tmp_path: Path) -> None:
    path = tmp_path / "calendar.rmcal"
    calendar_mail.apply_calendar_parts(path, _content(Attachment("i.ics", "text/calendar", _ics("REQUEST"))), "me@example.com", now=NOW)

    results = calendar_mail.apply_calendar_parts(
        path, _content(Attachment("x.ics", "text/calendar", _ics(None, summary="Подменённая"))), "me@example.com", now=NOW
    )

    assert results == []
    assert calendar_store.get_event(path, "meet-1@example.com").summary == "Планёрка"
