from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlparse
from uuid import uuid4

import caldav
import icalendar
import requests
from caldav.lib.error import AuthorizationError, NotFoundError

from redmail import calendar_store, itip
from redmail.applog import get_logger

_log = get_logger("caldav")
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
_CURRENT_USER_PRINCIPAL_PROP = "{DAV:}current-user-principal"
_PRINCIPAL_COLLECTION_SET_PROP = "{DAV:}principal-collection-set"

#: Сколько пользователей сервера обходим при поиске «что мне открыли».
#: Больше — это уже перебор по всей организации: и долго, и сервер вправе
#: счесть это перебором.
MAX_SCANNED_PRINCIPALS = 300
# Делегирование по-календарьсерверному (Apple, Nextcloud, SOGo): принципал,
# которому нас назначили доверенным лицом, отдаёт свой дом календарей. У VK
# таких свойств может не быть — тогда список просто пуст, ошибки нет.
_PROXY_PROPS = [
    "{http://calendarserver.org/ns/}calendar-proxy-read-for",
    "{http://calendarserver.org/ns/}calendar-proxy-write-for",
]
_CALENDAR_DISCOVERY_PROPS = [
    "{DAV:}resourcetype",
    "{DAV:}displayname",
    "{DAV:}owner",
    "{DAV:}current-user-privilege-set",
]


@dataclass
class _PropfindResult:
    """Один <response> из multistatus-ответа на PROPFIND: href, статус и
    свойства (tag → значение: текст, список тегов для resourcetype, список
    href для ссылочных свойств, сам элемент для сложных вроде
    current-user-privilege-set)."""

    href: str
    status: int
    properties: dict[str, object]


def _propfind_body(props: list[str]) -> str:
    """Тело PROPFIND-запроса под нужные свойства. Библиотека caldav в
    разных версиях по-разному принимает список свойств и по-разному
    отдаёт разобранный ответ (в 3.x — results, в 2.x — только дерево);
    свой XML на входе и свой разбор дерева на выходе (_parse_multistatus)
    не зависят от этих различий — нужен только сырой lxml-tree ответа."""
    from lxml import etree

    root = etree.Element("{DAV:}propfind", nsmap={"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"})
    prop = etree.SubElement(root, "{DAV:}prop")
    for name in props:
        etree.SubElement(prop, name)
    return etree.tostring(root, xml_declaration=True, encoding="utf-8").decode("utf-8")


def _status_code(text: str | None) -> int:
    for part in (text or "").split():
        if part.isdigit():
            return int(part)
    return 200


def _prop_value(element) -> object:
    children = [child for child in element if isinstance(child.tag, str)]
    if not children:
        return (element.text or "").strip()
    if element.tag == "{DAV:}resourcetype":
        return [child.tag for child in children]
    if all(child.tag == "{DAV:}href" for child in children):
        return [(child.text or "").strip() for child in children]
    return element


