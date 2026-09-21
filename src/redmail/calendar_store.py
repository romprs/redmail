from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
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
# Локальный календарь ни с чем не синхронизируется: это личные записи и
# задачи, участникам они уходят только вручную («Переслать» — письмо с
# приглашением). Встречи же живут в календаре с сервером, помеченном
# основным (см. set_default_calendar).
_DEFAULT_CALENDAR_NAME = "Задачи"
_LEGACY_DEFAULT_CALENDAR_NAMES = ("Мои встречи",)
_DEFAULT_CALENDAR_COLOR = "#3B6FB6"

SOURCE_LOCAL = "local"
SOURCE_CALDAV = "caldav"
# Подписка на .ics по ссылке (Google Календарь: «Закрытый адрес в формате
# iCal»; любой опубликованный календарь). Односторонняя: только чтение.
# Адрес хранится в том же поле caldav_url — схема не меняется.
SOURCE_ICS = "ics"
# Календарь Exchange по EWS — тем же подключением, что и почта Exchange:
# в организации CalDAV может быть не включён, а календарь отдаётся только
# по EWS (замечание: «календарь в exchange передаётся по ews, у нас это не
# предусмотрено»). Адрес не нужен — берётся учётная запись Exchange.
SOURCE_EWS = "ews"

# Способы напоминания о встрече. Выбираются при создании встречи — общего
# умолчания нет намеренно: у одной встречи уместно окно, у другой голос.
REMIND_NONE = "none"
REMIND_WINDOW = "window"
REMIND_VOICE = "voice"
REMIND_BOTH = "both"
REMIND_MODES = (REMIND_NONE, REMIND_WINDOW, REMIND_VOICE, REMIND_BOTH)

# Столбцы, добавленные после первого релиза — CREATE TABLE IF NOT EXISTS их
# для уже существующих файлов не создаст, поэтому досоздаём миграцией.
_MIGRATIONS = (
    "ALTER TABLE events ADD COLUMN recurrence_rule TEXT",
    "ALTER TABLE events ADD COLUMN color TEXT",
    f"ALTER TABLE events ADD COLUMN calendar_id TEXT NOT NULL DEFAULT '{DEFAULT_CALENDAR_ID}'",
    f"ALTER TABLE calendars ADD COLUMN source_type TEXT NOT NULL DEFAULT '{SOURCE_LOCAL}'",
    "ALTER TABLE calendars ADD COLUMN caldav_url TEXT NOT NULL DEFAULT ''",
    # Встреча изменена здесь и ещё не отправлена на сервер своего календаря.
    # Раньше при каждой синхронизации на сервер уходили ВСЕ свои встречи —
    # Exchange при этом рассылал участникам обновления, а VK отвечал 412 на
    # встречи, изменённые с тех пор в другом месте.
    "ALTER TABLE events ADD COLUMN needs_push INTEGER NOT NULL DEFAULT 0",
    # Даты, исключённые из серии (EXDATE и изменённые экземпляры, которые
    # хранятся отдельными записями) — JSON-список ISO-времён в UTC.
    "ALTER TABLE events ADD COLUMN exdates TEXT NOT NULL DEFAULT '[]'",
    # Основной календарь: в него по умолчанию попадают встречи, созданные
    # в программе и голосовым помощником. Признак ровно у одного календаря.
    "ALTER TABLE calendars ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0",
    # Напоминание о встрече: за сколько минут и каким способом (см.
    # REMIND_MODES). -1 в remind_minutes — напоминать не нужно.
    "ALTER TABLE events ADD COLUMN remind_minutes INTEGER NOT NULL DEFAULT -1",
    f"ALTER TABLE events ADD COLUMN remind_mode TEXT NOT NULL DEFAULT '{REMIND_NONE}'",
)

# Экземпляр повторяющейся встречи (изменённый или развёрнутый сервером)
# хранится отдельной записью: у всех экземпляров серии один UID, и при
# хранении по UID каждый следующий затирал предыдущий — от серии оставался
# один день (жалоба: «сначала данные прилетели, потом часть пропала»).
INSTANCE_SEPARATOR = "|RID:"


def instance_uid(uid: str, recurrence_id: datetime) -> str:
    stamp = recurrence_id.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{uid}{INSTANCE_SEPARATOR}{stamp}"


def is_instance_uid(uid: str) -> bool:
    return INSTANCE_SEPARATOR in uid


