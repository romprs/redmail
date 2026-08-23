from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from redmail.ews_client import EwsAccount, EwsSession, send_message
from redmail.smtp_client import OutgoingAttachment, OutgoingMessage


def _account(**overrides) -> EwsAccount:
    defaults = dict(email="ivan@example.com", username="ivan@example.com", password="secret")
    defaults.update(overrides)
    return EwsAccount(**defaults)


def _fake_folder(name: str, *, total_count: int = 0, children: list | None = None) -> SimpleNamespace:
    folder = SimpleNamespace(
        name=name,
        id=f"id-{name}",
        total_count=total_count,
        children=children or [],
        all=MagicMock(),
    )
    return folder


def _fake_item(
    *,
    id_: str = "item-1",
    changekey: str = "ck-1",
    subject: str = "Тест",
    sender_name: str = "Иван",
    sender_email: str = "ivan@example.com",
    date: datetime | None = None,
    has_attachments: bool = False,
    categories: list[str] | None = None,
    importance: str = "Normal",
    is_read: bool = False,
    mime_content: bytes = b"raw",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id_,
        changekey=changekey,
        subject=subject,
        sender=SimpleNamespace(name=sender_name, email_address=sender_email) if sender_email else None,
        datetime_received=date,
        message_id="<msg-1@example.com>",
        has_attachments=has_attachments,
        categories=categories or [],
        importance=importance,
        is_read=is_read,
        mime_content=mime_content,
        save=MagicMock(),
        move=MagicMock(),
        delete=MagicMock(),
    )


def _session(exchange_account) -> EwsSession:
    with patch("redmail.ews_client.Configuration"), patch(
        "redmail.ews_client.ExchangeAccount", return_value=exchange_account
    ):
        return EwsSession(_account())


def test_list_folders_walks_tree_and_builds_paths() -> None:
    leaf = _fake_folder("Проекты")
    inbox = _fake_folder("Входящие", children=[leaf])
    exchange_account = SimpleNamespace(msg_folder_root=_fake_folder("root", children=[inbox]))
    session = _session(exchange_account)

    folders = session.list_folders()

    names = [f.name for f in folders]
    assert "Входящие" in names
    assert "Входящие/Проекты" in names


def test_folder_message_count_then_fetch_summaries() -> None:
    item = _fake_item(date=datetime(2026, 1, 15, 10, 30))
    inbox = _fake_folder("Входящие", total_count=1)
    inbox.all.return_value.order_by.return_value = [item]
    exchange_account = SimpleNamespace(msg_folder_root=_fake_folder("root", children=[inbox]))
    session = _session(exchange_account)
    session.list_folders()

    count = session.folder_message_count("Входящие")
    summaries = session.fetch_summaries(limit=10)

    assert count == 1
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.sender == "Иван"
    assert summary.sender_email == "ivan@example.com"
    assert summary.date == "2026-01-15 10:30"
    assert summary.importance == "normal"


def test_fetch_summaries_without_selected_folder_returns_empty() -> None:
    exchange_account = SimpleNamespace(msg_folder_root=_fake_folder("root"))
    session = _session(exchange_account)
    assert session.fetch_summaries() == []


def test_marker_color_round_trip_via_categories() -> None:
    item = _fake_item()
    inbox = _fake_folder("Входящие", total_count=1)
    inbox.all.return_value.order_by.return_value = [item]
    exchange_account = SimpleNamespace(msg_folder_root=_fake_folder("root", children=[inbox]), fetch=MagicMock())
    exchange_account.fetch.return_value = [item]
    session = _session(exchange_account)
    session.list_folders()
    session.folder_message_count("Входящие")
    [summary] = session.fetch_summaries()

    session.set_marker("Входящие", summary.uid, "red")

    assert item.categories == ["RedMail Red"]
    item.save.assert_called_once_with(update_fields=["categories"])


def test_set_marker_same_color_is_a_noop() -> None:
    exchange_account = SimpleNamespace(msg_folder_root=_fake_folder("root"), fetch=MagicMock())
    session = _session(exchange_account)

    session.set_marker("Входящие", 42, "red", previous_color="red")

    exchange_account.fetch.assert_not_called()


def test_fetch_message_raw_uses_mime_content() -> None:
    item = _fake_item(mime_content=b"From: a@example.com\r\n\r\nhi")
    inbox = _fake_folder("Входящие", total_count=1)
    inbox.all.return_value.order_by.return_value = [item]
    exchange_account = SimpleNamespace(msg_folder_root=_fake_folder("root", children=[inbox]), fetch=MagicMock())
    exchange_account.fetch.return_value = [item]
    session = _session(exchange_account)
    session.list_folders()
    session.folder_message_count("Входящие")
    [summary] = session.fetch_summaries()

    raw = session.fetch_message_raw("Входящие", summary.uid)

    assert raw == b"From: a@example.com\r\n\r\nhi"


def test_get_item_for_unknown_uid_raises() -> None:
    exchange_account = SimpleNamespace(msg_folder_root=_fake_folder("root"))
    session = _session(exchange_account)
    try:
        session.fetch_message_raw("Входящие", 999)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown uid")


def test_move_and_delete_messages() -> None:
    item = _fake_item()
    inbox = _fake_folder("Входящие", total_count=1)
    inbox.all.return_value.order_by.return_value = [item]
    trash = _fake_folder("Корзина")
    exchange_account = SimpleNamespace(
        msg_folder_root=_fake_folder("root", children=[inbox, trash]), fetch=MagicMock()
    )
    exchange_account.fetch.return_value = [item]
    session = _session(exchange_account)
    session.list_folders()
    session.folder_message_count("Входящие")
    [summary] = session.fetch_summaries()

    session.move_messages("Входящие", [summary.uid], "Корзина")
    item.move.assert_called_once_with(trash)

    session.delete_messages("Входящие", [summary.uid])
    item.delete.assert_called_once()


def test_kerberos_auth_does_not_use_password_credentials() -> None:
    exchange_account = SimpleNamespace(msg_folder_root=_fake_folder("root"))
    with patch("redmail.ews_client.Configuration") as mock_config, patch(
        "redmail.ews_client.ExchangeAccount", return_value=exchange_account
    ), patch("redmail.ews_client.Credentials") as mock_credentials:
        EwsSession(_account(auth_type="kerberos", password=""))
    mock_credentials.assert_not_called()
    assert mock_config.call_args.kwargs["credentials"] is None


def test_send_message_builds_ews_message_and_sends() -> None:
    with patch("redmail.ews_client.EwsMessage") as mock_message_cls:
        mock_message = MagicMock()
        mock_message_cls.return_value = mock_message
        exchange_account = SimpleNamespace(msg_folder_root=_fake_folder("root"))
        session = _session(exchange_account)

        message = OutgoingMessage(
            sender="ivan@example.com",
            to=["them@example.com"],
            subject="Тема",
            body="Текст",
            attachments=[OutgoingAttachment(filename="a.txt", content_type="text/plain", payload=b"data")],
        )
        send_message(session, message)

    mock_message.attach.assert_called_once()
    mock_message.send.assert_called_once()
