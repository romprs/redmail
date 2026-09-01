from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from redmail.smtp_client import OutgoingAttachment, OutgoingMessage, SmtpAccount, send_message
from redmail.smtp_client import test_connection as smtp_test_connection


def test_send_message_starttls_flow() -> None:
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client

    with patch("redmail.smtp_client.smtplib.SMTP", return_value=fake_client) as smtp_ctor:
        account = SmtpAccount(host="smtp.example.com", username="ivan", password="secret")
        message = OutgoingMessage(
            sender="ivan@example.com",
            to=["boss@example.com"],
            subject="Тема письма",
            body="Текст письма с кириллицей",
        )
        send_message(account, message)

    smtp_ctor.assert_called_once_with("smtp.example.com", 587, timeout=30)
    fake_client.starttls.assert_called_once()
    fake_client.login.assert_called_once_with("ivan", "secret")
    fake_client.send_message.assert_called_once()

    sent = fake_client.send_message.call_args[0][0]
    assert sent["Subject"] == "Тема письма"
    assert sent["To"] == "boss@example.com"
    assert sent["From"] == "ivan@example.com"
    assert sent.get_content().strip() == "Текст письма с кириллицей"


def test_send_message_kerberos_auth_uses_gssapi_sasl_not_password(monkeypatch) -> None:
    # SSO: пароль не хранится и не отправляется на сервер вовсе — вход по
    # Kerberos-билету через SASL GSSAPI. Реальный пакет gssapi требует
    # системных библиотек Kerberos, которых на машине для тестов нет —
    # подменяем весь модуль redmail.gssapi_sasl мок-объектом.
    import redmail

    fake_gssapi_sasl = MagicMock()
    # См. комментарий в test_imap_client.py — нужны и атрибут пакета, и
    # ключ sys.modules, иначе "from redmail import gssapi_sasl" может
    # найти уже импортированный ранее настоящий модуль в обход подмены.
    monkeypatch.setattr(redmail, "gssapi_sasl", fake_gssapi_sasl, raising=False)
    monkeypatch.setitem(sys.modules, "redmail.gssapi_sasl", fake_gssapi_sasl)

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client

    with patch("redmail.smtp_client.smtplib.SMTP", return_value=fake_client):
        account = SmtpAccount(host="smtp.corp.local", username="ivan", password="", auth_type="kerberos")
        message = OutgoingMessage(sender="ivan@corp.local", to=["boss@corp.local"], subject="S", body="B")
        send_message(account, message)

    fake_client.login.assert_not_called()
    fake_gssapi_sasl.smtp_sasl_login.assert_called_once_with(fake_client, "smtp.corp.local", "ivan")
    fake_client.send_message.assert_called_once()


def test_test_connection_authenticates_but_does_not_send() -> None:
    # Кнопка "Проверить подключение" в настройках - должна залогиниться
    # и сразу отключиться, не отправляя ни одного письма.
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client

    with patch("redmail.smtp_client.smtplib.SMTP", return_value=fake_client):
        account = SmtpAccount(host="smtp.example.com", username="ivan", password="secret")
        smtp_test_connection(account)

    fake_client.starttls.assert_called_once()
    fake_client.login.assert_called_once_with("ivan", "secret")
    fake_client.send_message.assert_not_called()


def test_test_connection_kerberos_uses_gssapi_sasl(monkeypatch) -> None:
    import redmail

    fake_gssapi_sasl = MagicMock()
    monkeypatch.setattr(redmail, "gssapi_sasl", fake_gssapi_sasl, raising=False)
    monkeypatch.setitem(sys.modules, "redmail.gssapi_sasl", fake_gssapi_sasl)

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client

    with patch("redmail.smtp_client.smtplib.SMTP", return_value=fake_client):
        account = SmtpAccount(host="smtp.corp.local", username="ivan", password="", auth_type="kerberos")
        smtp_test_connection(account)

    fake_client.login.assert_not_called()
    fake_gssapi_sasl.smtp_sasl_login.assert_called_once_with(fake_client, "smtp.corp.local", "ivan")
    fake_client.send_message.assert_not_called()


def test_send_message_implicit_ssl_skips_starttls() -> None:
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client

    with patch("redmail.smtp_client.smtplib.SMTP_SSL", return_value=fake_client) as smtp_ctor:
        account = SmtpAccount(
            host="smtp.example.com", username="ivan", password="secret", port=465, use_ssl=True
        )
        message = OutgoingMessage(sender="ivan@example.com", to=["a@example.com"], subject="S", body="B")
        send_message(account, message)

    smtp_ctor.assert_called_once_with("smtp.example.com", 465, timeout=30)
    fake_client.starttls.assert_not_called()
    fake_client.login.assert_called_once_with("ivan", "secret")


