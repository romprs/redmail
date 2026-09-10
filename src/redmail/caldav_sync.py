from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlparse
from uuid import uuid4

import caldav
import requests
from caldav.lib.error import AuthorizationError, NotFoundError

from redmail import itip
from redmail.calendar_store import Event

# Некоторые корпоративные прокси/WAF перед CalDAV-сервером отличают
# автоматизированные HTTP-библиотеки от обычных десктопных клиентов именно
# по User-Agent и применяют к ним более строгую политику — жалоба
# "из Evolution и через веб событие создаётся нормально, а из этого клиента
# нет, хотя проверка подключения (чтение) проходит" указывает ровно на такую
# избирательную фильтрацию по заголовку, а не на блокировку метода PUT как
# такового (раз PUT работает у других клиентов на этом же сервере). Библиотека
# caldav по умолчанию подставляет "python-caldav/<версия>" — максимально
# узнаваемую сигнатуру скриптовой библиотеки; заменяем её на нейтральную,
# не выдающую, что это python-скрипт, но и не выдающую себя за чужой продукт.
_CALDAV_USER_AGENT = "redmail-caldav-client/1.0"


def _with_connection_retry(func, *args, **kwargs):
    """Один повтор САМОГО сетевого вызова при ConnectionError ("Remote end
    closed connection without response" и т.п.) — жалоба: "не
    синхронизируется календарь, при этом проверка подключения проходит".
    Проверка подключения — это единственный лёгкий PROPFIND, а сама
    синхронизация — несколько последовательных запросов (push каждой
    встречи, потом fetch) через пул соединений requests/caldav; сервер
    (или прокси/балансировщик перед ним в закрытой корпоративной сети)
    вполне может закрыть простаивающее keep-alive-соединение между
    запросами, не ответив на очередной — классический случай устаревшего
    соединения, который в подавляющем большинстве случаев лечится одним
    повтором на свежем соединении, а не признак настоящей неполадки с
    сервером или данными. Обёрнуто вокруг КОНКРЕТНОГО вызова caldav/requests,
    а не всего метода целиком — иначе к моменту, когда метод сам ловит
    Exception и заворачивает его в CalDavSyncError, исходный
    requests.exceptions.ConnectionError уже не различить."""
    try:
        return func(*args, **kwargs)
    except requests.exceptions.ConnectionError:
        return func(*args, **kwargs)

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
    # "password" — Basic (логин + app-пароль); "kerberos" — SSO по доменному
    # билету через SPNEGO/Negotiate, как у IMAP/SMTP (Account.auth_type).
    # Уточнение от пользователя: веб-приложение VK авторизует через keytab,
    # то есть HTTP-фронт (тот же, что отдаёт CalDAV) принимает Negotiate —
    # тогда app-пароль (который VK для сторонних клиентов требует и порой
    # капризно: "Application password is REQUIRED") не нужен вовсе.
    auth_type: str = "password"
    # См. imap_client.Account.keytab_path/principal — тот же необязательный
    # keytab как источник билета для SSO.
    keytab_path: str = ""
    principal: str = ""


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
            f" Сервер предлагает: {', '.join(schemes)} — включая Negotiate (Kerberos/SPNEGO): "
            "переключите способ входа учётной записи на Kerberos (SSO) — CalDAV тогда пойдёт "
            "по доменному билету, без app-пароля."
        )
    return f" Сервер предлагает только: {', '.join(schemes)} — SSO (Negotiate) на этом сервере недоступен."


@dataclass
class CalDavCalendarInfo:
    """Один календарь, обнаруженный на сервере при обходе calendar-home-set —
    задача "настроить получение расшаренных календарей для VK Mail". У VK
    (и вообще по CalDAV, см. переписку с пользователем) нет делегирования
    всего аккаунта (calendar-proxy, как у Apple/Nextcloud) — есть только
    пошаренные по отдельности календари, и после того как коллега
    расшарил календарь и приглашение принято В ВЕБ-ИНТЕРФЕЙСЕ VK (accept-
    invite по CalDAV не бывает), сервер сам кладёт этот календарь в НАШ
    calendar-home-set как обычную коллекцию — единственное, что остаётся
    сделать здесь, это её ОБНАРУЖИТЬ и показать, чей это календарь."""

    url: str
    name: str
    owner: str | None
    is_shared: bool
    read_only: bool


