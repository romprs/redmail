from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from redmail.imap_client import Attachment, MessageContent, MessageSummary
from redmail.paths import app_dir

# Поднимаем при любом изменении формы того, что кэшируется в messages
# (новое поле и т.п.) — иначе старые строки молча остаются с значениями по
# умолчанию (например, без скрепки) и никогда не обновляются сами, пока
# папку не пересохранят по другой причине (см. save_folder_summaries).
#
# Версия 5: get_message_content/save_message_content раньше вообще не
# сохраняли content.html, content.inline_images и реквизиты (from_/to/cc/
# bcc) — при повторном открытии письма из кэша тело показывалось как голый
# текст без картинок (жалоба: "Ошибка отображения осталась"), хотя сервер
# отдавал корректный HTML. Старые кэшированные строки этих полей никогда не
# содержали, поэтому их нужно не мигрировать, а стереть — переисправит save
# при следующей загрузке письма с сервера.
#
# Версия 6: MessageSummary.to (адресаты — нужны для колонки "Кому" в папке
# "Отправленные", жалоба: "в отправленных нет поля адресат") и is_answered
# (флаг \Answered — жалоба: "если мы ответили на письмо, это никак не
# отражается") — старые закэшированные сводки папок этих полей не содержат.
_SCHEMA_VERSION = 6

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS folders (
    account TEXT NOT NULL,
    folder TEXT NOT NULL,
    exists_count INTEGER NOT NULL,
    PRIMARY KEY (account, folder)
);

CREATE TABLE IF NOT EXISTS messages (
    account TEXT NOT NULL,
    folder TEXT NOT NULL,
    uid INTEGER NOT NULL,
    position INTEGER NOT NULL,
    subject TEXT NOT NULL,
    sender TEXT NOT NULL,
    sender_email TEXT NOT NULL,
    date TEXT NOT NULL,
    message_id TEXT NOT NULL,
    has_attachments INTEGER NOT NULL DEFAULT 0,
    marker_color TEXT,
    importance TEXT NOT NULL DEFAULT 'normal',
    is_read INTEGER NOT NULL DEFAULT 0,
    body TEXT,
    recipients_to TEXT NOT NULL DEFAULT '',
    is_answered INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account, folder, uid)
);

