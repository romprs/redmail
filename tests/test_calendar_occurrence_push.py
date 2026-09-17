from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import icalendar

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from redmail import caldav_sync, calendar_store, calendar_sync, ews_calendar, itip  # noqa: E402

START = datetime(2026, 9, 14, 1, 30, tzinfo=timezone.utc)
WEEK = (datetime(2026, 9, 14, tzinfo=timezone.utc), datetime(2026, 9, 21, tzinfo=timezone.utc))


def _series(**kwargs) -> calendar_store.Event:
    base = dict(
        uid="daily-1", summary="Оперативка", dtstart=START, dtend=START + timedelta(hours=1),
        recurrence_rule="FREQ=DAILY;COUNT=7", is_organizer=True, calendar_id="vk",
        organizer_email="me@x.ru", attendees=[calendar_store.Attendee(email="a@x.ru")],
    )
    base.update(kwargs)
    return calendar_store.Event(**base)


def test_detach_moves_one_day_without_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "c.rmcal"
    calendar_store.save_event(path, _series())
    day = next(e for e in calendar_store.list_events(path, *WEEK) if e.dtstart.day == 16)
    assert calendar_store.is_series(path, day)
    instance = calendar_store.detach_occurrence(path, day)
    assert instance.uid == "daily-1|RID:20260916T013000Z" and instance.recurrence_rule is None
    moved = calendar_store.reschedule_event(path, instance.uid, day.dtstart + timedelta(hours=5), day.dtend + timedelta(hours=5))
    assert moved.sequence == 1 and calendar_store.events_to_push(path, "vk")[0].uid == instance.uid
    days = sorted((e.dtstart.day, e.dtstart.hour) for e in calendar_store.list_events(path, *WEEK))
    assert days == [(14, 1), (15, 1), (16, 6), (17, 1), (18, 1), (19, 1), (20, 1)]


def test_itip_for_one_day_uses_series_uid_and_recurrence_id() -> None:
    event = _series(uid="daily-1|RID:20260916T013000Z", recurrence_rule=None,
                    dtstart=START + timedelta(days=2, hours=5), dtend=START + timedelta(days=2, hours=6))
    for ics in (itip.build_request_ics(event, "me@x.ru", "Я"), itip.build_cancel_ics(event, "me@x.ru", "Я")):
        vevent = icalendar.Calendar.from_ical(ics).walk("VEVENT")[0]
        assert str(vevent["UID"]) == "daily-1"
        assert vevent["RECURRENCE-ID"].dt == datetime(2026, 9, 16, 1, 30, tzinfo=timezone.utc)
    # письмо участнику разбирается обратно в тот же день серии
    parsed = itip.parse_ics_events(itip.build_request_ics(event, "me@x.ru", "Я"), "a@x.ru")
    assert [e.uid for e in parsed] == ["daily-1|RID:20260916T013000Z"]


class _Server:
    def __init__(self, events=()):
        self.events = list(events)
        self.calls = []

    def fetch_events(self, start, end):
        return list(self.events)

    def push_event(self, event):
        self.calls.append(("series", event.uid))

    def push_occurrence(self, event):
        self.calls.append(("day", event.uid))

    def cancel_occurrence(self, uid):
        self.calls.append(("cancel", uid))

    def delete_event(self, uid):
        self.calls.append(("delete", uid))


def test_sync_sends_moved_and_cancelled_days(tmp_path: Path) -> None:
    path = tmp_path / "c.rmcal"
    calendar = calendar_store.Calendar(id="vk", name="CalDAV", color="#000", source_type=calendar_store.SOURCE_CALDAV)
    calendar_store.save_event(path, _series())
    days = calendar_store.list_events(path, *WEEK)
    moved = calendar_store.detach_occurrence(path, days[2])
    calendar_store.reschedule_event(path, moved.uid, moved.dtstart + timedelta(hours=2), moved.dtend + timedelta(hours=2))
    cancelled = calendar_store.detach_occurrence(path, days[4])
    calendar_store.delete_event(path, cancelled.uid)
    calendar_store.remember_server_delete(path, cancelled.uid, "vk")

    # Сервер ещё отдаёт серию без переноса — день не должен задвоиться.
    server = _Server([_series()])
    report = calendar_sync.sync_calendar(path, calendar, server, *WEEK)
    assert ("day", moved.uid) in server.calls and ("cancel", cancelled.uid) in server.calls
    assert not any(kind in ("series", "delete") for kind, _uid in server.calls) and report.errors == []
    assert len([e for e in calendar_store.list_events(path, *WEEK) if e.dtstart.day == 16]) == 1


