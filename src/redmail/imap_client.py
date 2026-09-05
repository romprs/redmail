from __future__ import annotations

import functools
import imaplib
from dataclasses import dataclass, field
from email import message_from_bytes
from email.header import decode_header
from email.message import Message

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError

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

# Сентинел по умолчанию для set_marker(previous_color=...) — отличает "вызывающий
# код не знает текущий маркер" (безопасный медленный путь: снять все
# возможные keyword'ы) от "previous_color=None" (точно знает, что маркера не
# было — снимать нечего). Спутать их означало бы на реальном сервере либо
# лишние round trip'ы, либо оставленный висеть старый keyword.
UNKNOWN_MARKER = object()


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
    перехватываются — переподключение их не лечит, показываем как есть."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except (OSError, EOFError, imaplib.IMAP4.abort) as exc:
            try:
                self._reconnect()
            except Exception:
                raise exc from None  # переподключиться тоже не вышло — исходная ошибка нагляднее
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
        self._client = IMAPClient(account.host, port=account.port, ssl=account.use_ssl)
        self._login()
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

            gssapi_sasl.imap_sasl_login(self._client, self.account.host, self.account.username)
        else:
            self._client.login(self.account.username, self.account.password)

    def close(self) -> None:
        try:
            self._client.logout()
        except Exception:
            pass

    def _reconnect(self) -> None:
        self._client = IMAPClient(self.account.host, port=self.account.port, ssl=self.account.use_ssl)
        self._login()
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
        if previous_color is UNKNOWN_MARKER:
            to_remove = list(all_keywords)
        elif previous_color is None:
            to_remove = []  # маркера не было — снимать нечего, кроме самого нового keyword'а ниже не нужно
        else:
            to_remove = [MARKER_COLORS[previous_color]]

        if color is None:
            self._remove_flags_best_effort(uid, [b"\\Flagged", *to_remove])
            return
        keyword = MARKER_COLORS[color]
        self._remove_flags_best_effort(uid, [k for k in to_remove if k != keyword])
        try:
            self._client.add_flags([uid], [b"\\Flagged", keyword])
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
        если сервер поддерживает MOVE (RFC 6851), иначе COPY + удаление."""
        if not uids:
            return
        self._select(folder)
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
            return MessageContent(text=_decode_payload(message), **header_kwargs)
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

    for part in message.walk():
        if part.is_multipart():
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

    if text is None:
        text = "(письмо в формате HTML — предпросмотр текста недоступен)" if html is not None else "(нет текстового содержимого)"

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
    marker_color = next((_COLOR_BY_KEYWORD[f] for f in flags if f in _COLOR_BY_KEYWORD), None)
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
    return _disposition_is_attachment(structure)


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