CREATE TABLE IF NOT EXISTS attachments (
    account TEXT NOT NULL,
    folder TEXT NOT NULL,
    uid INTEGER NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    payload BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS inline_images (
    account TEXT NOT NULL,
    folder TEXT NOT NULL,
    uid INTEGER NOT NULL,
    content_id TEXT NOT NULL,
    content_type TEXT NOT NULL,
    payload BLOB NOT NULL
);
"""

# Столбцы, добавленные после первого релиза кэша — CREATE TABLE IF NOT EXISTS
# их для уже существующих баз не создаст, поэтому досоздаём миграцией.
_MIGRATIONS = (
    "ALTER TABLE messages ADD COLUMN has_attachments INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE messages ADD COLUMN marker_color TEXT",
    "ALTER TABLE messages ADD COLUMN importance TEXT NOT NULL DEFAULT 'normal'",
    "ALTER TABLE messages ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE messages ADD COLUMN html TEXT",
    "ALTER TABLE messages ADD COLUMN content_from TEXT",
    "ALTER TABLE messages ADD COLUMN content_to TEXT",
    "ALTER TABLE messages ADD COLUMN content_cc TEXT",
    "ALTER TABLE messages ADD COLUMN content_bcc TEXT",
    "ALTER TABLE messages ADD COLUMN recipients_to TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE messages ADD COLUMN is_answered INTEGER NOT NULL DEFAULT 0",
)


def _db_path() -> Path:
    return app_dir() / "cache.sqlite3"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    for migration in _MIGRATIONS:
        try:
            conn.execute(migration)
        except sqlite3.OperationalError:
            pass  # столбец уже есть

    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is None or row[0] != str(_SCHEMA_VERSION):
        # Формат закэшированных писем поменялся — старые строки не соответствуют
        # текущим полям (например, "\\Gmail-скрепка" видна только там, где кэш
        # уже пересчитан). Кэш — не источник истины, его безопасно стереть
        # целиком и заново набрать с сервера.
        conn.execute("DELETE FROM folders")
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM attachments")
        conn.execute("DELETE FROM inline_images")
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(_SCHEMA_VERSION),),
        )
        conn.commit()
    return conn


def get_folder_exists(account_key: str, folder: str) -> int | None:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT exists_count FROM folders WHERE account = ? AND folder = ?",
            (account_key, folder),
        ).fetchone()
    return row[0] if row else None


def get_folder_summaries(account_key: str, folder: str) -> list[MessageSummary]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT uid, subject, sender, sender_email, date, message_id, has_attachments, marker_color, "
            "importance, is_read, recipients_to, is_answered "
            "FROM messages WHERE account = ? AND folder = ? ORDER BY position ASC",
            (account_key, folder),
        ).fetchall()
    return [
        MessageSummary(
            uid=uid,
            subject=subject,
            sender=sender,
            sender_email=sender_email,
            date=date,
            message_id=message_id,
            has_attachments=bool(has_attachments),
            marker_color=marker_color,
            importance=importance,
            is_read=bool(is_read),
            to=recipients_to,
            is_answered=bool(is_answered),
        )
        for uid, subject, sender, sender_email, date, message_id, has_attachments, marker_color, importance, is_read,
        recipients_to, is_answered in rows
    ]


def save_folder_summaries(
    account_key: str, folder: str, exists_count: int, summaries: list[MessageSummary]
) -> None:
    """Кэширует сводки папки, не трогая уже закэшированные тела/вложения писем,
    которые в этом списке остались (только у пропавших — видимо, удалённых — чистим)."""
    uids = [s.uid for s in summaries]
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO folders (account, folder, exists_count) VALUES (?, ?, ?) "
            "ON CONFLICT(account, folder) DO UPDATE SET exists_count = excluded.exists_count",
            (account_key, folder, exists_count),
        )
        if uids:
            placeholders = ",".join("?" * len(uids))
            conn.execute(
                f"DELETE FROM messages WHERE account = ? AND folder = ? AND uid NOT IN ({placeholders})",
                (account_key, folder, *uids),
            )
        else:
            conn.execute("DELETE FROM messages WHERE account = ? AND folder = ?", (account_key, folder))
        conn.executemany(
            "INSERT INTO messages "
            "(account, folder, uid, position, subject, sender, sender_email, date, message_id, "
            "has_attachments, marker_color, importance, is_read, recipients_to, is_answered) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(account, folder, uid) DO UPDATE SET "
            "position = excluded.position, subject = excluded.subject, sender = excluded.sender, "
            "sender_email = excluded.sender_email, date = excluded.date, message_id = excluded.message_id, "
            "has_attachments = excluded.has_attachments, marker_color = excluded.marker_color, "
            "importance = excluded.importance, is_read = excluded.is_read, recipients_to = excluded.recipients_to, "
            "is_answered = excluded.is_answered",
            [
                (
                    account_key, folder, s.uid, position, s.subject, s.sender, s.sender_email, s.date,
                    s.message_id, int(s.has_attachments), s.marker_color, s.importance, int(s.is_read), s.to,
                    int(s.is_answered),
                )
                for position, s in enumerate(summaries)
            ],
        )
        conn.commit()


def set_answered(account_key: str, folder: str, uid: int) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            "UPDATE messages SET is_answered = 1 WHERE account = ? AND folder = ? AND uid = ?",
            (account_key, folder, uid),
        )
        conn.commit()


def set_marker(account_key: str, folder: str, uid: int, color: str | None) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            "UPDATE messages SET marker_color = ? WHERE account = ? AND folder = ? AND uid = ?",
            (color, account_key, folder, uid),
        )
        conn.commit()


def set_read(account_key: str, folder: str, uid: int, read: bool) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            "UPDATE messages SET is_read = ? WHERE account = ? AND folder = ? AND uid = ?",
            (int(read), account_key, folder, uid),
        )
        conn.commit()


def delete_messages(account_key: str, folder: str, uids: list[int]) -> None:
    if not uids:
        return
    placeholders = ",".join("?" * len(uids))
    with closing(_connect()) as conn:
        conn.execute(
            f"DELETE FROM messages WHERE account = ? AND folder = ? AND uid IN ({placeholders})",
            (account_key, folder, *uids),
        )
        conn.execute(
            f"DELETE FROM attachments WHERE account = ? AND folder = ? AND uid IN ({placeholders})",
            (account_key, folder, *uids),
        )
        conn.commit()


def get_message_content(account_key: str, folder: str, uid: int) -> MessageContent | None:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT body, html, content_from, content_to, content_cc, content_bcc, subject "
            "FROM messages WHERE account = ? AND folder = ? AND uid = ? AND body IS NOT NULL",
            (account_key, folder, uid),
        ).fetchone()
        if row is None:
            return None
        attachment_rows = conn.execute(
            "SELECT filename, content_type, payload FROM attachments WHERE account = ? AND folder = ? AND uid = ?",
            (account_key, folder, uid),
        ).fetchall()
        inline_rows = conn.execute(
            "SELECT content_id, content_type, payload FROM inline_images "
            "WHERE account = ? AND folder = ? AND uid = ?",
            (account_key, folder, uid),
        ).fetchall()
    attachments = [Attachment(filename=f, content_type=c, payload=p) for f, c, p in attachment_rows]
    inline_images = {content_id: (content_type, payload) for content_id, content_type, payload in inline_rows}
    return MessageContent(
        text=row[0],
        attachments=attachments,
        html=row[1] or "",
        inline_images=inline_images,
        subject=row[6] or "",
        from_=row[2] or "",
        to=row[3] or "",
        cc=row[4] or "",
        bcc=row[5] or "",
    )


def save_message_content(account_key: str, folder: str, uid: int, content: MessageContent) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO messages "
            "(account, folder, uid, position, subject, sender, sender_email, date, message_id, "
            "body, html, content_from, content_to, content_cc, content_bcc) "
            "VALUES (?, ?, ?, -1, '', '', '', '', '', ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(account, folder, uid) DO UPDATE SET "
            "body = excluded.body, html = excluded.html, content_from = excluded.content_from, "
            "content_to = excluded.content_to, content_cc = excluded.content_cc, content_bcc = excluded.content_bcc",
            (
                account_key,
                folder,
                uid,
                content.text,
                content.html,
                content.from_,
                content.to,
                content.cc,
                content.bcc,
            ),
        )
        conn.execute(
            "DELETE FROM attachments WHERE account = ? AND folder = ? AND uid = ?", (account_key, folder, uid)
        )
        conn.executemany(
            "INSERT INTO attachments (account, folder, uid, filename, content_type, payload) VALUES (?, ?, ?, ?, ?, ?)",
            [(account_key, folder, uid, a.filename, a.content_type, a.payload) for a in content.attachments],
        )
        conn.execute(
            "DELETE FROM inline_images WHERE account = ? AND folder = ? AND uid = ?", (account_key, folder, uid)
        )
        conn.executemany(
            "INSERT INTO inline_images (account, folder, uid, content_id, content_type, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (account_key, folder, uid, content_id, content_type, payload)
                for content_id, (content_type, payload) in content.inline_images.items()
            ],
        )
        conn.commit()
