from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from redmail import calendar_store, calendar_sync

START = datetime(2026, 9, 1, tzinfo=timezone.utc)
END = datetime(2026, 12, 1, tzinfo=timezone.utc)


def _event(uid: str, *, day: int = 10, organizer: bool = True, calendar_id: str = "cal") -> calendar_store.Event:
    start = datetime(2026, 9, day, 9, 0, tzinfo=timezone.utc)
    return calendar_store.Event(
        uid=uid, summary=f"Встреча {uid}", dtstart=start, dtend=start + timedelta(hours=1),
        is_organizer=organizer, calendar_id=calendar_id,
    )


class FakeServer:
    def __init__(self, events=(), *, push_error=None, fetch_error=None) -> None:
        self.events = {event.uid: event for event in events}
        self.pushed: list[str] = []
        self.deleted: list[str] = []
        self.push_error = push_error
        self.fetch_error = fetch_error

    def push_event(self, event) -> None:
        if self.push_error is not None:
            raise self.push_error
        self.pushed.append(event.uid)
        self.events[event.uid] = event

    def delete_event(self, uid: str) -> None:
        self.deleted.append(uid)
        self.events.pop(uid, None)

    def fetch_events(self, start, end):
        if self.fetch_error is not None:
            raise self.fetch_error
        return [calendar_store.Event(**{**event.__dict__, "calendar_id": "other"}) for event in self.events.values()]


def _calendar() -> calendar_store.Calendar:
    return calendar_store.Calendar(id="cal", name="Exchange", color="#000", source_type=calendar_store.SOURCE_EWS)


def test_only_events_changed_here_are_pushed(tmp_path: Path) -> None:
    """Раньше на сервер уходили все свои встречи при каждой синхронизации:
    Exchange рассылал участникам обновления снова и снова."""
    path = tmp_path / "calendar.rmcal"
    calendar_store.save_event(path, _event("from-server"))  # получена с сервера раньше
    calendar_store.save_event(path, _event("edited-here", day=11), needs_push=True)
    server = FakeServer([_event("from-server")])

    report = calendar_sync.sync_calendar(path, _calendar(), server, START, END)
    again = calendar_sync.sync_calendar(path, _calendar(), server, START, END)

    assert server.pushed == ["edited-here"]
    assert report.pushed == 1 and again.pushed == 0


def test_conflict_on_server_keeps_server_copy_and_does_not_retry(tmp_path: Path) -> None:
    path = tmp_path / "calendar.rmcal"
    local = _event("shared")
    local.summary = "Правка здесь"
    calendar_store.save_event(path, local, needs_push=True)
    server_copy = _event("shared")
    server_copy.summary = "Правка на сервере"
    server = FakeServer([server_copy], push_error=RuntimeError("PutError at '412 Precondition Failed'"))

    report = calendar_sync.sync_calendar(path, _calendar(), server, START, END)

    assert report.conflicts == 1 and report.errors == []
    assert calendar_store.get_event(path, "shared").summary == "Правка на сервере"
    assert calendar_store.events_to_push(path, "cal") == []


def test_other_push_errors_keep_local_edit_and_do_not_stop_the_calendar(tmp_path: Path) -> None:
    path = tmp_path / "calendar.rmcal"
    local = _event("mine")
    local.summary = "Не ушло"
    calendar_store.save_event(path, local, needs_push=True)
    server_copy = _event("mine")
    server_copy.summary = "Старая версия"
    server = FakeServer([server_copy, _event("other", day=12)], push_error=RuntimeError("нет сети"))

    report = calendar_sync.sync_calendar(path, _calendar(), server, START, END)

    assert len(report.errors) == 1
    assert calendar_store.get_event(path, "mine").summary == "Не ушло"  # не затёрта
    assert calendar_store.get_event(path, "other") is not None  # остальное пришло
    assert [event.uid for event in calendar_store.events_to_push(path, "cal")] == ["mine"]


def test_event_deleted_here_is_deleted_on_server_and_not_pulled_back(tmp_path: Path) -> None:
    path = tmp_path / "calendar.rmcal"
    calendar_store.save_event(path, _event("gone"))
    calendar_store.delete_event(path, "gone")
    calendar_store.remember_server_delete(path, "gone", "cal")
    server = FakeServer([_event("gone")])

    report = calendar_sync.sync_calendar(path, _calendar(), server, START, END)

    assert server.deleted == ["gone"] and report.deleted_on_server == 1
    assert calendar_store.get_event(path, "gone") is None


def test_event_removed_on_server_is_removed_here(tmp_path: Path) -> None:
    path = tmp_path / "calendar.rmcal"
    calendar_store.save_event(path, _event("removed-elsewhere"))
    calendar_store.save_event(path, _event("still-there", day=12))
    server = FakeServer([_event("still-there", day=12)])

    report = calendar_sync.sync_calendar(path, _calendar(), server, START, END)

    assert report.removed == 1
    assert calendar_store.get_event(path, "removed-elsewhere") is None
    assert calendar_store.get_event(path, "still-there").calendar_id == "cal"


def test_fetch_error_is_reported_without_touching_local_events(tmp_path: Path) -> None:
    path = tmp_path / "calendar.rmcal"
    calendar_store.save_event(path, _event("kept"))
    server = FakeServer(fetch_error=RuntimeError("Unauthorized"))

    report = calendar_sync.sync_calendar(path, _calendar(), server, START, END)

    assert report.errors and "Unauthorized" in report.errors[0]
    assert calendar_store.get_event(path, "kept") is not None


def test_read_only_subscription_never_pushes(tmp_path: Path) -> None:
    path = tmp_path / "calendar.rmcal"
    calendar_store.save_event(path, _event("local-edit"), needs_push=True)
    server = FakeServer([])

    calendar_sync.sync_calendar(path, _calendar(), server, START, END, read_only=True)

    assert server.pushed == []


def test_pulled_copy_keeps_local_color(tmp_path: Path) -> None:
    path = tmp_path / "calendar.rmcal"
    colored = _event("painted")
    colored.color = "#FF0000"
    calendar_store.save_event(path, colored)
    server = FakeServer([_event("painted")])

    calendar_sync.sync_calendar(path, _calendar(), server, START, END)

    assert calendar_store.get_event(path, "painted").color == "#FF0000"
