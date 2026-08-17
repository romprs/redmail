from __future__ import annotations

from dataclasses import dataclass
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

    def fetch_folder_summaries(self, folder: str = "INBOX", limit: int = 50) -> list[MessageSummary]:
        self._select(folder)
        uids = sorted(self._client.search(["ALL"]))[-limit:]
        if not uids:
            return []
        response = self._client.fetch(uids, ["ENVELOPE"])
        return [_to_summary(uid, response[uid][b"ENVELOPE"]) for uid in reversed(uids)]

    def fetch_message_body(self, folder: str, uid: int) -> str:
        self._select(folder)
        response = self._client.fetch([uid], ["BODY.PEEK[]"])
        raw = response[uid][b"BODY[]"]
        return _extract_text(message_from_bytes(raw))

    def _select(self, folder: str) -> None:
        # Папка уже открыта этой же сессией — второй SELECT только теряет время.
        if self._selected_folder != folder:
            self._client.select_folder(folder, readonly=True)
            self._selected_folder = folder


def _extract_text(message: Message) -> str:
    if message.is_multipart():
        plain_part = next(
            (part for part in message.walk() if part.get_content_type() == "text/plain"),
            None,
        )
        if plain_part is not None:
            return _decode_payload(plain_part)
        html_part = next(
            (part for part in message.walk() if part.get_content_type() == "text/html"),
            None,
        )
        if html_part is not None:
            return "(письмо в формате HTML — предпросмотр текста недоступен)"
        return "(нет текстового содержимого)"
    if message.get_content_type() == "text/plain":
        return _decode_payload(message)
    return "(письмо в формате HTML — предпросмотр текста недоступен)"


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
