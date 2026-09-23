"""Ежедневник: задачи, отметка исполнения, перенос на другой день."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from redmail import task_store as ts

DAY = date(2026, 9, 24)
NOON = datetime(2026, 9, 24, 3, 0, tzinfo=timezone.utc)


def _path(tmp_path: Path) -> Path:
    return tmp_path / ts.TASKS_DB


def test_task_is_saved_and_read_back(tmp_path: Path) -> None:
    path = _path(tmp_path)
    saved = ts.save_task(path, ts.Task(
        title="  Подготовить отчёт  ", notes="по BoQ", day=DAY, due=NOON,
        priority=ts.PRIORITY_HIGH, remind_minutes=15, remind_mode="window",
    ))
    assert saved.uid and saved.title == "Подготовить отчёт"
    again = ts.get_task(path, saved.uid)
    assert again is not None
    assert again.day == DAY and again.due == NOON and again.priority == ts.PRIORITY_HIGH
    assert again.status == ts.STATUS_NEW and again.percent == 0
    assert [entry[1] for entry in ts.task_log(path, saved.uid)] == ["создана"]


def test_completion_is_recorded_with_time(tmp_path: Path) -> None:
    """«Фиксировать исполнение»: у выполненной задачи есть время и запись
    в журнале."""
    path = _path(tmp_path)
    task = ts.save_task(path, ts.Task(title="Позвонить подрядчику", day=DAY))
    done = ts.set_status(path, task.uid, ts.STATUS_DONE)
    assert done is not None and done.done and done.percent == 100
    assert done.completed_at is not None
    assert (datetime.now(timezone.utc) - done.completed_at) < timedelta(minutes=5)
    actions = [entry[1] for entry in ts.task_log(path, task.uid)]
    assert actions == ["создана", "статус"]
    assert "Выполнена" in ts.task_log(path, task.uid)[-1][2]

    # сняли отметку — время выполнения сбрасывается
    back = ts.set_status(path, task.uid, ts.STATUS_IN_PROGRESS, percent=40)
    assert back is not None and back.completed_at is None and back.percent == 40


def test_unfinished_task_stays_visible_next_day(tmp_path: Path) -> None:
    path = _path(tmp_path)
    ts.save_task(path, ts.Task(title="Вчерашняя", day=DAY - timedelta(days=1)))
    ts.save_task(path, ts.Task(title="Сегодняшняя", day=DAY))
    done_yesterday = ts.save_task(path, ts.Task(title="Вчера закрыта", day=DAY - timedelta(days=1)))
    ts.set_status(path, done_yesterday.uid, ts.STATUS_DONE)

    titles = [task.title for task in ts.list_tasks(path, day=DAY)]
    assert titles == ["Вчерашняя", "Сегодняшняя"] or titles == ["Сегодняшняя", "Вчерашняя"]
    assert "Вчера закрыта" not in titles


def test_carry_over_moves_only_open_tasks(tmp_path: Path) -> None:
    path = _path(tmp_path)
    open_task = ts.save_task(path, ts.Task(title="Не успел", day=DAY))
    closed = ts.save_task(path, ts.Task(title="Сделано", day=DAY))
    ts.set_status(path, closed.uid, ts.STATUS_DONE)

    moved = ts.carry_over(path, DAY, DAY + timedelta(days=1))
    assert moved == 1
    assert ts.get_task(path, open_task.uid).day == DAY + timedelta(days=1)
    assert ts.get_task(path, closed.uid).day == DAY
    assert any(entry[1] == "перенос" for entry in ts.task_log(path, open_task.uid))


def test_task_from_letter_keeps_the_link(tmp_path: Path) -> None:
    path = _path(tmp_path)
    task = ts.save_task(path, ts.Task(
        title="Ответить Захарову", day=DAY,
        mail=ts.MailRef(account="rsponomarev@amurgpz.ru", folder="Входящие", uid=42,
                        subject="Иерархия Инфотех", sender="nazaharov@amurgpz.ru"),
    ))
    again = ts.get_task(path, task.uid)
    assert again.mail is not None
    assert again.mail.folder == "Входящие" and again.mail.uid == 42
    assert again.mail.subject == "Иерархия Инфотех"


def test_due_tasks_respect_reminder_settings(tmp_path: Path) -> None:
    path = _path(tmp_path)
    now = datetime.now(timezone.utc)
    soon = ts.save_task(path, ts.Task(
        title="Скоро срок", due=now + timedelta(minutes=5), remind_minutes=15, remind_mode="window",
    ))
    ts.save_task(path, ts.Task(title="Без напоминания", due=now + timedelta(minutes=5)))
    ts.save_task(path, ts.Task(
        title="Далеко", due=now + timedelta(hours=5), remind_minutes=15, remind_mode="window",
    ))
    due = ts.due_tasks(path, now)
    assert [task.title for task in due] == ["Скоро срок"]

    ts.set_status(path, soon.uid, ts.STATUS_DONE)
    assert ts.due_tasks(path, now) == []


def test_overdue_flag_and_day_note(tmp_path: Path) -> None:
    path = _path(tmp_path)
    now = datetime.now(timezone.utc)
    late = ts.save_task(path, ts.Task(title="Просрочена", due=now - timedelta(hours=1)))
    assert ts.get_task(path, late.uid).overdue
    ts.set_status(path, late.uid, ts.STATUS_DONE)
    assert not ts.get_task(path, late.uid).overdue

    assert ts.day_note(path, DAY) == ""
    ts.save_day_note(path, DAY, "Совещание перенесли на пятницу")
    assert ts.day_note(path, DAY) == "Совещание перенесли на пятницу"
    assert ts.day_summary(path, DAY) == (0, 0)


def test_tasks_travel_with_the_profile() -> None:
    """Ежедневник должен переезжать вместе с профилем, иначе задачи
    потеряются при переносе на другой компьютер."""
    from redmail import profile, profile_transfer

    assert profile.TASKS_DB in profile_transfer.PROFILE_FILES
