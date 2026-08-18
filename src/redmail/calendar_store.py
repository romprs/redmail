from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

# Свой формат: один файл SQLite на календарь — та же идея, что у архива
# писем (archive_store.py). Сервер календаря (Exchange/VK Mail) не нужен:
# в закрытой корпоративной среде без CalDAV/EWS-доступа события хранятся
# локально, а встречи/ответы/переносы передаются через обычную почту как
# .ics-вложения (iTIP, RFC 5546) — см. itip.py.
_FORMAT_VERSION = 1

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
    raw_ics BLOB
);
"""

_COLUMNS = (
    "id, uid, sequence, summary, description, location, dtstart, dtend, all_day, "
    "organizer_email, organizer_name, is_organizer, status, my_participation, attendees, raw_ics"
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
    raw_ics: bytes | None = None


def new_uid() -> str:
    return f"{uuid4()}@redmail"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def create_calendar(path: Path) -> None:
    """Создаёт пустой файл календаря. Не трогает уже существующий по этому пути."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(path)) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = 'format_version'").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('format_version', ?)", (str(_FORMAT_VERSION),)
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


def _row_to_event(row) -> Event:
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
        raw_ics=row[15],
    )


def list_events(path: Path, start: datetime | None = None, end: datetime | None = None) -> list[Event]:
    """События, пересекающиеся с полуинтервалом [start, end) (обе границы
    опциональны). Без границ — все события, отсортированные по началу."""
    create_calendar(path)
    query = f"SELECT {_COLUMNS} FROM events"
    clauses = []
    params: list[str] = []
    if end is not None:
        clauses.append("dtstart < ?")
        params.append(end.isoformat())
    if start is not None:
        clauses.append("dtend > ?")
        params.append(start.isoformat())
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY dtstart"
    with closing(_connect(path)) as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_event(row) for row in rows]


def get_event(path: Path, uid: str) -> Event | None:
    create_calendar(path)
    with closing(_connect(path)) as conn:
        row = conn.execute(f"SELECT {_COLUMNS} FROM events WHERE uid = ?", (uid,)).fetchone()
    return _row_to_event(row) if row else None


def save_event(path: Path, event: Event) -> None:
    """Вставляет или обновляет по UID (UID устойчив между переносами — это
    один и тот же iCalendar-объект с растущим SEQUENCE, не новое событие)."""
    create_calendar(path)
    with closing(_connect(path)) as conn:
        conn.execute(
            "INSERT INTO events (uid, sequence, summary, description, location, dtstart, dtend, all_day, "
            "organizer_email, organizer_name, is_organizer, status, my_participation, attendees, raw_ics) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET "
            "sequence=excluded.sequence, summary=excluded.summary, description=excluded.description, "
            "location=excluded.location, dtstart=excluded.dtstart, dtend=excluded.dtend, all_day=excluded.all_day, "
            "organizer_email=excluded.organizer_email, organizer_name=excluded.organizer_name, "
            "is_organizer=excluded.is_organizer, status=excluded.status, my_participation=excluded.my_participation, "
            "attendees=excluded.attendees, raw_ics=excluded.raw_ics",
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
                event.raw_ics,
            ),
        )
        conn.commit()


def delete_event(path: Path, uid: str) -> None:
    with closing(_connect(path)) as conn:
        conn.execute("DELETE FROM events WHERE uid = ?", (uid,))
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
