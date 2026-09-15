"""Проверка сквозного пути Exchange: обход папки кладёт заголовки в
локальную базу. Раньше письма в списке не появлялись вовсе."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from redmail import cache_store, sync_engine
from redmail.ews_client import EwsAccount, EwsSession


def _item(n: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"item-{n}",
        changekey=f"ck-{n}",
        subject=f"Письмо {n}",
        sender=SimpleNamespace(name="Иван", email_address="ivan@example.com"),
        datetime_received=datetime(2026, 9, 15, 10, n % 60),
        message_id=f"<{n}@example.com>",
        has_attachments=False,
        categories=[],
        importance="Normal",
        is_read=False,
    )


def _session(items: list[SimpleNamespace]) -> EwsSession:
    inbox = SimpleNamespace(name="Входящие", id="id-inbox", total_count=len(items), children=[], all=MagicMock())
    inbox.all.return_value.only.return_value = items
    root = SimpleNamespace(name="root", id="id-root", total_count=0, children=[inbox], all=MagicMock())
    exchange_account = SimpleNamespace(msg_folder_root=root, fetch=MagicMock(return_value=items))
    with patch("redmail.ews_client.Configuration"), patch(
        "redmail.ews_client.ExchangeAccount", return_value=exchange_account
    ):
        session = EwsSession(EwsAccount(email="ivan@example.com", password="secret", server="mail.example.com"))
    session.list_folders()
    return session


def test_folder_walk_puts_headers_into_local_database(tmp_path: Path) -> None:
    items = [_item(n) for n in range(1, 31)]
    session = _session(items)

    with patch("redmail.cache_store._db_path", return_value=tmp_path / "mail.sqlite3"):
        stats = sync_engine.sync_all_folders(session, "ews:mail.example.com:ivan@example.com", ["Входящие"])
        stored = cache_store.count_folder_summaries("ews:mail.example.com:ivan@example.com", "Входящие")

    assert stats.added == 30
    assert stored == 30
    assert stats.server_busy is False


def test_second_walk_adds_nothing_new(tmp_path: Path) -> None:
    items = [_item(n) for n in range(1, 11)]
    session = _session(items)
    key = "ews:mail.example.com:ivan@example.com"

    with patch("redmail.cache_store._db_path", return_value=tmp_path / "mail.sqlite3"):
        sync_engine.sync_all_folders(session, key, ["Входящие"])
        again = sync_engine.sync_all_folders(session, key, ["Входящие"])
        stored = cache_store.count_folder_summaries(key, "Входящие")

    assert again.added == 0
    assert stored == 10
