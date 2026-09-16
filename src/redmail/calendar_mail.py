"""Встречи из писем: приглашения, отмены, ответы участников и .ics-файлы.

Раньше приглашение попадало в календарь только когда письмо открывали, и
только если вложение было помечено как text/calendar. Файл встречи,
пришедший как обычное вложение (приложение/ics, «meeting.ics»), и письма,
которые не открывали, в календарь не попадали вовсе (пожелание: «настроить
анализ писем на наличие вложений ics и включение данных в календарь»).

Здесь — разбор без интерфейса: его вызывают и при открытии письма, и
фоновая докачка писем для каждого скачанного тела.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import icalendar

from redmail import calendar_store, itip
from redmail.applog import get_logger

_log = get_logger("calmail")

_ICS_CONTENT_TYPES = {"text/calendar", "application/ics", "text/x-vcalendar", "application/x-ics"}
_ICS_EXTENSIONS = (".ics", ".vcs", ".ical", ".icalendar")
_ITIP_METHODS = {"REQUEST", "CANCEL", "REPLY"}

#: Встречи, закончившиеся раньше этого срока, из писем не добавляем: при
#: первой докачке почты иначе в календарь приехали бы все приглашения за годы.
PAST_HORIZON = timedelta(days=30)


@dataclass
class CalendarPartResult:
    method: str  # REQUEST | CANCEL | REPLY | PUBLISH
    event: calendar_store.Event | None = None
    invite: itip.IncomingInvite | None = None
    imported: list[calendar_store.Event] = field(default_factory=list)
    replying_attendee: str = ""
    participation: str = ""


def calendar_payloads(content) -> list[bytes]:
    """Части письма с данными календаря — по типу или по имени файла."""
    payloads = []
    for attachment in getattr(content, "attachments", None) or []:
        content_type = (getattr(attachment, "content_type", "") or "").lower()
        filename = (getattr(attachment, "filename", "") or "").lower()
        if content_type not in _ICS_CONTENT_TYPES and not filename.endswith(_ICS_EXTENSIONS):
            continue
        payload = getattr(attachment, "payload", b"") or b""
        if b"BEGIN:VCALENDAR" not in payload.upper():
            continue
        payloads.append(payload)
    return payloads


def has_calendar_data(content) -> bool:
    return bool(calendar_payloads(content))


def apply_calendar_parts(path: Path, content, my_email: str, *, now: datetime | None = None) -> list[CalendarPartResult]:
    """Разбирает календарные части письма и вносит их в календарь.

    Приглашение (REQUEST) добавляет или обновляет встречу, не трогая уже
    данный ответ; отмена (CANCEL) помечает встречу отменённой; ответ
    участника (REPLY) обновляет его статус в своей встрече; файл встречи без
    METHOD или с PUBLISH добавляется как есть. Ничего не отправляет.
    Повторный разбор того же письма ничего не меняет."""
    cutoff = (now or datetime.now(timezone.utc)) - PAST_HORIZON
    results: list[CalendarPartResult] = []
    for payload in calendar_payloads(content):
        try:
            calendar = icalendar.Calendar.from_ical(payload)
        except Exception as exc:
            _log.info("Письмо: календарное вложение не разобрано: %s", exc)
            continue
        method = str(calendar.get("method", "") or "").upper()
        try:
            if method in _ITIP_METHODS:
                result = _apply_itip(path, payload, method, my_email, cutoff)
            else:
                result = _apply_publish(path, payload, my_email, cutoff)
        except Exception as exc:
            _log.warning("Письмо: встреча из вложения не внесена (%s): %s", method or "PUBLISH", exc)
            continue
        if result is not None:
            results.append(result)
    return results


def _apply_itip(path: Path, payload: bytes, method: str, my_email: str, cutoff: datetime) -> CalendarPartResult | None:
    invite = itip.parse_invite(payload, my_email=my_email)
    event = invite.event
    if method == "REQUEST":
        if event.dtend < cutoff and event.recurrence_rule is None:
            return None  # давно прошедшая встреча
        stored = calendar_store.apply_invite(path, "REQUEST", event)
        return CalendarPartResult(method=method, event=stored, invite=invite)
    if method == "CANCEL":
        stored = calendar_store.apply_invite(path, "CANCEL", event)
        return CalendarPartResult(method=method, event=stored, invite=invite)
    if not invite.replying_attendee_email:
        return None
    participation = next(
        (a.participation for a in event.attendees if a.email == invite.replying_attendee_email), "needs-action"
    )
    stored = calendar_store.apply_reply(path, event.uid, invite.replying_attendee_email, participation)
    return CalendarPartResult(
        method=method, event=stored or event, invite=invite,
        replying_attendee=invite.replying_attendee_email, participation=participation,
    )


def _apply_publish(path: Path, payload: bytes, my_email: str, cutoff: datetime) -> CalendarPartResult | None:
    imported = []
    for event in itip.parse_ics_events(payload, my_email):
        if event.dtend < cutoff and event.recurrence_rule is None:
            continue
        existing = calendar_store.get_event(path, event.uid)
        if existing is not None:
            event.calendar_id = existing.calendar_id
            event.color = event.color or existing.color
            event.my_participation = existing.my_participation
        calendar_store.save_event(path, event)
        imported.append(event)
    if not imported:
        return None
    return CalendarPartResult(method="PUBLISH", event=imported[0], imported=imported)
