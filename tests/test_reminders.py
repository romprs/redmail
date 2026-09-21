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


def test_others_meeting_is_announced_with_author(tmp_path: Path) -> None:
    """Чужую встречу называем по автору: тема «Планёрка» без имени
    организатора ничего не говорит."""
    calendar = tmp_path / "test.rmcal"
    start = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
    calendar_store.save_event(calendar, calendar_store.Event(
        uid="theirs@redmail", summary="Планёрка", dtstart=start, dtend=start + timedelta(hours=1),
        organizer_email="orlov@example.com", organizer_name="Орлов Олег", is_organizer=False,
        remind_minutes=15, remind_mode=calendar_store.REMIND_VOICE,
    ))
    state = reminders.ReminderState(tmp_path / reminders.STATE_FILE)
    now = start - timedelta(minutes=15)

    reminder = reminders.due_reminders(calendar, state, now)[0]
    assert reminder.mine is False and reminder.organizer == "Орлов Олег"
    assert "Орлов Олег" in reminders.spoken_text(reminder, now)


def test_own_meeting_is_announced_without_author(tmp_path: Path) -> None:
    calendar = tmp_path / "test.rmcal"
    event = _event(calendar, mode=calendar_store.REMIND_VOICE)
    state = reminders.ReminderState(tmp_path / reminders.STATE_FILE)
    now = event.dtstart - timedelta(minutes=15)

    reminder = reminders.due_reminders(calendar, state, now)[0]
    assert reminder.mine is True
    assert "позвал" not in reminders.spoken_text(reminder, now)


def _invitation(path: Path, *, uid: str = "inv@redmail", author: str = "Орлов Олег") -> calendar_store.Event:
    """Приглашение с сервера: организатор чужой, способ напоминания в нём
    никто не выбирал."""
    start = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
    event = calendar_store.Event(
        uid=uid, summary="Планёрка", dtstart=start, dtend=start + timedelta(hours=1),
        organizer_email="orlov@example.com", organizer_name=author, is_organizer=False,
    )
    calendar_store.save_event(path, event)
    return event


def test_others_meeting_reminded_by_settings(tmp_path: Path) -> None:
    """О чужой встрече напоминаем по настройке: в приглашении способ
    выбирать некому."""
    calendar = tmp_path / "test.rmcal"
    event = _invitation(calendar)
    state = reminders.ReminderState(tmp_path / reminders.STATE_FILE)
    policy = reminders.OthersPolicy(mode=calendar_store.REMIND_VOICE, minutes=20)

    assert reminders.due_reminders(calendar, state, event.dtstart - timedelta(minutes=30), policy) == []
    due = reminders.due_reminders(calendar, state, event.dtstart - timedelta(minutes=15), policy)
    assert [r.summary for r in due] == ["Планёрка"]
    assert due[0].mode == calendar_store.REMIND_VOICE and due[0].mine is False


def test_others_meetings_can_be_limited_to_chosen_authors(tmp_path: Path) -> None:
    calendar = tmp_path / "test.rmcal"
    _invitation(calendar, uid="from-boss@redmail", author="Орлов Олег")
    _invitation(calendar, uid="from-other@redmail", author="Гостев Пётр")
    state = reminders.ReminderState(tmp_path / reminders.STATE_FILE)
    policy = reminders.OthersPolicy(mode=calendar_store.REMIND_WINDOW, minutes=15, authors=("Орлов",))

    now = datetime(2026, 9, 1, 9, 50, tzinfo=timezone.utc)
    assert [r.uid for r in reminders.due_reminders(calendar, state, now, policy)] == ["from-boss@redmail"]


def test_others_meetings_can_be_turned_off(tmp_path: Path) -> None:
    calendar = tmp_path / "test.rmcal"
    event = _invitation(calendar)
    state = reminders.ReminderState(tmp_path / reminders.STATE_FILE)
    policy = reminders.OthersPolicy(mode=calendar_store.REMIND_NONE)

    assert reminders.due_reminders(calendar, state, event.dtstart - timedelta(minutes=10), policy) == []


def test_own_choice_wins_over_settings_for_others_meeting(tmp_path: Path) -> None:
    """Если способ у встречи выбран руками, настройка его не перебивает."""
    calendar = tmp_path / "test.rmcal"
    event = _invitation(calendar)
    event.remind_minutes = 5
    event.remind_mode = calendar_store.REMIND_BOTH
    calendar_store.save_event(calendar, event)
    state = reminders.ReminderState(tmp_path / reminders.STATE_FILE)
    policy = reminders.OthersPolicy(mode=calendar_store.REMIND_VOICE, minutes=60)

    assert reminders.due_reminders(calendar, state, event.dtstart - timedelta(minutes=30), policy) == []
    due = reminders.due_reminders(calendar, state, event.dtstart - timedelta(minutes=3), policy)
    assert due and due[0].mode == calendar_store.REMIND_BOTH


