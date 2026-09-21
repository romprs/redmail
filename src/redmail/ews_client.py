from __future__ import annotations

import zlib
from datetime import datetime
from dataclasses import dataclass
from email import message_from_bytes

from exchangelib import BASIC, DELEGATE, GSSAPI, HTMLBody, NTLM
from exchangelib import Account as ExchangeAccount
from exchangelib import Configuration, Credentials, FaultTolerance, FileAttachment, Folder, Mailbox
from exchangelib.items import Message as EwsMessage

from redmail.applog import get_logger
from redmail.imap_client import (
    UNKNOWN_MARKER,
    FolderInfo,
    MessageContent,
    MessageGoneError,
    MessageSummary,
    extract_content,
)
from redmail.smtp_client import OutgoingMessage

_log = get_logger("ews")

_AUTH_TYPE_MAP = {"basic": BASIC, "ntlm": NTLM, "kerberos": GSSAPI}
_IMPORTANCE_MAP = {"High": "high", "Normal": "normal", "Low": "low"}

# EWS не поддерживает произвольные keyword-флаги как IMAP — ближайший
# аналог для цветного маркера здесь это "категории" Outlook (произвольный
# список текстовых меток, тоже показываются цветными плашками в самом
# Outlook, если завести категорию с тем же именем и назначить ей цвет).
MARKER_CATEGORIES: dict[str, str] = {
    "red": "RedMail Red",
    "orange": "RedMail Orange",
    "yellow": "RedMail Yellow",
    "green": "RedMail Green",
    "blue": "RedMail Blue",
    "purple": "RedMail Purple",
}
_COLOR_BY_CATEGORY = {v: k for k, v in MARKER_CATEGORIES.items()}


#: Хвосты адреса, которые остаются, если скопировать ссылку из браузера.
_EWS_PATH_SUFFIXES = ("/ews/exchange.asmx", "/ews/services.wsdl", "/ews")


def normalize_ews_server(value: str) -> str:
    """Оставляет от адреса сервера только имя узла.

    В поле "Сервер" естественно вставить ссылку целиком, как она
    открывается в браузере (https://svb-mail.corp.amurgpz.ru/EWS/
    Exchange.asmx). Библиотека ждёт здесь только имя узла и сама
    дописывает путь, а разные написания одного и того же сервера ещё и
    давали разные ключи локальной копии — после правки адреса почта
    выглядела как пропавшая и скачивалась заново."""
    server = (value or "").strip()
    if not server:
        return ""
    if "://" in server:
        server = server.split("://", 1)[1]
    server = server.split("?", 1)[0].rstrip("/")
    lowered = server.lower()
    for suffix in _EWS_PATH_SUFFIXES:
        if lowered.endswith(suffix):
            server = server[: -len(suffix)]
            break
    return server.rstrip("/")


@dataclass
class EwsAccount:
    email: str
    username: str = ""  # для NTLM: DOMAIN\пользователь; для basic: обычно = email; для kerberos не используется
    password: str = ""
    server: str = ""  # явный адрес EWS-сервера; пусто = автообнаружение (autodiscover)
    auth_type: str = "basic"  # "basic" | "ntlm" | "kerberos"
    #: Ящики коллег, на папки которых мы подписаны. Открываются НАШЕЙ
    #: учётной записью по правам, которые выдал владелец (доступ делегата) —
    #: пароль владельца не нужен и не хранится.
    shared_mailboxes: tuple[str, ...] = ()
    #: Хранить ли письма подписанных ящиков локально: с офлайн-копией они
    #: доступны без сети и попадают в автоархив, без неё — запрашиваются с
    #: сервера при открытии папки (чужой ящик бывает очень большим).
    shared_offline: bool = False

    def __post_init__(self) -> None:
        self.server = normalize_ews_server(self.server)
        # Из настроек список приходит обычным list — приводим к кортежу,
        # чтобы учётная запись оставалась сравнимой и хешируемой.
        self.shared_mailboxes = tuple(
            address.strip() for address in self.shared_mailboxes if str(address).strip()
        )
        if not self.username:
            # Для Kerberos/SSO логин не нужен для входа (билет и так
            # привязан к пользователю ОС), но username всё равно
            # используется как часть ключа кэша сообщений
            # (CachedMailbox._account_key = f"{host}:{username}") — без
            # этого два разных Kerberos-пользователя на одном домене
            # (одинаковый host, пустой username у обоих) получили бы один
            # и тот же ключ и делили бы кэш чужих писем.
            self.username = self.email

    @property
    def host(self) -> str:
        # Используется только как ключ кэша/учётной записи (см.
        # CachedMailbox._account_key) — реальный адрес сервера, если он не
        # указан явно, приложение узнаёт через autodiscover само.
        return self.server or self.email.rsplit("@", 1)[-1]


