from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path
from unittest.mock import MagicMock, patch

from redmail import archive_store
from redmail.imap_client import UNKNOWN_MARKER, Account, MessageContent, MessageSummary
from redmail.mailbox import ArchiveSource, CachedMailbox


def _account() -> Account:
    return Account(host="imap.example.com", username="ivan", password="secret")


def _summary(uid: int, **kwargs) -> MessageSummary:
    base = dict(
        uid=uid, subject="S", sender="Ivan", sender_email="ivan@example.com", date="2026-08-18 10:00",
        message_id=f"<{uid}@example.com>",
    )
    base.update(kwargs)
    return MessageSummary(**base)


class FakeSession:
    """Сервер с полным списком писем — тем набором методов, который нужен
    полной синхронизации (sync_engine): STATUS, список UID, сводки по UID,
    флаги по UID."""

    def __init__(self, messages: dict[int, MessageSummary], uidvalidity: int = 1) -> None:
        self.messages = dict(messages)
        self.uidvalidity = uidvalidity
        self.calls: list[tuple] = []
        self.fetch_message_content = MagicMock(return_value=MessageContent(text="hello"))
        self.fetch_message_raw = MagicMock(return_value=b"raw bytes")
        self.set_marker = MagicMock()
        self.set_read = MagicMock()
        self.set_answered = MagicMock()
        self.move_messages = MagicMock()
        self.delete_messages = MagicMock()
        self.close = MagicMock()
        self.append_message = MagicMock()

    def folder_status(self, folder):
        self.calls.append(("status", folder))
        return self.uidvalidity, len(self.messages)

    def search_uids(self, folder, *, before=None):
        self.calls.append(("search", folder, before))
        return sorted(self.messages)

    def fetch_summaries_by_uids(self, folder, uids):
        self.calls.append(("summaries", folder, tuple(uids)))
        return [self.messages[u] for u in uids if u in self.messages]

    def fetch_flags(self, folder, uids):
        self.calls.append(("flags", folder, tuple(uids)))
        return {
            u: (self.messages[u].is_read, self.messages[u].is_answered, self.messages[u].marker_color)
            for u in uids if u in self.messages
        }


def _mailbox(tmp_path: Path, session: FakeSession):
    return patch("redmail.cache_store._db_path", return_value=tmp_path / "mail.sqlite3"), CachedMailbox(session, _account())


def test_first_open_syncs_folder_then_reads_only_from_local_copy(tmp_path: Path) -> None:
    session = FakeSession({1: _summary(1), 2: _summary(2)})
    ctx, mailbox = _mailbox(tmp_path, session)
    with ctx:
        assert mailbox.folder_summaries("INBOX") == []  # база пуста, в сеть из потока интерфейса не ходим
        assert not mailbox.is_folder_synced("INBOX")
        first = mailbox.refresh_folder("INBOX")
        session.calls.clear()
        mailbox.folder_summaries("INBOX")
        mailbox.folder_summaries("INBOX")
        assert mailbox.is_folder_synced("INBOX")

    assert [s.uid for s in first] == [2, 1]  # новые сверху
    assert session.calls == []  # после синхронизации папка читается только из базы


def test_refresh_adds_new_deletes_missing_and_updates_flags(tmp_path: Path) -> None:
    # Зеркало сервера: новое письмо появляется, удалённое на сервере
    # исчезает локально, смена флага в другом клиенте подхватывается.
    session = FakeSession({1: _summary(1), 2: _summary(2, is_read=False)})
    ctx, mailbox = _mailbox(tmp_path, session)
    with ctx:
        mailbox.refresh_folder("INBOX")
        del session.messages[1]
        session.messages[3] = _summary(3)
        session.messages[2] = _summary(2, is_read=True, marker_color="red")
        second = mailbox.refresh_folder("INBOX")

    assert [s.uid for s in second] == [3, 2]
    by_uid = {s.uid: s for s in second}
    assert by_uid[2].is_read is True and by_uid[2].marker_color == "red"


