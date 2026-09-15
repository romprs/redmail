"""Список учётных записей в Параметрах: выключенная запись остаётся в нём
и после подключения другой (жалоба: "чтобы поставить галочку, она должна
быть в списке — её нет")."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from redmail import config_store
from redmail.ews_client import EwsAccount
from redmail.imap_client import Account
from redmail.ui.main_window import MainWindow, account_key


class FakeWindow(SimpleNamespace):
    """Окно без виджетов: методам нужны только словари подключений."""

    def __init__(self, **connected):
        super().__init__(
            mailboxes=connected.get("mailboxes", {}),
            mailbox_accounts=connected.get("accounts", {}),
            mailbox_smtp_accounts=connected.get("smtp", {}),
            mailbox_protocols=connected.get("protocols", {}),
        )

    _account_title = staticmethod(MainWindow._account_title)
    _known_accounts = MainWindow._known_accounts
    _save_all_accounts = MainWindow._save_all_accounts


def _fake_keyring(store: dict[str, str]):
    return (
        patch("redmail.secret_store.set_password", side_effect=lambda service, user, pw: store.__setitem__(user, pw)),
        patch("redmail.secret_store.get_password", side_effect=lambda service, user: store.get(user)),
    )


def test_disabled_imap_account_stays_in_the_list_after_connecting_exchange(tmp_path: Path) -> None:
    imap = Account(host="imap.corp.local", username="ivan@corp.local", password="p1")
    exchange = EwsAccount(email="ivan@vk.example", server="mail.vk.example", auth_type="kerberos")
    store: dict[str, str] = {}
    set_patch, get_patch = _fake_keyring(store)
    ews_key = account_key(exchange, "ews")

    with patch("redmail.config_store._accounts_path", return_value=tmp_path / "accounts.json"), patch(
        "redmail.config_store._ews_accounts_path", return_value=tmp_path / "ews_accounts.json"
    ), set_patch, get_patch:
        config_store.save_accounts([(imap, None)])  # запись уже была сохранена

        # Галочка снята: подключения нет, в словарях окна записи тоже нет.
        window = FakeWindow(
            mailboxes={ews_key: object()},
            accounts={ews_key: exchange},
            smtp={ews_key: None},
            protocols={ews_key: "ews"},
        )
        window._save_all_accounts()

        titles = [title for _key, title in window._known_accounts()]

    assert titles == [
        "IMAP: ivan@corp.local (imap.corp.local)",
        "Exchange: ivan@vk.example (mail.vk.example)",
    ] or titles == [
        "Exchange: ivan@vk.example (mail.vk.example)",
        "IMAP: ivan@corp.local (imap.corp.local)",
    ]


def test_explicit_disconnect_removes_account_from_the_list(tmp_path: Path) -> None:
    first = Account(host="imap.corp.local", username="ivan@corp.local", password="p1")
    second = Account(host="imap.vk.example", username="ivan@vk.example", password="p2")
    store: dict[str, str] = {}
    set_patch, get_patch = _fake_keyring(store)
    second_key = account_key(second, "imap")

    with patch("redmail.config_store._accounts_path", return_value=tmp_path / "accounts.json"), patch(
        "redmail.config_store._ews_accounts_path", return_value=tmp_path / "ews_accounts.json"
    ), set_patch, get_patch:
        config_store.save_accounts([(first, None), (second, None)])
        window = FakeWindow(
            mailboxes={second_key: object()},
            accounts={second_key: second},
            smtp={second_key: None},
            protocols={second_key: "imap"},
        )

        window._save_all_accounts(forget=account_key(first, "imap"))

        titles = [title for _key, title in window._known_accounts()]

    assert titles == ["IMAP: ivan@vk.example (imap.vk.example)"]


class ConnectWindow(SimpleNamespace):
    """Окно без виджетов: запоминает, что поставили в очередь подключения."""

    def __init__(self, connected=()):
        super().__init__(mailboxes={key: object() for key in connected}, queued=None, told=None)

    _connect_enabled_accounts = MainWindow._connect_enabled_accounts

    def _connect_accounts_async(self, queue):
        self.queued = queue


def test_enabled_account_connects_without_restart() -> None:
    imap = Account(host="imap.corp.local", username="ivan@corp.local", password="p1")
    exchange = EwsAccount(email="ivan@vk.example", server="mail.vk.example", auth_type="kerberos")
    window = ConnectWindow()

    with patch("redmail.ui.main_window.load_accounts", return_value=[(imap, None)]), patch(
        "redmail.ui.main_window.load_ews_accounts", return_value=[exchange]
    ):
        window._connect_enabled_accounts({account_key(imap, "imap")})

    assert [(protocol, account.username) for protocol, account, _smtp in window.queued] == [
        ("imap", "ivan@corp.local")
    ]


def test_already_connected_account_is_not_queued_twice() -> None:
    imap = Account(host="imap.corp.local", username="ivan@corp.local", password="p1")
    window = ConnectWindow(connected=[account_key(imap, "imap")])

    with patch("redmail.ui.main_window.load_accounts", return_value=[(imap, None)]), patch(
        "redmail.ui.main_window.load_ews_accounts", return_value=[]
    ), patch("redmail.ui.main_window.QMessageBox.information") as told:
        window._connect_enabled_accounts({account_key(imap, "imap")})

    assert window.queued is None
    assert told.called