def _parse_multistatus(tree) -> list[_PropfindResult]:
    """Разбор <multistatus> ответа PROPFIND (RFC 4918): по одному
    результату на <response>, в properties — только свойства из
    propstat со статусом 2xx."""
    results: list[_PropfindResult] = []
    if tree is None:
        return results
    for response in tree.iter("{DAV:}response"):
        href_el = response.find("{DAV:}href")
        href = (href_el.text or "").strip() if href_el is not None else ""
        properties: dict[str, object] = {}
        statuses: list[int] = []
        for propstat in response.findall("{DAV:}propstat"):
            status_el = propstat.find("{DAV:}status")
            code = _status_code(status_el.text if status_el is not None else None)
            statuses.append(code)
            if code // 100 != 2:
                continue
            prop = propstat.find("{DAV:}prop")
            if prop is None:
                continue
            for element in prop:
                if isinstance(element.tag, str):
                    properties[element.tag] = _prop_value(element)
        if statuses:
            status = 200 if any(code // 100 == 2 for code in statuses) else statuses[0]
        else:
            direct = response.find("{DAV:}status")
            status = _status_code(direct.text) if direct is not None else 200
        results.append(_PropfindResult(href=href, status=status, properties=properties))
    return results


def same_server(account_url: str, candidate: str) -> bool:
    """Ссылка ведёт на тот же сервер, что настроен в учётной записи?

    Обнаружение календарей идёт по href, которые вернул САМ сервер, и
    ходит по ним с нашим логином и паролем. Абсолютная ссылка на чужой
    узел увела бы учётные данные постороннему (достаточно одного
    скомпрометированного или враждебного календарного сервера), поэтому
    ходим только по своему: та же схема, тот же узел, тот же порт."""
    mine, theirs = urlparse(account_url), urlparse(candidate)
    if not theirs.netloc:
        return True  # относительный путь — это тот же сервер
    return (
        (theirs.scheme or mine.scheme).lower() == mine.scheme.lower()
        and theirs.hostname is not None
        and mine.hostname is not None
        and theirs.hostname.lower() == mine.hostname.lower()
        and (theirs.port or _default_port(theirs.scheme or mine.scheme))
        == (mine.port or _default_port(mine.scheme))
    )


def _default_port(scheme: str) -> int:
    return 443 if (scheme or "").lower() == "https" else 80


def _resourcetype_tags(result: "_PropfindResult") -> list[str]:
    """Теги DAV:resourcetype ответа списком — свойство приходит и одним
    значением, и списком, и вовсе отсутствует."""
    resourcetype = result.properties.get("{DAV:}resourcetype") or []
    return [str(t) for t in (resourcetype if isinstance(resourcetype, list) else [resourcetype])]


def _href_path(href: str) -> str:
    """Путь без схемы/хоста и завершающего слэша — owner может прийти и
    абсолютным URL, и просто путём, сравнивать нужно только путь."""
    return unquote(urlparse(href).path).rstrip("/")


def colleague_home_urls(account_url: str, login: str) -> list[str]:
    """Адреса, по которым у этого сервера лежат календари коллеги.

    Из собственного адреса вида
    https://сервер/principals/<домен>/<я>/calendars/<id>/ получаем
    https://сервер/principals/<домен>/<коллега>/calendars/ — и его же без
    завершающего /calendars/ на случай другой раскладки путей."""
    parsed = urlparse(account_url)
    parts = [part for part in unquote(parsed.path).split("/") if part]
    if "principals" not in parts:
        return []
    index = parts.index("principals")
    # /principals/<домен>/<логин>/… — логин идёт через один сегмент после домена.
    if len(parts) < index + 3:
        return []
    own = parts[: index + 3]
    own[index + 2] = login
    base = f"{parsed.scheme}://{parsed.netloc}/" + "/".join(own)
    return [f"{base}/calendars/", f"{base}/"]


def _is_foreign_owner(owner: str | None, my_principal_path: str) -> bool:
    """Календарь принадлежит не нам? Сравниваем пути принципалов, но
    снисходительно: у части серверов путь принципала и путь владельца
    отличаются хвостом (/principals/<домен>/<логин>/ против
    /principals/<домен>/<логин>/calendars/<id>/). Если один путь — начало
    другого, это один и тот же человек."""
    if not owner:
        return False
    owner_path, mine = _href_path(owner), my_principal_path
    if not mine:
        return False
    if owner_path == mine:
        return False
    return not (owner_path.startswith(mine + "/") or mine.startswith(owner_path + "/"))


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
            _log.error("CalDAV %s: не удалось создать соединение (%s): %s", account.url, account.auth_type, exc)
            raise CalDavSyncError(f"Не удалось создать CalDAV-соединение: {exc}") from exc
        self._calendar = None
        _log.info("CalDAV %s: соединение создано (%s, %s)", account.url, account.username, account.auth_type)

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
            principal_url = self._current_user_principal() or str(principal.url)
            _log.info("CalDAV %s: принципал %s", self.account.url, principal_url)
            home_urls = self._calendar_home_urls(principal)
        except AuthorizationError as exc:
            raise CalDavSyncError(f"Не удалось получить список календарей: {exc}.{_auth_scheme_hint(self.account.url)}") from exc
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось получить список календарей: {exc}") from exc

        my_principal_path = _href_path(principal_url)
        infos: list[CalDavCalendarInfo] = []
        seen: set[str] = set()
        failure: CalDavSyncError | None = None
        for home_url in home_urls:
            _log.info("CalDAV %s: обход дома календарей %s", self.account.url, home_url)
            try:
                self._collect_calendars(home_url, my_principal_path, infos, seen, depth_left=2)
            except CalDavSyncError as exc:
                # Домов несколько (свой, доверенных лиц, коллекция настроенного
                # календаря): отказ одного не должен прятать календари из
                # остальных — об ошибке скажем, только если не нашли ничего.
                _log.warning("CalDAV %s: дом %s не обойден: %s", self.account.url, home_url, exc)
                failure = failure or exc
        if not infos:
            # Сервер не показал ни одного календаря обходом домов (жалоба
            # "при поиске календарей не находит, но синхронится"): сам
            # настроенный адрес заведомо рабочий — проверяем его напрямую,
            # чтобы список не оказался пустым там, где синхронизация идёт.
            self._collect_configured_calendar(my_principal_path, infos, seen)
            if not infos and failure is not None:
                raise failure
        _log.info("CalDAV %s: домов календарей %d, найдено календарей %d (расшаренных %d)",
                  self.account.url, len(home_urls), len(infos), sum(1 for i in infos if i.is_shared))
        for info in infos:
            _log.info("CalDAV %s: календарь «%s» %s%s%s", self.account.url, info.name, info.url,
                      f", владелец {info.owner}" if info.owner else "",
                      " (расшаренный)" if info.is_shared else "")
        return infos

    def list_colleague_calendars(self, who: str) -> list[CalDavCalendarInfo]:
        """Календари коллеги — по его логину или адресу почты.

        У VK расшаренный календарь НЕ попадает в наш дом календарей (это
        видно в журнале: в доме только свои). Он остаётся под принципалом
        владельца, и открыть его можно СВОЕЙ учётной записью, если коллега
        выдал права. Адрес строится из нашего же: в пути
        /principals/<домен>/<логин>/calendars/ подменяется логин."""
        login = (who or "").strip()
        if not login:
            return []
        login = login.split("@", 1)[0]
        homes = colleague_home_urls(self.account.url, login)
        if not homes:
            raise CalDavSyncError(
                "Не удалось понять, где лежат календари коллеги: адрес календаря не похож на "
                "…/principals/<домен>/<логин>/calendars/…"
            )
        my_principal_path = _href_path(self._current_user_principal() or self.account.url)
        infos: list[CalDavCalendarInfo] = []
        seen: set[str] = set()
        failure: Exception | None = None
        for home in homes:
            _log.info("CalDAV %s: обход календарей коллеги %s", self.account.url, home)
            try:
                self._collect_calendars(home, my_principal_path, infos, seen, depth_left=2)
            except Exception as exc:
                _log.warning("CalDAV %s: календари коллеги (%s) не получены: %s", self.account.url, home, exc)
                failure = failure or exc
        if not infos and failure is not None:
            raise CalDavSyncError(
                f"Календари коллеги {login} недоступны: {failure}. Возможно, он не открыл вам доступ."
            )
        for info in infos:
            _log.info("CalDAV %s: календарь коллеги «%s» %s%s", self.account.url, info.name, info.url,
                      " (только чтение)" if info.read_only else "")
        return infos

    def list_principals(self, limit: int = MAX_SCANNED_PRINCIPALS) -> list[str]:
        """Пользователи сервера — из каталога принципалов. Пусто, если
        сервер не даёт его перечислить (многие закрывают это намеренно)."""
        my_principal = self._current_user_principal() or self.account.url
        collections = [
            str(self._client.url.join(href))
            for href in self._principal_hrefs(my_principal, _PRINCIPAL_COLLECTION_SET_PROP)
            if same_server(self.account.url, href)
        ]
        if not collections:
            # Каталог не назван — берём родителя своего принципала:
            # /principals/<домен>/<я>/ → /principals/<домен>/
            parent = my_principal.rstrip("/").rsplit("/", 1)[0] + "/"
            collections = [parent]
        found: list[str] = []
        seen: set[str] = set()
        for collection in collections:
            _log.info("CalDAV %s: перечисляю пользователей в %s", self.account.url, collection)
            try:
                response = _with_connection_retry(
                    self._client.propfind, collection, _propfind_body(["{DAV:}resourcetype"]), 1
                )
            except Exception as exc:
                _log.info("CalDAV %s: каталог пользователей %s не перечислен: %s", self.account.url, collection, exc)
                continue
            own_path = _href_path(collection)
            for result in _parse_multistatus(response.tree):
                if not same_server(self.account.url, result.href):
                    continue
                url = str(self._client.url.join(result.href))
                path = _href_path(url)
                if path == own_path or path in seen:
                    continue
                seen.add(path)
                found.append(url if url.endswith("/") else url + "/")
                if len(found) >= limit:
                    _log.info("CalDAV %s: пользователей больше %d — обход ограничен", self.account.url, limit)
                    return found
        return found

    def scan_shared_calendars(
        self, *, extra_logins=(), limit: int = MAX_SCANNED_PRINCIPALS, progress=None, stop=None
    ) -> list[CalDavCalendarInfo]:
        """Пробежать по пользователям сервера и собрать календари, которые
        ОТКРЫТЫ нам. Вопрос пользователя: «а нельзя сразу все календари
        пробежать и доступные подтянуть?» — можно, но только перебором:
        у VK нет ни одного свойства, где сервер перечислил бы, что тебе
        расшарили (в своём доме календарей их нет, см. журнал).

        extra_logins — логины из адресной книги: если сервер не даёт
        перечислить пользователей, обходим хотя бы известных коллег.
        progress(сделано, всего, имя) — для окна хода; stop() — отмена."""
        my_principal_path = _href_path(self._current_user_principal() or self.account.url)
        targets: list[str] = []
        seen_paths: set[str] = set()

        def add(url: str) -> None:
            path = _href_path(url)
            if path and path != my_principal_path and path not in seen_paths:
                seen_paths.add(path)
                targets.append(url)

        for principal_url in self.list_principals(limit):
            add(principal_url)
        for login in extra_logins:
            for home in colleague_home_urls(self.account.url, str(login).split("@", 1)[0]):
                add(home)
        if not targets:
            raise CalDavSyncError(
                "Сервер не даёт перечислить пользователей, а адресная книга пуста — "
                "укажите логин коллеги вручную."
            )

        infos: list[CalDavCalendarInfo] = []
        seen: set[str] = set()
        total = len(targets)
        _log.info("CalDAV %s: обход %d пользователей в поисках открытых календарей", self.account.url, total)
        for number, target in enumerate(targets, start=1):
            if stop is not None and stop():
                _log.info("CalDAV %s: обход прерван пользователем", self.account.url)
                break
            name = target.rstrip("/").rsplit("/", 1)[-1]
            if progress is not None:
                progress(number, total, name)
            for home in (target if target.rstrip("/").endswith("calendars") else target.rstrip("/") + "/calendars/",):
                try:
                    self._collect_calendars(home, my_principal_path, infos, seen, depth_left=1)
                except Exception as exc:
                    # Нет прав или нет такого дома — обычное дело при обходе.
                    _log.debug("CalDAV %s: %s пропущен: %s", self.account.url, home, exc)
        shared = [info for info in infos if info.is_shared]
        _log.info("CalDAV %s: открытых чужих календарей найдено %d", self.account.url, len(shared))
        return shared

    def _calendar_home_urls(self, principal) -> list[str]:
        """Свой дом календарей плюс дома принципалов, которые назначили нас
        доверенным лицом (calendar-proxy), плюс коллекция, в которой лежит
        НАСТРОЕННЫЙ календарь: у части серверов (VK) calendar-home-set у
        принципала не совпадает с реальным путём календарей, и без этого
        обход не находит ничего."""
        urls: list[str] = []
        seen_paths: set[str] = set()

        def remember(raw: str, source: str) -> None:
            if not raw:
                return
            # Проверяем ДО склейки: caldav на ссылку с чужим узлом бросает
            # ValueError, и враждебный (или просто криво настроенный) сервер
            # так ронял бы весь поиск календарей.
            if not same_server(self.account.url, raw):
                _log.warning("CalDAV %s: ссылка на чужой сервер пропущена (%s): %s", self.account.url, source, raw)
                return
            url = str(self._client.url.join(raw))
            path = _href_path(url)
            if path in seen_paths:
                return
            seen_paths.add(path)
            urls.append(url)
            _log.info("CalDAV %s: дом календарей %s (%s)", self.account.url, url, source)

        for href in self._principal_hrefs(str(principal.url), _CALDAV_HOME_SET_PROP):
            remember(href, "calendar-home-set")
        for prop in _PROXY_PROPS:
            for proxy_href in self._principal_hrefs(str(principal.url), prop):
                if not same_server(self.account.url, proxy_href):
                    _log.warning("CalDAV %s: доверенный принципал на чужом сервере пропущен: %s",
                                 self.account.url, proxy_href)
                    continue
                proxy_url = str(self._client.url.join(proxy_href))
                _log.info("CalDAV %s: доверенный принципал %s (%s)", self.account.url, proxy_url, prop.rsplit("}", 1)[-1])
                for href in self._principal_hrefs(proxy_url, _CALDAV_HOME_SET_PROP):
                    remember(href, f"доверенный {proxy_url}")
        if not urls:
            try:
                remember(str(principal.calendar_home_set.url), "calendar_home_set библиотеки")
            except Exception as exc:
                _log.warning("CalDAV %s: дом календарей не определён: %s", self.account.url, exc)
        parent = self.account.url.rstrip("/").rsplit("/", 1)[0] + "/"
        remember(parent, "коллекция настроенного календаря")
        return urls

    def _current_user_principal(self) -> str:
        """Свой принципал — тот, что сервер называет сам (DAV:current-user-
        principal у настроенного адреса).

        Библиотека caldav, когда сервер не ответил на её запрос принципала,
        молча подставляет БАЗОВЫЙ адрес — то есть сам календарь. На VK так и
        вышло: «принципал» оказался адресом календаря, и владелец
        собственных календарей (/principals/<домен>/<логин>/) с ним не
        совпадал — все свои календари помечались расшаренными (жалоба:
        «поиск дал 2 моих календаря»)."""
        try:
            response = _with_connection_retry(
                self._client.propfind, self.account.url, _propfind_body([_CURRENT_USER_PRINCIPAL_PROP]), 0
            )
        except Exception as exc:
            _log.info("CalDAV %s: принципал у сервера не спрошен: %s", self.account.url, exc)
            return ""
        for result in _parse_multistatus(response.tree):
            value = result.properties.get(_CURRENT_USER_PRINCIPAL_PROP)
            for href in value if isinstance(value, list) else [value]:
                if isinstance(href, str) and href and same_server(self.account.url, href):
                    return str(self._client.url.join(href))
        return ""

    def _principal_hrefs(self, principal_url: str, prop: str) -> list[str]:
        """Значение ссылочного свойства принципала (дом календарей, списки
        доверенных лиц). Отсутствие свойства — не ошибка: у каждого сервера
        свой набор."""
        hrefs: list[str] = []
        try:
            response = _with_connection_retry(self._client.propfind, principal_url, _propfind_body([prop]), 0)
            for result in _parse_multistatus(response.tree):
                value = result.properties.get(prop)
                for href in value if isinstance(value, list) else [value]:
                    if isinstance(href, str) and href:
                        hrefs.append(href)
        except Exception as exc:
            _log.info("CalDAV %s: свойство %s у %s не получено: %s",
                      self.account.url, prop.rsplit("}", 1)[-1], principal_url, exc)
        return hrefs

    def _collect_configured_calendar(
        self, my_principal_path: str, infos: list[CalDavCalendarInfo], seen: set[str]
    ) -> None:
        """Настроенный адрес как календарь — на случай, когда обход домов
        пуст, а синхронизация с этим адресом работает."""
        try:
            response = _with_connection_retry(
                self._client.propfind, self.account.url, _propfind_body(_CALENDAR_DISCOVERY_PROPS), 0
            )
        except Exception as exc:
            _log.warning("CalDAV %s: настроенный календарь не опрошен: %s", self.account.url, exc)
            return
        for result in _parse_multistatus(response.tree):
            info = self._calendar_info(result, my_principal_path, seen)
            if info is not None:
                infos.append(info)

    def _collect_calendars(
        self, url: str, my_principal_path: str, infos: list[CalDavCalendarInfo], seen: set[str], depth_left: int
    ) -> None:
        try:
            response = _with_connection_retry(
                self._client.propfind, url, _propfind_body(_CALENDAR_DISCOVERY_PROPS), 1
            )
        except AuthorizationError as exc:
            raise CalDavSyncError(f"Не удалось получить список календарей: {exc}.{_auth_scheme_hint(self.account.url)}") from exc
        except Exception as exc:
            if depth_left == 2:
                raise CalDavSyncError(f"Не удалось получить список календарей: {exc}") from exc
            _log.info("CalDAV %s: коллекция %s не отвечает: %s", self.account.url, url, exc)
            return  # вложенная коллекция не отвечает — не роняем весь список
        own_path = _href_path(url)
        for result in _parse_multistatus(response.tree):
            tags = _resourcetype_tags(result)
            # Что именно отдал сервер — в журнал: по жалобе "не находит, но
            # синхронится" иначе не увидеть, какие коллекции пришли и почему
            # не признаны календарями.
            _log.info("CalDAV %s: ответ %s статус %s тип [%s]%s", self.account.url, result.href, result.status,
                      ", ".join(t.rsplit("}", 1)[-1] for t in tags) or "—",
                      f" «{result.properties.get('{DAV:}displayname')}»" if result.properties.get("{DAV:}displayname") else "")
            if result.status not in (200, 207):
                continue
            if not same_server(self.account.url, result.href):
                # Сервер вернул ссылку на другой узел: ходить туда с нашим
                # логином и паролем нельзя (см. same_server).
                _log.warning("CalDAV %s: ответ со ссылкой на чужой сервер пропущен: %s",
                             self.account.url, result.href)
                continue
            full_url = str(self._client.url.join(result.href))
            path = _href_path(full_url)
            if path == own_path:
                continue  # сама обходимая коллекция
            # Точное имя тега, не подстрока "calendar": у расшаренных
            # коллекций в resourcetype бывает {http://calendarserver.org/ns/}shared
            # — "calendarserver" содержит "calendar", ложное срабатывание.
            if _CALDAV_CALENDAR_TAG in tags:
                info = self._calendar_info(result, my_principal_path, seen)
                if info is not None:
                    infos.append(info)
            elif any(t.endswith("}collection") for t in tags) and depth_left > 1:
                self._collect_calendars(full_url, my_principal_path, infos, seen, depth_left - 1)

    def _calendar_info(
        self, result: "_PropfindResult", my_principal_path: str, seen: set[str]
    ) -> CalDavCalendarInfo | None:
        """Описание календаря из ответа PROPFIND; None — если это не
        календарь или он уже в списке."""
        if result.status not in (200, 207) or _CALDAV_CALENDAR_TAG not in _resourcetype_tags(result):
            return None
        if not same_server(self.account.url, result.href):
            _log.warning("CalDAV %s: календарь на чужом сервере пропущен: %s", self.account.url, result.href)
            return None
        full_url = str(self._client.url.join(result.href))
        path = _href_path(full_url)
        if path in seen:
            return None
        seen.add(path)
        owner_raw = result.properties.get("{DAV:}owner")
        if isinstance(owner_raw, list):
            owner_raw = owner_raw[0] if owner_raw else None
        owner = owner_raw if isinstance(owner_raw, str) and owner_raw else None
        return CalDavCalendarInfo(
            url=full_url,
            name=result.properties.get("{DAV:}displayname") or result.href,
            owner=owner,
            is_shared=_is_foreign_owner(owner, my_principal_path),
            read_only=not _has_write_privilege(result.properties.get("{DAV:}current-user-privilege-set")),
        )

    def fetch_events(self, start: datetime, end: datetime, my_email: str) -> list[Event]:
        """События сервера в окне [start, end). Разбор VEVENT переиспользует
        itip.parse_ics_events — ту же логику, что уже проверена на реальных
        .ics-вложениях и импорте, вместо второго независимого парсера
        одного и того же формата."""
        calendar = self._primary_calendar()
        try:
            # Без разворачивания: сервер отдаёт серию целиком (основная запись
            # и изменённые дни), серия раскрывается у нас. С развёрнутыми днями
            # основная запись не приходила, и серию нельзя было править целиком.
            results = _with_connection_retry(calendar.date_search, start, end, expand=False)
        except Exception as exc:
            _log.error("CalDAV %s: получение событий не удалось: %s", self.account.url, exc)
            raise CalDavSyncError(f"Не удалось получить события с сервера: {exc}") from exc

        events: list[Event] = []
        seen = getattr(self, "_object_shapes", None)
        if seen is None:
            seen = self._object_shapes = {}
        for obj in results:
            href = str(getattr(obj, "url", "") or "")
            try:
                raw = obj.data
                raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else raw
                shape = describe_ics_shape(raw_bytes)
                if seen.get(href) != shape:
                    # Как сервер записал серию — без названий и участников, и только
                    # когда объект изменился: чтобы по журналу разбирать формат сервера.
                    seen[href] = shape
                    _log.info("CalDAV %s: объект %s — %s", self.account.url, href.rsplit("/", 1)[-1], shape)
                events.extend(itip.parse_ics_events(raw_bytes, my_email))
            except Exception as exc:
                # одно повреждённое/непонятное событие не должно валить всю синхронизацию
                _log.warning("CalDAV %s: объект %s пропущен: %s", self.account.url, href.rsplit("/", 1)[-1], exc)
                continue
        _log.info("CalDAV %s: получено объектов %d, событий %d (окно %s — %s)",
                  self.account.url, len(results), len(events), start.date(), end.date())
        return events

    def push_event(self, event: Event, organizer_email: str, organizer_name: str) -> None:
        """Создаёт событие на сервере или обновляет уже существующее
        (находит по UID — тот же UID, что уже используется локально)."""
        calendar = self._primary_calendar()
        ics_text = itip.build_caldav_ics(event, organizer_email, organizer_name).decode("utf-8")
        try:
            existing = _with_connection_retry(calendar.event_by_uid, event.uid)
        except NotFoundError:
            existing = None
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось проверить событие на сервере: {exc}") from exc

        try:
            if existing is not None:
                # В объекте на сервере могут быть и изменённые дни серии
                # (VEVENT с RECURRENCE-ID) — заменяем только основную запись.
                existing.data = _replace_master(existing.data, ics_text)
                _with_connection_retry(existing.save)
                _log.info("CalDAV %s: событие обновлено uid=%s", self.account.url, event.uid)
            else:
                _with_connection_retry(calendar.save_event, ics_text)
                _log.info("CalDAV %s: событие создано uid=%s", self.account.url, event.uid)
        except Exception as exc:
            _log.error("CalDAV %s: сохранение события uid=%s не удалось: %s", self.account.url, event.uid, exc)
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
            _log.error("CalDAV %s: проверка записи не удалась: %s", self.account.url, exc)
            raise CalDavSyncError(f"Запись на сервер не удалась: {exc}") from exc
        _log.info("CalDAV %s: проверка записи прошла", self.account.url)
        try:
            existing = _with_connection_retry(calendar.event_by_uid, test_uid)
            _with_connection_retry(existing.delete)
        except Exception:
            pass  # уборка тестового события — не критично, если не получилось

    def _series_resource(self, uid: str):
        calendar = self._primary_calendar()
        try:
            return _with_connection_retry(calendar.event_by_uid, calendar_store.series_uid(uid))
        except NotFoundError:
            raise CalDavSyncError("Серия встреч не найдена на сервере") from None
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось найти серию встреч на сервере: {exc}") from exc

    def push_occurrence(self, event: Event, organizer_email: str, organizer_name: str) -> None:
        """Изменённый день серии — VEVENT с RECURRENCE-ID в объекте серии."""
        resource = self._series_resource(event.uid)
        override = itip.build_vevent(
            event, organizer_email=organizer_email, organizer_name=organizer_name, status=event.status.upper()
        )
        try:
            resource.data = _set_override(resource.data, override, calendar_store.instance_start(event.uid))
            _with_connection_retry(resource.save)
            _log.info("CalDAV %s: день серии изменён uid=%s", self.account.url, event.uid)
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось изменить день серии на сервере: {exc}") from exc

    def cancel_occurrence(self, uid: str) -> None:
        """Отменённый день серии — EXDATE в основной записи серии."""
        try:
            resource = self._series_resource(uid)
        except CalDavSyncError:
            return  # серии уже нет — отменять нечего
        try:
            resource.data = _exclude_date(resource.data, calendar_store.instance_start(uid))
            _with_connection_retry(resource.save)
            _log.info("CalDAV %s: день серии отменён uid=%s", self.account.url, uid)
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось отменить день серии на сервере: {exc}") from exc

    def delete_event(self, uid: str) -> None:
        calendar = self._primary_calendar()
        try:
            existing = _with_connection_retry(calendar.event_by_uid, uid)
        except NotFoundError:
            return  # уже нет на сервере — нечего удалять, не ошибка
        except Exception as exc:
            raise CalDavSyncError(f"Не удалось найти событие на сервере: {exc}") from exc
        try:
            _with_connection_retry(existing.delete)
            _log.info("CalDAV %s: событие удалено uid=%s", self.account.url, uid)
        except Exception as exc:
            _log.error("CalDAV %s: удаление события uid=%s не удалось: %s", self.account.url, uid, exc)
            raise CalDavSyncError(f"Не удалось удалить событие на сервере: {exc}") from exc

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


def _as_text(data) -> str:
    return data.decode("utf-8") if isinstance(data, bytes) else str(data)


def _same_moment(value, moment) -> bool:
    return itip._to_utc(value.dt) == moment


def _replace_master(existing_data, new_ics: str) -> str:
    try:
        current = icalendar.Calendar.from_ical(_as_text(existing_data))
    except Exception:
        return new_ics  # серверный объект не разобрать — записываем новую версию целиком
    new = icalendar.Calendar.from_ical(new_ics)
    new_master = next(c for c in new.walk("VEVENT"))
    components = [c for c in current.subcomponents if not (c.name == "VEVENT" and "RECURRENCE-ID" not in c)]
    current.subcomponents = [new_master, *components]
    return current.to_ical().decode("utf-8")


def _set_override(existing_data, override, original) -> str:
    current = icalendar.Calendar.from_ical(_as_text(existing_data))
    current.subcomponents = [
        c for c in current.subcomponents
        if not (c.name == "VEVENT" and "RECURRENCE-ID" in c and _same_moment(c["RECURRENCE-ID"], original))
    ]
    current.add_component(override)
    return current.to_ical().decode("utf-8")


def _exclude_date(existing_data, original) -> str:
    current = icalendar.Calendar.from_ical(_as_text(existing_data))
    current.subcomponents = [
        c for c in current.subcomponents
        if not (c.name == "VEVENT" and "RECURRENCE-ID" in c and _same_moment(c["RECURRENCE-ID"], original))
    ]
    for component in current.walk("VEVENT"):
        if "RECURRENCE-ID" not in component:
            component.add("exdate", original)
            component["SEQUENCE"] = int(component.get("SEQUENCE", 0)) + 1
    return current.to_ical().decode("utf-8")


def describe_ics_shape(raw: bytes) -> str:
    """Строение объекта календаря для журнала: сколько записей, правило
    повтора, часовой пояс, изменённые и отменённые дни. Без названий,
    описаний и участников."""
    calendar = icalendar.Calendar.from_ical(raw)
    parts = []
    for vevent in calendar.walk("VEVENT"):
        start = vevent.get("DTSTART")
        tzid = start.params.get("TZID", "") if start is not None else ""
        kind = "день серии" if "RECURRENCE-ID" in vevent else ("серия" if "RRULE" in vevent else "встреча")
        item = kind
        if "RRULE" in vevent:
            item += f" RRULE={vevent['RRULE'].to_ical().decode('ascii')}"
        if tzid:
            item += f" TZID={tzid}"
        elif start is not None:
            value = start.dt
            item += " UTC" if getattr(value, "tzinfo", None) is not None else (" без пояса" if hasattr(value, "hour") else " весь день")
        exdates = sum(len(getattr(prop, "dts", [])) for prop in (vevent.get("EXDATE") if isinstance(vevent.get("EXDATE"), list) else [vevent.get("EXDATE")] if vevent.get("EXDATE") is not None else []))
        if exdates:
            item += f" EXDATE×{exdates}"
        if str(vevent.get("STATUS", "")).upper() == "CANCELLED":
            item += " отменена"
        parts.append(item)
    return "; ".join(parts) or "нет VEVENT"
