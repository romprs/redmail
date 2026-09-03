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
    capabilities = client.capabilities()
    if b"AUTH=GSSAPI" not in capabilities:
        # Реальный сервер (VK Mail) на попытку "AUTHENTICATE GSSAPI"
        # ответил "BAD invalid command" — то есть сам сервер не понял
        # команду, а не отверг билет. Сервер вообще не заявляет
        # поддержку GSSAPI в CAPABILITY — это не ошибка билета/сети,
        # SSO по IMAP на этом сервере в принципе не работает, независимо
        # от того, насколько корректен клиент. Показываем это явно
        # вместо непонятного "BAD invalid command".
        supported = ", ".join(
            cap.decode("ascii", errors="replace")[len("AUTH=") :]
            for cap in capabilities
            if cap.startswith(b"AUTH=")
        )
        raise GssapiSaslError(
            f"Сервер {host} не заявляет поддержку GSSAPI (SSO) по IMAP в CAPABILITY. "
            f"Поддерживаемые способы входа: {supported or 'не объявлены'}. "
            "SSO по этому протоколу здесь не сработает независимо от билета Kerberos."
        )
    context = GssapiSaslContext(service="imap", host=host, authzid=username)
    try:
        client.sasl_login("GSSAPI", context.step)
    except GssapiSaslError:
        raise
    except Exception as exc:
        # Сервер ЗАЯВЛЯЕТ AUTH=GSSAPI в CAPABILITY (иначе сработала бы
        # проверка выше), но сам обмен обрывается протокольной ошибкой
        # ("unexpected response: b'BAD invalid command'" — это низкоуровневая
        # ошибка разбора ответа сервера в imaplib, не имеющая отношения к
        # imapclient/этому коду) — реальная находка: сервер объявляет
        # поддержку механизма, который фактически не может завершить.
        raise GssapiSaslError(
            f"Сервер {host} заявляет поддержку GSSAPI (SSO) в CAPABILITY, но сам обмен "
            f"аутентификацией прерывается протокольной ошибкой: {exc}. Похоже, сервер "
            "объявляет этот способ входа, но не может фактически довести его до конца "
            "(частичная/сломанная настройка GSSAPI на стороне сервера, а не билет Kerberos)."
        ) from exc


def smtp_sasl_login(client: smtplib.SMTP, host: str, username: str) -> None:
    """Аутентифицирует уже открытую SMTP-сессию по Kerberos-билету вместо
    пароля.

    Не используем smtplib.SMTP.auth() — тот требует, чтобы authobject()
    возвращал ASCII-строку (внутри вызывается
    `authobject(challenge).encode('ascii')`), а бинарный GSS-токен почти
    всегда содержит байты вне ASCII. Повторяем тот же цикл обмена AUTH
    вручную, base64 кодируем/декодируем сами (как это делает и сам
    smtplib.auth() под капотом, но с байтами, а не принудительно с str)."""
    # EHLO мог быть не вызван вовсе (implicit TLS, порт 465 — в
    # _connect_and_authenticate() для этого случая нет ни ehlo(), ни
    # starttls()) — без него esmtp_features пуст, и проверка ниже ничего
    # не найдёт. Повторный EHLO безопасен и идемпотентен.
    client.ehlo()
    supported_auth = client.esmtp_features.get("auth", "").lower().split()
    if "gssapi" not in supported_auth:
        # Реальный сервер (VK Mail) на "AUTH GSSAPI" ответил "500 Invalid
        # command" — сервер не понял команду, а не отверг билет; он
        # просто не объявляет GSSAPI в EHLO. SSO по SMTP на этом сервере
        # не сработает независимо от билета Kerberos — показываем это
        # прямо, а не как невнятную ошибку протокола.
        raise GssapiSaslError(
            f"Сервер {host} не заявляет поддержку GSSAPI (SSO) по SMTP в ответе EHLO. "
            f"Поддерживаемые способы входа: {', '.join(supported_auth) or 'не объявлены'}. "
            "SSO по этому протоколу здесь не сработает независимо от билета Kerberos."
        )
    context = GssapiSaslContext(service="smtp", host=host, authzid=username)
    code, resp = client.docmd("AUTH", "GSSAPI")
    if code not in (235, 334, 503):
        # Сервер заявляет GSSAPI в EHLO (иначе сработала бы проверка выше),
        # но сама команда "AUTH GSSAPI" отвергнута сразу же, до первого
        # шага обмена (реальная находка на VK Mail: "500 5.5.1 Invalid
        # command"). Раз до цикла challenge/response дело не дошло — это
        # не отказ билету, а сервер объявляет способ входа, который
        # фактически не работает.
        raise GssapiSaslError(
            f"Сервер {host} заявляет поддержку GSSAPI (SSO) в EHLO, но сама команда "
            f"AUTH GSSAPI отвергнута сразу: ({code}, {resp!r}). Похоже, сервер объявляет "
            "этот способ входа, но не может фактически его выполнить (частичная/сломанная "
            "настройка GSSAPI на стороне сервера, а не билет Kerberos)."
        )
    while code == 334:
        challenge = base64.decodebytes(resp) if resp.strip() else b""
        token = context.step(challenge)
        response = base64.b64encode(token).decode("ascii")
        code, resp = client.docmd(response)
    if code not in (235, 503):
        raise smtplib.SMTPAuthenticationError(code, resp)
