from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path
from unittest.mock import MagicMock, patch

from redmail import archive_store, autoarchive, cache_store
from redmail.imap_client import Account, MessageSummary
from redmail.mailbox import CachedMailbox


def _raw(uid: int, size: int = 200) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = f"Old {uid}"
    msg["From"] = "a@example.com"
    msg.set_content("x" * size)
    return msg.as_bytes()


def _summary(uid: int, folder_date: str, size: int = 200, **kwargs) -> MessageSummary:
    base = dict(uid=uid, subject=f"Old {uid}", sender="A", sender_email="a@example.com", date=folder_date,
                message_id=f"<{uid}@x>", size=size)
    base.update(kwargs)
    return MessageSummary(**base)


class Server:
    def __init__(self, messages: dict[str, dict[int, MessageSummary]]) -> None:
        self.messages = messages
        self.deleted: list[tuple[str, list[int]]] = []
        self.raw_calls: list[tuple[str, int]] = []

    def folder_status(self, folder):
        return 1, len(self.messages.get(folder, {}))

    def search_uids(self, folder, *, before=None):
        return sorted(self.messages.get(folder, {}))

    def fetch_summaries_by_uids(self, folder, uids):
        return [self.messages[folder][u] for u in uids]

    def fetch_flags(self, folder, uids):
        return {u: (False, False, None) for u in uids}

    def fetch_message_raw(self, folder, uid):
        self.raw_calls.append((folder, uid))
        return _raw(uid)

    def fetch_message_content(self, folder, uid):
        from email import message_from_bytes

        from redmail.imap_client import extract_content

        return extract_content(message_from_bytes(_raw(uid)))

    def delete_messages(self, folder, uids):
        self.deleted.append((folder, list(uids)))
        for u in uids:
            self.messages[folder].pop(u, None)

    def close(self):
        pass


def _setup(tmp_path: Path):
    server = Server({
        "INBOX": {1: _summary(1, "2024-01-01 10:00", 1000), 2: _summary(2, "2025-01-01 10:00", 1000), 3: _summary(3, "2026-09-01 10:00", 1000)},
        "Trash": {9: _summary(9, "2020-01-01 10:00", 1000)},
    })
    mailbox = CachedMailbox(server, Account(host="imap.x", username="ivan", password="p"))
    return server, mailbox


def test_plan_picks_oldest_non_trash_until_target_and_nothing_below_threshold(tmp_path: Path) -> None:
    server, mailbox = _setup(tmp_path)
    with patch("redmail.cache_store._db_path", return_value=tmp_path / "mail.sqlite3"):
        mailbox.refresh_folder("INBOX")
        mailbox.refresh_folder("Trash")
        big = cache_store.storage_stats()["db_bytes"] * 10
        assert autoarchive.make_plan(mailbox.account_key, big).candidates == []  # база меньше порога — ничего
        plan = autoarchive.make_plan(mailbox.account_key, 1, skip_folders={"Trash"})
    # порог 1 байт: освободить нужно почти всю базу, кандидаты — от старых к новым, корзина пропущена
    assert [(f, u) for f, u, _s, _d in plan.candidates][:2] == [("INBOX", 1), ("INBOX", 2)]
    assert all(f != "Trash" for f, *_rest in plan.candidates)


def test_run_archives_verifies_then_deletes_on_server_and_keeps_index(tmp_path: Path) -> None:
    # Порядок: скачать целиком → записать в архив → проверить → удалить на
    # сервере → пометить в индексе. Письмо остаётся в списке папки, тело
    # читается из архива, флаги/удаление больше не трогают сервер.
    server, mailbox = _setup(tmp_path)
    archive_dir = tmp_path / "archives"
    with patch("redmail.cache_store._db_path", return_value=tmp_path / "mail.sqlite3"):
        mailbox.refresh_folder("INBOX")
        plan = autoarchive.make_plan(mailbox.account_key, 1)
        plan.candidates = plan.candidates[:2]  # uid 1 и 2
        plan.threshold_bytes = 10**9  # без ротации — оба письма в одном файле
        result = autoarchive.run(mailbox, plan, archive_dir, delete_on_server=True)

        assert result.archived == 2 and result.failed == 0
        assert server.deleted == [("INBOX", [1]), ("INBOX", [2])]
        assert [s.uid for s in mailbox.folder_summaries("INBOX")] == [3, 2, 1]  # единый индекс
        assert cache_store.count_archived(mailbox.account_key) == 2
        assert mailbox.message_content("INBOX", 1).subject == "Old 1"  # тело — из архива
        assert cache_store.get_message_content(mailbox.account_key, "INBOX", 1) is None  # в основной базе тела нет

        archive_path = Path(result.files[0])
        assert archive_store.list_messages(archive_path, "INBOX")[0].subject in ("Old 1", "Old 2")

        # Повторная синхронизация не считает архивные письма «удалёнными на сервере».
        again = mailbox.refresh_folder("INBOX")
        assert [s.uid for s in again] == [3, 2, 1]

        # Флаги и удаление архивного письма — без обращения к серверу.
        server.delete_messages = MagicMock()
        mailbox.set_read("INBOX", 1, True)
        mailbox.delete_messages("INBOX", [1])
        server.delete_messages.assert_not_called()
        assert [s.uid for s in mailbox.folder_summaries("INBOX")] == [3, 2]
        assert len(archive_store.list_messages(archive_path, "INBOX")) == 1


