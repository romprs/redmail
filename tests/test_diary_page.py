"""Страница ежедневника: задачи дня в таблице, отметка исполнения
галочкой, перенос незакрытого, задача из письма."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from redmail import task_store  # noqa: E402
from redmail.ui import main_window as mw  # noqa: E402


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_task_dialog_builds_task_with_due_and_reminder(qapp, tmp_path: Path) -> None:
    dialog = mw.TaskDialog(None, day=date(2026, 9, 24))
    dialog.title_edit.setText("Согласовать ведомость")
    dialog.due_check.setChecked(True)
    dialog.remind_check.setChecked(True)
    dialog.remind_spin.setValue(30)
    task = dialog.task()
    assert task.title == "Согласовать ведомость"
    assert task.day == date(2026, 9, 24)
    assert task.due is not None and task.due.tzinfo is not None
    assert task.remind_minutes == 30 and task.remind_mode != "none"
    dialog.deleteLater()


def test_task_dialog_refuses_empty_title(qapp, monkeypatch) -> None:
    warned = []
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    dialog = mw.TaskDialog(None, day=date(2026, 9, 24))
    dialog.accept()
    assert dialog.result() != mw.QDialog.DialogCode.Accepted and warned
    dialog.deleteLater()


def test_task_dialog_shows_history_and_source_letter(qapp, tmp_path: Path) -> None:
    path = tmp_path / task_store.TASKS_DB
    mail = task_store.MailRef(folder="Входящие", uid=7, subject="Иерархия Инфотех", sender="nazaharov@amurgpz.ru")
    saved = task_store.save_task(path, task_store.Task(title="Ответить", day=date(2026, 9, 24), mail=mail))
    task_store.set_status(path, saved.uid, task_store.STATUS_DONE)
    task = task_store.get_task(path, saved.uid)
    dialog = mw.TaskDialog(None, task=task, history=task_store.task_log(path, saved.uid))
    try:
        texts = [w.toPlainText() for w in dialog.findChildren(mw.QPlainTextEdit) if w.isReadOnly()]
        assert texts and "создана" in texts[0] and "статус" in texts[0]
        labels = [w.text() for w in dialog.findChildren(mw.QLabel)]
        assert any("Иерархия Инфотех" in text for text in labels)
        # правка не теряет привязку к письму
        assert dialog.task().mail is not None and dialog.task().mail.uid == 7
    finally:
        dialog.deleteLater()


def _window(qapp, tmp_path: Path, monkeypatch) -> mw.MainWindow:
    """Окно без сети: учётных записей нет, профиль во временном каталоге."""
    monkeypatch.setattr(mw.profile, "profile_dir", lambda: tmp_path)
    monkeypatch.setattr(mw, "load_accounts", lambda: [])
    monkeypatch.setattr(mw, "load_ews_accounts", lambda: [])
    window = mw.MainWindow()
    return window


def test_diary_page_shows_tasks_and_marks_done(qapp, tmp_path: Path, monkeypatch) -> None:
    window = _window(qapp, tmp_path, monkeypatch)
    try:
        today = date.today()
        window.on_diary_set_day(today)
        task_store.save_task(window.tasks_path, task_store.Task(title="Проверить BoQ", day=today))
        task_store.save_task(
            window.tasks_path,
            task_store.Task(title="Просроченная", day=today - timedelta(days=1)),
        )
        window.refresh_diary_view()
        titles = [window.diary_tasks_table.item(row, 1).text() for row in range(window.diary_tasks_table.rowCount())]
        assert "Проверить BoQ" in titles and "Просроченная" in titles

        row = titles.index("Проверить BoQ")
        window.diary_tasks_table.item(row, 0).setCheckState(Qt.CheckState.Checked)
        saved = [t for t in task_store.list_tasks(window.tasks_path) if t.title == "Проверить BoQ"][0]
        assert saved.done and saved.completed_at is not None
        assert "выполнено 1" in window.diary_title_label.text()
    finally:
        window.close()
        window.deleteLater()


def test_diary_carry_over_and_day_note(qapp, tmp_path: Path, monkeypatch) -> None:
    window = _window(qapp, tmp_path, monkeypatch)
    try:
        today = date.today()
        window.on_diary_set_day(today)
        task_store.save_task(window.tasks_path, task_store.Task(title="Не успел", day=today))
        window.refresh_diary_view()
        window.on_carry_tasks_over()
        moved = task_store.list_tasks(window.tasks_path)[0]
        assert moved.day == today + timedelta(days=1)

        window.diary_note_edit.setPlainText("Позвонить в 9:00")
        window._save_day_note()
        assert task_store.day_note(window.tasks_path, today) == "Позвонить в 9:00"
    finally:
        window.close()
        window.deleteLater()


def test_reminder_resident_also_reminds_about_tasks(tmp_path: Path) -> None:
    from redmail import reminders

    path = tmp_path / task_store.TASKS_DB
    now = datetime.now(timezone.utc)
    task_store.save_task(path, task_store.Task(
        title="Сдать отчёт", due=now + timedelta(minutes=5),
        remind_minutes=15, remind_mode="window",
    ))
    state = reminders.ReminderState(tmp_path / "reminders.json")
    due = reminders.task_reminders(path, state, now)
    assert [r.summary for r in due] == ["Задача: Сдать отчёт"]
    state.mark_shown(due[0], now)
    assert reminders.task_reminders(path, state, now) == []


def test_diary_section_can_be_opened_by_command() -> None:
    """Раздел ежедневника должен открываться и командой канала управления
    (её шлёт напоминалка и голосовой помощник)."""
    from redmail import ipc_server

    assert "diary" in ipc_server._SECTIONS

    class _Controller:
        def __init__(self):
            self.section = None

        def ipc_focus(self, section=None):
            self.section = section

    controller = _Controller()
    reply = ipc_server.handle_request(controller, {"action": "focus", "args": {"section": "diary"}})
    assert reply["ok"] is True and controller.section == "diary"
