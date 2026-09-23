"""Ежедневник: задачи и заметки дня.

Отдельно от календаря нарочно. Встреча — это время, на которое человек
занят; задача — то, что нужно сделать, и у неё другая жизнь: срок можно
двигать, её выполняют частично, отмечают сделанной и переносят на
следующий день (пожелание: «календарь — это события, но нужен ежедневник:
записная книжка с задачами и фиксацией исполнения»).

Хранится в профиле своим файлом SQLite — как календарь и книга контактов.
Каждое изменение статуса пишется в журнал задачи: видно не только что
сделано, но и когда.

Поля названы так, чтобы лечь на задачи Exchange (due_date, status,
percent_complete, complete_date) — синхронизация вторым этапом.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from redmail.applog import get_logger

_log = get_logger("tasks")

TASKS_DB = "tasks.rmtasks"

STATUS_NEW = "new"
STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"
STATUSES = (STATUS_NEW, STATUS_IN_PROGRESS, STATUS_DONE, STATUS_CANCELLED)

STATUS_LABELS = {
    STATUS_NEW: "Не начата",
    STATUS_IN_PROGRESS: "В работе",
    STATUS_DONE: "Выполнена",
    STATUS_CANCELLED: "Отменена",
}

PRIORITY_LOW = "low"
PRIORITY_NORMAL = "normal"
PRIORITY_HIGH = "high"
PRIORITIES = (PRIORITY_LOW, PRIORITY_NORMAL, PRIORITY_HIGH)

PRIORITY_LABELS = {
    PRIORITY_LOW: "Низкий",
    PRIORITY_NORMAL: "Обычный",
    PRIORITY_HIGH: "Высокий",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    day TEXT NOT NULL DEFAULT '',
    due TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    priority TEXT NOT NULL DEFAULT 'normal',
    percent INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    remind_minutes INTEGER NOT NULL DEFAULT -1,
    remind_mode TEXT NOT NULL DEFAULT 'none',
    source TEXT NOT NULL DEFAULT '',
    external_id TEXT NOT NULL DEFAULT '',
    mail_ref TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS tasks_day ON tasks (day);

CREATE TABLE IF NOT EXISTS task_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_uid TEXT NOT NULL,
    at TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS task_log_uid ON task_log (task_uid);

CREATE TABLE IF NOT EXISTS day_notes (
    day TEXT PRIMARY KEY,
    text TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
"""

_COLUMNS = (
    "id, uid, title, notes, day, due, status, priority, percent, completed_at, "
    "created_at, updated_at, remind_minutes, remind_mode, source, external_id, mail_ref"
)


@dataclass
class MailRef:
    """Письмо, из которого заведена задача: по нему её можно открыть."""

    account: str = ""
    folder: str = ""
    uid: int = 0
    subject: str = ""
    sender: str = ""

    def to_json(self) -> str:
        if not (self.folder or self.subject):
            return ""
        return json.dumps(
            {"account": self.account, "folder": self.folder, "uid": self.uid,
             "subject": self.subject, "sender": self.sender},
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str) -> "MailRef | None":
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        try:
            uid = int(data.get("uid") or 0)
        except (TypeError, ValueError):
            uid = 0
        return cls(
            account=str(data.get("account") or ""), folder=str(data.get("folder") or ""),
            uid=uid, subject=str(data.get("subject") or ""), sender=str(data.get("sender") or ""),
        )


@dataclass
class Task:
    title: str
    uid: str = ""
    id: int | None = None
    notes: str = ""
    #: День ежедневника, на который задача поставлена (ISO). Пусто — задача
    #: без дня, она висит в списке «Все задачи».
    day: date | None = None
    #: Срок с точностью до минуты (UTC) — по нему напоминание.
    due: datetime | None = None
    status: str = STATUS_NEW
    priority: str = PRIORITY_NORMAL
    percent: int = 0
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    remind_minutes: int = -1
    remind_mode: str = "none"
    #: Откуда задача: пусто — своя, «ews:<ящик>» — из Exchange (второй этап).
    source: str = ""
    external_id: str = ""
    mail: MailRef | None = None
    log: list[tuple[datetime, str, str]] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return self.status == STATUS_DONE

    @property
    def overdue(self) -> bool:
        if self.done or self.status == STATUS_CANCELLED or self.due is None:
            return False
        return self.due < datetime.now(timezone.utc)


