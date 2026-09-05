from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from dateutil.rrule import rrulestr

from redmail.imap_client import Attachment

# Свой формат: один файл SQLite на календарь — та же идея, что у архива
# писем (archive_store.py). Сервер календаря (Exchange/VK Mail) не нужен:
# в закрытой корпоративной среде без CalDAV/EWS-доступа события хранятся
# локально, а встречи/ответы/переносы передаются через обычную почту как
# .ics-вложения (iTIP, RFC 5546) — см. itip.py.
#
# В отличие от почтового кэша (cache_store.py), при смене схемы этот файл
# НЕЛЬЗЯ просто стирать и пересобирать — это единственная копия локально
# принятых приглашений и созданных пользователем встреч, а не что-то
# регенерируемое с сервера. Поэтому новые поля добавляются через
# ALTER TABLE-миграции (см. _MIGRATIONS), а не через wipe-on-mismatch.
_FORMAT_VERSION = 3

# Единственный календарь "по умолчанию", существовавший до появления
# нескольких календарей (жалоба: "в календаре нельзя сделать несколько
# календарей") — все уже сохранённые события молча остаются в нём же
# (миграция ниже проставляет его им как DEFAULT), пользователю не нужно
# ничего разбирать руками после обновления.
DEFAULT_CALENDAR_ID = "default"
_DEFAULT_CALENDAR_NAME = "Мои встречи"
_DEFAULT_CALENDAR_COLOR = "#3B6FB6"

# Столбцы, добавленные после первого релиза — CREATE TABLE IF NOT EXISTS их
# для уже существующих файлов не создаст, поэтому досоздаём миграцией.
_MIGRATIONS = (
    "ALTER TABLE events ADD COLUMN recurrence_rule TEXT",
    "ALTER TABLE events ADD COLUMN color TEXT",
    f"ALTER TABLE events ADD COLUMN calendar_id TEXT NOT NULL DEFAULT '{DEFAULT_CALENDAR_ID}'",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
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
    recurrence_rule TEXT,
    color TEXT,
    calendar_id TEXT NOT NULL DEFAULT 'default',
    raw_ics BLOB
);

CREATE TABLE IF NOT EXISTS event_attachments (
    event_uid TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    payload BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS calendars (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    visible INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0
);
"""

_COLUMNS = (
    "id, uid, sequence, summary, description, location, dtstart, dtend, all_day, "
    "organizer_email, organizer_name, is_organizer, status, my_participation, attendees, "
    "recurrence_rule, color, calendar_id, raw_ics"
)


@dataclass
class Attendee:
    email: str
    name: str = ""
    participation: str = "needs-action"  # needs-action | accepted | declined | tentative


@dataclass
class Event:
    uid: str
    summary: str
    dtstart: datetime  # всегда aware (UTC) — см. itip._to_utc
    dtend: datetime
    id: int | None = None
    sequence: int = 0
    description: str = ""
    location: str = ""
    all_day: bool = False
    organizer_email: str = ""
    organizer_name: str = ""
    is_organizer: bool = False
    status: str = "confirmed"  # confirmed | tentative | cancelled
    my_participation: str = "needs-action"
    attendees: list[Attendee] = field(default_factory=list)
    recurrence_rule: str | None = None  # значение RRULE (RFC 5545), напр. "FREQ=DAILY"
    color: str | None = None  # ручной цвет события (#RRGGBB); None — автоцвет по роли (организатор/участник)
    calendar_id: str = DEFAULT_CALENDAR_ID
    attachments: list[Attachment] = field(default_factory=list)
    raw_ics: bytes | None = None


@dataclass
class Calendar:
    id: str
    name: str
    color: str
    visible: bool = True
    sort_order: int = 0


def new_uid() -> str:
    return f"{uuid4()}@redmail"


def new_calendar_id() -> str:
    return str(uuid4())


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    for migration in _MIGRATIONS:
        try:
            conn.execute(migration)
        except sqlite3.OperationalError:
            pass  # столбец уже есть
    conn.commit()
    return conn


def create_calendar(path: Path) -> None:
    """Создаёт пустой файл календаря, либо доводит уже существующий (в т.ч.
    записанный старой версией приложения) до текущей схемы — миграции уже
    применены в _connect() выше, здесь только фиксируем текущую версию и
    гарантируем наличие календаря "по умолчанию" (INSERT OR IGNORE — не
    перезаписывает, если пользователь его уже переименовал/перекрасил)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(path)) as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('format_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(_FORMAT_VERSION),),
        )
        conn.execute(
            "INSERT OR IGNORE INTO calendars (id, name, color, visible, sort_order) VALUES (?, ?, ?, 1, 0)",
            (DEFAULT_CALENDAR_ID, _DEFAULT_CALENDAR_NAME, _DEFAULT_CALENDAR_COLOR),
        )
        conn.commit()


