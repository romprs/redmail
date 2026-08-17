from __future__ import annotations

from dataclasses import dataclass, field
from email import message_from_bytes
from email.header import decode_header
from email.message import Message

from imapclient import IMAPClient


@dataclass
class Account:
    host: str
    username: str
    password: str
    port: int = 993
    use_ssl: bool = True


@dataclass
class MessageSummary:
    uid: int
    subject: str
    sender: str
    sender_email: str
    date: str
    message_id: str


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

    def close(self) -> None:
        try:
            self._client.logout()
        except Exception:
            pass

    def __enter__(self) -> "ImapSession":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def list_folders(self) -> list[str]:
        return [
            name
            for flags, _delimiter, name in self._client.list_folders()
            if b"\\Noselect" not in flags
        ]

    def folder_message_count(self, folder: str) -> int:
        """SELECT папку, вернуть общее число писем в ней (EXISTS).

        Дешёвая операция (без сканирования, в отличие от SEARCH) — на ней
        удобно проверять, изменилось ли что-то в папке с прошлого раза,
        прежде чем платить за полный FETCH сводок (см. CachedMailbox).
        """
        status = self._client.select_folder(folder, readonly=True)
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
            response = self._client.fetch(f"{start}:*", ["ENVELOPE", "UID"])
        finally:
            self._client.use_uid = True

        by_seq = sorted(response.items(), key=lambda item: item[0], reverse=True)
        return [_to_summary(data[b"UID"], data[b"ENVELOPE"]) for _seq, data in by_seq]

    def fetch_folder_summaries(self, folder: str = "INBOX", limit: int = 50) -> list[MessageSummary]:
        self.folder_message_count(folder)
        return self.fetch_summaries(limit)

    def fetch_message_content(self, folder: str, uid: int) -> MessageContent:
        self._select(folder)
        response = self._client.fetch([uid], ["BODY.PEEK[]"])
        raw = response[uid][b"BODY[]"]
        return _extract_content(message_from_bytes(raw))

    def _select(self, folder: str) -> None:
        # Папка уже открыта этой же сессией — второй SELECT только теряет время.
        if self._selected_folder != folder:
            self._client.select_folder(folder, readonly=True)
            self._selected_folder = folder


def _extract_content(message: Message) -> MessageContent:
    if not message.is_multipart():
        if message.get_content_type() == "text/plain":
            return MessageContent(text=_decode_payload(message))
        return MessageContent(text="(письмо в формате HTML — предпросмотр текста недоступен)")

    text: str | None = None
    html_seen = False
    attachments: list[Attachment] = []

    for part in message.walk():
        if part.is_multipart():
            continue

        filename = part.get_filename()
        is_attachment = bool(filename) or part.get_content_disposition() == "attachment"
        if is_attachment:
            attachments.append(
                Attachment(
                    filename=filename or "(без имени)",
                    content_type=part.get_content_type(),
                    payload=part.get_payload(decode=True) or b"",
                )
            )
            continue

        content_type = part.get_content_type()
        if content_type == "text/plain" and text is None:
            text = _decode_payload(part)
        elif content_type == "text/html":
            html_seen = True

    if text is None:
        text = "(письмо в формате HTML — предпросмотр текста недоступен)" if html_seen else "(нет текстового содержимого)"

    return MessageContent(text=text, attachments=attachments)


def _decode_payload(part: Message) -> str:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _to_summary(uid: int, envelope) -> MessageSummary:
    sender_display, sender_email = _format_address(envelope.from_)
    message_id = envelope.message_id
    return MessageSummary(
        uid=uid,
        subject=_decode_subject(envelope.subject),
        sender=sender_display,
        sender_email=sender_email,
        date=envelope.date.strftime("%Y-%m-%d %H:%M") if envelope.date else "",
        message_id=message_id.decode("ascii", errors="replace") if message_id else "",
    )


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