_CALDAV_CALENDAR_TAG = "{urn:ietf:params:xml:ns:caldav}calendar"
_CALDAV_HOME_SET_PROP = "{urn:ietf:params:xml:ns:caldav}calendar-home-set"
_CALENDAR_DISCOVERY_PROPS = [
    "{DAV:}resourcetype",
    "{DAV:}displayname",
    "{DAV:}owner",
    "{DAV:}current-user-privilege-set",
]


def _href_path(href: str) -> str:
    """Путь без схемы/хоста и завершающего слэша — owner может прийти и
    абсолютным URL, и просто путём, сравнивать нужно только путь."""
    return unquote(urlparse(href).path).rstrip("/")


def _has_write_privilege(value: object) -> bool:
    """current-user-privilege-set не разбирается caldav-библиотекой в
    удобный список (вложенные <privilege><write/></privilege> не подходят
    под её общий разбор свойств) — приходит как есть, "сырым" XML-
    элементом, ищем write/all прямо в нём. Свойство отсутствует вовсе
    (None) — не считаем календарь урезанным без причины: сервер и так
    отклонит PUT 403-м, если прав на самом деле нет, а свойство мог просто
    не отдать (см. переписку: cs:invite/cs:shared-url у VK могут прийти
    404 — не факт, что current-user-privilege-set при этом тоже не будет)."""
    if value is None:
        return True
    if not hasattr(value, "iter"):
        return False
    for child in value.iter():
        local = child.tag.rsplit("}", 1)[-1] if isinstance(child.tag, str) else ""
        if local in ("write", "all", "write-content"):
            return True
    return False


