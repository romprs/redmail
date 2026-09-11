from __future__ import annotations

import functools
import imaplib
import threading
from dataclasses import dataclass, field
from email import message_from_bytes
from email.header import decode_header
from email.message import Message

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError

from redmail.applog import get_logger

_log = get_logger("imap")

_HEADER_FIELDS = "BODY.PEEK[HEADER.FIELDS (IMPORTANCE X-PRIORITY)]"

# Флаг \Flagged ставим всегда вместе с цветом — так другие IMAP-клиенты
# (Thunderbird, сам Outlook по IMAP) увидят письмо помеченным, даже если не
# понимают наш собственный keyword с цветом. $-префикс — общепринятое
# соглашение для нестандартных keyword-флагов (как $Forwarded, $MDNSent).
MARKER_COLORS: dict[str, bytes] = {
    "red": b"$RedMailRed",
    "orange": b"$RedMailOrange",
    "yellow": b"$RedMailYellow",
    "green": b"$RedMailGreen",
    "blue": b"$RedMailBlue",
    "purple": b"$RedMailPurple",
}
_COLOR_BY_KEYWORD = {v: k for k, v in MARKER_COLORS.items()}


def split_markers(value: str | None) -> list[str]:
    """Несколько маркеров на письме (пожелание: "на письмо можно поставить
    несколько маркеров") хранятся в том же поле marker_color через запятую
    ("red,blue") — так не меняются схема кэша/архива и все места, где это
    поле просто прокидывается дальше; разбирают его только те, кому нужны
    отдельные цвета (иконка, фильтр, меню, IMAP-флаги)."""
    return [color for color in (value or "").split(",") if color]


def join_markers(colors) -> str | None:
    ordered: list[str] = []
    for color in colors:
        if color and color not in ordered:
            ordered.append(color)
    return ",".join(ordered) or None

# Таймаут одной операции на сокете IMAP (секунды). Не ограничивает
# длительность загрузки большого письма целиком — только паузу между
# порциями данных от сервера.
SOCKET_TIMEOUT = 120

# Сентинел по умолчанию для set_marker(previous_color=...) — отличает "вызывающий
# код не знает текущий маркер" (безопасный медленный путь: снять все
# возможные keyword'ы) от "previous_color=None" (точно знает, что маркера не
# было — снимать нечего). Спутать их означало бы на реальном сервере либо
# лишние round trip'ы, либо оставленный висеть старый keyword.
UNKNOWN_MARKER = object()


def _is_recoverable_by_reconnect(exc: Exception) -> bool:
    """Протокольные ошибки (imaplib.IMAP4.error = IMAPClientError: команда
    сервером понята, но отвергнута), которые всё же лечатся
    переподключением — в отличие от остальных, см. _reconnecting:

    * "command SELECT/... illegal in state NONAUTH" — сервер молча
      разлогинил сессию после долгого простоя (жалоба: "после долгого
      простоя выдаёт... лечится перезапуском"), но САМ TCP-сокет при этом
      мог и не порваться, поэтому это не OSError/EOFError/IMAP4.abort;
    * "[UNAVAILABLE] Failed to open mailbox" / "Service temporarily
      unavailable" (RFC 5530: временный отказ подсистемы сервера — жалоба:
      "после сбоя сервера или принудительного простоя не восстанавливается
      подключение, обновление даёт ошибку") — сессия после такого сбоя
      сервера обычно уже невалидна, свежее соединение проходит."""
    text = str(exc)
    if "illegal in state" in text and "NONAUTH" in text:
        return True
    return "[UNAVAILABLE]" in text.upper()


