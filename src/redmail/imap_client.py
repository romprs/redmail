from __future__ import annotations

from dataclasses import dataclass, field
from email import message_from_bytes
from email.header import decode_header
from email.message import Message

from imapclient import IMAPClient

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


@dataclass
class Account:
    host: str
    username: str
    password: str
    port: int = 993
    use_ssl: bool = True


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


class ImapSession:
    """Одно живое IMAP-соединение на всё время работы с ящиком.

    Открывать новое соединение (TCP + TLS + логин) на каждый клик по папке
    или письму — секунды задержки на медленной сети. Здесь соединение
    держится, пока пользователь не переподключится или не закроет окно.
    """

    def __init__(self, account: Account):
        self.account = account
        self._client = IMAPClient(account.host, port=account.port, ssl=account.use_ssl)
        self._client.login(account.username, account.password)
        self._selected_folder: str | None = None
        self._selected_exists = 0
        self._raw_folders: list[tuple] = []

    def close(self) -> None:
        try:
            self._client.logout()
        except Exception:
            pass

    def __enter__(self) -> "ImapSession":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

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

    def trash_folder(self) -> str | None:
        for flags, _delimiter, name in self._raw_folders:
            if b"\\Trash" in flags:
                return name
        return None

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

    def fetch_message_content(self, folder: str, uid: int) -> MessageContent:
        return extract_content(message_from_bytes(self.fetch_message_raw(folder, uid)))

    def fetch_message_raw(self, folder: str, uid: int) -> bytes:
        """Полный RFC 822 письма как есть — нужен для выгрузки в архив без
        потерь (в отличие от fetch_message_content, который уже разобрал бы
        текст/вложения и потерял бы всё остальное, например точные заголовки)."""
        self._select(folder)
        response = self._client.fetch([uid], ["BODY.PEEK[]"])
        return response[uid][b"BODY[]"]

    def set_marker(self, folder: str, uid: int, color: str | None) -> None:
        self._select(folder)
        all_keywords = list(MARKER_COLORS.values())
        if color is None:
            self._client.remove_flags([uid], [b"\\Flagged", *all_keywords])
            return
        keyword = MARKER_COLORS[color]
        others = [k for k in all_keywords if k != keyword]
        if others:
            self._client.remove_flags([uid], others)
        self._client.add_flags([uid], [b"\\Flagged", keyword])

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
    if not message.is_multipart():
        if message.get_content_type() == "text/plain":
            return MessageContent(text=_decode_payload(message))
        if message.get_content_type() == "text/calendar":
            return MessageContent(text="", attachments=[_calendar_attachment(message)])
        return MessageContent(text="(письмо в формате HTML — предпросмотр текста недоступен)")

    text: str | None = None
    html_seen = False
    attachments: list[Attachment] = []

    for part in message.walk():
        if part.is_multipart():
            continue

        filename = part.get_filename()
        content_type = part.get_content_type()
        # text/calendar (RFC 5546 iTIP-приглашение) сохраняем как вложение
        # всегда — не только когда отправитель явно проставил
        # Content-Disposition: attachment/filename (не все серверы это
        # делают), иначе приглашение молча потеряется.
        is_attachment = bool(filename) or part.get_content_disposition() == "attachment" or content_type == "text/calendar"
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
        elif content_type == "text/html":
            html_seen = True

    if text is None:
        text = "(письмо в формате HTML — предпросмотр текста недоступен)" if html_seen else "(нет текстового содержимого)"

    return MessageContent(text=text, attachments=attachments)


def _calendar_attachment(part: Message) -> Attachment:
    return Attachment(
        filename=part.get_filename() or "invite.ics",
        content_type="text/calendar",
        payload=part.get_payload(decode=True) or b"",
    )


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
