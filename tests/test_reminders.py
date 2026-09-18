from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from redmail import calendar_store, reminders


def _event(path: Path, *, uid: str = "e1@redmail", minutes: int = 15, mode: str = calendar_store.REMIND_WINDOW):
    start = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
    event = calendar_store.Event(
        uid=uid,
        summary="Планёрка",
        dtstart=start,
        dtend=start + timedelta(hours=1),
        location="переговорная",
        organizer_email="me@example.com",
        is_organizer=True,
        remind_minutes=minutes,
        remind_mode=mode,
    )
    calendar_store.save_event(path, event)
    return event


def test_due_reminder_appears_once_until_snoozed(tmp_path: Path) -> None:
    """Напоминание показывается один раз: резидент опрашивает календарь
    каждые полминуты, и без отметки о показе окно открывалось бы снова и
    снова."""
    calendar = tmp_path / "test.rmcal"
    event = _event(calendar)
    state = reminders.ReminderState(tmp_path / reminders.STATE_FILE)
    now = event.dtstart - timedelta(minutes=10)

    due = reminders.due_reminders(calendar, state, now)
    assert [r.summary for r in due] == ["Планёрка"]

    state.mark_shown(due[0], now)
    assert reminders.due_reminders(calendar, state, now + timedelta(minutes=1)) == []

    state.snooze(due[0], now + timedelta(minutes=5))
    assert reminders.due_reminders(calendar, state, now + timedelta(minutes=2)) == []
    assert len(reminders.due_reminders(calendar, state, now + timedelta(minutes=6))) == 1


def test_state_survives_restart(tmp_path: Path) -> None:
    calendar = tmp_path / "test.rmcal"
    event = _event(calendar)
    path = tmp_path / reminders.STATE_FILE
    state = reminders.ReminderState(path)
    now = event.dtstart - timedelta(minutes=10)
    state.mark_shown(reminders.due_reminders(calendar, state, now)[0], now)

    assert reminders.due_reminders(calendar, reminders.ReminderState(path), now) == []


def test_reminder_mode_decides_voice_and_window() -> None:
    start = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
    def make(mode: str) -> reminders.Reminder:
        return reminders.Reminder("u", "Планёрка", start, start + timedelta(hours=1), "", mode)

    assert make(calendar_store.REMIND_WINDOW).shows_window and not make(calendar_store.REMIND_WINDOW).speaks
    assert make(calendar_store.REMIND_VOICE).speaks and not make(calendar_store.REMIND_VOICE).shows_window
    both = make(calendar_store.REMIND_BOTH)
    assert both.speaks and both.shows_window


def test_series_days_are_reminded_separately(tmp_path: Path) -> None:
    """У серии один UID на все дни — отметка о показе должна быть у каждого
    дня своя, иначе напоминание пришло бы только в первый день."""
    calendar = tmp_path / "test.rmcal"
    start = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
    calendar_store.save_event(calendar, calendar_store.Event(
        uid="series@redmail", summary="Планёрка", dtstart=start, dtend=start + timedelta(hours=1),
        organizer_email="me@example.com", is_organizer=True,
        recurrence_rule="FREQ=DAILY", remind_minutes=15, remind_mode=calendar_store.REMIND_VOICE,
    ))
    state = reminders.ReminderState(tmp_path / reminders.STATE_FILE)

    first = reminders.due_reminders(calendar, state, start - timedelta(minutes=10))
    assert len(first) == 1
    state.mark_shown(first[0], start - timedelta(minutes=10))

    second = reminders.due_reminders(calendar, state, start + timedelta(days=1, minutes=-10))
    assert len(second) == 1
    assert second[0].key != first[0].key


def test_spoken_text_uses_local_time_and_place() -> None:
    start = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
    reminder = reminders.Reminder("u", "Планёрка", start, start + timedelta(hours=1), "переговорная", "voice")
    text = reminders.spoken_text(reminder, start - timedelta(minutes=15))
    assert "Планёрка" in text and "переговорная" in text
    assert f"{start.astimezone():%H:%M}" in text


def test_today_events_only_current_day(tmp_path: Path) -> None:
    calendar = tmp_path / "test.rmcal"
    now = datetime.now().astimezone()
    today = now.replace(hour=12, minute=0, second=0, microsecond=0)
    calendar_store.save_event(calendar, calendar_store.Event(
        uid="today@redmail", summary="Сегодня", dtstart=today.astimezone(timezone.utc),
        dtend=(today + timedelta(hours=1)).astimezone(timezone.utc), organizer_email="me@example.com",
    ))
    calendar_store.save_event(calendar, calendar_store.Event(
        uid="later@redmail", summary="Послезавтра", dtstart=(today + timedelta(days=2)).astimezone(timezone.utc),
        dtend=(today + timedelta(days=2, hours=1)).astimezone(timezone.utc), organizer_email="me@example.com",
    ))

    assert [e.summary for e in reminders.today_events(calendar, now)] == ["Сегодня"]


def test_reminder_window_shows_subject_as_plain_text() -> None:
    """Тему встречи пишет тот, кто прислал приглашение: показываем её
    текстом, а не разметкой."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QLabel

    from redmail.ui import reminder_tray

    app = QApplication.instance() or QApplication([])
    start = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
    reminder = reminders.Reminder(
        "u", "<b>Планёрка</b>", start, start + timedelta(hours=1), "<i>переговорная</i>", "window"
    )
    window = reminder_tray.ReminderWindow(reminder)
    try:
        labels = window.findChildren(QLabel)
        assert labels[0].text() == "<b>Планёрка</b>"
        assert labels[0].textFormat() == Qt.TextFormat.PlainText
        assert all(
            label.textFormat() == Qt.TextFormat.PlainText
            for label in labels if label.text() in ("<b>Планёрка</b>", "<i>переговорная</i>")
        )
    finally:
        window.close()
    assert app is not None
