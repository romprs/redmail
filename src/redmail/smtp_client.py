from __future__ import annotations

import smtplib
from collections.abc import Callable
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid

from redmail.applog import get_logger

_log = get_logger("smtp")

# Жалоба: "ошибка отправки приходит именно если отправить из redmail, а
# при отправке из VK всё уходит без ошибок" — реальная причина не в
# запятой в поле "Кому" (тот путь уже фильтрует пустые адреса, см.
# _parse_recipient_list в main_window.py), а в том, что политика по
# умолчанию для EmailMessage — email.policy.default с cte_type="8bit":
# для любого текста с кириллицей (почти каждое письмо здесь) это даёт
# Content-Transfer-Encoding: 8bit. Но smtplib.SMTP.send_message() решает,
# слать ли ESMTP-параметр BODY=8BITMIME, ТОЛЬКО по тому, содержат ли
# non-ASCII символы САМИ АДРЕСА конверта (envelope from/to) — адреса это
# обычные email на латинице, поэтому BODY=8BITMIME не запрашивается,
# и письмо с 8-битным телом уходит без него: формальное нарушение RFC
# 6152, которое строгие корпоративные шлюзы контентной фильтрации вполне
# резонно отклоняют уже после приёма (SMTP error ... after end of data:
# 500 Message rejected) — то самое поведение, "уходит, но потом
# отбойник". cte_type="7bit" заставляет content manager всегда кодировать
# нелатинский текст через quoted-printable/base64 (7-битно чистые,
# универсально совместимые кодировки) вместо сырых 8-битных байт —
# независимо от того, что там на стороне сервера с 8BITMIME.
_OUTGOING_POLICY = policy.default.clone(cte_type="7bit")


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
    # См. Account.keytab_path/principal — тот же необязательный keytab.
    keytab_path: str = ""
    principal: str = ""


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
    # Отредактированное форматирование/встроенные картинки из ComposeDialog
    # (жалоба: "нет возможности вставить картинку... не даёт установить
    # какие-либо шрифты"). None — письмо чисто текстовое, шлём как раньше;
    # когда задано, `body` остаётся обычным текстовым fallback-содержимым
    # (RFC 2046 multipart/alternative — получатели без поддержки HTML
    # видят его), а html_body — реальное форматированное содержимое.
    html_body: str | None = None
    # cid -> (content_type, payload) — картинки, вставленные прямо в текст
    # письма (не файловые вложения), связываются с html_body через
    # <img src="cid:..."> так же, как их парсит imap_client.extract_content
    # у входящей почты.
    inline_images: dict[str, tuple[str, bytes]] = field(default_factory=dict)


def build_email_message(message: OutgoingMessage) -> EmailMessage:
    """Общая сборка RFC 822 письма — используется и для реальной отправки
    по SMTP, и для того, чтобы положить готовую копию письма прямо в
    "Отправленные"/"Черновики" через IMAP APPEND (сервер не всегда сам
    сохраняет копию исходящих — жалоба: "не отображается отправка почты,
    не появляется в папке отправленные")."""
    email_message = EmailMessage(policy=_OUTGOING_POLICY)
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
    if message.html_body:
        email_message.add_alternative(message.html_body, subtype="html")
        if message.inline_images:
            html_part = email_message.get_payload()[-1]
            for cid, (content_type, payload) in message.inline_images.items():
                maintype, _, subtype = content_type.partition("/")
                html_part.add_related(payload, maintype=maintype or "image", subtype=subtype or "png", cid=f"<{cid}>")

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


def _with_retry(operation: Callable[[], None]) -> None:
    """Один повтор на свежем соединении при обрыве на любом этапе —
    коннект, STARTTLS, аутентификация (в т.ч. GSSAPI) или сама отправка.
    Раньше единичный обрыв TCP/TLS падал прямо в интерфейс сырым
    "Connection unexpectedly closed" (smtplib.SMTPServerDisconnected,
    который наследуется от OSError) — жалоба при отправке приглашения на
    встречу: "Встреча сохранена, но не разослана". Тот же принцип, что
    уже применён для IMAP в imap_client.py._reconnecting."""
    try:
        operation()
    except (OSError, EOFError) as exc:
        _log.warning("SMTP: обрыв соединения (%s), повтор на новом соединении", exc)
        operation()


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

        gssapi_sasl.smtp_sasl_login(
            client,
            account.host,
            account.username,
            keytab_path=account.keytab_path,
            principal=account.principal,
        )
    else:
        client.login(account.username, account.password)
    _log.info("SMTP %s:%s: вход выполнен (%s, %s)", account.host, account.port, account.username, account.auth_type)
    return client


def test_connection(account: SmtpAccount) -> None:
    """Подключается и проходит аутентификацию, ничего не отправляя — для
    кнопки "Проверить подключение" в настройках (жалоба-пожелание:
    "может добавить кнопку проверки подключения для входящих, исходящих
    и календаря?"). Успех — соединение установлено и закрыто без ошибок;
    любая проблема (сеть, TLS, логин/SSO) всплывает как исключение."""
    def attempt() -> None:
        with _connect_and_authenticate(account):
            pass

    _with_retry(attempt)


def send_message(account: SmtpAccount, message: OutgoingMessage) -> None:
    email_message = build_email_message(message)

    def attempt() -> None:
        with _connect_and_authenticate(account) as client:
            client.send_message(email_message)

    recipients = getaddresses(email_message.get_all("To", []) + email_message.get_all("Cc", []) + email_message.get_all("Bcc", []))
    try:
        _with_retry(attempt)
    except Exception as exc:
        _log.error("SMTP %s: отправка не удалась (получателей %d, тема %r): %s", account.host, len(recipients), message.subject, exc)
        raise
    _log.info("SMTP %s: письмо отправлено (получателей %d, тема %r)", account.host, len(recipients), message.subject)