def new_uid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_text(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(timezone.utc).isoformat()


def _dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _day_text(value: date | None) -> str:
    return value.isoformat() if value else ""


def _day(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def create_tasks_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(path)) as conn:
        conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('format_version', '1')")
        conn.commit()


def _row_to_task(row) -> Task:
    return Task(
        id=row[0], uid=row[1], title=row[2], notes=row[3], day=_day(row[4]), due=_dt(row[5]),
        status=row[6] if row[6] in STATUSES else STATUS_NEW,
        priority=row[7] if row[7] in PRIORITIES else PRIORITY_NORMAL,
        percent=int(row[8] or 0), completed_at=_dt(row[9]), created_at=_dt(row[10]),
        updated_at=_dt(row[11]), remind_minutes=int(row[12]), remind_mode=row[13],
        source=row[14], external_id=row[15], mail=MailRef.from_json(row[16]),
    )


def _write_log(conn: sqlite3.Connection, uid: str, action: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO task_log (task_uid, at, action, detail) VALUES (?, ?, ?, ?)",
        (uid, _dt_text(_now()), action, detail),
    )


def save_task(path: Path, task: Task) -> Task:
    """Создаёт или обновляет задачу по uid и возвращает её как в базе."""
    create_tasks_file(path)
    uid = task.uid or new_uid()
    now = _now()
    percent = max(0, min(100, int(task.percent or 0)))
    status = task.status if task.status in STATUSES else STATUS_NEW
    if status == STATUS_DONE:
        percent = 100
    completed_at = task.completed_at if status == STATUS_DONE else None
    with closing(_connect(path)) as conn:
        row = conn.execute("SELECT status, title FROM tasks WHERE uid = ?", (uid,)).fetchone()
        created = _dt_text(task.created_at or now) if row is None else None
        if status == STATUS_DONE and completed_at is None:
            completed_at = now
        values = (
            uid, task.title.strip() or "(без темы)", task.notes, _day_text(task.day), _dt_text(task.due),
            status, task.priority if task.priority in PRIORITIES else PRIORITY_NORMAL, percent,
            _dt_text(completed_at), created or _dt_text(task.created_at or now), _dt_text(now),
            int(task.remind_minutes), task.remind_mode, task.source, task.external_id,
            task.mail.to_json() if task.mail else "",
        )
        conn.execute(
            "INSERT INTO tasks (uid, title, notes, day, due, status, priority, percent, completed_at, "
            "created_at, updated_at, remind_minutes, remind_mode, source, external_id, mail_ref) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET title=excluded.title, notes=excluded.notes, day=excluded.day, "
            "due=excluded.due, status=excluded.status, priority=excluded.priority, percent=excluded.percent, "
            "completed_at=excluded.completed_at, updated_at=excluded.updated_at, "
            "remind_minutes=excluded.remind_minutes, remind_mode=excluded.remind_mode, "
            "source=excluded.source, external_id=excluded.external_id, mail_ref=excluded.mail_ref",
            values,
        )
        if row is None:
            _write_log(conn, uid, "создана", task.title.strip())
        elif row[0] != status:
            _write_log(conn, uid, "статус", f"{STATUS_LABELS.get(row[0], row[0])} → {STATUS_LABELS.get(status, status)}")
        conn.commit()
        saved = conn.execute(f"SELECT {_COLUMNS} FROM tasks WHERE uid = ?", (uid,)).fetchone()
    return _row_to_task(saved)


def get_task(path: Path, uid: str) -> Task | None:
    create_tasks_file(path)
    with closing(_connect(path)) as conn:
        row = conn.execute(f"SELECT {_COLUMNS} FROM tasks WHERE uid = ?", (uid,)).fetchone()
    return _row_to_task(row) if row else None


def delete_task(path: Path, uid: str) -> None:
    create_tasks_file(path)
    with closing(_connect(path)) as conn:
        conn.execute("DELETE FROM tasks WHERE uid = ?", (uid,))
        conn.execute("DELETE FROM task_log WHERE task_uid = ?", (uid,))
        conn.commit()


def set_status(path: Path, uid: str, status: str, *, percent: int | None = None) -> Task | None:
    """Отметка исполнения: фиксируется время и остаётся в журнале задачи."""
    task = get_task(path, uid)
    if task is None:
        return None
    task.status = status if status in STATUSES else STATUS_NEW
    task.completed_at = _now() if task.status == STATUS_DONE else None
    if percent is not None:
        task.percent = percent
    elif task.status == STATUS_DONE:
        task.percent = 100
    return save_task(path, task)


def task_log(path: Path, uid: str) -> list[tuple[datetime, str, str]]:
    create_tasks_file(path)
    with closing(_connect(path)) as conn:
        rows = conn.execute(
            "SELECT at, action, detail FROM task_log WHERE task_uid = ? ORDER BY id", (uid,)
        ).fetchall()
    return [(_dt(row[0]) or _now(), row[1], row[2]) for row in rows]


def _order_key(task: Task) -> tuple:
    priority_rank = {PRIORITY_HIGH: 0, PRIORITY_NORMAL: 1, PRIORITY_LOW: 2}
    return (
        task.status == STATUS_DONE or task.status == STATUS_CANCELLED,
        task.due or datetime.max.replace(tzinfo=timezone.utc),
        priority_rank.get(task.priority, 1),
        task.title.casefold(),
    )


def list_tasks(
    path: Path, *, day: date | None = None, include_done: bool = True, only_open: bool = False
) -> list[Task]:
    """Задачи: все, либо страницы одного дня.

    На страницу дня попадает и то, что на этот день поставлено, и то, что
    на этот день просрочено — иначе незакрытая вчерашняя задача исчезла бы
    из виду."""
    create_tasks_file(path)
    with closing(_connect(path)) as conn:
        rows = conn.execute(f"SELECT {_COLUMNS} FROM tasks").fetchall()
    tasks = [_row_to_task(row) for row in rows]
    if day is not None:
        day_end = datetime.combine(day + timedelta(days=1), datetime.min.time()).astimezone().astimezone(timezone.utc)
        today = date.today()
        selected = []
        for task in tasks:
            if task.day == day:
                selected.append(task)
            elif task.day is None and task.due is not None and task.due < day_end and not task.done:
                selected.append(task)
            elif (
                day >= today
                and task.day is not None and task.day < day
                and not task.done and task.status != STATUS_CANCELLED
            ):
                # Незакрытая задача никуда не девается сама: она висит в
                # текущем дне, пока её не закроют (пожелание: «перенос без
                # кнопки, задания висят пока не закрою»). В прошлых днях
                # показываем только то, что на них и стояло.
                selected.append(task)
        tasks = selected
    if only_open or not include_done:
        tasks = [task for task in tasks if task.status not in (STATUS_DONE, STATUS_CANCELLED)]
    return sorted(tasks, key=_order_key)


def carry_over(path: Path, from_day: date, to_day: date) -> int:
    """Перенести незакрытые задачи дня на другой день (обычно на завтра)."""
    moved = 0
    for task in list_tasks(path, day=from_day, include_done=False):
        if task.day != from_day and task.day is not None:
            continue
        task.day = to_day
        save_task(path, task)
        with closing(_connect(path)) as conn:
            _write_log(conn, task.uid, "перенос", f"{from_day.isoformat()} → {to_day.isoformat()}")
            conn.commit()
        moved += 1
    return moved


def due_tasks(path: Path, now: datetime) -> list[Task]:
    """Задачи, о которых пора напомнить (для резидента напоминаний)."""
    result = []
    for task in list_tasks(path, include_done=False):
        if task.due is None or task.remind_mode == "none" or task.remind_minutes < 0:
            continue
        moment = task.due - timedelta(minutes=task.remind_minutes)
        if moment <= now:
            result.append(task)
    return result


def day_note(path: Path, day: date) -> str:
    create_tasks_file(path)
    with closing(_connect(path)) as conn:
        row = conn.execute("SELECT text FROM day_notes WHERE day = ?", (day.isoformat(),)).fetchone()
    return row[0] if row else ""


def save_day_note(path: Path, day: date, text: str) -> None:
    create_tasks_file(path)
    with closing(_connect(path)) as conn:
        conn.execute(
            "INSERT INTO day_notes (day, text, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(day) DO UPDATE SET text=excluded.text, updated_at=excluded.updated_at",
            (day.isoformat(), text, _dt_text(_now())),
        )
        conn.commit()


def day_summary(path: Path, day: date) -> tuple[int, int]:
    """(сколько задач на день, сколько из них выполнено) — для заголовка."""
    tasks = list_tasks(path, day=day)
    return len(tasks), sum(1 for task in tasks if task.done)


def list_open(path: Path) -> list[Task]:
    """Все незакрытые задачи — общий список «что на мне висит»."""
    return list_tasks(path, include_done=False)


def completed_between(path: Path, start: date, end: date) -> list[Task]:
    """Что сделано за период (включительно) — для отчёта.

    Сортировка по времени выполнения: отчёт читают сверху вниз, как
    ленту."""
    create_tasks_file(path)
    first = datetime.combine(start, datetime.min.time()).astimezone().astimezone(timezone.utc)
    last = datetime.combine(end + timedelta(days=1), datetime.min.time()).astimezone().astimezone(timezone.utc)
    done = []
    for task in list_tasks(path):
        if not task.done or task.completed_at is None:
            continue
        if first <= task.completed_at < last:
            done.append(task)
    return sorted(done, key=lambda task: task.completed_at or first)


def report_text(tasks: list[Task], start: date, end: date) -> str:
    """Отчёт за период простым текстом — его копируют в письмо или печатают."""
    header = (
        f"Выполнено за {start:%d.%m.%Y}" if start == end
        else f"Выполнено за период {start:%d.%m.%Y} — {end:%d.%m.%Y}"
    )
    lines = [header, ""]
    for task in tasks:
        when = task.completed_at.astimezone().strftime("%d.%m.%Y %H:%M") if task.completed_at else ""
        lines.append(f"{when}  {task.title}")
        if task.notes.strip():
            lines += [f"    {line}" for line in task.notes.strip().splitlines()]
    if not tasks:
        lines.append("(ничего не отмечено выполненным)")
    else:
        lines += ["", f"Всего: {len(tasks)}"]
    return "\n".join(lines)