def test_local_marker_color_survives_server_that_only_keeps_flagged(tmp_path: Path) -> None:
    # VK хранит только \Flagged: цвет помнит локальная база.
    session = FakeSession({1: _summary(1, marker_color="red")})
    ctx, mailbox = _mailbox(tmp_path, session)
    with ctx:
        mailbox.refresh_folder("INBOX")
        mailbox.set_marker("INBOX", 1, "green")
        session.messages[1] = _summary(1, marker_color="red")  # сервер по-прежнему говорит лишь «флаг есть»
        after = mailbox.refresh_folder("INBOX")
        session.messages[1] = _summary(1, marker_color=None)  # флаг сняли в другом клиенте
        cleared = mailbox.refresh_folder("INBOX")

    session.set_marker.assert_called_once_with("INBOX", 1, "green", previous_color=UNKNOWN_MARKER)
    assert after[0].marker_color == "green"
    assert cleared[0].marker_color is None


def test_uidvalidity_change_rebuilds_local_copy(tmp_path: Path) -> None:
    session = FakeSession({5: _summary(5)}, uidvalidity=100)
    ctx, mailbox = _mailbox(tmp_path, session)
    with ctx:
        mailbox.refresh_folder("INBOX")
        session.uidvalidity = 200
        session.messages = {1: _summary(1, subject="new")}
        after = mailbox.refresh_folder("INBOX")
    assert [(s.uid, s.subject) for s in after] == [(1, "new")]


def test_message_content_cached_after_first_fetch(tmp_path: Path) -> None:
    session = FakeSession({1: _summary(1)})
    ctx, mailbox = _mailbox(tmp_path, session)
    with ctx:
        first = mailbox.message_content("INBOX", 1)
        second = mailbox.message_content("INBOX", 1)
    assert session.fetch_message_content.call_count == 1
    assert first.text == second.text == "hello"


def test_download_bodies_fetches_newest_first_and_defers_large(tmp_path: Path) -> None:
    session = FakeSession({1: _summary(1, size=100), 2: _summary(2, size=50 * 1024 * 1024), 3: _summary(3, size=200)})
    ctx, mailbox = _mailbox(tmp_path, session)
    with ctx:
        mailbox.refresh_folder("INBOX")
        downloaded = mailbox.download_bodies()
        remaining = mailbox.download_bodies()
    assert downloaded == 2 and remaining == 0
    fetched = [call.args for call in session.fetch_message_content.call_args_list]
    assert fetched == [("INBOX", 3), ("INBOX", 1)]  # большое письмо 2 отложено, порядок — от новых к старым


def test_set_read_and_delete_update_session_and_local_copy(tmp_path: Path) -> None:
    session = FakeSession({1: _summary(1), 2: _summary(2)})
    ctx, mailbox = _mailbox(tmp_path, session)
    with ctx:
        mailbox.refresh_folder("INBOX")
        mailbox.set_read("INBOX", 1, True)
        mailbox.delete_messages("INBOX", [2])
        cached = mailbox.folder_summaries("INBOX")
    session.set_read.assert_called_once_with("INBOX", 1, True)
    session.delete_messages.assert_called_once_with("INBOX", [2])
    assert [(s.uid, s.is_read) for s in cached] == [(1, True)]


def test_move_to_trash_updates_session_and_cache(tmp_path: Path) -> None:
    session = FakeSession({1: _summary(1), 2: _summary(2)})
    ctx, mailbox = _mailbox(tmp_path, session)
    with ctx:
        mailbox.refresh_folder("INBOX")
        mailbox.move_to_trash("INBOX", [1], "Trash")
        cached = mailbox.folder_summaries("INBOX")
    session.move_messages.assert_called_once_with("INBOX", [1], "Trash")
    assert [s.uid for s in cached] == [2]


def test_different_accounts_do_not_share_cache(tmp_path: Path) -> None:
    session_a = FakeSession({1: _summary(1)})
    session_b = FakeSession({7: _summary(7)})
    account_b = Account(host="imap.example.com", username="petr", password="secret")
    with patch("redmail.cache_store._db_path", return_value=tmp_path / "mail.sqlite3"):
        a = CachedMailbox(session_a, _account()).refresh_folder("INBOX")
        b = CachedMailbox(session_b, account_b).refresh_folder("INBOX")
    assert [s.uid for s in a] == [1] and [s.uid for s in b] == [7]


