from __future__ import annotations

import base64
import smtplib

import gssapi


class GssapiSaslError(Exception):
    """Ошибка на любом шаге согласования GSSAPI (нет билета Kerberos, он
    просрочен, сервер отверг обмен и т.п.). Показываем как есть — в
    SSO-режиме пароль в приложении не хранится, поэтому автоматического
    "перелогина" по паролю здесь быть не может."""


class GssapiSaslContext:
    """Клиентская сторона SASL-механизма GSSAPI (RFC 4752) поверх
    Kerberos-билета, уже полученного ОС при входе пользователя в домен
    (RED OS + SSSD) — пароль в приложении не запрашивается и не хранится,
    вся аутентификация опирается на системный credential cache.

    Общая логика для IMAP (используется через imapclient.sasl_login) и
    SMTP (свой обмен AUTH — см. smtp_sasl_login: smtplib.SMTP.auth()
    принудительно кодирует ответ authobject() как ASCII-строку, что
    несовместимо с бинарными GSS-токенами)."""

    def __init__(self, service: str, host: str, authzid: str = "") -> None:
        try:
            target = gssapi.Name(f"{service}@{host}", gssapi.NameType.hostbased_service)
            self._ctx = gssapi.SecurityContext(name=target, usage="initiate")
        except gssapi.exceptions.GSSError as exc:
            raise GssapiSaslError(str(exc)) from exc
        self._authzid = authzid
        self._security_negotiated = False

    def step(self, challenge: bytes) -> bytes:
        try:
            if not self._ctx.complete:
                token = self._ctx.step(challenge or None)
                return token or b""
            if self._security_negotiated:
                return b""
            self._security_negotiated = True
            plaintext = self._ctx.unwrap(challenge).message
            if len(plaintext) < 4:
                raise GssapiSaslError(
                    "Сервер прислал некорректный ответ при согласовании уровня безопасности GSSAPI"
                )
            # RFC 4752 §3.1: байт 0 — маска уровней безопасности, которые
            # выбирает клиент (1 = "без слоя защиты сообщений" — мы и так
            # используем TLS для самого канала IMAP/SMTP, доп. GSS-слой не
            # нужен), байты 1-3 — макс. размер буфера (не используется без
            # слоя защиты, оставляем 0); остаток — имя авторизации в UTF-8.
            response = bytes([1, 0, 0, 0]) + self._authzid.encode("utf-8")
            return self._ctx.wrap(response, False).message
        except gssapi.exceptions.GSSError as exc:
            raise GssapiSaslError(str(exc)) from exc


def imap_sasl_login(client, host: str, username: str) -> None:
    """Аутентифицирует уже открытую IMAPClient-сессию по Kerberos-билету
    вместо пароля."""
    context = GssapiSaslContext(service="imap", host=host, authzid=username)
    client.sasl_login("GSSAPI", context.step)


def smtp_sasl_login(client: smtplib.SMTP, host: str, username: str) -> None:
    """Аутентифицирует уже открытую SMTP-сессию по Kerberos-билету вместо
    пароля.

    Не используем smtplib.SMTP.auth() — тот требует, чтобы authobject()
    возвращал ASCII-строку (внутри вызывается
    `authobject(challenge).encode('ascii')`), а бинарный GSS-токен почти
    всегда содержит байты вне ASCII. Повторяем тот же цикл обмена AUTH
    вручную, base64 кодируем/декодируем сами (как это делает и сам
    smtplib.auth() под капотом, но с байтами, а не принудительно с str)."""
    context = GssapiSaslContext(service="smtp", host=host, authzid=username)
    code, resp = client.docmd("AUTH", "GSSAPI")
    while code == 334:
        challenge = base64.decodebytes(resp) if resp.strip() else b""
        token = context.step(challenge)
        response = base64.b64encode(token).decode("ascii")
        code, resp = client.docmd(response)
    if code not in (235, 503):
        raise smtplib.SMTPAuthenticationError(code, resp)
