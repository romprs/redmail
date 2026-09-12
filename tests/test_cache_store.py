from __future__ import annotations

from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch

from redmail import cache_store
from redmail.imap_client import Attachment, MessageContent, MessageSummary


def _summary(
    uid: int, subject: str, *, has_attachments: bool = False, marker_color: str | None = None,
    importance: str = "normal", is_read: bool = False,
) -> MessageSummary:
    return MessageSummary(
        uid=uid, subject=subject, sender="Ivan", sender_email="ivan@example.com", date="2026-08-18 10:00",
        message_id=f"<{uid}@example.com>", has_attachments=has_attachments, marker_color=marker_color,
        importance=importance, is_read=is_read,
    )


def test_needs_initial_vacuum_when_many_free_pages(tmp_path: Path) -> None:
    # Инкрементальное ужатие на фрагментированной базе не работало (по
    # странице за вызов) — при большой доле свободных страниц нужен полный
    # VACUUM при старте, даже если режим auto_vacuum уже INCREMENTAL.
    db_path = tmp_path / "mail.sqlite3"
    with patch("redmail.cache_store._db_path", return_value=db_path):
        with closing(cache_store._connect()) as conn:
            conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
            conn.execute("VACUUM")
        assert cache_store.needs_initial_vacuum(min_bytes=0) is False

        def fake_connect(free_pages: int):
            conn = MagicMock()
            values = {"PRAGMA auto_vacuum": (2,), "PRAGMA freelist_count": (free_pages,), "PRAGMA page_count": (900_000,)}
            conn.execute.side_effect = lambda sql: MagicMock(fetchone=lambda: values[sql])
            return conn

        with patch("redmail.cache_store._connect", return_value=fake_connect(500_000)):
            assert cache_store.needs_initial_vacuum(min_bytes=0) is True
        with patch("redmail.cache_store._connect", return_value=fake_connect(1_000)):
            assert cache_store.needs_initial_vacuum(min_bytes=0) is False


def test_folder_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    with patch("redmail.cache_store._db_path", return_value=db_path):
        assert cache_store.get_folder_exists("acc", "INBOX") is None

        summaries = [_summary(2, "Newer"), _summary(1, "Older")]
        cache_store.save_folder_summaries("acc", "INBOX", exists_count=5, summaries=summaries)

        assert cache_store.get_folder_exists("acc", "INBOX") == 5
        cached = cache_store.get_folder_summaries("acc", "INBOX")
        assert [s.uid for s in cached] == [2, 1]
        assert cached[0].subject == "Newer"


