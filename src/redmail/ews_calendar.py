"""Календарь Exchange по EWS: чтение и запись встреч.

Зачем: у организации почта и календарь живут в Exchange, и календарь там
отдаётся тем же протоколом EWS, что и почта (CalDAV на сервере может быть
не включён вовсе). Раньше программа умела только локальный календарь,
CalDAV и подписку по ссылке — встречи Exchange не показывались.

Сопоставление полей: CalendarItem (exchangelib) → calendar_store.Event.
Свой UID встречи держим в поле uid (iCalUID) — по нему встреча находится
на сервере при повторной отправке, как и в CalDAV.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from exchangelib import EWSDateTime, EWSTimeZone, Mailbox
from exchangelib.items import CalendarItem
from exchangelib.properties import Attendee as EwsAttendee

from redmail import calendar_store
from redmail.applog import get_logger
from redmail.calendar_store import Attendee, Event

_log = get_logger("ews.calendar")

#: Ответ участника в Exchange → наше состояние участия.
_RESPONSE_MAP = {
    "Accept": "accepted",
    "Decline": "declined",
    "Tentative": "tentative",
    "NoResponseReceived": "needs-action",
    "Organizer": "accepted",
    "Unknown": "needs-action",
}
_PARTICIPATION_TO_RESPONSE = {value: key for key, value in _RESPONSE_MAP.items()}


class EwsCalendarError(Exception):
    """Ошибка работы с календарём Exchange, показываемая пользователю."""


def _to_utc(value) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        # EWSDateTime.astimezone() принимает только EWSTimeZone и на обычном
        # timezone.utc бросает InvalidTypeError — из-за этого пропускались ВСЕ
        # встречи Exchange («встреча пропущена: 'tzinfo' … must be of type
        # EWSTimeZone», 7354 записи в журнале за день). Сначала переводим в
        # обычный datetime, у него astimezone работает как обычно.
        plain = datetime(
            value.year, value.month, value.day, value.hour, value.minute, value.second,
            value.microsecond, tzinfo=value.tzinfo or timezone.utc,
        )
        return plain.astimezone(timezone.utc)
    # EWSDate (весь день) — начало суток
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def _ews_datetime(value: datetime) -> EWSDateTime:
    return EWSDateTime.from_datetime(_to_utc(value)).astimezone(EWSTimeZone("UTC"))


def _mailbox_email(mailbox) -> str:
    return (getattr(mailbox, "email_address", "") or "").strip()


def _attendees(item: CalendarItem) -> list[Attendee]:
    result: list[Attendee] = []
    for group in (item.required_attendees or [], item.optional_attendees or []):
        for attendee in group:
            mailbox = getattr(attendee, "mailbox", None)
            email = _mailbox_email(mailbox)
            if not email:
                continue
            response = getattr(attendee, "response_type", "") or "Unknown"
            result.append(
                Attendee(
                    email=email,
                    name=(getattr(mailbox, "name", "") or "").strip(),
                    participation=_RESPONSE_MAP.get(response, "needs-action"),
                )
            )
    return result


def item_to_event(item: CalendarItem, my_email: str) -> Event:
    """Встреча Exchange → наша Event. UID берём из iCalUID, чтобы одна и та
    же встреча не задваивалась при повторной синхронизации."""
    organizer = getattr(item, "organizer", None)
    organizer_email = _mailbox_email(organizer)
    my_participation = _RESPONSE_MAP.get(getattr(item, "my_response_type", "") or "Unknown", "needs-action")
    is_organizer = bool(organizer_email) and organizer_email.casefold() == (my_email or "").casefold()
    uid = str(item.uid or item.id or "")
    if getattr(item, "type", "") in ("Occurrence", "Exception"):
        # Просмотр календаря отдаёт каждый экземпляр серии с UID всей серии —
        # у каждого свой ключ, иначе от серии остаётся один день.
        uid = calendar_store.instance_uid(uid, _to_utc(getattr(item, "original_start", None) or item.start))
    return Event(
        uid=uid,
        summary=item.subject or "(без темы)",
        dtstart=_to_utc(item.start),
        dtend=_to_utc(item.end),
        description=str(getattr(item, "text_body", "") or getattr(item, "body", "") or ""),
        location=item.location or "",
        all_day=bool(item.is_all_day),
        organizer_email=organizer_email,
        organizer_name=(getattr(organizer, "name", "") or "").strip(),
        is_organizer=is_organizer,
        status="cancelled" if getattr(item, "is_cancelled", False) else "confirmed",
        my_participation="accepted" if is_organizer else my_participation,
        attendees=_attendees(item),
    )


EWS_VIEW_CHUNK = timedelta(days=14)


def _account_for(session, mailbox: str = ""):
    """Свой ящик или ящик коллеги (подписка): открывается нашей учётной
    записью, пароль владельца не нужен."""
    if mailbox:
        chooser = getattr(session, "mailbox_account", None)
        if chooser is not None:
            return chooser(mailbox)
    return getattr(session, "_account", None) or session


def fetch_events(session, start: datetime, end: datetime, my_email: str, mailbox: str = "") -> list[Event]:
    """Встречи из календаря учётной записи (или ящика коллеги) в окне [start, end)."""
    account = _account_for(session, mailbox)
    try:
        # Exchange отдаёт в одном просмотре не больше ~1000 встреч («You have
        # exceeded the maximum number of objects…»), а ежедневные серии за
        # полгода это легко превышают. Поэтому окно запрашивается частями.
        events: dict[str, Event] = {}
        chunk_start = start
        failed = 0
        last_error: Exception | None = None
        while chunk_start < end:
            chunk_end = min(chunk_start + EWS_VIEW_CHUNK, end)
            try:
                items = list(account.calendar.view(start=_ews_datetime(chunk_start), end=_ews_datetime(chunk_end)))
            except Exception as exc:
                # Сервер не ответил на эту часть окна («The request timed out»)
                # — берём остальные, а не теряем весь календарь.
                failed += 1
                last_error = exc
                _log.warning("EWS календарь: часть окна %s — %s не получена: %s", chunk_start.date(), chunk_end.date(), exc)
                chunk_start = chunk_end
                continue
            for item in items:
                if not isinstance(item, CalendarItem):
                    continue
                try:
                    event = item_to_event(item, my_email)
                except Exception as exc:  # одна встреча не должна ломать всю синхронизацию
                    _log.warning("EWS календарь: встреча пропущена (%s)", exc)
                    continue
                events[event.uid] = event  # встреча на стыке частей приходит дважды
            chunk_start = chunk_end
        if failed and not events:
            raise EwsCalendarError(f"Не удалось получить встречи Exchange: {last_error}")
        _log.info(
            "EWS календарь: получено встреч %d (окно %s — %s%s)",
            len(events), start.date(), end.date(), f", частей без ответа {failed}" if failed else "",
        )
        return list(events.values())
    except Exception as exc:
        _log.error("EWS календарь: получение встреч не удалось: %s", exc)
        raise EwsCalendarError(f"Не удалось получить встречи Exchange: {exc}") from exc


def _find_item(account, uid: str):
    if not uid:
        return None
    try:
        return account.calendar.filter(uid=uid).only("id", "changekey", "uid").first()
    except Exception:
        return None


def push_event(session, event: Event, mailbox: str = "") -> None:
    """Создать встречу на сервере или обновить уже существующую (по UID)."""
    account = _account_for(session, mailbox)
    try:
        existing = _find_item(account, event.uid)
        required = [
            EwsAttendee(mailbox=Mailbox(email_address=a.email, name=a.name or None), response_type="Unknown")
            for a in event.attendees if a.email
        ]
        if existing is not None:
            item = account.calendar.get(id=existing.id)
            item.subject = event.summary
            item.start = _ews_datetime(event.dtstart)
            item.end = _ews_datetime(event.dtend)
            item.location = event.location or None
            item.body = event.description or None
            if required:
                item.required_attendees = required
            item.save(send_meeting_invitations="SendToAllAndSaveCopy" if required else "SendToNone")
            _log.info("EWS календарь: встреча обновлена uid=%s", event.uid)
            return
        item = CalendarItem(
            account=account,
            folder=account.calendar,
            subject=event.summary,
            start=_ews_datetime(event.dtstart),
            end=_ews_datetime(event.dtend),
            location=event.location or None,
            body=event.description or None,
            required_attendees=required or None,
            uid=event.uid or None,
        )
        item.save(send_meeting_invitations="SendToAllAndSaveCopy" if required else "SendToNone")
        _log.info("EWS календарь: встреча создана uid=%s", event.uid)
    except Exception as exc:
        _log.error("EWS календарь: сохранение встречи uid=%s не удалось: %s", event.uid, exc)
        raise EwsCalendarError(f"Не удалось сохранить встречу в Exchange: {exc}") from exc


def delete_event(session, uid: str, mailbox: str = "") -> None:
    account = _account_for(session, mailbox)
    try:
        existing = _find_item(account, uid)
        if existing is None:
            return
        account.calendar.get(id=existing.id).delete(send_meeting_cancellations="SendToAllAndSaveCopy")
        _log.info("EWS календарь: встреча удалена uid=%s", uid)
    except Exception as exc:
        _log.error("EWS календарь: удаление встречи uid=%s не удалось: %s", uid, exc)
        raise EwsCalendarError(f"Не удалось удалить встречу в Exchange: {exc}") from exc


def _find_occurrence(account, uid: str):
    """Экземпляр серии Exchange по UID серии и исходному началу дня."""
    original = calendar_store.instance_start(uid)
    series = calendar_store.series_uid(uid)
    if original is None:
        return None
    window = timedelta(days=1)
    for item in account.calendar.view(start=_ews_datetime(original - window), end=_ews_datetime(original + window)):
        if not isinstance(item, CalendarItem) or str(getattr(item, "uid", "") or "") != series:
            continue
        if _to_utc(getattr(item, "original_start", None) or item.start) == original:
            return item
    return None


def push_occurrence(session, event: Event, mailbox: str = "") -> None:
    """Перенос или правка одного дня серии; участникам Exchange сообщит сам."""
    account = _account_for(session, mailbox)
    try:
        item = _find_occurrence(account, event.uid)
        if item is None:
            raise EwsCalendarError("день серии не найден на сервере")
        item.subject = event.summary
        item.start = _ews_datetime(event.dtstart)
        item.end = _ews_datetime(event.dtend)
        item.location = event.location or None
        item.save(send_meeting_invitations="SendToAllAndSaveCopy" if event.attendees else "SendToNone")
        _log.info("EWS календарь: день серии изменён uid=%s", event.uid)
    except EwsCalendarError:
        raise
    except Exception as exc:
        _log.error("EWS календарь: изменение дня серии uid=%s не удалось: %s", event.uid, exc)
        raise EwsCalendarError(f"Не удалось изменить день серии в Exchange: {exc}") from exc


def cancel_occurrence(session, uid: str, mailbox: str = "") -> None:
    account = _account_for(session, mailbox)
    try:
        item = _find_occurrence(account, uid)
        if item is None:
            return  # уже отменён
        item.delete(send_meeting_cancellations="SendToAllAndSaveCopy")
        _log.info("EWS календарь: день серии отменён uid=%s", uid)
    except Exception as exc:
        _log.error("EWS календарь: отмена дня серии uid=%s не удалось: %s", uid, exc)
        raise EwsCalendarError(f"Не удалось отменить день серии в Exchange: {exc}") from exc