def series_uid(uid: str) -> str:
    return uid.split(INSTANCE_SEPARATOR, 1)[0]


def instance_start(uid: str) -> datetime | None:
    """Исходное начало экземпляра (RECURRENCE-ID) из его ключа."""
    if INSTANCE_SEPARATOR not in uid:
        return None
    stamp = uid.split(INSTANCE_SEPARATOR, 1)[1]
    return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)

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

-- Встречи, удалённые здесь из календаря с сервером: удаление ещё нужно
-- передать на сервер, иначе следующая синхронизация вернёт их обратно.
CREATE TABLE IF NOT EXISTS pending_deletes (
    uid TEXT PRIMARY KEY,
    calendar_id TEXT NOT NULL
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
    sort_order INTEGER NOT NULL DEFAULT 0,
    source_type TEXT NOT NULL DEFAULT 'local',
    caldav_url TEXT NOT NULL DEFAULT ''
);
"""

_COLUMNS = (
    "id, uid, sequence, summary, description, location, dtstart, dtend, all_day, "
    "organizer_email, organizer_name, is_organizer, status, my_participation, attendees, "
    "recurrence_rule, color, calendar_id, raw_ics, exdates, remind_minutes, remind_mode"
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
    exdates: list[datetime] = field(default_factory=list)  # исключённые из серии начала экземпляров (UTC)
    # Напоминание: за сколько минут до начала предупредить и как именно.
    # remind_minutes < 0 или remind_mode == REMIND_NONE — не напоминать.
    remind_minutes: int = -1
    remind_mode: str = REMIND_NONE


@dataclass
class Calendar:
    id: str
    name: str
    color: str
    visible: bool = True
    sort_order: int = 0
    # "local" — обычный календарь только в этом файле; "caldav" — синхронизируется
    # с внешним CalDAV-сервером (см. caldav_url) через MainWindow.on_caldav_sync.
    # Раньше адрес CalDAV-сервера был один на весь аккаунт (настраивался в
    # Параметрах) — нельзя было подключить несколько внешних календарей и
    # локальные вперемешку с внешними.
    source_type: str = SOURCE_LOCAL
    caldav_url: str = ""
    # Основной календарь: сюда по умолчанию пишутся новые встречи, в том
    # числе созданные голосом. Ровно один на профиль (см. set_default_calendar).
    is_default: bool = False


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
        # В профилях, созданных до переименования, локальный календарь
        # так и назывался «Мои встречи». Переименовываем, только если имя
        # осталось прежним стандартным — своё название пользователя не трогаем.
        conn.execute(
            f"UPDATE calendars SET name = ? WHERE id = ? AND name IN ({','.join('?' * len(_LEGACY_DEFAULT_CALENDAR_NAMES))})",
            (_DEFAULT_CALENDAR_NAME, DEFAULT_CALENDAR_ID, *_LEGACY_DEFAULT_CALENDAR_NAMES),
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
        exdates=_load_exdates(row[19]),
        remind_minutes=row[20],
        remind_mode=row[21],
    )


def _load_exdates(value: str | None) -> list[datetime]:
    try:
        return [datetime.fromisoformat(item) for item in json.loads(value or "[]")]
    except (TypeError, ValueError):
        return []


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


def _utc_text(moment: datetime) -> str:
    """Время для хранения и сравнения — всегда строкой ISO в UTC. Время
    сравнивается в SQL как строки, и одно значение в другом поясе ломает
    порядок (см. list_events)."""
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return moment.isoformat()


def list_events(path: Path, start: datetime | None = None, end: datetime | None = None) -> list[Event]:
    """События, пересекающиеся с полуинтервалом [start, end) (обе границы
    опциональны). Повторяющиеся события (recurrence_rule) раскрываются в
    отдельные экземпляры внутри окна ТОЛЬКО когда заданы обе границы —
    без верхней границы разворачивать бесконечное RRULE (FREQ=DAILY без
    COUNT/UNTIL) было бы некуда, поэтому в этом случае возвращается только
    хранимый первый экземпляр как есть."""
    create_calendar(path)
    # Время хранится строкой ISO в UTC, и сравнение идёт как строк. Границу
    # в другом поясе приводим к UTC, иначе сравнение врёт: «…T00:00+09:00»
    # по строке больше «…T00:00+00:00», хотя это на девять часов раньше, —
    # так в «Сегодня» у напоминалки попадала завтрашняя встреча в 9:00.
    if start is not None and start.tzinfo is not None:
        start = start.astimezone(timezone.utc)
    if end is not None and end.tzinfo is not None:
        end = end.astimezone(timezone.utc)
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


def _expand_recurring(events: list[Event], start: datetime, end: datetime, local_tz=None) -> list[Event]:
    expanded: list[Event] = []
    for event in events:
        if not event.recurrence_rule:
            expanded.append(event)
            continue
        duration = event.dtend - event.dtstart
        # Правило раскрывается в местном времени, а не в UTC: «по будням в
        # 08:30» по Якутску — это 23:30 предыдущего дня по UTC, и BYDAY дал
        # бы не те дни недели. Встречи на весь день хранятся от полуночи UTC.
        rule_start = event.dtstart if event.all_day else event.dtstart.astimezone(local_tz)
        try:
            rule = rrulestr(f"RRULE:{event.recurrence_rule}", dtstart=rule_start)
            occurrences = [moment.astimezone(timezone.utc) for moment in rule.between(start, end, inc=True)]
        except (ValueError, TypeError):
            expanded.append(event)  # неразбираемое правило — не теряем событие целиком
            continue
        excluded = {moment.astimezone(timezone.utc).replace(microsecond=0) for moment in event.exdates}
        for occurrence_start in occurrences:
            if occurrence_start.astimezone(timezone.utc).replace(microsecond=0) in excluded:
                continue  # отменённый или изменённый экземпляр (он хранится отдельно)
            expanded.append(replace(event, dtstart=occurrence_start, dtend=occurrence_start + duration))
    expanded.sort(key=lambda e: e.dtstart)
    return expanded


def get_event(path: Path, uid: str) -> Event | None:
    create_calendar(path)
    with closing(_connect(path)) as conn:
        row = conn.execute(f"SELECT {_COLUMNS} FROM events WHERE uid = ?", (uid,)).fetchone()
        return _row_to_event(conn, row) if row else None


def save_event(path: Path, event: Event, *, needs_push: bool = False) -> None:
    """Вставляет или обновляет по UID (UID устойчив между переносами — это
    один и тот же iCalendar-объект с растущим SEQUENCE, не новое событие).

    needs_push=True — изменение сделано здесь (окно встречи, перенос мышью)
    и должно уйти на сервер календаря при следующей синхронизации. Копии,
    полученные с сервера, сохраняются без него и уже поставленную отметку
    не снимают."""
    create_calendar(path)
    with closing(_connect(path)) as conn:
        conn.execute(
            "INSERT INTO events (uid, sequence, summary, description, location, dtstart, dtend, all_day, "
            "organizer_email, organizer_name, is_organizer, status, my_participation, attendees, "
            "recurrence_rule, color, calendar_id, raw_ics, needs_push, exdates, "
            "remind_minutes, remind_mode) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET "
            "sequence=excluded.sequence, summary=excluded.summary, description=excluded.description, "
            "location=excluded.location, dtstart=excluded.dtstart, dtend=excluded.dtend, all_day=excluded.all_day, "
            "organizer_email=excluded.organizer_email, organizer_name=excluded.organizer_name, "
            "is_organizer=excluded.is_organizer, status=excluded.status, my_participation=excluded.my_participation, "
            "attendees=excluded.attendees, recurrence_rule=excluded.recurrence_rule, color=excluded.color, "
            "calendar_id=excluded.calendar_id, raw_ics=excluded.raw_ics, exdates=excluded.exdates, "
            "remind_minutes=excluded.remind_minutes, remind_mode=excluded.remind_mode, "
            "needs_push=MAX(events.needs_push, excluded.needs_push)",
            (
                event.uid,
                event.sequence,
                event.summary,
                event.description,
                event.location,
                _utc_text(event.dtstart),
                _utc_text(event.dtend),
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
                int(needs_push),
                json.dumps([moment.astimezone(timezone.utc).isoformat() for moment in event.exdates]),
                int(event.remind_minutes),
                event.remind_mode if event.remind_mode in REMIND_MODES else REMIND_NONE,
            ),
        )
        if needs_push:
            # Встречу снова завели здесь — прежнее удаление на сервер не нужно.
            conn.execute("DELETE FROM pending_deletes WHERE uid = ?", (event.uid,))
        _save_attachments(conn, event.uid, event.attachments)
        conn.commit()


def is_series(path: Path, event: Event) -> bool:
    """Встреча — день повторяющейся серии (развёрнутый из правила или
    отдельный экземпляр с сервера)? Тогда у пользователя спрашивают:
    менять только этот день или всю серию."""
    if is_instance_uid(event.uid):
        return True
    stored = get_event(path, event.uid)
    return stored is not None and bool(stored.recurrence_rule)


def add_exdate(path: Path, uid: str, moment: datetime) -> None:
    master = get_event(path, uid)
    if master is None or not master.recurrence_rule:
        return
    moment = moment.astimezone(timezone.utc).replace(microsecond=0)
    if moment not in {m.astimezone(timezone.utc).replace(microsecond=0) for m in master.exdates}:
        master.exdates.append(moment)
        save_event(path, master)


def detach_occurrence(path: Path, occurrence: Event) -> Event:
    """Один день серии — в отдельную запись (экземпляр с RECURRENCE-ID).
    occurrence — день в том виде, как его показывает календарь (время ещё
    не изменено). В серии этот день исключается, чтобы не показывался
    дважды. Уже отдельный экземпляр возвращается как есть."""
    if is_instance_uid(occurrence.uid):
        return get_event(path, occurrence.uid) or occurrence
    master = get_event(path, occurrence.uid)
    if master is None:
        return occurrence
    instance = replace(
        master, id=None, uid=instance_uid(master.uid, occurrence.dtstart), dtstart=occurrence.dtstart,
        dtend=occurrence.dtend, recurrence_rule=None, exdates=[],
    )
    save_event(path, instance)
    add_exdate(path, master.uid, occurrence.dtstart)
    return get_event(path, instance.uid) or instance


def events_to_push(path: Path, calendar_id: str) -> list[Event]:
    """Свои встречи календаря, изменённые здесь и ещё не отправленные."""
    create_calendar(path)
    with closing(_connect(path)) as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM events WHERE calendar_id = ? AND needs_push = 1", (calendar_id,)
        ).fetchall()
        return [_row_to_event(conn, row) for row in rows]


def mark_pushed(path: Path, uid: str) -> None:
    create_calendar(path)
    with closing(_connect(path)) as conn:
        conn.execute("UPDATE events SET needs_push = 0 WHERE uid = ?", (uid,))
        conn.commit()


def remember_server_delete(path: Path, uid: str, calendar_id: str) -> None:
    """Запоминает удаление встречи, которое нужно передать на сервер."""
    create_calendar(path)
    with closing(_connect(path)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pending_deletes (uid, calendar_id) VALUES (?, ?)", (uid, calendar_id)
        )
        conn.commit()


def pending_server_deletes(path: Path, calendar_id: str) -> list[str]:
    create_calendar(path)
    with closing(_connect(path)) as conn:
        rows = conn.execute("SELECT uid FROM pending_deletes WHERE calendar_id = ?", (calendar_id,)).fetchall()
    return [row[0] for row in rows]


def forget_server_delete(path: Path, uid: str) -> None:
    create_calendar(path)
    with closing(_connect(path)) as conn:
        conn.execute("DELETE FROM pending_deletes WHERE uid = ?", (uid,))
        conn.commit()


def stored_events_in_window(path: Path, calendar_id: str, start: datetime, end: datetime) -> list[tuple[str, bool]]:
    """(uid, ждёт ли отправки) встреч календаря, начинающихся в окне —
    без раскрытия повторов: нужно для зеркала удалений с сервера."""
    create_calendar(path)
    with closing(_connect(path)) as conn:
        rows = conn.execute(
            "SELECT uid, needs_push FROM events WHERE calendar_id = ? AND dtstart >= ? AND dtstart < ?",
            # В UTC, как хранится: по этому окну решается, что удалено на
            # сервере, — ошибка сравнения поясов здесь стоила бы встреч.
            (calendar_id, _utc_text(start), _utc_text(end)),
        ).fetchall()
    return [(row[0], bool(row[1])) for row in rows]


def delete_series(path: Path, uid: str) -> None:
    """Серия целиком: основная запись и все её отдельные дни."""
    series = series_uid(uid)
    with closing(_connect(path)) as conn:
        prefix = series + INSTANCE_SEPARATOR
        uids = [series] + [row[0] for row in conn.execute(
            "SELECT uid FROM events WHERE substr(uid, 1, ?) = ?", (len(prefix), prefix)
        )]
    for item in uids:
        delete_event(path, item)


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
    save_event(path, event, needs_push=True)
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
        # И не должна перекладывать встречу в другой календарь: Exchange
        # сам кладёт приглашение в свой календарь, а письмо с ним приходит
        # следом — без этого встреча прыгала между календарями.
        event.calendar_id = existing.calendar_id
        event.color = event.color or existing.color
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
    return Calendar(
        id=row[0], name=row[1], color=row[2], visible=bool(row[3]), sort_order=row[4],
        source_type=row[5], caldav_url=row[6], is_default=bool(row[7]),
    )


def list_calendars(path: Path) -> list[Calendar]:
    """Список "моих календарей" (жалоба: "в календаре нельзя сделать
    несколько календарей") — всегда хотя бы один, calendar по умолчанию
    создаётся в create_calendar() при первом обращении к файлу."""
    create_calendar(path)
    with closing(_connect(path)) as conn:
        rows = conn.execute(
            "SELECT id, name, color, visible, sort_order, source_type, caldav_url, is_default "
            "FROM calendars ORDER BY sort_order, name"
        ).fetchall()
        return [_row_to_calendar(row) for row in rows]


def default_calendar_id(path: Path) -> str:
    """Календарь, в который пишутся новые встречи — в том числе созданные
    голосовым помощником. Это помеченный основным; если такого нет —
    первый календарь с сервером (локальный держим для задач); если и его
    нет — локальный."""
    calendars = list_calendars(path)
    for calendar in calendars:
        if calendar.is_default:
            return calendar.id
    for calendar in calendars:
        if calendar.source_type != SOURCE_LOCAL:
            return calendar.id
    return calendars[0].id if calendars else DEFAULT_CALENDAR_ID


def set_default_calendar(path: Path, calendar_id: str) -> None:
    """Делает календарь основным. Признак ровно у одного: снимаем со всех
    остальных в той же записи."""
    create_calendar(path)
    with closing(_connect(path)) as conn:
        conn.execute("UPDATE calendars SET is_default = 0")
        conn.execute("UPDATE calendars SET is_default = 1 WHERE id = ?", (calendar_id,))
        conn.commit()


def due_reminders(path: Path, now: datetime, *, horizon_hours: int = 24) -> list[Event]:
    """Встречи, о которых пора напомнить: время напоминания уже наступило,
    а сама встреча ещё не началась... не закончилась. Разворачивает серии,
    поэтому напоминания приходят и на повторяющиеся встречи."""
    window_start = now - timedelta(hours=horizon_hours)
    window_end = now + timedelta(hours=horizon_hours)
    due: list[Event] = []
    for event in list_events(path, window_start, window_end):
        if event.status == "cancelled":
            continue
        if event.remind_mode == REMIND_NONE or event.remind_minutes < 0:
            continue
        moment = event.dtstart - timedelta(minutes=event.remind_minutes)
        if moment <= now < event.dtend:
            due.append(event)
    return sorted(due, key=lambda item: item.dtstart)


def create_user_calendar(
    path: Path, name: str, color: str, *, source_type: str = SOURCE_LOCAL, caldav_url: str = ""
) -> Calendar:
    create_calendar(path)
    calendar = Calendar(
        id=new_calendar_id(), name=name, color=color, visible=True, sort_order=len(list_calendars(path)),
        source_type=source_type, caldav_url=caldav_url,
    )
    with closing(_connect(path)) as conn:
        conn.execute(
            "INSERT INTO calendars (id, name, color, visible, sort_order, source_type, caldav_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                calendar.id, calendar.name, calendar.color, int(calendar.visible), calendar.sort_order,
                calendar.source_type, calendar.caldav_url,
            ),
        )
        conn.commit()
    return calendar


def set_calendar_caldav_url(path: Path, calendar_id: str, caldav_url: str) -> None:
    """Меняет адрес CalDAV-сервера у уже существующего календаря (пункт
    "Подключение…" в меню календаря) — сам источник (local/caldav) не
    меняется этой функцией, только адрес."""
    create_calendar(path)
    with closing(_connect(path)) as conn:
        conn.execute("UPDATE calendars SET caldav_url = ? WHERE id = ?", (caldav_url, calendar_id))
        conn.commit()


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
