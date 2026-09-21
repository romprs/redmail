from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from redmail.ews_client import EwsAccount, EwsSession, send_message
from redmail.imap_client import MessageGoneError
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


def test_list_folders_adds_branch_for_subscribed_colleague_mailbox() -> None:
    """Подписка на ящик коллеги: его папки — отдельная ветка дерева,
    открытая НАШЕЙ учётной записью (пароль владельца не нужен)."""
    my_root = SimpleNamespace(msg_folder_root=_fake_folder("root", children=[_fake_folder("Входящие")]))
    colleague = SimpleNamespace(msg_folder_root=_fake_folder("root", children=[_fake_folder("Входящие")]))
    with patch("redmail.ews_client.Configuration"), patch(
        "redmail.ews_client.ExchangeAccount", side_effect=[my_root, colleague]
    ) as account_cls:
        session = EwsSession(_account(shared_mailboxes=("petrov@example.com",)))
        names = [f.name for f in session.list_folders()]

    assert names == ["Входящие", "Ящик petrov@example.com/Входящие"]
    # Чужой ящик открыт правами делегата под нашей учётной записью.
    assert account_cls.call_args.kwargs["primary_smtp_address"] == "petrov@example.com"
    assert session._folder("Ящик petrov@example.com/Входящие") is colleague.msg_folder_root.children[0]


def test_list_folders_keeps_own_mail_when_colleague_mailbox_is_unavailable() -> None:
    my_root = SimpleNamespace(msg_folder_root=_fake_folder("root", children=[_fake_folder("Входящие")]))
    with patch("redmail.ews_client.Configuration"), patch(
        "redmail.ews_client.ExchangeAccount", side_effect=[my_root, PermissionError("доступ закрыт")]
    ):
        session = EwsSession(_account(shared_mailboxes=("petrov@example.com",)))
        names = [f.name for f in session.list_folders()]

    assert names == ["Входящие"]


def test_excluded_shared_folders_depends_on_offline_choice() -> None:
    """Галочка «хранить письма ящиков коллег локально» — ровно два варианта:
    выключено — папки коллег не обходит фоновая синхронизация, для них не
    качаются тела и они не идут в автоархив; включено — чужая почта живёт по
    тем же правилам, что и своя."""
    from redmail.ews_client import excluded_shared_folders

    folders = ["Inbox", "Sent Items", "Ящик petrov@example.com/Inbox", "Ящик petrov@example.com/Sent Items"]
    assert excluded_shared_folders(folders, False) == (
        "Ящик petrov@example.com/Inbox", "Ящик petrov@example.com/Sent Items",
    )
    assert excluded_shared_folders(folders, True) == ()

    # Своя папка с похожим названием из локальной копии не выпадает:
    # сравниваем с адресами подписанных ящиков, а не с одной меткой.
    own = ["Ящик подрядчика", "Ящик petrov@example.com/Inbox"]
    assert excluded_shared_folders(own, False, ("petrov@example.com",)) == ("Ящик petrov@example.com/Inbox",)


def test_folder_message_count_then_fetch_summaries() -> None:
    item = _fake_item(date=datetime(2026, 1, 15, 10, 30))
    inbox = _fake_folder("Входящие", total_count=1)
    inbox.all.return_value.only.return_value.order_by.return_value = [item]
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
    inbox.all.return_value.only.return_value.order_by.return_value = [item]
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
    inbox.all.return_value.only.return_value.order_by.return_value = [item]
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
    except MessageGoneError:
        pass
    else:
        raise AssertionError("expected MessageGoneError for unknown uid")


def test_move_and_delete_messages() -> None:
    item = _fake_item()
    inbox = _fake_folder("Входящие", total_count=1)
    inbox.all.return_value.only.return_value.order_by.return_value = [item]
    trash = _fake_folder("Корзина")
    exchange_account = SimpleNamespace(
        msg_folder_root=_fake_folder("root", children=[inbox, trash]), fetch=MagicMock()
    )
    exchange_account.fetch.return_value = [item]
    session = _session(exchange_account)
    session.list_folders()
    session.folder_message_count("Входящие")
    [summary] = session.fetch_summaries()

    exchange_account.bulk_move = MagicMock(return_value=[True])
    exchange_account.bulk_delete = MagicMock(return_value=[True])

    session.move_messages("Входящие", [summary.uid], "Корзина")
    moved = exchange_account.bulk_move.call_args.kwargs
    assert moved["ids"] == [("item-1", None)] and moved["to_folder"] is trash

    session.delete_messages("Входящие", [summary.uid])
    assert exchange_account.bulk_delete.call_args.kwargs["ids"] == [("item-1", None)]


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


def test_search_uids_asks_only_light_fields() -> None:
    """Перечисление папки не должно тянуть письма целиком: именно из-за
    этого обход папок на настоящем ящике не заканчивался."""
    item = _fake_item()
    inbox = _fake_folder("Входящие", total_count=1)
    only_query = inbox.all.return_value.only
    only_query.return_value = [item]
    exchange_account = SimpleNamespace(msg_folder_root=_fake_folder("root", children=[inbox]))
    session = _session(exchange_account)
    session.list_folders()

    uids = session.search_uids("Входящие")

    only_query.assert_called_once_with("id", "changekey", "is_read")
    assert len(uids) == 1
    assert session.fetch_flags("Входящие", uids) == {uids[0]: (False, False, None)}


