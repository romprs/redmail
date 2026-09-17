from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from redmail import calendar_store, calendar_sync, ews_calendar, itip

# Серия как на сервере VK: ежедневное совещание, один день перенесён,
# один отменён (EXDATE), и ещё один экземпляр отменён записью со STATUS.
SERIES_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//VK//Calendar//RU
BEGIN:VEVENT
UID:daily-1
SUMMARY:ежедневное оперативное совещание
DTSTART:20260914T013000Z
DTEND:20260914T034500Z
RRULE:FREQ=DAILY;COUNT=10
EXDATE:20260916T013000Z
END:VEVENT
BEGIN:VEVENT
UID:daily-1
RECURRENCE-ID:20260915T013000Z
SUMMARY:ежедневное оперативное совещание (перенесено)
DTSTART:20260915T050000Z
DTEND:20260915T060000Z
END:VEVENT
BEGIN:VEVENT
UID:daily-1
RECURRENCE-ID:20260917T013000Z
STATUS:CANCELLED
SUMMARY:ежедневное оперативное совещание
DTSTART:20260917T013000Z
DTEND:20260917T034500Z
END:VEVENT
END:VCALENDAR
""".encode("utf-8")

WEEK_START = datetime(2026, 9, 14, tzinfo=timezone.utc)
WEEK_END = datetime(2026, 9, 21, tzinfo=timezone.utc)


def _expanded_ics(days: range) -> bytes:
    """Как отдаёт библиотека caldav с expand=True: только экземпляры с
    RECURRENCE-ID и общим UID, без основной записи серии."""
    parts = [b"BEGIN:VCALENDAR\nVERSION:2.0\n"]
    for day in days:
        stamp = f"202609{day:02d}T013000Z".encode()
        parts.append(
            b"BEGIN:VEVENT\nUID:daily-1\nRECURRENCE-ID:" + stamp + b"\nSUMMARY:\xd1\x81\xd0\xbe\xd0\xb2\xd0\xb5\xd1\x89\xd0\xb0\xd0\xbd\xd0\xb8\xd0\xb5\n"
            b"DTSTART:" + stamp + b"\nDTEND:" + stamp.replace(b"0130", b"0345") + b"\nEND:VEVENT\n"
        )
    parts.append(b"END:VCALENDAR\n")
    return b"".join(parts)


def test_series_with_changed_and_cancelled_days(tmp_path: Path) -> None:
    events = itip.parse_ics_events(SERIES_ICS, "me@example.com")
    uids = sorted(event.uid for event in events)
    assert uids == ["daily-1", "daily-1|RID:20260915T013000Z"]  # отменённый экземпляр не хранится
    path = tmp_path / "c.rmcal"
    for event in events:
        calendar_store.save_event(path, event)
    week = calendar_store.list_events(path, WEEK_START, WEEK_END)
    starts = sorted(event.dtstart for event in week)
    # 14-е, перенесённое 15-е в 05:00, 18-е, 19-е, 20-е; 16-е (EXDATE) и 17-е (отменено) — нет
    assert [moment.strftime("%d %H:%M") for moment in starts] == ["14 01:30", "15 05:00", "18 01:30", "19 01:30", "20 01:30"]


def test_expanded_instances_are_not_collapsed_into_one_day(tmp_path: Path) -> None:
    events = itip.parse_ics_events(_expanded_ics(range(14, 21)), "me@example.com")
    assert len({event.uid for event in events}) == 7
    path = tmp_path / "c.rmcal"
    for event in events:
        calendar_store.save_event(path, event)
    assert len(calendar_store.list_events(path, WEEK_START, WEEK_END)) == 7


class _Server:
    def __init__(self, events):
        self.events = events
        self.pushed = []
        self.deleted = []

    def fetch_events(self, start, end):
        return list(self.events)

    def push_event(self, event):
        self.pushed.append(event.uid)

    def delete_event(self, uid):
        self.deleted.append(uid)


def test_sync_keeps_every_day_and_never_pushes_instances(tmp_path: Path) -> None:
    path = tmp_path / "c.rmcal"
    calendar = calendar_store.Calendar(id="vk", name="CalDAV", color="#000", source_type=calendar_store.SOURCE_CALDAV)
    # Как было до исправления: от серии в базе остался один день под UID серии.
    calendar_store.save_event(path, calendar_store.Event(
        uid="daily-1", summary="совещание", dtstart=datetime(2026, 9, 20, 1, 30, tzinfo=timezone.utc),
        dtend=datetime(2026, 9, 20, 3, 45, tzinfo=timezone.utc), calendar_id="vk",
    ))
    server = _Server(itip.parse_ics_events(_expanded_ics(range(14, 21)), "me@example.com"))
    calendar_sync.sync_calendar(path, calendar, server, WEEK_START, WEEK_END)
    calendar_sync.sync_calendar(path, calendar, server, WEEK_START, WEEK_END)
    week = calendar_store.list_events(path, WEEK_START, WEEK_END)
    assert len(week) == 7 and all(calendar_store.is_instance_uid(event.uid) for event in week)

    # Правка и удаление одного экземпляра на сервер не уходят чужим UID.
    instance = week[0]
    calendar_store.save_event(path, instance, needs_push=True)
    calendar_store.delete_event(path, week[1].uid)
    report = calendar_sync.sync_calendar(path, calendar, server, WEEK_START, WEEK_END)
    assert server.pushed == [] and server.deleted == [] and report.errors == []


def test_series_exdates_survive_round_trip_to_server_format() -> None:
    master = next(event for event in itip.parse_ics_events(SERIES_ICS, "me@example.com") if event.uid == "daily-1")
    master.is_organizer = True
    ics = itip.build_caldav_ics(master, "me@example.com", "Я")
    assert b"EXDATE" in ics
    again = next(event for event in itip.parse_ics_events(ics, "me@example.com") if event.uid == "daily-1")
    assert {moment.day for moment in again.exdates} == {15, 16, 17}


class _Item(SimpleNamespace):
    pass


def _ews_item(day: int, *, kind: str = "Occurrence") -> _Item:
    start = datetime(2026, 9, day, 1, 30, tzinfo=timezone.utc)
    return _Item(
        uid="series-uid", id=f"id-{day}", type=kind, subject="совещание", start=start, end=start + timedelta(hours=2),
        original_start=start, location="", text_body="", is_all_day=False, organizer=None,
        my_response_type="Accept", required_attendees=[], optional_attendees=[], is_cancelled=False,
    )


def test_ews_view_is_split_under_server_limit_and_keeps_occurrences(monkeypatch) -> None:
    monkeypatch.setattr(ews_calendar, "CalendarItem", _Item)
    start = datetime(2026, 8, 18, tzinfo=timezone.utc)
    end = start + timedelta(days=210)
    windows = []

    class Calendar:
        def view(self, start, end):
            if end - start > timedelta(days=14):
                raise RuntimeError("You have exceeded the maximum number of objects that can be returned")
            windows.append((start, end))
            # встреча на стыке двух частей приходит в обеих
            return [_ews_item(day) for day in range(1, 29) if start.day <= day <= end.day and start.month == 9]

    events = ews_calendar.fetch_events(SimpleNamespace(calendar=Calendar()), start, end, "me@example.com")
    assert len(windows) == 15
    assert len(events) == len({event.uid for event in events}) == 28
    assert all(event.uid.startswith("series-uid|RID:") for event in events)
    single = ews_calendar.item_to_event(_ews_item(3, kind="Single"), "me@example.com")
    assert single.uid == "series-uid"


@pytest.mark.parametrize("uid", ["x|RID:20260101T000000Z"])
def test_instance_uid_helpers(uid: str) -> None:
    assert calendar_store.is_instance_uid(uid) and calendar_store.series_uid(uid) == "x"
    assert calendar_store.instance_uid("x", datetime(2026, 1, 1, 3, tzinfo=timezone(timedelta(hours=3)))) == uid


def test_weekday_rule_expands_in_local_time() -> None:
    # Понедельник 08:30 по Якутску (UTC+9) — это воскресенье 23:30 по UTC.
    yakutsk = timezone(timedelta(hours=9))
    start_local = datetime(2026, 9, 14, 8, 30, tzinfo=yakutsk)  # понедельник
    event = calendar_store.Event(
        uid="weekdays", summary="Планёрка", dtstart=start_local.astimezone(timezone.utc),
        dtend=(start_local + timedelta(minutes=30)).astimezone(timezone.utc), recurrence_rule="FREQ=WEEKLY;BYDAY=MO,WE,FR",
    )
    window = (datetime(2026, 9, 13, tzinfo=timezone.utc), datetime(2026, 9, 20, tzinfo=timezone.utc))
    events = calendar_store._expand_recurring([event], *window, local_tz=yakutsk)
    assert [e.dtstart.astimezone(yakutsk).strftime("%a %H:%M") for e in events] == ["Mon 08:30", "Wed 08:30", "Fri 08:30"]
    assert all(e.dtstart.tzinfo == timezone.utc for e in events)
    # В UTC то же правило дало бы воскресенье, вторник и четверг.
    wrong = calendar_store._expand_recurring([event], *window, local_tz=timezone.utc)
    assert [e.dtstart.astimezone(yakutsk).strftime("%a") for e in wrong] != ["Mon", "Wed", "Fri"]


VK_LIKE_SERIES = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTIMEZONE
TZID:Asia/Yakutsk
BEGIN:STANDARD
DTSTART:19700101T000000
TZOFFSETFROM:+0900
TZOFFSETTO:+0900
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:vk-daily
SUMMARY:ежедневное оперативное совещание
DTSTART;TZID=Asia/Yakutsk:20260105T083000
DTEND;TZID=Asia/Yakutsk:20260105T104500
RRULE:FREQ=WEEKLY;UNTIL=20261231T235959Z;BYDAY=MO,TU,WE,TH,FR
EXDATE;TZID=Asia/Yakutsk:20260915T083000,20260916T083000
END:VEVENT
BEGIN:VEVENT
UID:vk-daily
RECURRENCE-ID;TZID=Asia/Yakutsk:20260917T083000
SUMMARY:ежедневное оперативное совещание
DTSTART;TZID=Asia/Yakutsk:20260917T093000
DTEND;TZID=Asia/Yakutsk:20260917T110000
END:VEVENT
END:VCALENDAR
""".encode("utf-8")


