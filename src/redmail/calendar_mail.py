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
from email.utils import parseaddr
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
    # Письмо пришло не от того, кто вправе менять встречу (отмену прислал не
    # организатор, ответ — не сам участник): в календарь не внесено.
    rejected_sender: str = ""


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
    Повторный разбор того же письма ничего не меняет.

    Менять уже известную встречу может только её организатор, ответ
    участника — только сам участник. Письма теперь разбираются и без
    открытия, поэтому отмену «от имени» организатора, присланную кем-то
    другим (UID встречи знает любой приглашённый), в календарь не вносим."""
    cutoff = (now or datetime.now(timezone.utc)) - PAST_HORIZON
    sender = parseaddr(getattr(content, "from_", "") or "")[1].strip().lower()
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
                result = _apply_itip(path, payload, method, my_email, cutoff, sender)
            else:
                result = _apply_publish(path, payload, my_email, cutoff)
        except Exception as exc:
            _log.warning("Письмо: встреча из вложения не внесена (%s): %s", method or "PUBLISH", exc)
            continue
        if result is not None:
            results.append(result)
    return results


def _same_address(first: str, second: str) -> bool:
    return bool(first) and bool(second) and first.strip().lower() == second.strip().lower()


def _apply_itip(
    path: Path, payload: bytes, method: str, my_email: str, cutoff: datetime, sender: str,
) -> CalendarPartResult | None:
    invite = itip.parse_invite(payload, my_email=my_email)
    event = invite.event
    existing = calendar_store.get_event(path, event.uid)
    organizer = (existing.organizer_email if existing is not None and existing.organizer_email else event.organizer_email)
    if method == "REQUEST":
        if event.dtend < cutoff and event.recurrence_rule is None:
            return None  # давно прошедшая встреча
        if existing is not None and not _same_address(sender, organizer) and not _same_address(sender, my_email):
            # Уже известную встречу меняет только организатор; переслать
            # приглашение коллеге можно, но изменить им нашу встречу — нет.
            return CalendarPartResult(method=method, event=existing, invite=invite, rejected_sender=sender or "?")
        stored = calendar_store.apply_invite(path, "REQUEST", event)
        return CalendarPartResult(method=method, event=stored, invite=invite)
    if method == "CANCEL":
        if not _same_address(sender, organizer) and not _same_address(sender, my_email):
            return CalendarPartResult(method=method, event=existing or event, invite=invite, rejected_sender=sender or "?")
        stored = calendar_store.apply_invite(path, "CANCEL", event)
        return CalendarPartResult(method=method, event=stored, invite=invite)
    if not invite.replying_attendee_email:
        return None
    if not _same_address(sender, invite.replying_attendee_email):
        return CalendarPartResult(
            method=method, event=existing or event, invite=invite,
            replying_attendee=invite.replying_attendee_email, rejected_sender=sender or "?",
        )
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
        if calendar_store.get_event(path, event.uid) is not None:
            # Файл встречи без METHOD ничего не говорит о том, кто вправе её
            # менять, — уже известную встречу им не перезаписываем.
            continue
        calendar_store.save_event(path, event)
        imported.append(event)
    if not imported:
        return None
    return CalendarPartResult(method="PUBLISH", event=imported[0], imported=imported)