def test_save_folder_summaries_drops_removed_messages_but_keeps_cached_body(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    with patch("redmail.cache_store._db_path", return_value=db_path):
        cache_store.save_folder_summaries("acc", "INBOX", 2, [_summary(1, "A"), _summary(2, "B")])
        cache_store.save_message_content("acc", "INBOX", 1, MessageContent(text="body of A"))

        # Письмо 2 пропало (удалено на сервере), письмо 1 осталось — обновляем список.
        cache_store.save_folder_summaries("acc", "INBOX", 1, [_summary(1, "A")])

        cached = cache_store.get_folder_summaries("acc", "INBOX")
        assert [s.uid for s in cached] == [1]
        # Тело письма 1, закэшированное раньше, не должно было потереться повторным сохранением списка.
        content = cache_store.get_message_content("acc", "INBOX", 1)
        assert content is not None
        assert content.text == "body of A"


def test_message_content_round_trip_with_attachment(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    with patch("redmail.cache_store._db_path", return_value=db_path):
        assert cache_store.get_message_content("acc", "INBOX", 42) is None

        content = MessageContent(
            text="Привет!",
            attachments=[Attachment(filename="notes.txt", content_type="text/plain", payload=b"hello")],
        )
        cache_store.save_message_content("acc", "INBOX", 42, content)

        cached = cache_store.get_message_content("acc", "INBOX", 42)
        assert cached is not None
        assert cached.text == "Привет!"
        assert len(cached.attachments) == 1
        assert cached.attachments[0].filename == "notes.txt"
        assert cached.attachments[0].payload == b"hello"


def test_message_content_round_trip_with_html_and_inline_images(tmp_path: Path) -> None:
    # Регрессия: get_message_content/save_message_content раньше сохраняли
    # только content.text — html, inline_images и реквизиты (from_/to/cc/bcc)
    # молча терялись, и при повторном открытии письма из кэша оно всегда
    # показывалось как голый текст без картинок, даже если сервер отдавал
    # полноценный HTML (жалоба: "Ошибка отображения осталась").
    db_path = tmp_path / "cache.sqlite3"
    with patch("redmail.cache_store._db_path", return_value=db_path):
        content = MessageContent(
            text="Plain fallback",
            html="<p>Привет <img src='cid:logo123'></p>",
            inline_images={"logo123": ("image/png", b"\x89PNG...")},
            from_="sender@example.com",
            to="me@example.com",
            cc="cc@example.com",
            bcc="bcc@example.com",
        )
        cache_store.save_message_content("acc", "INBOX", 99, content)

        cached = cache_store.get_message_content("acc", "INBOX", 99)
        assert cached is not None
        assert cached.html == "<p>Привет <img src='cid:logo123'></p>"
        assert cached.inline_images == {"logo123": ("image/png", b"\x89PNG...")}
        assert cached.from_ == "sender@example.com"
        assert cached.to == "me@example.com"
        assert cached.cc == "cc@example.com"
        assert cached.bcc == "bcc@example.com"

        # Повторное сохранение (например, письмо переоткрыли) не должно
        # оставлять "хвост" старых inline-картинок от предыдущей версии.
        cache_store.save_message_content(
            "acc", "INBOX", 99, MessageContent(text="Plain fallback", html="<p>updated</p>")
        )
        cached_again = cache_store.get_message_content("acc", "INBOX", 99)
        assert cached_again is not None
        assert cached_again.html == "<p>updated</p>"
        assert cached_again.inline_images == {}


def test_message_content_can_be_cached_before_folder_is_listed(tmp_path: Path) -> None:
    # Например: письмо только что отправлено и сразу открыто, до того как
    # список папки вообще был закэширован.
    db_path = tmp_path / "cache.sqlite3"
    with patch("redmail.cache_store._db_path", return_value=db_path):
        cache_store.save_message_content("acc", "Sent", 7, MessageContent(text="hi"))
        cached = cache_store.get_message_content("acc", "Sent", 7)
        assert cached is not None
        assert cached.text == "hi"


def test_folder_summaries_round_trip_marker_attachment_importance(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    with patch("redmail.cache_store._db_path", return_value=db_path):
        cache_store.save_folder_summaries(
            "acc", "INBOX", 1, [_summary(1, "A", has_attachments=True, marker_color="red", importance="high")]
        )
        cached = cache_store.get_folder_summaries("acc", "INBOX")

    assert cached[0].has_attachments is True
    assert cached[0].marker_color == "red"
    assert cached[0].importance == "high"


def test_set_marker_updates_cached_summary(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    with patch("redmail.cache_store._db_path", return_value=db_path):
        cache_store.save_folder_summaries("acc", "INBOX", 1, [_summary(1, "A")])
        cache_store.set_marker("acc", "INBOX", 1, "blue")
        cached = cache_store.get_folder_summaries("acc", "INBOX")

    assert cached[0].marker_color == "blue"


def test_set_marker_none_clears_cached_summary(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    with patch("redmail.cache_store._db_path", return_value=db_path):
        cache_store.save_folder_summaries("acc", "INBOX", 1, [_summary(1, "A", marker_color="blue")])
        cache_store.set_marker("acc", "INBOX", 1, None)
        cached = cache_store.get_folder_summaries("acc", "INBOX")

    assert cached[0].marker_color is None


def test_is_read_round_trips_through_save_and_get(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    with patch("redmail.cache_store._db_path", return_value=db_path):
        cache_store.save_folder_summaries("acc", "INBOX", 1, [_summary(1, "A", is_read=True)])
        cached = cache_store.get_folder_summaries("acc", "INBOX")

    assert cached[0].is_read is True


def test_set_read_updates_cached_summary(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    with patch("redmail.cache_store._db_path", return_value=db_path):
        cache_store.save_folder_summaries("acc", "INBOX", 1, [_summary(1, "A", is_read=False)])
        cache_store.set_read("acc", "INBOX", 1, True)
        cached = cache_store.get_folder_summaries("acc", "INBOX")

    assert cached[0].is_read is True


def test_delete_messages_removes_from_cache(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    with patch("redmail.cache_store._db_path", return_value=db_path):
        cache_store.save_folder_summaries("acc", "INBOX", 2, [_summary(1, "A"), _summary(2, "B")])
        cache_store.save_message_content(
            "acc", "INBOX", 1,
            MessageContent(text="body", attachments=[Attachment(filename="f.txt", content_type="text/plain", payload=b"x")]),
        )

        cache_store.delete_messages("acc", "INBOX", [1])

        cached = cache_store.get_folder_summaries("acc", "INBOX")
        assert [s.uid for s in cached] == [2]
        assert cache_store.get_message_content("acc", "INBOX", 1) is None


def test_delete_messages_noop_for_empty_list(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    with patch("redmail.cache_store._db_path", return_value=db_path):
        cache_store.save_folder_summaries("acc", "INBOX", 1, [_summary(1, "A")])
        cache_store.delete_messages("acc", "INBOX", [])
        cached = cache_store.get_folder_summaries("acc", "INBOX")

    assert [s.uid for s in cached] == [1]


def test_schema_version_bump_wipes_stale_cache(tmp_path: Path) -> None:
    # Реальный баг: папку закэшировали до того, как в кэш добавили новое поле
    # (например, has_attachments) — без сброса версии эта папка так и осталась
    # бы со старыми значениями по умолчанию (скрепка не появлялась нигде,
    # кроме папок, которые пересохранялись по другой причине).
    db_path = tmp_path / "cache.sqlite3"
    with patch("redmail.cache_store._db_path", return_value=db_path):
        cache_store.save_folder_summaries("acc", "INBOX", 1, [_summary(1, "A", has_attachments=True)])
        assert cache_store.get_folder_exists("acc", "INBOX") == 1

    with patch("redmail.cache_store._db_path", return_value=db_path), \
         patch("redmail.cache_store._SCHEMA_VERSION", cache_store._SCHEMA_VERSION + 1), \
         patch("redmail.cache_store._MIN_COMPATIBLE_VERSION", cache_store._SCHEMA_VERSION + 1):
        # "Новая версия приложения" видит несовпадающий schema_version и должна
        # очистить всё, что было закэшировано старой версией.
        assert cache_store.get_folder_exists("acc", "INBOX") is None
        assert cache_store.get_folder_summaries("acc", "INBOX") == []


def test_compatible_schema_bump_keeps_data_and_marks_bodies(tmp_path: Path) -> None:
    # Переход 6 → 7 (полная локальная копия): данные — в т.ч. цвета маркеров,
    # которых сервер VK не хранит, — не стираются; у уже скачанных тел
    # проставляется body_state='full'.
    import sqlite3
    from contextlib import closing

    from redmail.imap_client import MessageContent

    db_path = tmp_path / "cache.sqlite3"
    with patch("redmail.cache_store._db_path", return_value=db_path):
        cache_store.save_folder_summaries("acc", "INBOX", 1, [_summary(1, "A", marker_color="green")])
        cache_store.save_message_content("acc", "INBOX", 1, MessageContent(text="body"))
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute("UPDATE meta SET value = '6' WHERE key = 'schema_version'")
            conn.execute("UPDATE messages SET body_state = 'none'")
            conn.commit()
        cached = cache_store.get_folder_summaries("acc", "INBOX")
        assert [s.marker_color for s in cached] == ["green"]
        assert cache_store.count_messages_without_body("acc", 10**9) == 0  # тело уже есть — докачивать нечего
