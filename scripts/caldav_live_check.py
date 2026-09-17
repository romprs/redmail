"""Сквозная проверка повторяющихся встреч на настоящих серверах CalDAV
(Radicale и SOGo на dc1.test.local) кодом redmail.

Запуск: python scripts/caldav_live_check.py (лабораторные серверы, пользователи
calorg и calguest; адреса и пароль можно задать переменными RADICALE_URL,
SOGO_URL, CALDAV_TEST_PASSWORD).
"""
import os
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from redmail import caldav_sync, calendar_store, calendar_sync  # noqa: E402
from redmail.ui import main_window as mw  # noqa: E402

PASSWORD = os.environ.get("CALDAV_TEST_PASSWORD", "CalTest#2026")
SERVERS = {
    "Radicale": os.environ.get("RADICALE_URL", "http://192.168.0.12:5232/{user}/"),
    "SOGo": os.environ.get("SOGO_URL", "http://192.168.0.12/SOGo/dav/{user}/"),
}


def session_for(base: str, user: str):
    probe = caldav_sync.CalDavSession(caldav_sync.CalDavAccount(url=base.format(user=user), username=user, password=PASSWORD))
    calendars = [c for c in probe.list_calendars_detailed() if not c.is_shared]
    url = calendars[0].url
    return caldav_sync.CalDavSession(caldav_sync.CalDavAccount(url=url, username=user, password=PASSWORD)), url


def fresh_view(server, cal_id: str, window):
    """Что сейчас на сервере — синхронизацией в пустую базу."""
    path = Path(tempfile.mkdtemp()) / "view.rmcal"
    calendar = calendar_store.Calendar(id=cal_id, name="check", color="#000", source_type=calendar_store.SOURCE_CALDAV)
    report = calendar_sync.sync_calendar(path, calendar, server, *window, read_only=True)
    assert not report.errors, report.errors
    return sorted(
        ((e.dtstart.astimezone().strftime("%d %H:%M"), e.summary) for e in calendar_store.list_events(path, *window)
         if e.summary.startswith("redmail-проверка")),
    )


def check(name: str, base: str) -> None:
    print(f"\n===== {name} =====")
    org_session, url = session_for(base, "calorg")
    print("календарь организатора:", url)
    org = mw._CalDavCalendarServer(org_session, "calorg@test.local")
    # Уборка остатков прошлых прогонов.
    now = datetime.now(timezone.utc)
    for old in org.fetch_events(now - timedelta(days=30), now + timedelta(days=60)):
        if old.summary.startswith("redmail-проверка"):
            try:
                org.delete_event(calendar_store.series_uid(old.uid))
            except Exception as exc:
                print("   уборка:", exc)
    tomorrow = (datetime.now().astimezone() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    window = (tomorrow - timedelta(days=1), tomorrow + timedelta(days=10))
    path = Path(tempfile.mkdtemp()) / "org.rmcal"
    calendar = calendar_store.Calendar(id="org", name=name, color="#000", source_type=calendar_store.SOURCE_CALDAV, caldav_url=url)
    series = calendar_store.Event(
        uid=calendar_store.new_uid(), summary=f"redmail-проверка серии {int(time.time())}",
        dtstart=tomorrow.astimezone(timezone.utc), dtend=(tomorrow + timedelta(hours=1)).astimezone(timezone.utc),
        recurrence_rule="FREQ=DAILY;COUNT=5", is_organizer=True, organizer_email="calorg@test.local",
        organizer_name="calorg", calendar_id="org",
        attendees=[calendar_store.Attendee(email="calguest@test.local", name="Участник")],
    )
    calendar_store.save_event(path, series, needs_push=True)

    def sync(step):
        report = calendar_sync.sync_calendar(path, calendar, org, *window)
        print(f"{step}: отправлено {report.pushed}, получено {report.pulled}, удалено на сервере {report.deleted_on_server}, ошибок {report.errors}")
        assert not report.errors
        view = fresh_view(org, "view", window)
        for row in view:
            print("   ", row)
        return view

    view = sync("1. создана серия")
    assert len(view) == 5

    days = calendar_store.list_events(path, *window)
    second = [e for e in days if e.summary == series.summary][1]
    moved = calendar_store.detach_occurrence(path, second)
    calendar_store.reschedule_event(path, moved.uid, moved.dtstart + timedelta(hours=2), moved.dtend + timedelta(hours=2))
    view = sync("2. перенесён второй день на 2 часа")
    assert len(view) == 5 and sum(1 for when, _ in view if when.endswith("12:00")) == 1

    days = sorted((e for e in calendar_store.list_events(path, *window) if e.summary == series.summary), key=lambda e: e.dtstart)
    fourth = days[3]
    cancelled = calendar_store.detach_occurrence(path, fourth)
    calendar_store.delete_event(path, cancelled.uid)
    calendar_store.add_exdate(path, series.uid, calendar_store.instance_start(cancelled.uid))
    calendar_store.remember_server_delete(path, cancelled.uid, "org")
    view = sync("3. отменён четвёртый день")
    assert len(view) == 4

    master = calendar_store.get_event(path, series.uid)
    calendar_store.save_event(path, replace(master, summary=master.summary + " (переименована)", sequence=master.sequence + 1), needs_push=True)
    view = sync("4. переименована вся серия")
    assert len(view) == 4 and sum(1 for when, _ in view if when.endswith("12:00")) == 1
    assert all("переименована" in summary for when, summary in view if not when.endswith("12:00"))

    if name == "SOGo":
        guest_session, guest_url = session_for(base, "calguest")
        guest = mw._CalDavCalendarServer(guest_session, "calguest@test.local")
        guest_view = fresh_view(guest, "guest", window)
        print("у участника calguest:")
        for row in guest_view:
            print("   ", row)

    calendar_store.delete_series(path, series.uid)
    calendar_store.remember_server_delete(path, series.uid, "org")
    calendar_sync.sync_calendar(path, calendar, org, *window)
    print("   после удаления серии на сервере:", fresh_view(org, "view", window))


for name, base in SERVERS.items():
    try:
        check(name, base)
    except Exception as exc:
        import traceback

        traceback.print_exc()
        print(f"!!! {name}: {exc}")