SHARED_FOLDER_MARK = "Ящик "


def is_shared_folder(path: str, mailboxes: "tuple[str, ...] | list[str]" = ()) -> bool:
    """Папка подписанного ящика коллеги. Попадает ли она в офлайн-копию и
    архив, решает настройка учётной записи shared_offline: по умолчанию
    нет — чужой ящик может быть сколь угодно большим, а нужен обычно на
    просмотр.

    mailboxes — адреса подписанных ящиков. С ними сравнение точное: своя
    папка, названная «Ящик подрядчика», не должна выпадать из локальной
    копии из-за совпадения первых букв. Без них (старые вызовы) остаётся
    проверка по метке."""
    if not path.startswith(SHARED_FOLDER_MARK):
        return False
    if not mailboxes:
        return True
    return any(
        path == shared_folder_prefix(mailbox) or path.startswith(shared_folder_prefix(mailbox) + "/")
        for mailbox in mailboxes
    )


def excluded_shared_folders(folders, shared_offline: bool, mailboxes: "tuple[str, ...] | list[str]" = ()) -> tuple[str, ...]:
    """Папки подписанных ящиков, которые НЕ входят в локальную копию, когда
    офлайн-копия чужих ящиков выключена: их не обходит фоновая
    синхронизация, для них не качаются тела писем и они не попадают в
    автоархив. Включена — не исключается ничего, чужая почта живёт по тем
    же правилам, что и своя."""
    if shared_offline:
        return ()
    return tuple(name for name in folders if is_shared_folder(name, mailboxes))


def shared_folder_prefix(mailbox: str) -> str:
    """Ветка дерева папок для ящика коллеги. Имя пути — обычная строка с
    разделителем «/», поэтому подписанный ящик виден как отдельная ветка и
    работает со всем остальным кодом (кэш, синхронизация) без исключений."""
    return f"{SHARED_FOLDER_MARK}{mailbox}"


#: Служебные папки Exchange, которые не показываем и не синхронизируем:
#: почты в них нет, а серверу лишние запросы дорого обходятся.
_SERVICE_FOLDER_NAMES = {
    "Conversation Action Settings", "ExternalContacts", "Files", "Файлы",
    "Quick Step Settings", "Настройка быстрых действий", "SearchLog",
    "Yammer Root", "Корневая папка Yammer", "RSS Feeds", "RSS-подписки",
    "Sync Issues", "Ошибки синхронизации", "Recoverable Items",
    "Journal", "Журнал", "Notes", "Заметки", "Tasks", "Задачи",
    "Outbox", "Исходящие",
}


#: Поля, которых хватает для строки в списке писем. Тела и MIME здесь
#: нет намеренно: именно из-за них обход папок тянул каждое письмо целиком.
_SUMMARY_FIELDS = (
    "subject", "sender", "datetime_received", "message_id",
    "has_attachments", "categories", "importance", "is_read",
    "to_recipients", "display_to", "size",
)

#: Коды ответа сервера, означающие «такого письма здесь уже нет».
_GONE_ERRORS = ("ErrorItemNotFound", "ErrorInvalidIdMalformed", "ErrorInvalidIdNotAnItemAttachmentId")


def _is_gone(result) -> bool:
    return isinstance(result, Exception) and type(result).__name__ in _GONE_ERRORS


