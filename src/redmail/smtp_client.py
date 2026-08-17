from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage


@dataclass
class SmtpAccount:
    host: str
    username: str
    password: str
    port: int = 587
    use_ssl: bool = False  # True = неявный TLS (порт 465), False = STARTTLS (порт 587)


@dataclass
class OutgoingAttachment:
    filename: str
    content_type: str
    payload: bytes


@dataclass
class OutgoingMessage:
    sender: str
    to: list[str]
    subject: str
    body: str
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    attachments: list[OutgoingAttachment] = field(default_factory=list)


def send_message(account: SmtpAccount, message: OutgoingMessage) -> None:
    email_message = EmailMessage()
    email_message["From"] = message.sender
    email_message["To"] = ", ".join(message.to)
    email_message["Subject"] = message.subject
    if message.in_reply_to:
        email_message["In-Reply-To"] = message.in_reply_to
        email_message["References"] = " ".join([*message.references, message.in_reply_to])
    email_message.set_content(message.body)

    for attachment in message.attachments:
        maintype, _, subtype = attachment.content_type.partition("/")
        email_message.add_attachment(
            attachment.payload,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=attachment.filename,
        )

    smtp_cls = smtplib.SMTP_SSL if account.use_ssl else smtplib.SMTP
    with smtp_cls(account.host, account.port, timeout=30) as client:
        if not account.use_ssl:
            client.starttls()
        client.login(account.username, account.password)
        client.send_message(email_message)