def is_calendar_file(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with closing(sqlite3.connect(path)) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'format_version'").fetchone()
    except sqlite3.DatabaseError:
        return False
    return row is not None


def _row_to_event(conn: sqlite3.Connection, row) -> Event:
    return Event(
        id=row[0],
        uid=row[1],
        sequence=row[2],
        summary=row[3],
        description=row[4],
        location=row[5],
        dtstart=datetime.fromisoformat(row[6]),
        dtend=datetime.fromisoformat(row[7]),
        all_day=bool(row[8]),
        organizer_email=row[9],
        organizer_name=row[10],
        is_organizer=bool(row[11]),
        status=row[12],
        my_participation=row[13],
        attendees=[Attendee(**a) for a in json.loads(row[14])],
        recurrence_rule=row[15],
        color=row[16],
        calendar_id=row[17],
        attachments=_load_attachments(conn, row[1]),
        raw_ics=row[18],
    )


def _load_attachments(conn: sqlite3.Connection, uid: str) -> list[Attachment]:
    rows = conn.execute(
        "SELECT filename, content_type, payload FROM event_attachments WHERE event_uid = ?", (uid,)
    ).fetchall()
    return [Attachment(filename=r[0], content_type=r[1], payload=r[2]) for r in rows]


def _save_attachments(conn: sqlite3.Connection, uid: str, attachments: list[Attachment]) -> None:
    conn.execute("DELETE FROM event_attachments WHERE event_uid = ?", (uid,))
    for attachment in attachments:
        conn.execute(
            "INSERT INTO event_attachments (event_uid, filename, content_type, payload) VALUES (?, ?, ?, ?)",
            (uid, attachment.filename, attachment.content_type, attachment.payload),
        )


def list_events(path: Path, start: datetime | None = None, end: datetime | None = None) -> list[Event]:
    """События, пересекающиеся с полуинтервалом [start, end) (обе границы
    опциональны). Повторяющиеся события (recurrence_rule) раскрываются в
    отдельные экземпляры внутри окна ТОЛЬКО когда заданы обе границы —
    без верхней границы разворачивать бесконечное RRULE (FREQ=DAILY без
    COUNT/UNTIL) было бы некуда, поэтому в этом случае возвращается только
    хранимый первый экземпляр как есть."""
    create_calendar(path)
    query = f"SELECT {_COLUMNS} FROM events"
    clauses: list[str] = []
    params: list[str] = []

    if end is not None:
        if start is not None:
            # Хранится только первый экземпляр серии — она может пересекать
            # окно, даже если этот самый первый экземпляр был давно, поэтому
            # для повторяющихся проверяем только dtstart < end.
            clauses.append("((dtstart < ? AND dtend > ?) OR (recurrence_rule IS NOT NULL AND dtstart < ?))")
            params.extend([end.isoformat(), start.isoformat(), end.isoformat()])
        else:
            clauses.append("dtstart < ?")
            params.append(end.isoformat())
    elif start is not None:
        clauses.append("(dtend > ? OR recurrence_rule IS NOT NULL)")
        params.append(start.isoformat())

    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY dtstart"
    with closing(_connect(path)) as conn:
        rows = conn.execute(query, params).fetchall()
        events = [_row_to_event(conn, row) for row in rows]

    if start is None or end is None:
        return events
    return _expand_recurring(events, start, end)


def _expand_recurring(events: list[Event], start: datetime, end: datetime) -> list[Event]:
    expanded: list[Event] = []
    for event in events:
        if not event.recurrence_rule:
            expanded.append(event)
            continue
        duration = event.dtend - event.dtstart
        try:
            rule = rrulestr(f"RRULE:{event.recurrence_rule}", dtstart=event.dtstart)
            occurrences = rule.between(start, end, inc=True)
        except (ValueError, TypeError):
            expanded.append(event)  # неразбираемое правило — не теряем событие целиком
            continue
        for occurrence_start in occurrences:
            expanded.append(replace(event, dtstart=occurrence_start, dtend=occurrence_start + duration))
    expanded.sort(key=lambda e: e.dtstart)
    return expanded


def get_event(path: Path, uid: str) -> Event | None:
    create_calendar(path)
    with closing(_connect(path)) as conn:
        row = conn.execute(f"SELECT {_COLUMNS} FROM events WHERE uid = ?", (uid,)).fetchone()
        return _row_to_event(conn, row) if row else None


def save_event(path: Path, event: Event) -> None:
    """Вставляет или обновляет по UID (UID устойчив между переносами — это
    один и тот же iCalendar-объект с растущим SEQUENCE, не новое событие)."""
    create_calendar(path)
    with closing(_connect(path)) as conn:
        conn.execute(
            "INSERT INTO events (uid, sequence, summary, description, location, dtstart, dtend, all_day, "
            "organizer_email, organizer_name, is_organizer, status, my_participation, attendees, "
            "recurrence_rule, color, calendar_id, raw_ics) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET "
            "sequence=excluded.sequence, summary=excluded.summary, description=excluded.description, "
            "location=excluded.location, dtstart=excluded.dtstart, dtend=excluded.dtend, all_day=excluded.all_day, "
            "organizer_email=excluded.organizer_email, organizer_name=excluded.organizer_name, "
            "is_organizer=excluded.is_organizer, status=excluded.status, my_participation=excluded.my_participation, "
            "attendees=excluded.attendees, recurrence_rule=excluded.recurrence_rule, color=excluded.color, "
            "calendar_id=excluded.calendar_id, raw_ics=excluded.raw_ics",
            (
                event.uid,
                event.sequence,
                event.summary,
                event.description,
                event.location,
                event.dtstart.isoformat(),
                event.dtend.isoformat(),
                int(event.all_day),
                event.organizer_email,
                event.organizer_name,
                int(event.is_organizer),
                event.status,
                event.my_participation,
                json.dumps([a.__dict__ for a in event.attendees], ensure_ascii=False),
                event.recurrence_rule,
                event.color,
                event.calendar_id,
                event.raw_ics,
            ),
        )
        _save_attachments(conn, event.uid, event.attachments)
        conn.commit()


def delete_event(path: Path, uid: str) -> None:
    with closing(_connect(path)) as conn:
        conn.execute("DELETE FROM events WHERE uid = ?", (uid,))
        conn.execute("DELETE FROM event_attachments WHERE event_uid = ?", (uid,))
        conn.commit()


def reschedule_event(path: Path, uid: str, dtstart: datetime, dtend: datetime) -> Event | None:
    """Меняет время своего события и увеличивает SEQUENCE — по iTIP это
    сигнал участникам, что приглашение нужно перечитать заново, а не
    считать дублем. Рассылку обновлённого приглашения делает вызывающий
    код (нужен доступ к SMTP), здесь только локальное состояние."""
    event = get_event(path, uid)
    if event is None:
        return None
    event.dtstart = dtstart
    event.dtend = dtend
    event.sequence += 1
    save_event(path, event)
    return event


def apply_invite(path: Path, method: str, event: Event) -> Event:
    """Применяет входящее REQUEST/CANCEL к локальному календарю — создаёт
    или обновляет событие по UID. Для REPLY используйте apply_reply:
    ответ участника обновляет его статус в уже существующем (нашем)
    событии, а не создаёт/подменяет событие целиком."""
    create_calendar(path)
    existing = get_event(path, event.uid)
    if existing is not None and method != "CANCEL":
        # Повторная присылка того же приглашения не должна затирать уже
        # отправленный организатору ответ на него.
        event.my_participation = existing.my_participation
    if method == "CANCEL":
        if existing is not None:
            existing.status = "cancelled"
            save_event(path, existing)
            return existing
        event.status = "cancelled"
    save_event(path, event)
    return event


def set_my_participation(path: Path, uid: str, participation: str) -> Event | None:
    """Мой собственный ответ на приглашение, которое я получил как участник
    (accepted/declined/tentative) — не путать с apply_reply, который для
    организатора обрабатывает REPLY, пришедший ОТ участника."""
    event = get_event(path, uid)
    if event is None:
        return None
    event.my_participation = participation
    save_event(path, event)
    return event


def apply_reply(path: Path, uid: str, attendee_email: str, participation: str) -> Event | None:
    """Организатор получил ответ участника (accepted/declined/tentative) —
    обновляет статус этого участника в своей копии события."""
    event = get_event(path, uid)
    if event is None:
        return None
    for attendee in event.attendees:
        if attendee.email.lower() == attendee_email.lower():
            attendee.participation = participation
            break
    else:
        event.attendees.append(Attendee(email=attendee_email, participation=participation))
    save_event(path, event)
    return event


def _row_to_calendar(row) -> Calendar:
    return Calendar(id=row[0], name=row[1], color=row[2], visible=bool(row[3]), sort_order=row[4])


def list_calendars(path: Path) -> list[Calendar]:
    """Список "моих календарей" (жалоба: "в календаре нельзя сделать
    несколько календарей") — всегда хотя бы один, calendar по умолчанию
    создаётся в create_calendar() при первом обращении к файлу."""
    create_calendar(path)
    with closing(_connect(path)) as conn:
        rows = conn.execute(
            "SELECT id, name, color, visible, sort_order FROM calendars ORDER BY sort_order, name"
        ).fetchall()
        return [_row_to_calendar(row) for row in rows]


def create_user_calendar(path: Path, name: str, color: str) -> Calendar:
    create_calendar(path)
    calendar = Calendar(id=new_calendar_id(), name=name, color=color, visible=True, sort_order=len(list_calendars(path)))
    with closing(_connect(path)) as conn:
        conn.execute(
            "INSERT INTO calendars (id, name, color, visible, sort_order) VALUES (?, ?, ?, ?, ?)",
            (calendar.id, calendar.name, calendar.color, int(calendar.visible), calendar.sort_order),
        )
        conn.commit()
    return calendar


def rename_calendar(path: Path, calendar_id: str, name: str) -> None:
    create_calendar(path)
    with closing(_connect(path)) as conn:
        conn.execute("UPDATE calendars SET name = ? WHERE id = ?", (name, calendar_id))
        conn.commit()


def set_calendar_color(path: Path, calendar_id: str, color: str) -> None:
    create_calendar(path)
    with closing(_connect(path)) as conn:
        conn.execute("UPDATE calendars SET color = ? WHERE id = ?", (color, calendar_id))
        conn.commit()


def set_calendar_visible(path: Path, calendar_id: str, visible: bool) -> None:
    create_calendar(path)
    with closing(_connect(path)) as conn:
        conn.execute("UPDATE calendars SET visible = ? WHERE id = ?", (int(visible), calendar_id))
        conn.commit()


def delete_calendar(path: Path, calendar_id: str) -> None:
    """Удаляет календарь вместе со всеми его событиями. Не запрещает
    удалить последний оставшийся календарь на уровне хранилища — это
    решение интерфейса (там же, где подтверждение), чтобы правило можно
    было увидеть и изменить в одном месте."""
    create_calendar(path)
    with closing(_connect(path)) as conn:
        uids = [row[0] for row in conn.execute("SELECT uid FROM events WHERE calendar_id = ?", (calendar_id,))]
        for uid in uids:
            conn.execute("DELETE FROM event_attachments WHERE event_uid = ?", (uid,))
        conn.execute("DELETE FROM events WHERE calendar_id = ?", (calendar_id,))
        conn.execute("DELETE FROM calendars WHERE id = ?", (calendar_id,))
        conn.commit()
