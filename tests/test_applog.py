from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from redmail import applog


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    # Каждый тест — свой каталог и «чистое» состояние настройки, чтобы
    # обработчики не копились между тестами.
    monkeypatch.setattr(applog, "app_dir", lambda: tmp_path)
    monkeypatch.setattr(applog, "_configured", False)
    root = logging.getLogger("redmail")
    for handler in list(root.handlers):
        root.removeHandler(handler)
    yield tmp_path
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()


def test_setup_logging_writes_connection_events_to_file(isolated_log) -> None:
    # Пожелание: "писать лог подключений и синхронизаций, чтобы отладить
    # подключения" — файл появляется в каталоге данных, строки читаемы.
    path = applog.setup_logging()
    assert path == isolated_log / "logs" / "redmail.log"

    applog.get_logger("imap").info("IMAP mail.corp.ru:993: вход выполнен (ivan, password)")
    for handler in logging.getLogger("redmail").handlers:
        handler.flush()

    text = path.read_bytes().decode("utf-8")
    assert "INFO    redmail.imap: IMAP mail.corp.ru:993: вход выполнен (ivan, password)" in text
    assert applog.tail_text() == text


def test_setup_logging_is_idempotent_and_installs_excepthook(isolated_log) -> None:
    import sys

    previous = sys.excepthook
    try:
        applog.setup_logging()
        applog.setup_logging()
        from logging.handlers import RotatingFileHandler

        file_handlers = [h for h in logging.getLogger("redmail").handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 1  # pytest добавляет свои перехватчики — считаем только файловый
        assert sys.excepthook is not previous  # необработанные исключения тоже попадут в журнал
    finally:
        sys.excepthook = previous


def test_imap_session_logs_login_and_reconnect(isolated_log) -> None:
    # Реальные точки журнала: вход и переподключение после обрыва — именно
    # то, что нужно разбирать по жалобам "после простоя выдаёт ошибку".
    from redmail.imap_client import Account, ImapSession

    applog.setup_logging()
    fake_client = MagicMock()
    fake_client.use_uid = True
    fake_client.select_folder.return_value = {b"EXISTS": 0}
    fake_client.list_folders.side_effect = [OSError("connection reset"), []]
    with patch("redmail.imap_client.IMAPClient", return_value=fake_client):
        session = ImapSession(Account(host="mail.corp.ru", username="ivan", password="secret"))
        session.list_folders()
    for handler in logging.getLogger("redmail").handlers:
        handler.flush()
    text = applog.tail_text()
    assert "вход выполнен (ivan, password)" in text
    assert "обрыв соединения (connection reset), переподключение" in text
    assert "secret" not in text  # пароль в журнал не попадает


def test_account_config_no_longer_persists_keytab_fields(tmp_path, monkeypatch) -> None:
    # Поля keytab/principal убраны из настроек (пользователь: "keytab
    # серверный, поля keytab убирай") — в файле учётной записи их больше нет,
    # а старый файл с этими ключами по-прежнему читается.
    from redmail import config_store
    from redmail.imap_client import Account
    from redmail.smtp_client import SmtpAccount

    account = Account(host="imap.corp.ru", username="ivan", password="x", auth_type="kerberos")
    smtp = SmtpAccount(host="smtp.corp.ru", username="ivan", password="x", auth_type="kerberos")
    data = config_store._account_dict(account, smtp)
    assert "keytab_path" not in data and "principal" not in data

    legacy = dict(data, keytab_path="/etc/x.keytab", principal="ivan@CORP")
    loaded, loaded_smtp = config_store._account_from_dict(legacy, "ivan", "")
    assert loaded.auth_type == "kerberos" and loaded_smtp.auth_type == "kerberos"
