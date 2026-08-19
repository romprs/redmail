from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.message import Message

import icalendar

from redmail.calendar_store import Attendee, Event

# iTIP (RFC 5546) поверх обычной почты — единственный способ приглашений/
# ответов/переносов, который одинаково работает через Exchange, VK Mail и
# любой другой сервер по IMAP/SMTP: сам .ics-формат встраивания в письмо
# (text/calendar, METHOD:REQUEST/REPLY/CANCEL) — общий стандарт, которому
# следует и Outlook, а не что-то специфичное для одного сервера.

_PARTSTAT_TO_LOCAL = {
    "ACCEPTED": "accepted",
    "DECLINED": "declined",
    "TENTATIVE": "tentative",
    "NEEDS-ACTION": "needs-action",
}
_PARTSTAT_TO_ICAL = {v: k for k, v in _PARTSTAT_TO_LOCAL.items()}


@dataclass
class IncomingInvite:
    method: str  # "REQUEST" | "REPLY" | "CANCEL" | ...
    event: Event
    replying_attendee_email: str = ""  # заполнено только для method == "REPLY"


class NotAnInviteError(Exception):
    """В письме нет разбираемого text/calendar с VEVENT."""


def find_calendar_part(message: Message) -> bytes | None:
    """Ищет вложенный .ics (text/calendar — так его встраивает любой iTIP-
    совместимый отправитель: Outlook/Exchange, VK Mail, сам redmail) в уже
    разобранном письме."""
    if message.get_content_type() == "text/calendar":
        return message.get_payload(decode=True)
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/calendar":
                return part.get_payload(decode=True)
    return None


def parse_invite(ics_bytes: bytes, my_email: str) -> IncomingInvite:
    cal = icalendar.Calendar.from_ical(ics_bytes)
    method = str(cal.get("method", "REQUEST")).upper()

    vevent = None
    for component in cal.walk():
        if component.name == "VEVENT":
            vevent = component
            break
    if vevent is None:
        raise NotAnInviteError("В .ics нет VEVENT")

    dtstart_value = vevent["DTSTART"].dt
    all_day = not isinstance(dtstart_value, datetime) and isinstance(dtstart_value, date)
    dtstart = _to_utc(dtstart_value)
    dtend_prop = vevent.get("DTEND")
    dtend = _to_utc(dtend_prop.dt) if dtend_prop is not None else dtstart

    organizer_prop = vevent.get("ORGANIZER")
    organizer_email = _address_email(organizer_prop) if organizer_prop else ""
    organizer_name = _address_name(organizer_prop) if organizer_prop else ""

    attendees: list[Attendee] = []
    my_participation = "needs-action"
    for prop in _as_list(vevent.get("ATTENDEE")):
        email_addr = _address_email(prop)
        partstat = _PARTSTAT_TO_LOCAL.get(str(prop.params.get("PARTSTAT", "NEEDS-ACTION")).upper(), "needs-action")
        attendees.append(Attendee(email=email_addr, name=_address_name(prop), participation=partstat))
        if my_email and email_addr.lower() == my_email.lower():
            my_participation = partstat

    replying_attendee_email = attendees[0].email if method == "REPLY" and attendees else ""

    rrule_prop = vevent.get("RRULE")
    recurrence_rule = rrule_prop.to_ical().decode("ascii") if rrule_prop is not None else None

    event = Event(
        uid=str(vevent.get("UID", "")),
        sequence=int(vevent.get("SEQUENCE", 0)),
        summary=str(vevent.get("SUMMARY", "")),
        description=str(vevent.get("DESCRIPTION", "")),
        location=str(vevent.get("LOCATION", "")),
        dtstart=dtstart,
        dtend=dtend,
        all_day=all_day,
        recurrence_rule=recurrence_rule,
        organizer_email=organizer_email,
        organizer_name=organizer_name,
        is_organizer=bool(my_email) and organizer_email.lower() == my_email.lower(),
        status="cancelled" if method == "CANCEL" else "confirmed",
        my_participation=my_participation,
        attendees=attendees,
        raw_ics=ics_bytes,
    )
    return IncomingInvite(method=method, event=event, replying_attendee_email=replying_attendee_email)


