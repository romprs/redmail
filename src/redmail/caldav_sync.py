from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import caldav
from caldav.lib.error import NotFoundError

from redmail import itip
from redmail.calendar_store import Event

# CalDAV с сервером клиента (VK Mail/Exchange) — сеть закрытая корпоративная,
# у самого redmail нет прямого способа её нащупать заранее, поэтому адрес
# сервера вводится пользователем вручную в настройках (см. SettingsDialog/
# config_store.load_caldav_settings), а не автоопределяется. Логин/пароль —
# те же, что для IMAP/SMTP (общий Account), см. MainWindow.on_caldav_sync.
#
# Двусторонняя синхронизация: события, где мы организатор, отправляются на
# сервер (push); всё, что есть на сервере в окне синхронизации, подтягивается
# в локальный calendar_store (pull) и связывается по UID (тот же UID, что
# использует iTIP-путь через почту — см. itip.py) — событие, полученное
# когда-то по приглашению, и то же событие с CalDAV-сервера не задвоятся.


@dataclass
class CalDavAccount:
    url: str
    username: str
    password: str


class CalDavSyncError(Exception):
    """Ошибка подключения к серверу или синхронизации — показывается
    пользователю как есть, не проглатывается молча (тот же принцип, что и
    у остальных сетевых операций в проекте)."""


class CalDavSession:
    """Одно CalDAV-соединение на сессию синхронизации — тот же принцип, что
    у ImapSession: подключение переиспользуется, а не открывается заново на
    каждую операцию. Синхронизация запускается вручную (кнопка в
    календаре), автоматического фонового опроса нет — сервер ещё ни разу
    не проверялся вживую, включать это по таймеру было бы преждевременно."""

    def __init__(self, account: CalDavAccount):
        self.account = account
        try:
            self._client = caldav.DAVClient(
                account.url, username=account.username, password=account.password
            )
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось создать CalDAV-соединение: {exc}") from exc
        self._calendar = None

    def _primary_calendar(self):
        if self._calendar is None:
            try:
                principal = self._client.principal()
                calendars = principal.calendars()
            except Exception as exc:
                raise CalDavSyncError(f"Не удалось подключиться к CalDAV-серверу: {exc}") from exc
            if not calendars:
                raise CalDavSyncError("На сервере CalDAV не найдено ни одного календаря")
            self._calendar = calendars[0]
        return self._calendar

    def list_calendar_names(self) -> list[str]:
        try:
            principal = self._client.principal()
            return [cal.get_display_name() or str(cal.url) for cal in principal.calendars()]
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось получить список календарей: {exc}") from exc

    def fetch_events(self, start: datetime, end: datetime, my_email: str) -> list[Event]:
        """События сервера в окне [start, end). Разбор VEVENT переиспользует
        itip.parse_ics_events — ту же логику, что уже проверена на реальных
        .ics-вложениях и импорте, вместо второго независимого парсера
        одного и того же формата."""
        calendar = self._primary_calendar()
        try:
            results = calendar.date_search(start, end)
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось получить события с сервера: {exc}") from exc

        events: list[Event] = []
        for obj in results:
            try:
                raw = obj.data
                raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else raw
                events.extend(itip.parse_ics_events(raw_bytes, my_email))
            except Exception:
                continue  # одно повреждённое/непонятное событие не должно валить всю синхронизацию
        return events

    def push_event(self, event: Event, organizer_email: str, organizer_name: str) -> None:
        """Создаёт событие на сервере или обновляет уже существующее
        (находит по UID — тот же UID, что уже используется локально)."""
        calendar = self._primary_calendar()
        ics_text = itip.build_caldav_ics(event, organizer_email, organizer_name).decode("utf-8")
        try:
            existing = calendar.get_event_by_uid(event.uid)
        except NotFoundError:
            existing = None
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось проверить событие на сервере: {exc}") from exc

        try:
            if existing is not None:
                existing.data = ics_text
                existing.save()
            else:
                calendar.save_event(ics_text)
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось сохранить событие на сервере: {exc}") from exc

    def delete_event(self, uid: str) -> None:
        calendar = self._primary_calendar()
        try:
            existing = calendar.get_event_by_uid(uid)
        except NotFoundError:
            return  # уже нет на сервере — нечего удалять, не ошибка
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось найти событие на сервере: {exc}") from exc
        try:
            existing.delete()
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось удалить событие на сервере: {exc}") from exc

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