def test_send_message_sets_reply_headers() -> None:
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client

    with patch("redmail.smtp_client.smtplib.SMTP", return_value=fake_client):
        account = SmtpAccount(host="smtp.example.com", username="ivan", password="secret")
        message = OutgoingMessage(
            sender="ivan@example.com",
            to=["boss@example.com"],
            subject="Re: Тема",
            body="Ответ",
            in_reply_to="<orig123@example.com>",
        )
        send_message(account, message)

    sent = fake_client.send_message.call_args[0][0]
    assert sent["In-Reply-To"] == "<orig123@example.com>"
    assert "<orig123@example.com>" in sent["References"]


def test_send_message_multiple_recipients() -> None:
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client

    with patch("redmail.smtp_client.smtplib.SMTP", return_value=fake_client):
        account = SmtpAccount(host="smtp.example.com", username="ivan", password="secret")
        message = OutgoingMessage(
            sender="ivan@example.com",
            to=["a@example.com", "b@example.com"],
            subject="S",
            body="B",
        )
        send_message(account, message)

    sent = fake_client.send_message.call_args[0][0]
    assert sent["To"] == "a@example.com, b@example.com"


def test_send_message_sets_cc_and_bcc_headers() -> None:
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client

    with patch("redmail.smtp_client.smtplib.SMTP", return_value=fake_client):
        account = SmtpAccount(host="smtp.example.com", username="ivan", password="secret")
        message = OutgoingMessage(
            sender="ivan@example.com",
            to=["a@example.com"],
            subject="S",
            body="B",
            cc=["cc1@example.com", "cc2@example.com"],
            bcc=["secret@example.com"],
        )
        send_message(account, message)

    sent = fake_client.send_message.call_args[0][0]
    assert sent["Cc"] == "cc1@example.com, cc2@example.com"
    # Сам заголовок Bcc реально вырезается smtplib.send_message() при
    # отправке (см. его исходники) — здесь проверяем только то, что мы
    # его выставили, а не поведение stdlib.
    assert sent["Bcc"] == "secret@example.com"


def test_send_message_without_cc_bcc_omits_headers() -> None:
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client

    with patch("redmail.smtp_client.smtplib.SMTP", return_value=fake_client):
        account = SmtpAccount(host="smtp.example.com", username="ivan", password="secret")
        message = OutgoingMessage(sender="ivan@example.com", to=["a@example.com"], subject="S", body="B")
        send_message(account, message)

    sent = fake_client.send_message.call_args[0][0]
    assert sent["Cc"] is None
    assert sent["Bcc"] is None


def test_send_message_with_attachment() -> None:
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client

    with patch("redmail.smtp_client.smtplib.SMTP", return_value=fake_client):
        account = SmtpAccount(host="smtp.example.com", username="ivan", password="secret")
        message = OutgoingMessage(
            sender="ivan@example.com",
            to=["boss@example.com"],
            subject="Отчёт",
            body="Во вложении отчёт.",
            attachments=[
                OutgoingAttachment(filename="report.txt", content_type="text/plain", payload=b"data-inside")
            ],
        )
        send_message(account, message)

    sent = fake_client.send_message.call_args[0][0]
    attachments = [part for part in sent.iter_attachments()]
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "report.txt"
    assert attachments[0].get_payload(decode=True) == b"data-inside"


def test_send_message_with_calendar_invite_carries_method_param() -> None:
    # Outlook и другие iTIP-совместимые клиенты определяют, что вложение —
    # приглашение (а не просто файл), по параметру method= у Content-Type,
    # рядом с METHOD: внутри самого .ics.
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client

    with patch("redmail.smtp_client.smtplib.SMTP", return_value=fake_client):
        account = SmtpAccount(host="smtp.example.com", username="ivan", password="secret")
        message = OutgoingMessage(
            sender="ivan@example.com",
            to=["colleague@example.com"],
            subject="Приглашение: Совещание",
            body="Вас пригласили на встречу.",
            attachments=[
                OutgoingAttachment(
                    filename="invite.ics",
                    content_type="text/calendar",
                    payload=b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n",
                    content_type_params={"method": "REQUEST"},
                )
            ],
        )
        send_message(account, message)

    sent = fake_client.send_message.call_args[0][0]
    attachment = next(sent.iter_attachments())
    assert attachment.get_content_type() == "text/calendar"
    assert attachment.get_param("method") == "REQUEST"