def test_message_raw_and_search_uids_delegate_uncached(tmp_path: Path) -> None:
    session = FakeSession({1: _summary(1)})
    ctx, mailbox = _mailbox(tmp_path, session)
    with ctx:
        assert mailbox.message_raw("INBOX", 1) == mailbox.message_raw("INBOX", 1) == b"raw bytes"
        assert mailbox.search_uids("INBOX", before=None) == [1]
    assert session.fetch_message_raw.call_count == 2


def test_message_content_uses_separate_reader_session(tmp_path: Path) -> None:
    # Открытие письма не должно ждать фоновой синхронизации/автоархива на
    # основном соединении: интерактивные чтения идут по второму IMAP-
    # соединению того же аккаунта, фоновые (background=True) — по основному.
    session = FakeSession({1: _summary(1)})
    reader = MagicMock()
    reader.fetch_message_content.return_value = MessageContent(text="via reader")
    reader.fetch_message_raw.return_value = b"raw via reader"
    ctx, mailbox = _mailbox(tmp_path, session)
    with ctx, patch("redmail.mailbox.isinstance", create=True, return_value=True), \
         patch("redmail.mailbox.ImapSession", return_value=reader) as factory:
        assert mailbox.message_content("INBOX", 1).text == "via reader"
        assert mailbox.message_raw("INBOX", 1) == b"raw via reader"
        assert mailbox.message_raw("INBOX", 1, background=True) == b"raw bytes"
        assert mailbox.message_content("INBOX", 1).text == "via reader"  # из кэша, второй раз в сеть не ходим
        mailbox.close()
    factory.assert_called_once()
    assert session.fetch_message_content.call_count == 0
    reader.fetch_message_content.assert_called_once_with("INBOX", 1)
    reader.close.assert_called_once()
    session.close.assert_called_once()


def test_reader_session_falls_back_to_main_when_second_connection_fails(tmp_path: Path) -> None:
    session = FakeSession({1: _summary(1)})
    ctx, mailbox = _mailbox(tmp_path, session)
    with ctx, patch("redmail.mailbox.isinstance", create=True, return_value=True), \
         patch("redmail.mailbox.ImapSession", side_effect=OSError("too many connections")):
        assert mailbox.message_content("INBOX", 1).text == "hello"
    session.fetch_message_content.assert_called_once_with("INBOX", 1)


def test_close_delegates_to_session() -> None:
    session = FakeSession({})
    CachedMailbox(session, _account()).close()
    session.close.assert_called_once()


def _archive_message(subject: str) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "a@example.com"
    msg.set_content("тело")
    return msg.as_bytes()


def test_archive_source_same_protocol_as_cached_mailbox(tmp_path: Path) -> None:
    archive_path = tmp_path / "test.rmarchive"
    archive_store.create_archive(archive_path)
    archive_store.append_raw_message(archive_path, "F", _archive_message("A"))

    source = ArchiveSource(archive_path)
    summaries = source.folder_summaries("F")
    assert len(summaries) == 1
    assert summaries[0].subject == "A"
    assert source.refresh_folder("F")[0].subject == "A"
    content = source.message_content("F", summaries[0].uid)
    assert content.text.strip() == "тело"


def test_archive_source_set_marker_and_delete(tmp_path: Path) -> None:
    archive_path = tmp_path / "test.rmarchive"
    archive_store.create_archive(archive_path)
    msg_id = archive_store.append_raw_message(archive_path, "F", _archive_message("A"))

    source = ArchiveSource(archive_path)
    source.set_marker("F", msg_id, "green")
    assert source.folder_summaries("F")[0].marker_color == "green"
    source.delete_messages("F", [msg_id])
    assert source.folder_summaries("F") == []


def test_archive_source_close_and_set_read_are_noops(tmp_path: Path) -> None:
    ArchiveSource(tmp_path / "test.rmarchive").close()
    ArchiveSource(tmp_path / "test.rmarchive").set_read("F", 1, False)
