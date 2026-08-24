from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid


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
    # Доп. параметры Content-Type — нужно для приглашений: MIME-параметр
    # method= (RFC 5546) рядом с METHOD: внутри самого .ics — многие клиенты
    # (Outlook в их числе) ищут именно внешний параметр, чтобы показать
    # интерфейс "принять/отклонить", а не просто вложение.
    content_type_params: dict[str, str] = field(default_factory=dict)


@dataclass
class OutgoingMessage:
    sender: str
    to: list[str]
    subject: str
    body: str
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    attachments: list[OutgoingAttachment] = field(default_factory=list)


def build_email_message(message: OutgoingMessage) -> EmailMessage:
    """Общая сборка RFC 822 письма — используется и для реальной отправки
    по SMTP, и для того, чтобы положить готовую копию письма прямо в
    "Отправленные"/"Черновики" через IMAP APPEND (сервер не всегда сам
    сохраняет копию исходящих — жалоба: "не отображается отправка почты,
    не появляется в папке отправленные")."""
    email_message = EmailMessage()
    email_message["From"] = message.sender
    email_message["To"] = ", ".join(message.to)
    if message.cc:
        email_message["Cc"] = ", ".join(message.cc)
    if message.bcc:
        # smtplib.send_message() сам добавляет адреса из Bcc в список
        # получателей SMTP-конверта и одновременно вырезает сам заголовок
        # Bcc из фактически передаваемого письма (см. исходники smtplib) —
        # получателям Bcc не виден друг друга и остальным получателям.
        email_message["Bcc"] = ", ".join(message.bcc)
    email_message["Subject"] = message.subject
    email_message["Date"] = formatdate(localtime=True)
    email_message["Message-Id"] = make_msgid()
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
            params=attachment.content_type_params or None,
        )
    return email_message


def send_message(account: SmtpAccount, message: OutgoingMessage) -> None:
    email_message = build_email_message(message)
    smtp_cls = smtplib.SMTP_SSL if account.use_ssl else smtplib.SMTP
    with smtp_cls(account.host, account.port, timeout=30) as client:
        if not account.use_ssl:
            client.starttls()
        client.login(account.username, account.password)
        client.send_message(email_message)