def _local_date_text(value) -> str:
    """Дата письма для списка — в местном времени, как у IMAP. Exchange отдаёт
    время в UTC, и раньше оно записывалось как есть: письма Exchange были
    «датированы по Гринвичу», а письма VK — по местному времени."""
    if value is None:
        return ""
    if getattr(value, "tzinfo", None) is not None:
        value = datetime.fromtimestamp(value.timestamp())
    return value.strftime("%Y-%m-%d %H:%M")


def _format_mailboxes(mailboxes) -> str:
    """Получатели для колонки «Кому» — в том же виде, что у IMAP."""
    parts = []
    for mailbox in mailboxes or []:
        name = getattr(mailbox, "name", "") or ""
        email = getattr(mailbox, "email_address", "") or ""
        parts.append(f"{name} <{email}>" if name and email else (email or name))
    return ", ".join(parts)
#: Сколько ждать, если сервер просит притормозить (секунды). Настоящий
#: Exchange просил 80 секунд, а мы сдавались на 60 — календарь не грузился.
#: Но и пять минут оказалось слишком: библиотека тем же пределом повторяет
#: запросы после таймаута, и один зависший запрос держал соединение по
#: несколько минут. Две минуты — с запасом на просьбу сервера, без
#: многоминутных повторов.
_MAX_THROTTLE_WAIT = 120

#: Сколько одновременных соединений с сервером Exchange. У библиотеки по
#: умолчанию ОДНО на весь ящик: пока фоновая синхронизация календаря ждала
#: ответа сервера (по минуте на запрос, десятки минут подряд), пометка,
#: удаление и отправка письма стояли в очереди за ней — жалоба «ставлю
#: маркер, удаляю, отправляю письмо — подвисает на минуту или более».
_MAX_CONNECTIONS = 4

#: Сколько писем запрашивать у сервера одним запросом (по умолчанию в
#: exchangelib — 100, на 250 круг по папке заметно короче).
_FETCH_CHUNK = 250


class EwsConnectionError(Exception):
    """Не удалось подключиться или авторизоваться на сервере Exchange."""


