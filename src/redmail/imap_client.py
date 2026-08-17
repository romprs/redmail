from __future__ import annotations

from dataclasses import dataclass
from email.header import decode_header

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
    date: str


def fetch_inbox_summaries(account: Account, limit: int = 50) -> list[MessageSummary]:
    with IMAPClient(account.host, port=account.port, ssl=account.use_ssl) as client:
        client.login(account.username, account.password)
        client.select_folder("INBOX", readonly=True)
        uids = sorted(client.search(["ALL"]))[-limit:]
        if not uids:
            return []
        response = client.fetch(uids, ["ENVELOPE"])
        return [_to_summary(uid, response[uid][b"ENVELOPE"]) for uid in reversed(uids)]


def _to_summary(uid: int, envelope) -> MessageSummary:
    return MessageSummary(
        uid=uid,
        subject=_decode_subject(envelope.subject),
        sender=_format_address(envelope.from_),
        date=envelope.date.strftime("%Y-%m-%d %H:%M") if envelope.date else "",
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


def _format_address(addresses) -> str:
    if not addresses:
        return "(неизвестно)"
    address = addresses[0]
    if address.name:
        return _decode_rfc2047(address.name)
    mailbox = address.mailbox.decode("utf-8", errors="replace") if address.mailbox else ""
    host = address.host.decode("utf-8", errors="replace") if address.host else ""
    return f"{mailbox}@{host}" if mailbox and host else mailbox or "(неизвестно)"