def build_request_ics(event: Event, organizer_email: str, organizer_name: str) -> bytes:
    return _build(
        "REQUEST", event, organizer_email=organizer_email, organizer_name=organizer_name, status="CONFIRMED"
    )


def build_cancel_ics(event: Event, organizer_email: str, organizer_name: str) -> bytes:
    return _build(
        "CANCEL", event, organizer_email=organizer_email, organizer_name=organizer_name, status="CANCELLED"
    )


def build_reply_ics(event: Event, attendee_email: str, attendee_name: str, participation: str) -> bytes:
    cal = _new_calendar("REPLY")
    vevent = icalendar.Event()
    vevent.add("uid", event.uid)
    vevent.add("summary", event.summary)
    vevent.add("sequence", event.sequence)
    vevent.add("dtstamp", datetime.now(timezone.utc))
    vevent.add("dtstart", event.dtstart.date() if event.all_day else event.dtstart)

    organizer = icalendar.vCalAddress(f"mailto:{event.organizer_email}")
    if event.organizer_name:
        organizer.params["CN"] = event.organizer_name
    vevent.add("organizer", organizer, encode=0)

    attendee = icalendar.vCalAddress(f"mailto:{attendee_email}")
    attendee.params["CN"] = attendee_name or attendee_email
    attendee.params["PARTSTAT"] = _PARTSTAT_TO_ICAL.get(participation, "NEEDS-ACTION")
    vevent.add("attendee", attendee, encode=0)

    cal.add_component(vevent)
    return cal.to_ical()


def _build(method: str, event: Event, *, organizer_email: str, organizer_name: str, status: str) -> bytes:
    cal = _new_calendar(method)
    vevent = icalendar.Event()
    vevent.add("uid", event.uid)
    vevent.add("summary", event.summary)
    if event.description:
        vevent.add("description", event.description)
    if event.location:
        vevent.add("location", event.location)
    vevent.add("sequence", event.sequence)
    vevent.add("status", status)
    vevent.add("dtstamp", datetime.now(timezone.utc))
    if event.all_day:
        vevent.add("dtstart", event.dtstart.date())
        vevent.add("dtend", event.dtend.date())
    else:
        vevent.add("dtstart", event.dtstart)
        vevent.add("dtend", event.dtend)
    if event.recurrence_rule:
        vevent.add("rrule", icalendar.vRecur.from_ical(event.recurrence_rule))

    organizer = icalendar.vCalAddress(f"mailto:{organizer_email}")
    organizer.params["CN"] = organizer_name or organizer_email
    vevent.add("organizer", organizer, encode=0)

    for attendee in event.attendees:
        addr = icalendar.vCalAddress(f"mailto:{attendee.email}")
        addr.params["CN"] = attendee.name or attendee.email
        addr.params["ROLE"] = "REQ-PARTICIPANT"
        addr.params["PARTSTAT"] = _PARTSTAT_TO_ICAL.get(attendee.participation, "NEEDS-ACTION")
        addr.params["RSVP"] = "TRUE" if method == "REQUEST" else "FALSE"
        vevent.add("attendee", addr, encode=0)

    cal.add_component(vevent)
    return cal.to_ical()


def _new_calendar(method: str) -> icalendar.Calendar:
    cal = icalendar.Calendar()
    cal.add("prodid", "-//RedMail//RU")
    cal.add("version", "2.0")
    cal.add("method", method)
    return cal


def _as_list(prop) -> list:
    if prop is None:
        return []
    return prop if isinstance(prop, list) else [prop]


def _address_email(prop) -> str:
    value = str(prop)
    return value[7:] if value.lower().startswith("mailto:") else value


def _address_name(prop) -> str:
    return str(prop.params.get("CN", "")) if hasattr(prop, "params") else ""


def _to_utc(value) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