class CalDavSession:
    """Одно CalDAV-соединение на сессию синхронизации — тот же принцип, что
    у ImapSession: подключение переиспользуется, а не открывается заново на
    каждую операцию. Синхронизация запускается вручную (кнопка в
    календаре), автоматического фонового опроса нет — сервер ещё ни разу
    не проверялся вживую, включать это по таймеру было бы преждевременно."""

    def __init__(self, account: CalDavAccount):
        self.account = account
        # Без явного таймаута зависший/недоступный сервер (закрытая
        # корпоративная сеть, где угодно может быть неверно настроенный
        # прокси/файрвол) мог держать HTTP-запрос сколько угодно — а весь
        # on_caldav_sync() до сих пор шёл синхронно в основном потоке
        # интерфейса: жалоба "после настройки CalDAV сломалась отправка,
        # просмотр, переход между папками и получение почты" — на деле не
        # сломалась, а всё это время буквально ждала одного зависшего
        # сетевого запроса. См. MainWindow.on_caldav_sync — сама
        # синхронизация теперь выполняется в фоновом потоке, но таймаут
        # всё равно нужен: без него поток просто завис бы бесконечно вместо
        # того, чтобы сообщить об ошибке.
        client_kwargs: dict = {"timeout": 30, "headers": {"User-Agent": _CALDAV_USER_AGENT}}
        try:
            if account.auth_type == "kerberos":
                # Импорт внутри — см. gssapi_sasl.py: requests_gssapi тянет
                # системные библиотеки Kerberos, которых нет там, где SSO не
                # используется (и на машине для тестов); обычный пароль от
                # этого ломаться не должен.
                import requests_gssapi

                from redmail import gssapi_sasl

                # mutual_authentication=OPTIONAL, а не REQUIRED по умолчанию:
                # за корпоративным reverse-proxy/балансировщиком ответ сервера
                # часто приходит без встречного GSSAPI-токена, и строгая
                # взаимная проверка роняла бы уже успешно прошедший вход.
                # creds=None — билет из системного кэша; с keytab — из него.
                client_kwargs["auth"] = requests_gssapi.HTTPSPNEGOAuth(
                    mutual_authentication=requests_gssapi.OPTIONAL,
                    creds=gssapi_sasl.acquire_credentials(account.keytab_path, account.principal),
                )
            else:
                client_kwargs["username"] = account.username
                client_kwargs["password"] = account.password
            self._client = caldav.DAVClient(account.url, **client_kwargs)
            # Всегда обычный requests.Session вместо того, что выбрал caldav:
            # при наличии niquests он берёт его, а niquests по умолчанию
            # договаривается об HTTP/2 через ALPN. Жалоба (и после смены
            # User-Agent, и после прямого URL календаря): чтение работает,
            # а PUT события рвётся "Remote end closed connection without
            # response" — при этом Evolution (libsoup, HTTP/1.1) на том же
            # сервере события создаёт. Обрыв запроса с телом без единого
            # байта ответа при рабочих запросах без тела — характерный
            # симптом прокси/балансировщика с неполной поддержкой HTTP/2
            # для методов с телом; обычный requests ходит по HTTP/1.1, как
            # Evolution. Заодно с ним документирована и отлажена связка с
            # HTTPSPNEGOAuth (хук на 401 через r.connection/r.request.copy()).
            try:
                self._client.session.close()
            except Exception:
                pass
            self._client.session = requests.Session()
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось создать CalDAV-соединение: {exc}") from exc
        self._calendar = None

    def _primary_calendar(self):
        if self._calendar is None:
            # account.url — это URL КОНКРЕТНОГО календаря (см.
            # list_calendars_detailed для обнаружения таких URL, включая
            # расшаренные другими пользователями), обращаемся к нему
            # напрямую. Раньше здесь было principal.calendars()[0] —
            # дискавери первого попавшегося календаря аккаунта НЕЗАВИСИМО
            # от того, какой именно caldav_url был настроен: из-за этого
            # ЛЮБОЙ второй настроенный CalDAV-календарь того же аккаунта
            # молча синхронизировался бы с тем же самым первым календарём,
            # что и делало поддержку нескольких/расшаренных календарей
            # бессмысленной — без этого исправления задача "получение
            # расшаренных календарей для VK Mail" попросту не работала бы.
            self._calendar = caldav.Calendar(client=self._client, url=self.account.url)
        return self._calendar

    def list_calendar_names(self) -> list[str]:
        try:
            principal = self._client.principal()
            return [cal.get_display_name() or str(cal.url) for cal in _with_connection_retry(principal.calendars)]
        except AuthorizationError as exc:
            raise CalDavSyncError(f"Не удалось получить список календарей: {exc}.{_auth_scheme_hint(self.account.url)}") from exc
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось получить список календарей: {exc}") from exc

    def list_calendars_detailed(self) -> list[CalDavCalendarInfo]:
        """Все календари, включая расшаренные нам коллегами (не только
        собственные) — owner коллекции указывает на ЧУЖОЙ принципал, если
        это так. См. CalDavCalendarInfo — задача "настроить получение
        расшаренных календарей для VK Mail".

        Жалоба после первой версии: "поиск находит основной, но не находит
        расшаренный другого пользователя", при том что Evolution на том же
        сервере его видит. Два отличия от простого PROPFIND Depth:1 по
        одному calendar-home-set, которые Evolution (e-webdav-discover)
        делает, а первая версия — нет: (1) calendar-home-set у принципала
        может содержать НЕСКОЛЬКО href (свои календари в одном доме, чужие
        расшаренные — в другом), caldav-библиотека берёт только первый;
        (2) внутри дома могут лежать обычные под-коллекции (например,
        shared/), в которых уже лежат календари — Depth:1 их не видит,
        Evolution в такие коллекции спускается."""
        try:
            principal = self._client.principal()
            home_urls = self._calendar_home_urls(principal)
        except AuthorizationError as exc:
            raise CalDavSyncError(f"Не удалось получить список календарей: {exc}.{_auth_scheme_hint(self.account.url)}") from exc
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось получить список календарей: {exc}") from exc

        my_principal_path = _href_path(str(principal.url))
        infos: list[CalDavCalendarInfo] = []
        seen: set[str] = set()
        for home_url in home_urls:
            self._collect_calendars(home_url, my_principal_path, infos, seen, depth_left=2)
        return infos

    def _calendar_home_urls(self, principal) -> list[str]:
        """Все href из calendar-home-set принципала (не только первый)."""
        urls: list[str] = []
        try:
            response = _with_connection_retry(
                self._client.propfind, str(principal.url), [_CALDAV_HOME_SET_PROP], 0
            )
            for result in response.results:
                value = result.properties.get(_CALDAV_HOME_SET_PROP)
                hrefs = value if isinstance(value, list) else [value]
                for href in hrefs:
                    if isinstance(href, str) and href:
                        urls.append(str(self._client.url.join(href)))
        except Exception:
            pass  # ниже — обычный путь библиотеки, как раньше
        if not urls:
            urls.append(str(principal.calendar_home_set.url))
        return urls

    def _collect_calendars(
        self, url: str, my_principal_path: str, infos: list[CalDavCalendarInfo], seen: set[str], depth_left: int
    ) -> None:
        try:
            response = _with_connection_retry(self._client.propfind, url, _CALENDAR_DISCOVERY_PROPS, 1)
        except AuthorizationError as exc:
            raise CalDavSyncError(f"Не удалось получить список календарей: {exc}.{_auth_scheme_hint(self.account.url)}") from exc
        except Exception as exc:
            if depth_left == 2:
                raise CalDavSyncError(f"Не удалось получить список календарей: {exc}") from exc
            return  # вложенная коллекция не отвечает — не роняем весь список
        own_path = _href_path(url)
        for result in response.results:
            if result.status not in (200, 207):
                continue
            resourcetype = result.properties.get("{DAV:}resourcetype") or []
            tags = [str(t) for t in (resourcetype if isinstance(resourcetype, list) else [resourcetype])]
            full_url = str(self._client.url.join(result.href))
            path = _href_path(full_url)
            if path == own_path:
                continue  # сама обходимая коллекция
            # Точное имя тега, не подстрока "calendar": у расшаренных
            # коллекций в resourcetype бывает {http://calendarserver.org/ns/}shared
            # — "calendarserver" содержит "calendar", ложное срабатывание.
            is_calendar = _CALDAV_CALENDAR_TAG in tags
            is_collection = any(t.endswith("}collection") for t in tags)
            if is_calendar:
                if path in seen:
                    continue
                seen.add(path)
                owner_raw = result.properties.get("{DAV:}owner")
                owner = owner_raw if isinstance(owner_raw, str) and owner_raw else None
                infos.append(CalDavCalendarInfo(
                    url=full_url,
                    name=result.properties.get("{DAV:}displayname") or result.href,
                    owner=owner,
                    is_shared=owner is not None and _href_path(owner) != my_principal_path,
                    read_only=not _has_write_privilege(result.properties.get("{DAV:}current-user-privilege-set")),
                ))
            elif is_collection and depth_left > 1:
                self._collect_calendars(full_url, my_principal_path, infos, seen, depth_left - 1)

    def fetch_events(self, start: datetime, end: datetime, my_email: str) -> list[Event]:
        """События сервера в окне [start, end). Разбор VEVENT переиспользует
        itip.parse_ics_events — ту же логику, что уже проверена на реальных
        .ics-вложениях и импорте, вместо второго независимого парсера
        одного и того же формата."""
        calendar = self._primary_calendar()
        try:
            results = _with_connection_retry(calendar.date_search, start, end)
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
            existing = _with_connection_retry(calendar.get_event_by_uid, event.uid)
        except NotFoundError:
            existing = None
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось проверить событие на сервере: {exc}") from exc

        try:
            if existing is not None:
                existing.data = ics_text
                _with_connection_retry(existing.save)
            else:
                _with_connection_retry(calendar.save_event, ics_text)
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось сохранить событие на сервере: {exc}") from exc

    def test_write_access(self) -> None:
        """Настоящая проверка записи: создаёт одноразовое тестовое событие на
        сервере и сразу удаляет его. В отличие от одной лишь проверки чтения
        (PROPFIND/список календарей), это ловит именно ту ситуацию, из-за
        которой возникла путаница — "проверка подключения проходит, а
        синхронизация нет": проверка подключения раньше проверяла только
        чтение, поэтому расхождение между чтением и записью (например,
        избирательная фильтрация PUT на стороне сети по каким-то признакам
        запроса) не обнаруживалось заранее, а всплывало только при реальной
        синхронизации. Бросает CalDavSyncError с текстом самой сетевой
        ошибки, если запись не удалась — чтение при этом может быть исправно."""
        calendar = self._primary_calendar()
        test_uid = f"redmail-conntest-{uuid4()}@redmail"
        start = datetime.now(timezone.utc).replace(microsecond=0)
        test_event = Event(
            uid=test_uid,
            summary="redmail: проверка подключения (можно удалить)",
            dtstart=start,
            dtend=start + timedelta(minutes=1),
            organizer_email=self.account.username,
            organizer_name=self.account.username,
            is_organizer=True,
        )
        ics_text = itip.build_caldav_ics(test_event, test_event.organizer_email, test_event.organizer_name).decode("utf-8")
        try:
            _with_connection_retry(calendar.save_event, ics_text)
        except Exception as exc:
            raise CalDavSyncError(f"Запись на сервер не удалась: {exc}") from exc
        try:
            existing = _with_connection_retry(calendar.get_event_by_uid, test_uid)
            _with_connection_retry(existing.delete)
        except Exception:
            pass  # уборка тестового события — не критично, если не получилось

    def delete_event(self, uid: str) -> None:
        calendar = self._primary_calendar()
        try:
            existing = _with_connection_retry(calendar.get_event_by_uid, uid)
        except NotFoundError:
            return  # уже нет на сервере — нечего удалять, не ошибка
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось найти событие на сервере: {exc}") from exc
        try:
            _with_connection_retry(existing.delete)
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось удалить событие на сервере: {exc}") from exc

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