def test_run_skips_message_and_keeps_it_on_server_when_archive_write_fails(tmp_path: Path) -> None:
    server, mailbox = _setup(tmp_path)
    with patch("redmail.cache_store._db_path", return_value=tmp_path / "mail.sqlite3"):
        mailbox.refresh_folder("INBOX")
        plan = autoarchive.make_plan(mailbox.account_key, 1)
        plan.candidates = plan.candidates[:1]
        with patch("redmail.autoarchive.archive_store.append_raw_message", side_effect=OSError("disk full")):
            result = autoarchive.run(mailbox, plan, tmp_path / "archives", delete_on_server=True)
    assert result.archived == 0 and result.failed == 1
    assert server.deleted == []  # без проверенной записи в архив ничего не удаляется


def test_archive_files_rotate_by_size(tmp_path: Path) -> None:
    server, mailbox = _setup(tmp_path)
    with patch("redmail.cache_store._db_path", return_value=tmp_path / "mail.sqlite3"):
        mailbox.refresh_folder("INBOX")
        plan = autoarchive.make_plan(mailbox.account_key, 1)
        plan.threshold_bytes = 1  # каждый файл «переполнен» сразу после первого письма
        result = autoarchive.run(mailbox, plan, tmp_path / "archives")
    assert result.archived == 3
    assert len(result.files) == 3 and sorted(Path(f).name for f in result.files)[0].startswith("autoarchive-")


def test_default_mode_keeps_messages_on_server(tmp_path: Path) -> None:
    # По умолчанию автоархив только освобождает локальную базу: письмо в
    # архиве и в индексе, на сервере остаётся.
    server, mailbox = _setup(tmp_path)
    with patch("redmail.cache_store._db_path", return_value=tmp_path / "mail.sqlite3"):
        mailbox.refresh_folder("INBOX")
        plan = autoarchive.make_plan(mailbox.account_key, 1)
        plan.candidates = plan.candidates[:1]
        plan.threshold_bytes = 10**9
        result = autoarchive.run(mailbox, plan, tmp_path / "archives")
        assert result.archived == 1 and server.deleted == []
        assert 1 in server.messages["INBOX"]
        assert mailbox.message_content("INBOX", 1).subject == "Old 1"
        assert [s.uid for s in mailbox.refresh_folder("INBOX")] == [3, 2, 1]


def test_relocate_archives_moves_files_into_profile_and_fixes_index(tmp_path: Path) -> None:
    # Договорённость: архивы живут в профиле; файлы из старого каталога
    # переезжают, указатели в индексе обновляются, тела читаются дальше.
    server, mailbox = _setup(tmp_path)
    old_dir = tmp_path / "old_archives"
    new_dir = tmp_path / "profile" / "archives"
    with patch("redmail.cache_store._db_path", return_value=tmp_path / "mail.sqlite3"):
        mailbox.refresh_folder("INBOX")
        plan = autoarchive.make_plan(mailbox.account_key, 1)
        plan.candidates = plan.candidates[:1]
        plan.threshold_bytes = 10**9
        result = autoarchive.run(mailbox, plan, old_dir)
        assert Path(result.files[0]).parent == old_dir
        moved = autoarchive.relocate_archives(old_dir, new_dir)
        assert moved == 1 and not list(old_dir.glob("*.rmarchive")) and list(new_dir.glob("autoarchive-*.rmarchive"))
        assert mailbox.message_content("INBOX", 1).subject == "Old 1"
        assert autoarchive.relocate_archives(old_dir, new_dir) == 0  # повторно — нечего
