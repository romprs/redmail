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
    # "password" — обычный AUTH LOGIN/PLAIN; "kerberos" — SSO для сервера в
    # домене, см. Account.auth_type в imap_client.py и gssapi_sasl.py.
    auth_type: str = "password"


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


def _connect_and_authenticate(account: SmtpAccount) -> smtplib.SMTP:
    smtp_cls = smtplib.SMTP_SSL if account.use_ssl else smtplib.SMTP
    client = smtp_cls(account.host, account.port, timeout=30)
    if not account.use_ssl:
        client.starttls()
    if account.auth_type == "kerberos":
        # Импорт внутри функции — см. imap_client.py._login: gssapi
        # нужен только для SSO и не должен ломать обычный пароль там,
        # где нет системных библиотек Kerberos.
        from redmail import gssapi_sasl

        gssapi_sasl.smtp_sasl_login(client, account.host, account.username)
    else:
        client.login(account.username, account.password)
    return client


def test_connection(account: SmtpAccount) -> None:
    """Подключается и проходит аутентификацию, ничего не отправляя — для
    кнопки "Проверить подключение" в настройках (жалоба-пожелание:
    "может добавить кнопку проверки подключения для входящих, исходящих
    и календаря?"). Успех — соединение установлено и закрыто без ошибок;
    любая проблема (сеть, TLS, логин/SSO) всплывает как исключение."""
    with _connect_and_authenticate(account):
        pass


def send_message(account: SmtpAccount, message: OutgoingMessage) -> None:
    email_message = build_email_message(message)
    with _connect_and_authenticate(account) as client:
        client.send_message(email_message)