def test_vk_like_year_series_with_timezone(tmp_path: Path) -> None:
    yakutsk = timezone(timedelta(hours=9))
    events = itip.parse_ics_events(VK_LIKE_SERIES, "me@x.ru")
    path = tmp_path / "c.rmcal"
    for event in events:
        calendar_store.save_event(path, event)
    week = (datetime(2026, 9, 13, 15, tzinfo=timezone.utc), datetime(2026, 9, 20, 15, tzinfo=timezone.utc))  # пн–вс по Якутску
    stored = calendar_store.list_events(path, *week)
    # вт 15 и ср 16 отменены (EXDATE), чт 17 перенесён на 09:30, сб/вс нет
    expected = sorted(["Mon 14 08:30", "Thu 17 09:30", "Fri 18 08:30"])
    local = calendar_store._expand_recurring(
        [calendar_store.get_event(path, "vk-daily")], *week, local_tz=yakutsk
    ) + [e for e in stored if calendar_store.is_instance_uid(e.uid)]
    assert sorted(e.dtstart.astimezone(yakutsk).strftime("%a %d %H:%M") for e in local) == expected
    # за окно синхронизации (7 месяцев) серия — это около 150 рабочих дней, как в журнале VK
    window = (datetime(2026, 8, 18, tzinfo=timezone.utc), datetime(2027, 3, 16, tzinfo=timezone.utc))
    series_days = calendar_store._expand_recurring([calendar_store.get_event(path, "vk-daily")], *window, local_tz=yakutsk)
    assert 95 <= len(series_days) <= 100  # до 31.12.2026: сен–дек ≈ 97 будней минус 3