def test_fetch_summaries_by_uids_requests_headers_without_body() -> None:
    item = _fake_item(date=datetime(2026, 3, 1, 9, 0))
    inbox = _fake_folder("Входящие", total_count=1)
    inbox.all.return_value.only.return_value = [item]
    exchange_account = SimpleNamespace(
        msg_folder_root=_fake_folder("root", children=[inbox]), fetch=MagicMock(return_value=[item])
    )
    session = _session(exchange_account)
    session.list_folders()
    uids = session.search_uids("Входящие")

    summaries = session.fetch_summaries_by_uids("Входящие", uids)

    fields = exchange_account.fetch.call_args.kwargs["only_fields"]
    assert "mime_content" not in fields and "body" not in fields
    assert "subject" in fields and "sender" in fields
    assert [s.subject for s in summaries] == ["Тест"]


def test_search_uids_keeps_only_last_folder_in_memory() -> None:
    first = _fake_folder("Входящие", total_count=1)
    first.all.return_value.only.return_value = [_fake_item(id_="a")]
    second = _fake_folder("Архив", total_count=1)
    second.all.return_value.only.return_value = [_fake_item(id_="b")]
    exchange_account = SimpleNamespace(msg_folder_root=_fake_folder("root", children=[first, second]))
    session = _session(exchange_account)
    session.list_folders()

    session.search_uids("Входящие")
    session.search_uids("Архив")

    assert list(session._flags) == ["Архив"]


def test_server_address_is_reduced_to_host_name() -> None:
    """Адрес, скопированный из браузера, должен работать так же, как имя
    узла: иначе и подключение не поднимется, и локальная копия заведётся
    вторая."""
    from_browser = _account(server="https://svb-mail.corp.amurgpz.ru/EWS/Exchange.asmx")
    wsdl = _account(server="https://svb-mail.corp.amurgpz.ru/EWS/Services.wsdl")
    plain = _account(server="svb-mail.corp.amurgpz.ru")

    assert from_browser.server == "svb-mail.corp.amurgpz.ru"
    assert wsdl.server == "svb-mail.corp.amurgpz.ru"
    assert plain.server == "svb-mail.corp.amurgpz.ru"
    assert from_browser.host == plain.host


def test_empty_server_means_autodiscover() -> None:
    assert _account(server="").server == ""
    assert _account(server="   ").server == ""


def test_connection_waits_when_server_asks_to_throttle() -> None:
    """Сервер, попросивший подождать, не должен сразу ронять обход папок.

    Настоящий Exchange просил 80 секунд, а предел был 60 — и каждая часть
    окна календаря падала с «Max timeout reached». Ждём заметно дольше:
    синхронизация идёт в фоне."""
    with patch("redmail.ews_client.Configuration") as config, patch("redmail.ews_client.ExchangeAccount"):
        EwsSession(_account(server="mail.example.com"))

    policy = config.call_args.kwargs["retry_policy"]
    assert policy.max_wait >= 120


def test_connection_allows_parallel_requests() -> None:
    """У библиотеки по умолчанию одно соединение на ящик: пока фоновая
    синхронизация календаря ждала сервер (по минуте на запрос), пометка,
    удаление и отправка письма стояли в очереди за ней. Соединений должно
    быть несколько."""
    with patch("redmail.ews_client.Configuration") as config, patch("redmail.ews_client.ExchangeAccount"):
        EwsSession(_account(server="mail.example.com"))

    assert config.call_args.kwargs["max_connections"] > 1
    # И повтор после таймаута не должен растягиваться на минуты.
    assert config.call_args.kwargs["retry_policy"].max_wait <= 120


class ErrorItemNotFound(Exception):
    """Так exchangelib сообщает, что письма на сервере нет."""


class ErrorMimeContentConversionFailed(Exception):
    """Так exchangelib сообщает, что письмо не переводится в MIME."""


def _session_with_listed_item(item, *, fetch):
    inbox = _fake_folder("Входящие", total_count=1)
    inbox.all.return_value.only.return_value = [item]
    trash = _fake_folder("Удаленные")
    exchange_account = SimpleNamespace(
        msg_folder_root=_fake_folder("root", children=[inbox, trash]), fetch=fetch,
        bulk_move=MagicMock(), bulk_delete=MagicMock(),
    )
    session = _session(exchange_account)
    session.list_folders()
    [uid] = session.search_uids("Входящие")
    return session, exchange_account, uid


def test_moving_messages_already_gone_from_server_is_not_an_error() -> None:
    """Письмо уже удалили в другом месте: цель «убрать из папки» достигнута."""
    session, exchange_account, uid = _session_with_listed_item(_fake_item(), fetch=MagicMock())
    exchange_account.bulk_move.return_value = [ErrorItemNotFound("нет")]

    session.move_messages("Входящие", [uid], "Удаленные")  # не поднимает ошибку


