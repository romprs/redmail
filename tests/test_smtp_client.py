from __future__ import annotations

from unittest.mock import MagicMock, patch

from redmail.smtp_client import OutgoingMessage, SmtpAccount, send_message


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
