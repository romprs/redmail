"""Синхронизация одного календаря с его сервером.

Общая часть для CalDAV (VK и др.), Exchange по EWS и подписок .ics: что
отправлять, что удалять на сервере, что забирать и что убирать локально.
Сетевую работу делает «сервер» — небольшой адаптер с методами
push_event/delete_event/fetch_events, поэтому логику можно проверить без
настоящих серверов.

Порядок внутри календаря:
1. Удаления, сделанные здесь, уходят на сервер — иначе шаг 3 вернёт
   удалённые встречи обратно.
2. На сервер уходят только встречи, изменённые здесь (отметка needs_push).
   Раньше при каждой синхронизации отправлялись все свои встречи: Exchange
   рассылал участникам обновления, а VK отвечал 412 на встречи, которые с
   тех пор меняли в другом месте.
3. С сервера забираются встречи окна; локальные встречи окна, которых на
   сервере больше нет, убираются (если они не ждут отправки).

Ошибка одной встречи не останавливает календарь, ошибка одного календаря
не останавливает остальные: всё собирается в отчёт.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from redmail import calendar_store
from redmail.applog import get_logger

_log = get_logger("calsync")

#: Признаки ответа «на сервере другая версия» (CalDAV 412, EWS конфликт).
_CONFLICT_HINTS = ("412", "precondition failed", "errorirresolvableconflict", "conflict")


def is_conflict(exc: Exception) -> bool:
    text = str(exc).casefold()
    return any(hint in text for hint in _CONFLICT_HINTS)


@dataclass
class CalendarSyncReport:
    name: str = ""
    pushed: int = 0
    pulled: int = 0
    removed: int = 0
    deleted_on_server: int = 0
    conflicts: int = 0
    errors: list[str] = field(default_factory=list)


def sync_calendar(
    path: Path,
    calendar: calendar_store.Calendar,
    server,
    window_start: datetime,
    window_end: datetime,
    *,
    read_only: bool = False,
) -> CalendarSyncReport:
    report = CalendarSyncReport(name=calendar.name)

    if not read_only:
        for uid in calendar_store.pending_server_deletes(path, calendar.id):
            try:
                if calendar_store.is_instance_uid(uid):
                    cancel = getattr(server, "cancel_occurrence", None)
                    if cancel is not None:
                        cancel(uid)  # отмена одного дня серии
                        calendar_store.forget_server_delete(path, uid)
                        report.deleted_on_server += 1
                    else:
                        calendar_store.forget_server_delete(path, uid)
                    continue
                server.delete_event(uid)
            except Exception as exc:
                report.errors.append(f"удаление встречи: {exc}")
                _log.warning("Календарь «%s»: удаление uid=%s на сервере не удалось: %s", calendar.name, uid, exc)
                continue
            calendar_store.forget_server_delete(path, uid)
            report.deleted_on_server += 1

        for event in calendar_store.events_to_push(path, calendar.id):
            if not event.is_organizer:
                calendar_store.mark_pushed(path, event.uid)  # чужие встречи сервер правит сам
                continue
            try:
                if calendar_store.is_instance_uid(event.uid):
                    push_occurrence = getattr(server, "push_occurrence", None)
                    if push_occurrence is None:
                        calendar_store.mark_pushed(path, event.uid)
                        continue
                    push_occurrence(event)  # перенос одного дня серии
                else:
                    server.push_event(event)
            except Exception as exc:
                if is_conflict(exc):
                    # На сервере встречу уже меняли в другом месте: верной
                    # считаем серверную копию, она придёт на шаге 3.
                    _log.info("Календарь «%s»: uid=%s изменена на сервере, берём серверную копию", calendar.name, event.uid)
                    calendar_store.mark_pushed(path, event.uid)
                    report.conflicts += 1
                    continue
                report.errors.append(f"«{event.summary}»: {exc}")
                _log.warning("Календарь «%s»: отправка uid=%s не удалась: %s", calendar.name, event.uid, exc)
                continue
            calendar_store.mark_pushed(path, event.uid)
            report.pushed += 1

    try:
        server_events = list(server.fetch_events(window_start, window_end))
    except Exception as exc:
        report.errors.append(f"получение встреч: {exc}")
        _log.warning("Календарь «%s»: получение встреч не удалось: %s", calendar.name, exc)
        return report

    waiting_delete = set(calendar_store.pending_server_deletes(path, calendar.id))
    waiting_push = {event.uid for event in calendar_store.events_to_push(path, calendar.id)}
    server_uids: set[str] = set()
    for event in server_events:
        server_uids.add(event.uid)
        if event.uid in waiting_delete:
            continue  # удалена здесь, удаление на сервер ещё не прошло
        existing = calendar_store.get_event(path, event.uid)
        if event.uid in waiting_push:
            continue  # локальная правка не ушла — не затираем её серверной копией
        event = replace(event, calendar_id=calendar.id, origin=calendar_store.ORIGIN_SERVER)
        if existing is not None:
            # Полей, которых нет в iCalendar (ручной цвет) и вложений сервер
            # может не хранить — не даём синхронизации тихо их стереть.
            if not event.attachments and existing.attachments:
                event.attachments = existing.attachments
            if not event.color and existing.color:
                event.color = existing.color
        calendar_store.save_event(path, event)
        report.pulled += 1

    # Серия пришла с сервера раньше, чем туда ушёл перенос одного её дня:
    # этот день в серии не должен появиться второй раз.
    for uid in waiting_push:
        original = calendar_store.instance_start(uid)
        if original is not None:
            calendar_store.add_exdate(path, calendar_store.series_uid(uid), original)

    for uid, waiting in calendar_store.stored_events_in_window(path, calendar.id, window_start, window_end):
        if uid in server_uids or waiting:
            continue
        calendar_store.delete_event(path, uid)
        report.removed += 1

    _log.info(
        "Календарь «%s»: отправлено %d, получено %d, убрано %d, удалено на сервере %d, конфликтов %d, ошибок %d",
        calendar.name, report.pushed, report.pulled, report.removed, report.deleted_on_server,
        report.conflicts, len(report.errors),
    )
    return report