def _reconnecting(method):
    """После простоя реальный IMAP-сервер молча рвёт TCP-соединение (никто
    не обязан держать сессию вечно — RFC 3501 не гарантирует этого), и
    следующая же операция падала с сырой сетевой ошибкой (жалоба
    пользователя: "после простоя часто выдаёт ошибку подключения").
    OSError — общий предок и для обрыва соединения, и для TLS-ошибок в
    современном Python.

    imaplib.IMAP4.abort — ОТДЕЛЬНО от OSError, хотя семантически это тот
    же случай: сам imaplib документирует его как "Service errors - close
    and retry" (imaplib.py), и на практике так оборачивает разрыв TLS
    ("EOF occurred in violation of protocol") при чтении строки ответа —
    `class error(Exception)` в стандартной библиотеке НЕ наследуется от
    OSError, так что раньше это вообще не попадало под переподключение
    (жалоба: "периодически выдаёт ошибки" при обновлении/чтении письма —
    ошибка показывалась как есть с первого же раза, без единой попытки
    восстановить соединение). Настоящие протокольные ошибки
    (IMAPClientError на команду, которую сервер понял, но отверг) НЕ
    перехватываются — переподключение их не лечит, показываем как есть,
    КРОМЕ "illegal in state NONAUTH" — см. _is_stale_session_error."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        # Одно соединение — одна команда за раз. Тело письма грузится в
        # фоновом потоке, а отметка "прочитано"/маркер/следующий fetch
        # могли уйти в тот же сокет из другого потока параллельно; imaplib
        # к этому не готов — ответы перемешиваются, и один из потоков ждёт
        # своего ответа вечно (жалоба: "подвисает при переходе от письма к
        # письму на загрузке письма"). RLock — потому что декорированные
        # методы вызывают друг друга.
        with self._lock:
            try:
                return method(self, *args, **kwargs)
            except (OSError, EOFError, imaplib.IMAP4.abort) as exc:
                _log.warning("IMAP %s: %s — обрыв соединения (%s), переподключение", self.account.host, method.__name__, exc)
                try:
                    self._reconnect()
                except Exception as reconnect_exc:
                    _log.error("IMAP %s: переподключение не удалось: %s", self.account.host, reconnect_exc)
                    raise exc from None  # переподключиться тоже не вышло — исходная ошибка нагляднее
                return method(self, *args, **kwargs)
            except imaplib.IMAP4.error as exc:
                if not _is_recoverable_by_reconnect(exc):
                    _log.error("IMAP %s: %s — ошибка протокола: %s", self.account.host, method.__name__, exc)
                    raise
                _log.warning("IMAP %s: %s — сессия недействительна (%s), переподключение", self.account.host, method.__name__, exc)
                try:
                    self._reconnect()
                except Exception as reconnect_exc:
                    _log.error("IMAP %s: переподключение не удалось: %s", self.account.host, reconnect_exc)
                    raise exc from None
                return method(self, *args, **kwargs)

    return wrapper


@dataclass
class Account:
    host: str
    username: str
    password: str
    port: int = 993
    use_ssl: bool = True
    # "password" — обычный LOGIN; "kerberos" — SSO для почтового сервера в
    # домене: аутентификация идёт по Kerberos-билету, который ОС уже
    # выдала при входе пользователя в домен (RED OS + SSSD), пароль в
    # приложении не хранится и не используется (см. gssapi_sasl.py).
    auth_type: str = "password"
    # Необязательный keytab как источник билета для SSO (вместо билета из
    # системного кэша) и principal, для которого он выписан — см.
    # gssapi_sasl.acquire_credentials.
    keytab_path: str = ""
    principal: str = ""


@dataclass
class FolderInfo:
    name: str
    delimiter: str


@dataclass
class MessageSummary:
    uid: int
    subject: str
    sender: str
    sender_email: str
    date: str
    message_id: str
    has_attachments: bool = False
    marker_color: str | None = None
    importance: str = "normal"  # "high" | "normal" | "low"
    is_read: bool = False
    # Только для списка "Отправленные" — там "От кого" всегда сам
    # пользователь, бесполезная колонка (жалоба: "в отправленных нет поля
    # адресат, невозможно понять кому писали"). Достаётся бесплатно — те же
    # данные ENVELOPE, что уже фетчатся для sender, просто ещё одно поле.
    to: str = ""
    # Стандартный IMAP-флаг \Answered — раньше нигде не читался и не
    # проставлялся (жалоба: "если мы ответили на письмо, это никак не
    # отражается, нужен какой-то признак").
    is_answered: bool = False


@dataclass
class Attachment:
    filename: str
    content_type: str
    payload: bytes

    @property
    def size(self) -> int:
        return len(self.payload)


@dataclass
class MessageContent:
    text: str
    attachments: list[Attachment] = field(default_factory=list)
    html: str = ""
    # Content-Id (без угловых скобок) -> (content_type, данные) — картинки,
    # встроенные в HTML через <img src="cid:...">, а не обычные вложения.
    inline_images: dict[str, tuple[str, bytes]] = field(default_factory=dict)
    # Реквизиты письма (тема/отправитель/получатели) — раньше нигде не
    # показывались при просмотре письма (жалоба: "невидно его реквизитов
    # (тема, отправитель, адресаты)"). MessageSummary уже даёт subject/
    # sender для списка писем, но не даёт To/Cc — они здесь.
    subject: str = ""
    from_: str = ""
    to: str = ""
    cc: str = ""
    bcc: str = ""


class ImapSession:
    """Одно живое IMAP-соединение на всё время работы с ящиком.

    Открывать новое соединение (TCP + TLS + логин) на каждый клик по папке
    или письму — секунды задержки на медленной сети. Здесь соединение
    держится, пока пользователь не переподключится или не закроет окно.
    """

    def __init__(self, account: Account):
        self.account = account
        self._lock = threading.RLock()
        self._client = self._new_client()
        try:
            self._login()
        except Exception as exc:
            _log.error("IMAP %s:%s: вход не удался (%s, %s): %s", account.host, account.port, account.username, account.auth_type, exc)
            raise
        self._selected_folder: str | None = None
        self._selected_exists = 0
        self._raw_folders: list[tuple] = []

    def _login(self) -> None:
        if self.account.auth_type == "kerberos":
            # Импорт внутри функции: пакет gssapi требует системных
            # библиотек Kerberos, которых нет на части машин (Windows,
            # окружения без домена) — обычный пароль не должен ломаться
            # из-за отсутствия зависимости, нужной только для SSO.
            from redmail import gssapi_sasl

            gssapi_sasl.imap_sasl_login(
                self._client,
                self.account.host,
                self.account.username,
                keytab_path=self.account.keytab_path,
                principal=self.account.principal,
            )
        else:
            self._client.login(self.account.username, self.account.password)
        _log.info("IMAP %s:%s: вход выполнен (%s, %s)", self.account.host, self.account.port, self.account.username, self.account.auth_type)

    def _new_client(self) -> IMAPClient:
        # Таймаут на сокете обязателен: без него любое зависшее чтение
        # (сервер молча перестал отвечать, рассинхрон ответов) блокирует
        # поток навсегда — с таймаутом это OSError, которую _reconnecting
        # лечит переподключением и повтором.
        return IMAPClient(self.account.host, port=self.account.port, ssl=self.account.use_ssl, timeout=SOCKET_TIMEOUT)

    def close(self) -> None:
        with self._lock:
            try:
                self._client.logout()
            except Exception:
                pass

    def _reconnect(self) -> None:
        self._client = self._new_client()
        try:
            self._login()
        except Exception as exc:
            _log.error("IMAP %s: повторный вход не удался: %s", self.account.host, exc)
            raise
        if self._selected_folder is not None:
            # Кое-что из вызывающего кода (fetch_summaries) не делает
            # собственный SELECT — полагается, что папка уже выбрана
            # предыдущим folder_message_count()/_select(). Восстанавливаем
            # это состояние сразу, иначе повтор упал бы снова, уже по
            # другой причине (ничего не выбрано).
            self._client.select_folder(self._selected_folder, readonly=False)

    def __enter__(self) -> "ImapSession":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    @_reconnecting
    def list_folders(self) -> list[FolderInfo]:
        # Сырой ответ запоминаем — из него же достаём папку "Корзина" в
        # trash_folder(), без второго похода на сервер (find_special_folder
        # библиотеки сам заново вызывает list_folders).
        self._raw_folders = self._client.list_folders()
        return [
            FolderInfo(name=name, delimiter=(delimiter or b"/").decode("ascii", errors="replace"))
            for flags, delimiter, name in self._raw_folders
            if b"\\Noselect" not in flags
        ]

    @_reconnecting
    def create_folder(self, name: str) -> None:
        self._client.create_folder(name)

    @_reconnecting
    def rename_folder(self, old_name: str, new_name: str) -> None:
        self._client.rename_folder(old_name, new_name)

    def trash_folder(self) -> str | None:
        return self._special_folder(b"\\Trash", ("trash", "корзин"))

    def sent_folder(self) -> str | None:
        return self._special_folder(b"\\Sent", ("sent", "отправленн"))

    def drafts_folder(self) -> str | None:
        return self._special_folder(b"\\Drafts", ("draft", "черновик"))

    def _special_folder(self, special_use_flag: bytes, name_hints: tuple[str, ...]) -> str | None:
        # Сначала — SPECIAL-USE (RFC 6154), это надёжно, сервер сам сказал,
        # какая папка какая. Но объявляет его не каждый реальный сервер —
        # без запасного варианта по имени такая папка оставалась вовсе не
        # распознанной: письмо можно было открыть, но не отправить (жалоба
        # "из черновика не даёт отправить" — двойной клик по письму в
        # "Черновиках" тихо считал, что это просто обычная папка, и
        # открывал письмо на просмотр, а не на редактирование).
        for flags, _delimiter, name in self._raw_folders:
            if special_use_flag in flags:
                return name
        for _flags, _delimiter, name in self._raw_folders:
            lowered = name.lower()
            if any(hint in lowered for hint in name_hints):
                return name
        return None

    @_reconnecting
    def append_message(self, folder: str, raw: bytes, *, flags: tuple[bytes, ...] = ()) -> None:
        """Кладёт готовое (уже собранное) сообщение в папку напрямую,
        минуя SMTP — для "Отправленные" (сервер сам не всегда сохраняет
        копию исходящих) и "Черновики" (письмо, которое никуда не
        отправлялось)."""
        self._client.append(folder, raw, flags=flags)

    @_reconnecting
    def folder_message_count(self, folder: str) -> int:
        """SELECT папку, вернуть общее число писем в ней (EXISTS).

        Дешёвая операция (без сканирования, в отличие от SEARCH) — на ней
        удобно проверять, изменилось ли что-то в папке с прошлого раза,
        прежде чем платить за полный FETCH сводок (см. CachedMailbox).
        """
        status = self._client.select_folder(folder, readonly=False)
        self._selected_folder = folder
        self._selected_exists = status[b"EXISTS"]
        return self._selected_exists

    @_reconnecting
    def folder_unseen_count(self, folder: str) -> int:
        """Число непрочитанных писем в папке — жалоба: "подписывать
        количество писем" рядом с папкой в дереве. STATUS, а не
        select_folder/SEARCH: не переключает "текущую" папку сессии
        (folder_message_count это делает) и не сканирует письма — можно
        дёшево опросить сразу много папок подряд при построении дерева."""
        status = self._client.folder_status(folder, ["UNSEEN"])
        for key, value in status.items():
            key_name = key.decode("ascii", errors="replace") if isinstance(key, bytes) else str(key)
            if key_name.upper() == "UNSEEN":
                return int(value)
        return 0

    @_reconnecting
    def fetch_summaries(self, limit: int = 50) -> list[MessageSummary]:
        """Сводки последних `limit` писем уже выбранной папки.

        Требует, чтобы перед этим была вызвана folder_message_count —
        отдельного SELECT здесь больше нет.
        """
        total = self._selected_exists
        if total == 0:
            return []
        start = max(1, total - limit + 1)

        # Порядковые номера, а не UID — иначе пришлось бы всё равно узнавать
        # реальные UID через SEARCH. UID запрашиваем отдельным полем: он
        # возвращается независимо от режима нумерации.
        self._client.use_uid = False
        try:
            response = self._client.fetch(
                f"{start}:*", ["ENVELOPE", "UID", "FLAGS", "BODYSTRUCTURE", _HEADER_FIELDS]
            )
        finally:
            self._client.use_uid = True

        by_seq = sorted(response.items(), key=lambda item: item[0], reverse=True)
        return [_to_summary(data) for _seq, data in by_seq]

    def fetch_folder_summaries(self, folder: str = "INBOX", limit: int = 50) -> list[MessageSummary]:
        self.folder_message_count(folder)
        return self.fetch_summaries(limit)

    @_reconnecting
    def search_uids(self, folder: str, *, before=None) -> list[int]:
        """UID всех писем папки (или только тех, что старше даты `before`) —
        в отличие от fetch_summaries/fetch_folder_summaries это не
        ограничено последними `limit` письмами: нужно для массовой
        выгрузки в архив (вся папка / всё до даты), где важна ПОЛНАЯ папка,
        а не то, что сейчас показано в таблице."""
        self._select(folder)
        criteria = ["BEFORE", before.strftime("%d-%b-%Y")] if before else "ALL"
        return list(self._client.search(criteria))

    def fetch_message_content(self, folder: str, uid: int) -> MessageContent:
        return extract_content(message_from_bytes(self.fetch_message_raw(folder, uid)))

    @_reconnecting
    def fetch_message_raw(self, folder: str, uid: int) -> bytes:
        """Полный RFC 822 письма как есть — нужен для выгрузки в архив без
        потерь (в отличие от fetch_message_content, который уже разобрал бы
        текст/вложения и потерял бы всё остальное, например точные заголовки)."""
        self._select(folder)
        response = self._client.fetch([uid], ["BODY.PEEK[]"])
        return response[uid][b"BODY[]"]

    @_reconnecting
    def set_read(self, folder: str, uid: int, read: bool) -> None:
        self._select(folder)
        if read:
            self._client.add_flags([uid], [b"\\Seen"])
        else:
            self._client.remove_flags([uid], [b"\\Seen"])

    @_reconnecting
    def set_answered(self, folder: str, uid: int) -> None:
        """Ставит стандартный флаг \\Answered на письмо, на которое только
        что был отправлен ответ — не снимается обратно (как и в других
        почтовых клиентах, "ответили" необратимо для этого письма)."""
        self._select(folder)
        self._client.add_flags([uid], [b"\\Answered"])

    @_reconnecting
    def set_marker(self, folder: str, uid: int, color: str | None, *, previous_color=UNKNOWN_MARKER) -> None:
        """Ставит/снимает \\Flagged + наш цветной keyword-флаг.

        Gmail принимает произвольные keyword-флаги без вопросов, но
        реальный корпоративный сервер (обнаружено на VK Mail) отвечает
        "BAD [PARSE] Unable to parse flag" на STORE с несколькими нашими
        keyword'ами разом — по всей видимости, сервер не разрешает
        произвольные (не объявленные в PERMANENTFLAGS) keyword-флаги
        вообще. Поэтому: (1) снимаем/ставим keyword'ы по одному, а не
        разом — один отклонённый не должен мешать остальным; (2) если
        сервер в принципе не принимает цветной keyword, тихо откатываемся
        на стандартный \\Flagged, чтобы разметка не ломалась полностью
        из-за того, что сервер не умеет в цвета.

        `previous_color` — если вызывающий код уже знает текущий маркер
        (обычно да — он же его и показывает в таблице), снимаем ТОЛЬКО
        этот один keyword вместо того, чтобы вслепую пытаться снять все
        6 возможных цветов на каждую смену маркера. На реальном сервере с
        заметной сетевой задержкой это была разница между 1-2 round trip'ами
        и 6 (жалоба: "маркер на письмах устанавливается очень долго").
        Если previous_color не передан — поведение как раньше (безопасный,
        но медленный вариант "снять всё возможное")."""
        if previous_color is not UNKNOWN_MARKER and previous_color == color:
            return  # уже в нужном состоянии — нечего менять, даже SELECT не нужен
        self._select(folder)
        all_keywords = list(MARKER_COLORS.values())
        # `color`/`previous_color` — один цвет или несколько через запятую
        # (см. split_markers): на письме может быть несколько маркеров.
        new_keywords = [MARKER_COLORS[c] for c in split_markers(color) if c in MARKER_COLORS]
        if previous_color is UNKNOWN_MARKER:
            to_remove = list(all_keywords)
        elif previous_color is None:
            to_remove = []  # маркера не было — снимать нечего, кроме самого нового keyword'а ниже не нужно
        else:
            to_remove = [MARKER_COLORS[c] for c in split_markers(previous_color) if c in MARKER_COLORS]

        if not new_keywords:
            self._remove_flags_best_effort(uid, [b"\\Flagged", *to_remove])
            return
        self._remove_flags_best_effort(uid, [k for k in to_remove if k not in new_keywords])
        try:
            self._client.add_flags([uid], [b"\\Flagged", *new_keywords])
        except IMAPClientError:
            self._client.add_flags([uid], [b"\\Flagged"])

    def _remove_flags_best_effort(self, uid: int, flags: list[bytes]) -> None:
        for flag in flags:
            try:
                self._client.remove_flags([uid], [flag])
            except IMAPClientError:
                pass  # флаг и так не поддерживается/не был установлен — не критично

    @_reconnecting
    def move_messages(self, folder: str, uids: list[int], target_folder: str) -> None:
        """Переносит письма в другую папку (например, в корзину) — атомарно,
        если сервер поддерживает MOVE (RFC 6851), иначе COPY + удаление.

        Если целевой папки ещё нет на сервере — создаёт её и повторяет
        один раз, вместо того чтобы падать с сырым "[TRYCREATE] Folder
        does not exist" (жалоба на правила сортировки почты: правило
        может ссылаться на папку, которую пользователь только собирался
        создать, а не создал заранее руками)."""
        if not uids:
            return
        self._select(folder)
        try:
            self._move_or_copy(uids, target_folder)
        except IMAPClientError as exc:
            if "TRYCREATE" not in str(exc).upper():
                raise
            self._client.create_folder(target_folder)
            self._move_or_copy(uids, target_folder)

    def _move_or_copy(self, uids: list[int], target_folder: str) -> None:
        if self._client.has_capability("MOVE"):
            self._client.move(uids, target_folder)
        else:
            self._client.copy(uids, target_folder)
            self._client.delete_messages(uids)
            self._client.expunge()

    @_reconnecting
    def delete_messages(self, folder: str, uids: list[int]) -> None:
        """Безвозвратное удаление (Shift+Удалить, либо удаление из самой корзины)."""
        if not uids:
            return
        self._select(folder)
        self._client.delete_messages(uids)
        self._client.expunge()

    def _select(self, folder: str) -> None:
        # Папка уже открыта этой же сессией — второй SELECT только теряет время.
        if self._selected_folder != folder:
            self._client.select_folder(folder, readonly=False)
            self._selected_folder = folder


def _iter_body_parts(message: Message):
    """Как Message.walk(), но НЕ спускается внутрь message/rfc822
    (пересланное письмо целиком как вложение) — Message.walk() у стандартной
    библиотеки считает такую часть "multipart" (её единственный payload —
    вложенный Message-объект) и лезет внутрь неё саму же, вместо того
    чтобы отдать её как один цельный узел вызывающему коду."""
    if message.get_content_type() == "message/rfc822":
        yield message
        return
    if message.is_multipart():
        for part in message.get_payload():
            yield from _iter_body_parts(part)
    else:
        yield message


def extract_content(message: Message) -> MessageContent:
    # Заголовки живут на верхнем уровне сообщения независимо от того,
    # multipart оно или нет — читаем их один раз, а не в каждой из веток
    # ниже (жалоба: "при просмотре письма невидно его реквизитов"), и
    # передаём во все ветки через один хелпер, а не повторяя 5 kwargs в
    # каждом return.
    header_kwargs = dict(
        subject=_decode_header_text(message.get("Subject")),
        from_=_decode_header_text(message.get("From")),
        to=_decode_header_text(message.get("To")),
        cc=_decode_header_text(message.get("Cc")),
        bcc=_decode_header_text(message.get("Bcc")),
    )

    if not message.is_multipart():
        if message.get_content_type() == "text/plain":
            # "or" — не просто "если текста вообще не было" (None), но и
            # "если он декодировался в пустую строку" (например,
            # get_payload(decode=True) не осилил конкретную кодировку и
            # вернул None → _decode_payload даёт "") — иначе панель чтения
            # оставалась молча пустой безо всякой подсказки, хотя реальный
            # веб-интерфейс почты то же письмо показывал с текстом (жалоба:
            # "некоторые письма открываются так [пусто], а на самом деле
            # они такие [с текстом]").
            return MessageContent(text=_decode_payload(message) or "(нет текстового содержимого)", **header_kwargs)
        if message.get_content_type() == "text/html":
            return MessageContent(
                text="(письмо в формате HTML — предпросмотр текста недоступен)",
                html=_decode_payload(message),
                **header_kwargs,
            )
        if message.get_content_type() == "text/calendar":
            return MessageContent(text="", attachments=[_calendar_attachment(message)], **header_kwargs)
        return MessageContent(
            text="(письмо в формате HTML — предпросмотр текста недоступен)", **header_kwargs
        )

    text: str | None = None
    html: str | None = None
    attachments: list[Attachment] = []
    inline_images: dict[str, tuple[str, bytes]] = {}

    for part in _iter_body_parts(message):
        if part.get_content_type() == "message/rfc822":
            # Пересланное письмо целиком как вложение (Outlook и другие
            # клиенты часто пересылают именно так, без явного
            # Content-Disposition: attachment на этой части) — раньше
            # message.walk() спускался ВНУТРЬ него (Python считает
            # message/rfc822 "multipart", раз её единственный payload —
            # вложенный Message), и текст/HTML пересылаемого письма
            # подмешивался в тело исходного вместо того, чтобы стать одним
            # отдельным вложением — жалоба: "у письма есть вложение, но
            # его нет в просмотре и при открытии".
            nested = part.get_payload(0)
            nested_subject = _decode_header_text(nested.get("Subject")) if nested else ""
            filename = _decode_filename(part.get_filename()) or f"{nested_subject or 'Пересланное письмо'}.eml"
            attachments.append(
                Attachment(
                    filename=filename,
                    content_type="message/rfc822",
                    payload=nested.as_bytes() if nested else (part.get_payload(decode=True) or b""),
                )
            )
            continue

        filename = _decode_filename(part.get_filename())
        content_type = part.get_content_type()
        content_disposition = part.get_content_disposition()  # 'attachment' | 'inline' | None
        content_id = (part.get("Content-Id") or "").strip().strip("<>")

        # Картинка со своим Content-Id — то, на что ссылается <img
        # src="cid:..."> в HTML-теле, а не отдельное вложение для скачивания
        # (даже если у неё есть имя файла и/или Content-Disposition).
        if content_id and content_type.startswith("image/"):
            inline_images[content_id] = (content_type, part.get_payload(decode=True) or b"")
            continue

        # text/plain и text/html — кандидаты в само тело письма, а не во
        # вложение, даже если у части задан Content-Type: ...; name="..."
        # (part.get_filename() читает и его, не только
        # Content-Disposition: filename=) — реальные HTML-рассылки
        # (например, от Авито) так подписывают HTML-часть письма без
        # всякого намерения сделать её вложением. Раньше bool(filename)
        # ниже срабатывал именно на этом и уводил всё письмо во вложение,
        # оставляя тело пустым (жалоба: "письма в формате html не
        # просматриваются"). Вложением текстовая часть считается только
        # при явном Content-Disposition: attachment.
        is_body_part = content_type in ("text/plain", "text/html") and content_disposition != "attachment"

        # text/calendar (RFC 5546 iTIP-приглашение) сохраняем как вложение
        # всегда — не только когда отправитель явно проставил
        # Content-Disposition: attachment/filename (не все серверы это
        # делают), иначе приглашение молча потеряется.
        is_attachment = not is_body_part and (
            bool(filename) or content_disposition == "attachment" or content_type == "text/calendar"
        )
        if is_attachment:
            attachments.append(
                _calendar_attachment(part) if content_type == "text/calendar" else Attachment(
                    filename=filename or "(без имени)",
                    content_type=content_type,
                    payload=part.get_payload(decode=True) or b"",
                )
            )
            continue

        if content_type == "text/plain" and text is None:
            text = _decode_payload(part)
        elif content_type == "text/html" and html is None:
            html = _decode_payload(part)

    if not text:
        # "not text", а не "text is None" — часть text/plain могла найтись,
        # но не осилить декодирование (get_payload(decode=True) вернул
        # None → _decode_payload дала "") и оставить панель чтения молча
        # пустой без единой подсказки (жалоба: "некоторые письма
        # открываются так [пусто], а на самом деле они такие [с текстом]").
        text = "(письмо в формате HTML — предпросмотр текста недоступен)" if html else "(нет текстового содержимого)"

    return MessageContent(
        text=text,
        attachments=attachments,
        html=html or "",
        inline_images=inline_images,
        **header_kwargs,
    )


def _calendar_attachment(part: Message) -> Attachment:
    return Attachment(
        filename=_decode_filename(part.get_filename()) or "invite.ics",
        content_type="text/calendar",
        payload=part.get_payload(decode=True) or b"",
    )


def _decode_header_text(value: str | None) -> str:
    """Как _decode_filename, но для обычных текстовых заголовков (Subject/
    From/To/Cc) — не все сервера сворачивают RFC 2047 encoded-word до
    ENVELOPE, который парсит imapclient (см. _decode_subject); здесь тот
    же случай, но для содержимого письма, разбираемого через email.message."""
    if not value or "=?" not in value:
        return value or ""
    return _decode_rfc2047(value.encode("ascii", errors="replace"))


def _decode_filename(filename: str | None) -> str | None:
    """get_filename() отдаёт значение как есть — некоторые сервера (в т.ч.
    встречалось от Exchange) кодируют имя файла в RFC 2047 (=?utf-8?B?...?=)
    вместо RFC 2231, которое email.message понимает само. Без декодирования
    имя вложения показывалось бы пользователю нечитаемой кодированной
    строкой вместо настоящего имени файла."""
    if not filename or "=?" not in filename:
        return filename
    return _decode_rfc2047(filename.encode("ascii", errors="replace"))


def _decode_payload(part: Message) -> str:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _to_summary(data: dict) -> MessageSummary:
    envelope = data[b"ENVELOPE"]
    sender_display, sender_email = _format_address(envelope.from_)
    message_id = envelope.message_id
    flags = data.get(b"FLAGS", ())
    marker_color = join_markers(_COLOR_BY_KEYWORD[f] for f in flags if f in _COLOR_BY_KEYWORD)
    if marker_color is None and b"\\Flagged" in flags:
        # Жалоба: "не сохраняется проставленный маркер, через какое-то
        # время пропадает" — сервер (VK Mail) не хранит произвольные
        # keyword-флаги ($RedMailRed и т.п.: их нет в PERMANENTFLAGS),
        # принимает их только на время сессии и молча теряет, а стандартный
        # \Flagged хранит. Раньше без keyword'а маркер считался снятым —
        # теперь \Flagged без цвета = маркер по умолчанию; конкретный цвет
        # при этом восстанавливается из локального кэша (см.
        # CachedMailbox.refresh_folder).
        marker_color = "red"
    return MessageSummary(
        uid=data[b"UID"],
        subject=_decode_subject(envelope.subject),
        sender=sender_display,
        sender_email=sender_email,
        date=envelope.date.strftime("%Y-%m-%d %H:%M") if envelope.date else "",
        message_id=message_id.decode("ascii", errors="replace") if message_id else "",
        has_attachments=_body_has_attachment(data[b"BODYSTRUCTURE"]) if b"BODYSTRUCTURE" in data else False,
        marker_color=marker_color,
        importance=parse_importance(message_from_bytes(data.get(b"BODY[HEADER.FIELDS (IMPORTANCE X-PRIORITY)]", b""))),
        is_read=b"\\Seen" in flags,
        to=_format_address_list(getattr(envelope, "to", None)),
        is_answered=b"\\Answered" in flags,
    )


def _body_has_attachment(structure) -> bool:
    """Смотрит в BODYSTRUCTURE, не скачивая тело письма целиком.

    BODYSTRUCTURE — сырая вложенная структура по RFC 3501 (см. imapclient
    response_types.BodyData), без готового поля "это вложение". Ищем
    Content-Disposition: attachment паттерн-мэтчингом (2-элементный кортеж
    вида (b'ATTACHMENT', (...))), а не по фиксированному индексу — точная
    позиция disposition в кортеже "плавает" в зависимости от типа part'а
    (у text/* и message/rfc822 есть дополнительные поля перед ней).
    """
    if structure.is_multipart:
        parts, rest = structure[0], structure[1:]
        if _disposition_is_attachment(rest):
            return True
        return any(_body_has_attachment(part) for part in parts)
    # message/rfc822 (пересланное письмо целиком) — общепринято вложение
    # само по себе, независимо от явного Content-Disposition: attachment
    # (многие клиенты, включая Outlook, его не выставляют на такой части) —
    # без этого скрепка в списке писем не показывалась вовсе (жалоба:
    # "у письма есть вложение, но его нет в просмотре и при открытии").
    if len(structure) >= 2 and _field_upper(structure[0]) == "MESSAGE" and _field_upper(structure[1]) == "RFC822":
        return True
    return _disposition_is_attachment(structure)


def _field_upper(value) -> str:
    if isinstance(value, bytes):
        return value.decode("ascii", errors="replace").upper()
    return str(value).upper()


def _disposition_is_attachment(fields) -> bool:
    for value in fields:
        if isinstance(value, (tuple, list)) and len(value) == 2 and isinstance(value[0], bytes):
            if value[0].upper() == b"ATTACHMENT":
                return True
    return False


def parse_importance(headers: Message) -> str:
    """Классифицирует важность письма по заголовкам Importance/X-Priority.

    Публичная — используется и для IMAP (заголовки приходят отдельным полем
    FETCH), и для archive_store (заголовки уже есть в разобранном письме)."""
    importance = (headers.get("Importance") or "").strip().lower()
    if importance in ("high", "urgent"):
        return "high"
    if importance == "low":
        return "low"
    priority = (headers.get("X-Priority") or "").strip()
    if priority[:1] in ("1", "2"):
        return "high"
    if priority[:1] in ("4", "5"):
        return "low"
    return "normal"


def _decode_subject(raw: bytes | None) -> str:
    if not raw:
        return "(без темы)"
    return _decode_rfc2047(raw)


def _decode_rfc2047(raw: bytes) -> str:
    # Имена отправителей и темы писем сервер отдаёт как есть — они бывают
    # в кодированных словах RFC 2047 (=?utf-8?B?...?=), а не только сырым UTF-8.
    parts = decode_header(raw.decode("ascii", errors="replace"))
    return "".join(
        chunk.decode(encoding or "utf-8", errors="replace") if isinstance(chunk, bytes) else chunk
        for chunk, encoding in parts
    )


def _format_address_list(addresses) -> str:
    """Все адреса списка (To/Cc), через запятую — используется только для
    отображения в списке писем, не для машинного разбора (см.
    _parse_recipient_list в main_window.py для этого)."""
    if not addresses:
        return ""
    parts = []
    for address in addresses:
        mailbox = address.mailbox.decode("utf-8", errors="replace") if address.mailbox else ""
        host = address.host.decode("utf-8", errors="replace") if address.host else ""
        email = f"{mailbox}@{host}" if mailbox and host else mailbox
        name = _decode_rfc2047(address.name) if address.name else ""
        parts.append(f"{name} <{email}>" if name and email else (email or name))
    return ", ".join(parts)


def _format_address(addresses) -> tuple[str, str]:
    """Возвращает (отображаемое имя, email-адрес) первого адреса в списке."""
    if not addresses:
        return "(неизвестно)", ""
    address = addresses[0]
    mailbox = address.mailbox.decode("utf-8", errors="replace") if address.mailbox else ""
    host = address.host.decode("utf-8", errors="replace") if address.host else ""
    email = f"{mailbox}@{host}" if mailbox and host else mailbox
    if address.name:
        return _decode_rfc2047(address.name), email
    return (email or "(неизвестно)"), email