def test_meeting_link_found_in_description() -> None:
    text = (
        "Повестка: итоги недели.\n"
        "Подключиться: https://telemost.yandex.ru/j/12345678901234.\n"
        "Материалы: https://docs.example.ru/a?b=1"
    )
    assert reminders.links_in(text) == [
        "https://telemost.yandex.ru/j/12345678901234", "https://docs.example.ru/a?b=1",
    ]
    assert reminders.meeting_link(text) == "https://telemost.yandex.ru/j/12345678901234"
    assert reminders.meeting_link("просто текст без ссылок") == ""


def test_only_web_links_become_clickable() -> None:
    """Текст встречи пишет автор приглашения: кликабельны только http(s), всё
    остальное — просто текст."""
    text = "file:///etc/passwd javascript:alert(1) <b>жирно</b> https://zoom.us/j/1"
    assert reminders.links_in(text) == ["https://zoom.us/j/1"]


def test_reminder_window_shows_description_with_links_and_join_button() -> None:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from redmail.ui import reminder_tray

    QApplication.instance() or QApplication([])
    start = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
    reminder = reminders.Reminder(
        "u", "Планёрка", start, start + timedelta(hours=1), "", "window",
        description="Ссылка: https://teams.microsoft.com/l/meetup-join/abc <script>x</script>",
    )
    window = reminder_tray.ReminderWindow(reminder)
    try:
        html_text = reminder_tray.description_html(reminder.description)
        assert '<a href="https://teams.microsoft.com/l/meetup-join/abc">' in html_text
        assert "&lt;script&gt;" in html_text and "<script>" not in html_text
        assert window.join_link == "https://teams.microsoft.com/l/meetup-join/abc"
        join = next(b for b in window.findChildren(QPushButton) if b.text() == "Подключиться к встрече")
        assert not join.isHidden()
    finally:
        window.close()


def test_resident_stays_alive_while_the_app_runs(monkeypatch) -> None:
    """Найдено на .80: значок в трее был, а напоминания не всплывали и меню
    не отзывалось — объект резидента удалялся сразу после создания, потому
    что main() не держал на него ссылку. Пока приложение работает, резидент
    должен быть жив."""
    import gc
    import os
    import weakref

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from redmail.ui import reminder_tray

    app = QApplication.instance() or QApplication([])
    alive_during_exec: list[bool] = []
    created: list = []

    class FakeTray:
        def __init__(self, application) -> None:
            created.append(weakref.ref(self))

    def fake_exec() -> int:
        gc.collect()
        alive_during_exec.append(created[0]() is not None)
        return 0

    monkeypatch.setattr(reminder_tray, "ReminderTray", FakeTray)
    monkeypatch.setattr(reminder_tray, "QApplication", lambda argv: app)
    monkeypatch.setattr(app, "exec", fake_exec)
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    monkeypatch.setattr(reminder_tray.applog, "setup_logging", lambda: None)

    assert reminder_tray.main(["redmail-reminder"]) == 0
    assert alive_during_exec == [True]


def test_today_menu_is_grouped_by_calendar(tmp_path: Path) -> None:
    """Пожелание: в «Сегодня» разделение по календарям — одна и та же встреча
    из двух календарей иначе выглядела дублем."""
    calendar = tmp_path / "test.rmcal"
    work = calendar_store.create_user_calendar(calendar, "Exchange", "#1E88E5", source_type=calendar_store.SOURCE_EWS)
    now = datetime.now().astimezone()
    nine = now.replace(hour=9, minute=0, second=0, microsecond=0)
    for uid, cal_id, hour in (("a", work.id, 11), ("b", calendar_store.DEFAULT_CALENDAR_ID, 9), ("c", work.id, 9)):
        start = nine.replace(hour=hour)
        calendar_store.save_event(calendar, calendar_store.Event(
            uid=uid, summary=uid, dtstart=start.astimezone(timezone.utc),
            dtend=(start + timedelta(hours=1)).astimezone(timezone.utc), organizer_email="me@x.ru", calendar_id=cal_id,
        ))
    groups = reminders.group_by_calendar(calendar, reminders.today_events(calendar, now))
    assert [(cal.name, [e.uid for e in events]) for cal, events in groups] == [
        ("Задачи", ["b"]), ("Exchange", ["c", "a"]),
    ]