def test_other_bulk_failures_are_reported() -> None:
    session, exchange_account, uid = _session_with_listed_item(_fake_item(), fetch=MagicMock())
    exchange_account.bulk_delete.return_value = [RuntimeError("доступ запрещён")]

    try:
        session.delete_messages("Входящие", [uid])
    except RuntimeError as exc:
        assert "доступ запрещён" in str(exc)
    else:
        raise AssertionError("ожидалась ошибка удаления")


def test_opening_message_gone_from_server_raises_gone_error() -> None:
    session, _account, uid = _session_with_listed_item(
        _fake_item(), fetch=MagicMock(return_value=[ErrorItemNotFound("нет")])
    )

    try:
        session.fetch_message_raw("Входящие", uid)
    except MessageGoneError:
        pass
    else:
        raise AssertionError("ожидалась MessageGoneError")


def test_message_is_rebuilt_when_server_cannot_convert_to_mime() -> None:
    """Exchange иногда не отдаёт MIME — собираем письмо из тела и вложений."""
    rebuilt = _fake_item(subject="Приглашение")
    rebuilt.body = "Текст письма"
    rebuilt.attachments = []
    rebuilt.to_recipients = [SimpleNamespace(name="Пётр", email_address="petr@example.com")]
    rebuilt.cc_recipients = []
    rebuilt.datetime_sent = datetime(2026, 9, 16, 9, 0)
    fetch = MagicMock(side_effect=[[ErrorMimeContentConversionFailed("нет MIME")], [rebuilt]])
    session, _account, uid = _session_with_listed_item(_fake_item(), fetch=fetch)

    content = session.fetch_message_content("Входящие", uid)

    assert "Текст письма" in (content.text or "")


def test_message_can_be_opened_before_folder_was_listed_in_this_session() -> None:
    """После перезапуска номера писем из локальной базы ещё не сопоставлены
    с идентификаторами Exchange — папку перечитываем сами."""
    item = _fake_item(mime_content=b"From: a@example.com\r\n\r\nhi")
    inbox = _fake_folder("Входящие", total_count=1)
    inbox.all.return_value.only.return_value = [item]
    exchange_account = SimpleNamespace(
        msg_folder_root=_fake_folder("root", children=[inbox]), fetch=MagicMock(return_value=[item])
    )
    first = _session(exchange_account)
    first.list_folders()
    [uid] = first.search_uids("Входящие")

    restarted = _session(exchange_account)
    restarted.list_folders()

    assert restarted.fetch_message_raw("Входящие", uid).endswith(b"hi")


def test_sent_folder_summary_carries_recipients() -> None:
    item = _fake_item()
    item.to_recipients = [
        SimpleNamespace(name="Комарова Светлана", email_address="svkomarova@example.com"),
        SimpleNamespace(name="", email_address="petr@example.com"),
    ]
    item.size = 2048
    session, exchange_account, uid = _session_with_listed_item(item, fetch=MagicMock(return_value=[item]))

    [summary] = session.fetch_summaries_by_uids("Входящие", [uid])

    assert summary.to == "Комарова Светлана <svkomarova@example.com>, petr@example.com"
    assert summary.size == 2048


def test_unread_counter_is_read_fresh_from_server() -> None:
    inbox = _fake_folder("Входящие", total_count=3)
    inbox.unread_count = 1

    def refresh():
        inbox.unread_count = 2  # на сервере прочитали/пришло новое

    inbox.refresh = MagicMock(side_effect=refresh)
    exchange_account = SimpleNamespace(msg_folder_root=_fake_folder("root", children=[inbox]))
    session = _session(exchange_account)
    session.list_folders()

    assert session.folder_unseen_count("Входящие") == 2


def test_message_date_is_shown_in_local_time() -> None:
    """Exchange отдаёт время в UTC; в списке — местное, как у писем VK."""
    from datetime import timezone

    item = _fake_item(date=datetime(2026, 9, 16, 5, 30, tzinfo=timezone.utc))
    session, _account, uid = _session_with_listed_item(item, fetch=MagicMock(return_value=[item]))

    [summary] = session.fetch_summaries_by_uids("Входящие", [uid])

    expected = datetime(2026, 9, 16, 5, 30, tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    assert summary.date == expected


def test_summary_recipients_fall_back_to_display_to() -> None:
    from types import SimpleNamespace

    from redmail import ews_client

    session = ews_client.EwsSession.__new__(ews_client.EwsSession)
    session._id_map = {}
    item = SimpleNamespace(
        id="AAMk1", changekey="ck", subject="Fwd: Обновление УХ", sender=None, datetime_received=None,
        message_id="<m@x>", has_attachments=False, categories=None, importance="Normal", is_read=True,
        to_recipients=None, display_to="Иванов Иван; Петров Пётр", size=100,
    )
    assert session._to_summary(item).to == "Иванов Иван; Петров Пётр"
