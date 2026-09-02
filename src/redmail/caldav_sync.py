from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime

import caldav
from caldav.lib.error import AuthorizationError, NotFoundError

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


def probe_auth_schemes(url: str) -> list[str]:
    """Делает запрос без учётных данных к CalDAV-серверу и смотрит, какие
    схемы аутентификации он предлагает в ответе 401 (заголовок
    WWW-Authenticate) — чтобы понять, есть ли там Kerberos/SPNEGO
    (Negotiate), а не гадать вслепую по одному лишь "Unauthorized".
    Реальный повод: тонкий веб-клиент (аналог OWA) у той же почты
    заходит через SSO, значит Kerberos-инфраструктура в организации
    есть — но CalDAV в этом приложении сейчас умеет только логин и
    пароль (Basic), и неясно, предлагает ли сам CalDAV-сервер вообще
    Negotiate, пока не проверишь напрямую."""
    req = urllib.request.Request(url, method="PROPFIND", headers={"Depth": "0"})
    try:
        urllib.request.urlopen(req, timeout=10)
        return []  # ответил без 401 вовсе — не тот случай, для которого зовут эту функцию
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            return []
        values = exc.headers.get_all("WWW-Authenticate") or []
        return [v.split(None, 1)[0] for v in values if v.strip()]
    except urllib.error.URLError:
        return []  # диагностика best-effort — не должна маскировать исходную ошибку вызывающего кода


def _auth_scheme_hint(url: str) -> str:
    schemes = probe_auth_schemes(url)
    if not schemes:
        return ""
    if any(s.lower() == "negotiate" for s in schemes):
        return (
            f" Сервер предлагает: {', '.join(schemes)} — включая Negotiate (Kerberos/SPNEGO), "
            "но CalDAV в этом приложении пока умеет только обычный логин и пароль, не SSO."
        )
    return f" Сервер предлагает только: {', '.join(schemes)} — SSO (Negotiate) для CalDAV здесь недоступен."


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
                # Без явного таймаута зависший/недоступный сервер (закрытая
                # корпоративная сеть, где угодно может быть неверно
                # настроенный прокси/файрвол) мог держать HTTP-запрос
                # сколько угодно — а весь on_caldav_sync() до сих пор шёл
                # синхронно в основном потоке интерфейса: жалоба "после
                # настройки CalDAV сломалась отправка, просмотр, переход
                # между папками и получение почты" — на деле не сломалась,
                # а всё это время буквально ждала одного зависшего сетевого
                # запроса. См. MainWindow.on_caldav_sync — сама синхронизация
                # теперь выполняется в фоновом потоке, но таймаут всё равно
                # нужен: без него поток просто завис бы бесконечно вместо
                # того, чтобы сообщить об ошибке.
                account.url, username=account.username, password=account.password, timeout=30
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
        except AuthorizationError as exc:
            raise CalDavSyncError(f"Не удалось получить список календарей: {exc}.{_auth_scheme_hint(self.account.url)}") from exc
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