class EwsSession:
    """То же назначение, что у ImapSession (src/redmail/imap_client.py), но
    поверх Exchange Web Services (библиотека exchangelib) — для серверов
    Exchange/Office 365, где IMAP отключён политикой безопасности или где
    нужен вход через SSO (Kerberos) без ввода пароля в приложении.

    Публичный интерфейс намеренно повторяет ImapSession метод-в-метод
    (list_folders/create_folder/rename_folder/trash_folder/
    folder_message_count/fetch_summaries/search_uids/
    fetch_message_content/fetch_message_raw/set_read/set_marker/
    move_messages/delete_messages/close) — CachedMailbox (mailbox.py)
    работает с "session" по duck typing, не зная, IMAP это или EWS, и
    поэтому весь остальной UI (дерево папок, таблица писем, кэш) подходит
    без изменений для обоих протоколов.
    """

    #: Цвет маркера хранится в categories — это "сложное" поле EWS, и
    #: чтобы прочитать его у всех писем папки, пришлось бы запрашивать
    #: письма целиком. Цвета с сервера не перечитываем, локальные
    #: остаются как есть (см. sync_engine.sync_folder_headers).
    supports_marker_sync = False

    def __init__(self, account: EwsAccount):
        self.account = account
        auth_type = _AUTH_TYPE_MAP.get(account.auth_type, BASIC)
        credentials = None
        if account.auth_type != "kerberos":
            # Kerberos/SSO: билет берётся из окружения ОС (см. GSSAPI в
            # exchangelib) — пароль в приложении не нужен и не хранится.
            credentials = Credentials(account.username or account.email, account.password)
        # Exchange отвечает "The server cannot service this request right
        # now", когда клиент выбрал свою долю запросов. По умолчанию
        # библиотека сразу поднимает ошибку — вместо этого подождём,
        # сколько просит сервер, но не дольше минуты, чтобы закрытие окна
        # не упиралось в это ожидание.
        config_kwargs: dict = {
            "auth_type": auth_type,
            "credentials": credentials,
            "retry_policy": FaultTolerance(max_wait=_MAX_THROTTLE_WAIT),
            "max_connections": _MAX_CONNECTIONS,
        }
        if account.server:
            config_kwargs["server"] = account.server
        try:
            config = self._config = Configuration(**config_kwargs)
            self._account = ExchangeAccount(
                primary_smtp_address=account.email,
                config=config,
                autodiscover=not bool(account.server),
                access_type=DELEGATE,
            )
        except Exception as exc:
            _log.error("EWS %s: подключение не удалось (%s, %s): %s", account.server or "autodiscover", account.email, account.auth_type, exc)
            raise EwsConnectionError(str(exc)) from exc
        _log.info("EWS %s: подключение выполнено (%s, %s)", account.server or "autodiscover", account.email, account.auth_type)

        self._folders_by_path: dict[str, Folder] = {}
        self._selected_folder: str | None = None
        self._selected_folder_obj: Folder | None = None
        # Наш синтетический int-uid (по образцу IMAP UID) -> (id, changekey)
        # реального письма в EWS. EWS адресует письма строковой парой
        # id+changekey, а не одним числом — этот кэш позволяет остальному
        # приложению (кэш сообщений, таблица писем, отметка "прочитано",
        # цветной маркер, перемещение/удаление) работать с тем же типом
        # uid, что и для IMAP, без переделки всего приложения под другой
        # тип идентификатора письма. crc32 — детерминированный, не зависит
        # от PYTHONHASHSEED (в отличие от встроенного hash()).
        self._id_map: dict[int, tuple[str, str]] = {}
        # Признак "прочитано" по последней просмотренной папке. Раньше тут
        # лежали сами письма всех папок сразу — на настоящем ящике это
        # съедало и память, и время.
        self._flags: dict[str, dict[int, bool]] = {}
        # Папки, где получатели уже дописаны старым письмам (см.
        # sync_engine._backfill_recipients).
        self.recipients_backfilled: set[str] = set()

    def mailbox_account(self, email: str):
        """Ящик коллеги, открытый ПОД СВОЕЙ учётной записью (доступ делегата):
        подписка на чужой календарь или папку не требует его пароля — только
        выданных им прав. Пусто — свой ящик."""
        email = (email or "").strip()
        if not email or email.casefold() == (self.account.email or "").casefold():
            return self._account
        cached = getattr(self, "_mailbox_accounts", None)
        if cached is None:
            cached = self._mailbox_accounts = {}
        key = email.casefold()
        if key not in cached:
            try:
                cached[key] = ExchangeAccount(
                    primary_smtp_address=email,
                    config=self._config,
                    autodiscover=not bool(self.account.server),
                    access_type=DELEGATE,
                )
            except Exception as exc:
                _log.error("EWS: ящик %s не открылся: %s", email, exc)
                raise EwsConnectionError(f"Ящик {email} не открылся: {exc}") from exc
            _log.info("EWS: открыт ящик коллеги %s (правами %s)", email, self.account.email)
        return cached[key]

    def close(self) -> None:
        pass  # exchangelib сам управляет пулом HTTP-соединений, отдельно закрывать нечего

    def __enter__(self) -> "EwsSession":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def list_folders(self) -> list[FolderInfo]:
        """Только почтовые папки. Раньше возвращались все подряд, включая
        служебные (Задачи, Заметки, Файлы, Yammer, SearchLog, Conversation
        Action Settings) — синхронизация ломилась в 64 папки и упиралась в
        ограничение сервера «The server cannot service this request right
        now» (жалоба: "всё висит на синхронизации с ящиком ews")."""
        self._folders_by_path = {}
        result: list[FolderInfo] = []

        def is_mail_folder(folder: Folder) -> bool:
            folder_class = (getattr(folder, "folder_class", "") or "").upper()
            if folder_class and not folder_class.startswith("IPF.NOTE"):
                return False  # задачи, заметки, контакты, календари и прочее
            name = (folder.name or "")
            return name not in _SERVICE_FOLDER_NAMES

        def walk(folder: Folder, prefix: str) -> None:
            for child in folder.children:
                if not is_mail_folder(child):
                    continue
                path = f"{prefix}/{child.name}" if prefix else child.name
                self._folders_by_path[path] = child
                result.append(FolderInfo(name=path, delimiter="/"))
                walk(child, path)

        try:
            walk(self._account.msg_folder_root, "")
        except Exception as exc:
            raise EwsConnectionError(str(exc)) from exc
        for mailbox in self.account.shared_mailboxes:
            # Ящик коллеги — отдельной веткой дерева. Отказ по одному ящику
            # (права отозваны, ящик удалён) не должен лишать пользователя
            # собственной почты, поэтому только в журнал.
            prefix = shared_folder_prefix(mailbox)
            try:
                walk(self.mailbox_account(mailbox).msg_folder_root, prefix)
            except Exception as exc:
                _log.warning("EWS: папки ящика %s недоступны: %s", mailbox, exc)
        return result

    def create_folder(self, name: str) -> None:
        parent_path, _, short_name = name.rpartition("/")
        parent = self._folders_by_path.get(parent_path) if parent_path else self._account.msg_folder_root
        if parent is None:
            raise ValueError(f"Родительская папка не найдена: {parent_path}")
        Folder(parent=parent, name=short_name).save()

    def rename_folder(self, old_name: str, new_name: str) -> None:
        folder = self._folders_by_path.get(old_name)
        if folder is None:
            raise ValueError(f"Папка не найдена: {old_name}")
        _, _, short_new_name = new_name.rpartition("/")
        folder.name = short_new_name
        folder.save(update_fields=["name"])

    def trash_folder(self) -> str | None:
        return self._special_folder_path(lambda account: account.trash)

    def sent_folder(self) -> str | None:
        # На практике не используется для реальной отправки — EWS сам
        # сохраняет копию в "Отправленные" при Message.send(save_copy=True)
        # (умолчание exchangelib), в отличие от IMAP/SMTP, где сервер не
        # всегда это делает сам. Но метод даёт единый интерфейс с
        # ImapSession — остальному коду (например, определению, что
        # текущая папка — Черновики) не нужно знать про протокол.
        return self._special_folder_path(lambda account: account.sent)

    def drafts_folder(self) -> str | None:
        return self._special_folder_path(lambda account: account.drafts)

    def _special_folder_path(self, get_special_folder) -> str | None:
        try:
            special = get_special_folder(self._account)
        except Exception:
            return None
        for path, folder in self._folders_by_path.items():
            if folder.id == special.id:
                return path
        return None

    def _folder(self, path: str) -> Folder:
        folder = self._folders_by_path.get(path)
        if folder is None:
            raise ValueError(f"Папка не найдена: {path}")
        return folder

    def folder_message_count(self, folder: str) -> int:
        folder_obj = self._folder(folder)
        self._selected_folder = folder
        self._selected_folder_obj = folder_obj
        return folder_obj.total_count

    def fetch_summaries(self, limit: int = 50) -> list[MessageSummary]:
        if self._selected_folder_obj is None:
            return []
        items = self._selected_folder_obj.all().only(*_SUMMARY_FIELDS).order_by("-datetime_received")[:limit]
        return [self._to_summary(item) for item in items]

    def fetch_folder_summaries(self, folder: str, limit: int = 50) -> list[MessageSummary]:
        self.folder_message_count(folder)
        return self.fetch_summaries(limit)

    def search_uids(self, folder: str, *, before=None) -> list[int]:
        """Перечисляет письма папки, запрашивая только идентификатор, ключ
        изменения и признак "прочитано": такой запрос сервер отдаёт
        страницами и не читает письма целиком. Раньше здесь был
        folder.all() без ограничения полей — exchangelib на каждую сотню
        писем дозапрашивал их целиком, с телом и вложениями. На настоящем
        ящике обход папок из-за этого не заканчивался, письма в список не
        попадали, а процессор был занят разбором тел (жалоба: "пробегал по
        всем папкам, но письма не подтягивались, потом всё зависало с
        загрузкой процессора до 120%")."""
        folder_obj = self._folder(folder)
        items = folder_obj.all().only("id", "changekey", "is_read")
        if before is not None:
            items = items.filter(datetime_received__lt=before)
        flags: dict[int, bool] = {}
        for item in items:
            flags[self._register(item)] = bool(item.is_read)
        if before is None:
            # Синхронизация сразу после search_uids спрашивает флаги
            # порциями — отвечаем из этого же списка. Держим только
            # последнюю папку: весь ящик в памяти держать незачем.
            self._flags = {folder: flags}
        return list(flags)

    def folder_unseen_count(self, folder: str) -> int:
        """Непрочитанные в папке для счётчика в дереве. Раньше у Exchange
        этого не было вовсе, и число после удаления или прочтения не
        менялось до перезапуска."""
        folder_obj = self._folder(folder)
        try:
            folder_obj.refresh()
        except Exception as exc:
            _log.debug("EWS: счётчик папки %s не обновлён: %s", folder, exc)
        return int(getattr(folder_obj, "unread_count", 0) or 0)

    def folder_status(self, folder: str) -> tuple[int, int]:
        """(UIDVALIDITY, число писем): у EWS нет UIDVALIDITY — наши uid
        детерминированы (crc32 от id письма), возвращаем 0."""
        return 0, self._folder(folder).total_count

    def _folder_flags(self, folder: str) -> dict[int, bool]:
        if folder not in self._flags:
            self.search_uids(folder)
        return self._flags.get(folder, {})

    def fetch_summaries_by_uids(self, folder: str, uids: list[int]) -> list[MessageSummary]:
        """Заголовки запрашиваются только для перечисленных писем и только
        нужными полями — без тела и вложений."""
        ids = [self._id_map[uid] for uid in uids if uid in self._id_map]
        if not ids:
            return []
        summaries: list[MessageSummary] = []
        for item in self._account.fetch(ids=ids, only_fields=_SUMMARY_FIELDS, chunk_size=_FETCH_CHUNK):
            if isinstance(item, Exception):
                _log.warning("EWS: заголовок письма не прочитан: %s", item)
                continue
            summaries.append(self._to_summary(item))
        return summaries

    def fetch_flags(self, folder: str, uids: list[int]) -> dict[int, tuple[bool, bool, str | None]]:
        """Отвечает из списка, полученного при перечислении папки. Признак
        "отвечено" и цвет маркера EWS дёшево не отдаёт, поэтому цвет
        синхронизация не трогает (см. supports_marker_sync)."""
        flags = self._folder_flags(folder)
        return {uid: (flags[uid], False, None) for uid in uids if uid in flags}

    def fetch_message_content(self, folder: str, uid: int) -> MessageContent:
        return extract_content(message_from_bytes(self.fetch_message_raw(folder, uid)))

    def fetch_message_raw(self, folder: str, uid: int) -> bytes:
        item = self._get_item(uid, folder)
        mime = getattr(item, "mime_content", None)
        if mime:
            return mime
        # Exchange не всегда умеет отдать письмо в MIME
        # (ErrorMimeContentConversionFailed — чаще всего приглашения и
        # письма, созданные не почтовым клиентом). Тогда собираем письмо
        # сами из тела и вложений, иначе в области просмотра пусто
        # (жалоба: "часть содержимого писем не отображается").
        return self._build_mime(item)

    def _build_mime(self, item) -> bytes:
        from email.message import EmailMessage
        from email.utils import format_datetime

        message = EmailMessage()
        message["Subject"] = item.subject or ""
        sender = getattr(item, "sender", None)
        if sender is not None:
            message["From"] = _format_mailboxes([sender])
        for header, field in (("To", "to_recipients"), ("Cc", "cc_recipients")):
            value = _format_mailboxes(getattr(item, field, None))
            if value:
                message[header] = value
        sent = getattr(item, "datetime_sent", None) or getattr(item, "datetime_received", None)
        if sent is not None:
            try:
                message["Date"] = format_datetime(sent)
            except (TypeError, ValueError):
                pass
        body = getattr(item, "body", None) or ""
        if type(body).__name__ == "HTMLBody":
            message.set_content(str(body), subtype="html")
        else:
            message.set_content(str(body))
        for attachment in getattr(item, "attachments", None) or []:
            content = getattr(attachment, "content", None)
            if content is None:
                continue  # вложенное письмо (ItemAttachment) — пропускаем
            maintype, _, subtype = (attachment.content_type or "application/octet-stream").partition("/")
            message.add_attachment(
                content, maintype=maintype, subtype=subtype or "octet-stream",
                filename=attachment.name or "вложение",
                cid=f"<{attachment.content_id}>" if getattr(attachment, "content_id", None) else None,
            )
        return message.as_bytes()

    def set_read(self, folder: str, uid: int, read: bool) -> None:
        item = self._get_item(uid, folder)
        item.is_read = read
        item.save(update_fields=["is_read"])

    def set_marker(self, folder: str, uid: int, color: str | None, *, previous_color=UNKNOWN_MARKER) -> None:
        if previous_color is not UNKNOWN_MARKER and previous_color == color:
            return
        item = self._get_item(uid, folder)
        categories = [c for c in (item.categories or []) if c not in MARKER_CATEGORIES.values()]
        if color is not None:
            categories.append(MARKER_CATEGORIES[color])
        item.categories = categories
        item.save(update_fields=["categories"])

    def move_messages(self, folder: str, uids: list[int], target_folder: str) -> None:
        """Одним пакетным запросом на порцию писем. Раньше на каждое письмо
        шло два запроса (прочитать целиком, затем перенести) — удаление
        сотни писем тянулось минутами, а синхронизация тем временем
        возвращала их в список (жалоба: "не удаляются письма")."""
        if not uids:
            return
        target = self._folder(target_folder)
        ids = self._ids_for(folder, uids)
        self._check_bulk("перенос", self._account.bulk_move(ids=ids, to_folder=target, chunk_size=_FETCH_CHUNK))

    def delete_messages(self, folder: str, uids: list[int]) -> None:
        if not uids:
            return
        ids = self._ids_for(folder, uids)
        self._check_bulk("удаление", self._account.bulk_delete(ids=ids, chunk_size=_FETCH_CHUNK))

    def _ids_for(self, folder: str, uids: list[int]) -> list[tuple[str, None]]:
        """Идентификаторы писем без ключа изменения: для переноса и
        удаления он не нужен, а устаревший ключ сервер отвергает."""
        missing = [uid for uid in uids if uid not in self._id_map]
        if missing:
            self.search_uids(folder)  # после перезапуска сопоставление ещё не заполнено
        return [(self._id_map[uid][0], None) for uid in uids if uid in self._id_map]

    @staticmethod
    def _check_bulk(action: str, results) -> None:
        """Письма, которых на сервере уже нет, считаются обработанными —
        цель (убрать их из папки) и так достигнута. Остальные ошибки
        поднимаются одной понятной ошибкой."""
        failures = []
        for result in results:
            if result is True or not isinstance(result, Exception):
                continue
            if _is_gone(result):
                _log.info("EWS: %s — письма на сервере уже нет (%s)", action, type(result).__name__)
                continue
            failures.append(result)
        if failures:
            raise RuntimeError(f"Exchange: {action} не удалось для {len(failures)} писем: {failures[0]}")

    def _register(self, item) -> int:
        # & 0x7FFFFFFF — держим uid в диапазоне обычного 32-битного
        # положительного int, как настоящие IMAP UID (некоторый код в
        # приложении сортирует/сравнивает uid как числа).
        uid = zlib.crc32(item.id.encode("utf-8")) & 0x7FFFFFFF
        self._id_map[uid] = (item.id, item.changekey)
        return uid

    def respond_to_meeting(self, folder: str, uid: int, participation: str) -> None:
        """Ответ на письмо-приглашение средствами Exchange (принять, под
        вопросом, отклонить). Сервер сам отметит ответ во встрече и сообщит
        организатору — как делает Outlook."""
        from redmail import ews_calendar

        item = self._get_item(uid, folder)
        ews_calendar.respond_to_item(item, participation)
        _log.info("EWS: ответ «%s» на приглашение %s/%d отправлен", participation, folder, uid)

    def _get_item(self, uid: int, folder: str | None = None):
        entry = self._id_map.get(uid)
        if entry is None and folder is not None and folder in self._folders_by_path:
            # После перезапуска программы сопоставление номеров писем с
            # идентификаторами Exchange пусто, пока папку не обойдут —
            # открыть письмо до этого было нельзя.
            self.search_uids(folder)
            entry = self._id_map.get(uid)
        if entry is None:
            raise MessageGoneError("Письма нет в папке на сервере — его удалили или перенесли")
        ews_id, _changekey = entry
        # Без ключа изменения: он устаревает при каждом изменении письма
        # (прочитано, маркер), и сервер отвечает, будто письма нет.
        (item,) = self._account.fetch(ids=[(ews_id, None)])
        if _is_gone(item):
            self._id_map.pop(uid, None)
            raise MessageGoneError("Письма нет на сервере — его удалили или перенесли")
        if isinstance(item, Exception):
            if type(item).__name__ == "ErrorMimeContentConversionFailed":
                (item,) = self._account.fetch(
                    ids=[(ews_id, None)],
                    only_fields=_SUMMARY_FIELDS + ("body", "attachments", "cc_recipients", "datetime_sent"),
                )
                if not isinstance(item, Exception):
                    item.mime_content = None
                    return item
            raise RuntimeError(f"Exchange: письмо не прочитано: {item}")
        return item

    def _to_summary(self, item) -> MessageSummary:
        uid = self._register(item)
        sender_name = ""
        sender_email = ""
        if item.sender is not None:
            sender_name = item.sender.name or ""
            sender_email = item.sender.email_address or ""
        marker_color = None
        for category in item.categories or []:
            if category in _COLOR_BY_CATEGORY:
                marker_color = _COLOR_BY_CATEGORY[category]
                break
        return MessageSummary(
            uid=uid,
            subject=item.subject or "",
            sender=sender_name or sender_email or "(неизвестно)",
            sender_email=sender_email,
            date=_local_date_text(item.datetime_received),
            message_id=item.message_id or "",
            has_attachments=bool(item.has_attachments),
            marker_color=marker_color,
            importance=_IMPORTANCE_MAP.get(item.importance, "normal"),
            is_read=bool(item.is_read),
            # Копии писем, отправленных через SMTP и положенных в «Отправленные»
            # сервером, приходят без списка получателей — остаётся строка
            # display_to с именами.
            to=_format_mailboxes(getattr(item, "to_recipients", None)) or (getattr(item, "display_to", "") or "").strip(),
            size=int(getattr(item, "size", 0) or 0),
        )