RESOURCE = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:daily-1
SUMMARY:Оперативка
DTSTART:20260914T013000Z
DTEND:20260914T023000Z
RRULE:FREQ=DAILY;COUNT=7
SEQUENCE:0
END:VEVENT
BEGIN:VEVENT
UID:daily-1
RECURRENCE-ID:20260915T013000Z
SUMMARY:Оперативка (перенос)
DTSTART:20260915T050000Z
DTEND:20260915T060000Z
END:VEVENT
END:VCALENDAR
"""


def test_caldav_resource_edits_keep_other_days() -> None:
    day = _series(uid="daily-1|RID:20260916T013000Z", recurrence_rule=None,
                  dtstart=datetime(2026, 9, 16, 7, tzinfo=timezone.utc), dtend=datetime(2026, 9, 16, 8, tzinfo=timezone.utc))
    override = itip.build_vevent(day, organizer_email="me@x.ru", organizer_name="Я", status="CONFIRMED")
    with_override = caldav_sync._set_override(RESOURCE, override, calendar_store.instance_start(day.uid))
    events = {e.uid: e for e in itip.parse_ics_events(with_override.encode(), "me@x.ru")}
    assert set(events) == {"daily-1", "daily-1|RID:20260915T013000Z", "daily-1|RID:20260916T013000Z"}

    cancelled = caldav_sync._exclude_date(with_override, datetime(2026, 9, 16, 1, 30, tzinfo=timezone.utc))
    events = {e.uid: e for e in itip.parse_ics_events(cancelled.encode(), "me@x.ru")}
    assert "daily-1|RID:20260916T013000Z" not in events
    assert datetime(2026, 9, 16, 1, 30, tzinfo=timezone.utc) in events["daily-1"].exdates

    new_master = itip.build_caldav_ics(_series(summary="Оперативка v2"), "me@x.ru", "Я").decode()
    replaced = caldav_sync._replace_master(RESOURCE, new_master)
    events = {e.uid: e for e in itip.parse_ics_events(replaced.encode(), "me@x.ru")}
    assert events["daily-1"].summary == "Оперативка v2" and "daily-1|RID:20260915T013000Z" in events


def test_ews_occurrence_found_by_series_uid_and_original_start(monkeypatch) -> None:
    class Item(SimpleNamespace):
        def save(self, **kwargs):
            self.saved = kwargs

        def delete(self, **kwargs):
            self.deleted = kwargs

    monkeypatch.setattr(ews_calendar, "CalendarItem", Item)
    original = datetime(2026, 9, 16, 1, 30, tzinfo=timezone.utc)
    target = Item(uid="daily-1", original_start=original, start=original, end=original + timedelta(hours=1))
    other = Item(uid="daily-1", original_start=original + timedelta(days=1), start=original, end=original)
    account = SimpleNamespace(calendar=SimpleNamespace(view=lambda start, end: [other, target]))

    moved = _series(uid="daily-1|RID:20260916T013000Z", recurrence_rule=None,
                    dtstart=original + timedelta(hours=3), dtend=original + timedelta(hours=4))
    ews_calendar.push_occurrence(account, moved)
    assert target.saved["send_meeting_invitations"] == "SendToAllAndSaveCopy" and not hasattr(other, "saved")
    ews_calendar.cancel_occurrence(account, moved.uid)
    assert target.deleted["send_meeting_cancellations"] == "SendToAllAndSaveCopy"


def _host(tmp_path: Path, monkeypatch, scope: str):
    from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

    from redmail.ui import main_window as mw

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(mw.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    host = QMainWindow()
    host.calendar_path = tmp_path / "c.rmcal"
    host.SCOPE_ONE, host.SCOPE_ALL = mw.MainWindow.SCOPE_ONE, mw.MainWindow.SCOPE_ALL
    host._ask_series_scope = lambda event, action: scope
    host.refresh_calendar_view = lambda: None
    host._is_exchange_calendar = lambda _id: False
    host._is_server_calendar = lambda _id: True
    host._schedule_calendar_sync = lambda: None
    host.sent = []
    host._send_request_to_attendees = lambda event, **kw: host.sent.append(("request", event.uid))
    host._send_message_in_background = lambda message, **kw: host.sent.append(("cancel", message.subject))
    host.smtp_account = object()
    host.account = SimpleNamespace(username="me@x.ru")
    calendar_store.save_event(host.calendar_path, _series())
    return host, mw


def _in_future(path: Path) -> None:
    # Перенос в прошлое окно не разрешает — переносим серию в будущее.
    master = calendar_store.get_event(path, "daily-1")
    shift = datetime.now(timezone.utc).replace(hour=1, minute=30, second=0, microsecond=0) + timedelta(days=3) - master.dtstart
    calendar_store.save_event(path, calendar_store.Event(**{**master.__dict__, "dtstart": master.dtstart + shift, "dtend": master.dtend + shift}))


def test_drag_one_day_or_whole_series(tmp_path: Path, monkeypatch) -> None:
    host, mw = _host(tmp_path, monkeypatch, "one")
    _in_future(host.calendar_path)
    master = calendar_store.get_event(host.calendar_path, "daily-1")
    window = (master.dtstart - timedelta(hours=2), master.dtstart + timedelta(days=7))
    day = calendar_store.list_events(host.calendar_path, *window)[1]
    mw.MainWindow.on_calendar_event_drag_rescheduled(host, day, 0, 120)
    assert calendar_store.get_event(host.calendar_path, "daily-1").dtstart == master.dtstart  # серия на месте
    assert host.sent and "|RID:" in host.sent[-1][1]

    host._ask_series_scope = lambda event, action: "all"
    day = calendar_store.list_events(host.calendar_path, *window)[0]
    mw.MainWindow.on_calendar_event_drag_rescheduled(host, day, 1, 0)
    assert calendar_store.get_event(host.calendar_path, "daily-1").dtstart == master.dtstart + timedelta(days=1)


def test_cancel_one_day_keeps_series_and_notifies(tmp_path: Path, monkeypatch) -> None:
    host, mw = _host(tmp_path, monkeypatch, "one")
    day = calendar_store.list_events(host.calendar_path, *WEEK)[3]
    host.selected_calendar_event = day
    mw.MainWindow.on_cancel_event(host)
    days = [e.dtstart.day for e in calendar_store.list_events(host.calendar_path, *WEEK)]
    assert 17 not in days and len(days) == 6
    pending = calendar_store.pending_server_deletes(host.calendar_path, "vk")
    assert pending == ["daily-1|RID:20260917T013000Z"] and host.sent[0][0] == "cancel"
