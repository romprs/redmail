from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from redmail import calendar_store
from redmail.calendar_store import Attendee, Event
from redmail.imap_client import Attachment


def _event(uid: str = "e1@redmail", start_hour: int = 10) -> Event:
    start = datetime(2026, 9, 1, start_hour, tzinfo=timezone.utc)
    return Event(
        uid=uid,
        summary="Совещание",
        dtstart=start,
        dtend=start + timedelta(hours=1),
        organizer_email="organizer@example.com",
        organizer_name="Организатор",
        is_organizer=False,
        attendees=[Attendee(email="me@example.com", name="Я", participation="needs-action")],
    )


def test_create_and_is_calendar_file(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    assert calendar_store.is_calendar_file(path) is False
    calendar_store.create_calendar(path)
    assert calendar_store.is_calendar_file(path) is True


def test_save_and_get_event(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    event = _event()
    calendar_store.save_event(path, event)

    fetched = calendar_store.get_event(path, "e1@redmail")
    assert fetched is not None
    assert fetched.summary == "Совещание"
    assert fetched.dtstart == event.dtstart
    assert fetched.attendees == [Attendee(email="me@example.com", name="Я", participation="needs-action")]


def test_get_event_missing_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    calendar_store.create_calendar(path)
    assert calendar_store.get_event(path, "nope") is None


def test_color_defaults_to_none_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    event = _event()
    assert event.color is None
    calendar_store.save_event(path, event)
    assert calendar_store.get_event(path, event.uid).color is None

    colored = _event(uid="e2@redmail")
    colored.color = "#8E24AA"
    calendar_store.save_event(path, colored)
    assert calendar_store.get_event(path, "e2@redmail").color == "#8E24AA"


def test_save_event_upserts_by_uid(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    event = _event()
    calendar_store.save_event(path, event)
    event.summary = "Перенесённое совещание"
    event.sequence = 1
    calendar_store.save_event(path, event)

    all_events = calendar_store.list_events(path)
    assert len(all_events) == 1
    assert all_events[0].summary == "Перенесённое совещание"
    assert all_events[0].sequence == 1


def test_list_events_filters_by_range(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    calendar_store.save_event(path, _event("e1@redmail", start_hour=9))
    calendar_store.save_event(path, _event("e2@redmail", start_hour=15))

    window_start = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    in_range = calendar_store.list_events(path, start=window_start)
    assert [e.uid for e in in_range] == ["e2@redmail"]


def test_delete_event(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    calendar_store.save_event(path, _event())
    calendar_store.delete_event(path, "e1@redmail")
    assert calendar_store.get_event(path, "e1@redmail") is None


def test_reschedule_event_bumps_sequence(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    calendar_store.save_event(path, _event())
    new_start = datetime(2026, 9, 2, 14, tzinfo=timezone.utc)
    new_end = new_start + timedelta(hours=1)

    updated = calendar_store.reschedule_event(path, "e1@redmail", new_start, new_end)

    assert updated is not None
    assert updated.dtstart == new_start
    assert updated.sequence == 1
    assert calendar_store.get_event(path, "e1@redmail").dtstart == new_start


def test_reschedule_event_missing_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    calendar_store.create_calendar(path)
    assert calendar_store.reschedule_event(path, "nope", datetime.now(timezone.utc), datetime.now(timezone.utc)) is None


def test_apply_invite_creates_new_event(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    event = calendar_store.apply_invite(path, "REQUEST", _event())
    assert event.status == "confirmed"
    assert calendar_store.get_event(path, "e1@redmail") is not None


def test_apply_invite_preserves_my_participation_on_resend(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    calendar_store.apply_invite(path, "REQUEST", _event())
    calendar_store.set_my_participation(path, "e1@redmail", "accepted")

    # Организатор прислал то же приглашение ещё раз (например, поправил
    # текст) — наш собственный ответ на него не должен слететь обратно в
    # needs-action.
    resent = _event()
    resent.summary = "Совещание (уточнение)"
    updated = calendar_store.apply_invite(path, "REQUEST", resent)
    assert updated.my_participation == "accepted"
    assert updated.summary == "Совещание (уточнение)"


def test_apply_invite_cancel_marks_cancelled(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    calendar_store.apply_invite(path, "REQUEST", _event())
    cancelled = calendar_store.apply_invite(path, "CANCEL", _event())
    assert cancelled.status == "cancelled"
    assert calendar_store.get_event(path, "e1@redmail").status == "cancelled"


def test_set_my_participation_updates_own_status_only(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    calendar_store.save_event(path, _event())
    updated = calendar_store.set_my_participation(path, "e1@redmail", "declined")
    assert updated is not None
    assert updated.my_participation == "declined"
    # Список приглашённых (чужие статусы) моим собственным ответом не трогается.
    assert updated.attendees[0].participation == "needs-action"


def test_set_my_participation_missing_event_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    calendar_store.create_calendar(path)
    assert calendar_store.set_my_participation(path, "nope", "accepted") is None


def test_apply_reply_updates_attendee_participation(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    calendar_store.save_event(path, _event())
    updated = calendar_store.apply_reply(path, "e1@redmail", "me@example.com", "accepted")
    assert updated is not None
    assert updated.attendees[0].participation == "accepted"


def test_apply_reply_adds_unknown_attendee(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    calendar_store.save_event(path, _event())
    updated = calendar_store.apply_reply(path, "e1@redmail", "other@example.com", "declined")
    assert updated is not None
    emails = {a.email: a.participation for a in updated.attendees}
    assert emails["other@example.com"] == "declined"


def test_apply_reply_missing_event_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    calendar_store.create_calendar(path)
    assert calendar_store.apply_reply(path, "nope", "a@example.com", "accepted") is None


def test_list_events_expands_recurring_event_within_window(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    daily = _event("daily@redmail", start_hour=9)
    daily.recurrence_rule = "FREQ=DAILY"
    calendar_store.save_event(path, daily)

    window_start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    window_end = datetime(2026, 9, 4, tzinfo=timezone.utc)
    occurrences = calendar_store.list_events(path, start=window_start, end=window_end)

    assert [e.dtstart.day for e in occurrences] == [1, 2, 3]
    assert all(e.uid == "daily@redmail" for e in occurrences)
    # Продолжительность экземпляра сохраняется такой же, как у исходного.
    assert all(e.dtend - e.dtstart == timedelta(hours=1) for e in occurrences)


def test_list_events_finds_recurring_series_whose_first_occurrence_is_before_window(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    daily = _event("daily@redmail", start_hour=9)
    daily.dtstart = datetime(2026, 1, 1, 9, tzinfo=timezone.utc)
    daily.dtend = daily.dtstart + timedelta(hours=1)
    daily.recurrence_rule = "FREQ=DAILY"
    calendar_store.save_event(path, daily)

    # Серия началась в январе, но ежедневно продолжается — сентябрьское
    # окно всё равно должно найти в нём экземпляры.
    occurrences = calendar_store.list_events(
        path, start=datetime(2026, 9, 1, tzinfo=timezone.utc), end=datetime(2026, 9, 2, tzinfo=timezone.utc)
    )
    assert len(occurrences) == 1
    assert occurrences[0].dtstart == datetime(2026, 9, 1, 9, tzinfo=timezone.utc)


def test_list_events_without_bounds_does_not_expand_recurring_event(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    daily = _event("daily@redmail")
    daily.recurrence_rule = "FREQ=DAILY"
    calendar_store.save_event(path, daily)

    all_events = calendar_store.list_events(path)
    assert len(all_events) == 1
    assert all_events[0].dtstart == daily.dtstart


def test_opening_pre_recurrence_schema_file_migrates_in_place(tmp_path: Path) -> None:
    # Реальный баг, пойманный на живой проверке: файл calendar.rmcal,
    # записанный до появления recurrence_rule, при первом же вызове
    # list_events() падал с "no such column: recurrence_rule" — событие
    # было на диске, но ничего не отображалось (исключение из слота Qt
    # тихо проглатывалось, без видимой ошибки). Календарь — не кэш, стирать
    # его при смене схемы, как cache_store.py, нельзя: только миграция.
    path = tmp_path / "old.rmcal"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            sequence INTEGER NOT NULL DEFAULT 0,
            summary TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            dtstart TEXT NOT NULL,
            dtend TEXT NOT NULL,
            all_day INTEGER NOT NULL DEFAULT 0,
            organizer_email TEXT NOT NULL DEFAULT '',
            organizer_name TEXT NOT NULL DEFAULT '',
            is_organizer INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'confirmed',
            my_participation TEXT NOT NULL DEFAULT 'needs-action',
            attendees TEXT NOT NULL DEFAULT '[]',
            raw_ics BLOB
        );
        """
    )
    conn.execute("INSERT INTO meta (key, value) VALUES ('format_version', '1')")
    conn.execute(
        "INSERT INTO events (uid, summary, dtstart, dtend) VALUES (?, ?, ?, ?)",
        ("old-event@redmail", "Старое событие", "2026-09-01T10:00:00+00:00", "2026-09-01T11:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    events = calendar_store.list_events(path)  # не должно бросить OperationalError

    assert len(events) == 1
    assert events[0].uid == "old-event@redmail"
    assert events[0].summary == "Старое событие"
    assert events[0].recurrence_rule is None

    # save_event после миграции работает как обычно, старые данные не потерялись.
    calendar_store.save_event(path, _event("new-event@redmail"))
    assert {e.uid for e in calendar_store.list_events(path)} == {"old-event@redmail", "new-event@redmail"}


def test_save_and_get_event_round_trips_attachments(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    event = _event()
    event.attachments = [
        Attachment(filename="agenda.pdf", content_type="application/pdf", payload=b"%PDF-fake-bytes"),
        Attachment(filename="notes.txt", content_type="text/plain", payload=b"some notes"),
    ]
    calendar_store.save_event(path, event)

    fetched = calendar_store.get_event(path, "e1@redmail")
    assert fetched is not None
    assert [a.filename for a in fetched.attachments] == ["agenda.pdf", "notes.txt"]
    assert fetched.attachments[0].payload == b"%PDF-fake-bytes"
    assert fetched.attachments[0].content_type == "application/pdf"


def test_save_event_replaces_attachments_not_appends(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    event = _event()
    event.attachments = [Attachment(filename="v1.txt", content_type="text/plain", payload=b"old")]
    calendar_store.save_event(path, event)

    event.attachments = [Attachment(filename="v2.txt", content_type="text/plain", payload=b"new")]
    calendar_store.save_event(path, event)

    fetched = calendar_store.get_event(path, "e1@redmail")
    assert [a.filename for a in fetched.attachments] == ["v2.txt"]


def test_event_without_attachments_round_trips_empty_list(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    calendar_store.save_event(path, _event())
    fetched = calendar_store.get_event(path, "e1@redmail")
    assert fetched.attachments == []


def test_delete_event_also_deletes_attachments(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    event = _event()
    event.attachments = [Attachment(filename="a.txt", content_type="text/plain", payload=b"x")]
    calendar_store.save_event(path, event)
    calendar_store.delete_event(path, "e1@redmail")

    with sqlite3.connect(path) as conn:
        remaining = conn.execute("SELECT COUNT(*) FROM event_attachments WHERE event_uid = ?", ("e1@redmail",)).fetchone()
    assert remaining[0] == 0


def test_list_events_includes_attachments(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    event = _event()
    event.attachments = [Attachment(filename="a.txt", content_type="text/plain", payload=b"x")]
    calendar_store.save_event(path, event)

    events = calendar_store.list_events(path)
    assert len(events) == 1
    assert events[0].attachments[0].filename == "a.txt"


def test_all_day_event_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "test.rmcal"
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    event = Event(uid="allday@redmail", summary="Отпуск", dtstart=start, dtend=start + timedelta(days=1), all_day=True)
    calendar_store.save_event(path, event)
    fetched = calendar_store.get_event(path, "allday@redmail")
    assert fetched.all_day is True