def send_message(session: EwsSession, message: OutgoingMessage) -> None:
    """Отправляет письмо через тот же EWS-аккаунт, что и чтение — для
    Exchange не нужен отдельный SMTP-релей и его отдельные логин/пароль,
    в отличие от IMAP-аккаунтов (см. smtp_client.send_message).

    ВАЖНО: .ics-вложения (приглашения на встречу) уходят как обычные
    файловые вложения — EWS не даёт пронести MIME-параметр method=REQUEST
    у вложения, как это делает smtp_client через email.message. Получатель
    сможет открыть .ics вручную, но встроенная кнопка "Принять/Отклонить"
    в почтовом клиенте получателя может не появиться, в отличие от
    отправки через SMTP. Полноценные встречи Exchange (CalendarItem) —
    отдельная, более крупная задача, здесь не реализована.
    """
    ews_message = EwsMessage(
        account=session._account,
        subject=message.subject,
        # Отредактированное форматирование из ComposeDialog (жалоба: "не
        # даёт установить какие-либо шрифты") — exchangelib отличает
        # HTML-тело от обычного текста через HTMLBody, а не по содержимому.
        body=HTMLBody(message.html_body) if message.html_body else message.body,
        to_recipients=[Mailbox(email_address=addr) for addr in message.to],
        cc_recipients=[Mailbox(email_address=addr) for addr in message.cc] if message.cc else None,
        bcc_recipients=[Mailbox(email_address=addr) for addr in message.bcc] if message.bcc else None,
    )
    for cid, (content_type, payload) in message.inline_images.items():
        ews_message.attach(
            FileAttachment(name=cid, content=payload, content_type=content_type, is_inline=True, content_id=cid)
        )
    for attachment in message.attachments:
        ews_message.attach(
            FileAttachment(name=attachment.filename, content=attachment.payload, content_type=attachment.content_type)
        )
    ews_message.send()
