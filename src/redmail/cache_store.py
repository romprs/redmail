from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from redmail.imap_client import Attachment, MessageContent, MessageSummary
from redmail.paths import app_dir

_SCHEMA = """
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
    flagged INTEGER NOT NULL DEFAULT 0,
    importance TEXT NOT NULL DEFAULT 'normal',
    body TEXT,
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
"""

# Столбцы добавились после первого релиза кэша — для баз, созданных раньше,
# CREATE TABLE IF NOT EXISTS их не добавит, поэтому досоздаём миграцией.
_MIGRATIONS = (
    "ALTER TABLE messages ADD COLUMN has_attachments INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE messages ADD COLUMN flagged INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE messages ADD COLUMN importance TEXT NOT NULL DEFAULT 'normal'",
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
            "SELECT uid, subject, sender, sender_email, date, message_id, has_attachments, flagged, importance "
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
            flagged=bool(flagged),
            importance=importance,
        )
        for uid, subject, sender, sender_email, date, message_id, has_attachments, flagged, importance in rows
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
            "has_attachments, flagged, importance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(account, folder, uid) DO UPDATE SET "
            "position = excluded.position, subject = excluded.subject, sender = excluded.sender, "
            "sender_email = excluded.sender_email, date = excluded.date, message_id = excluded.message_id, "
            "has_attachments = excluded.has_attachments, flagged = excluded.flagged, "
            "importance = excluded.importance",
            [
                (
                    account_key, folder, s.uid, position, s.subject, s.sender, s.sender_email, s.date,
                    s.message_id, int(s.has_attachments), int(s.flagged), s.importance,
                )
                for position, s in enumerate(summaries)
            ],
        )
        conn.commit()


def set_flagged(account_key: str, folder: str, uid: int, flagged: bool) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            "UPDATE messages SET flagged = ? WHERE account = ? AND folder = ? AND uid = ?",
            (int(flagged), account_key, folder, uid),
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
            "SELECT body FROM messages WHERE account = ? AND folder = ? AND uid = ? AND body IS NOT NULL",
            (account_key, folder, uid),
        ).fetchone()
        if row is None:
            return None
        attachment_rows = conn.execute(
            "SELECT filename, content_type, payload FROM attachments WHERE account = ? AND folder = ? AND uid = ?",
            (account_key, folder, uid),
        ).fetchall()
    attachments = [Attachment(filename=f, content_type=c, payload=p) for f, c, p in attachment_rows]
    return MessageContent(text=row[0], attachments=attachments)


def save_message_content(account_key: str, folder: str, uid: int, content: MessageContent) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO messages "
            "(account, folder, uid, position, subject, sender, sender_email, date, message_id, body) "
            "VALUES (?, ?, ?, -1, '', '', '', '', '', ?) "
            "ON CONFLICT(account, folder, uid) DO UPDATE SET body = excluded.body",
            (account_key, folder, uid, content.text),
        )
        conn.execute(
            "DELETE FROM attachments WHERE account = ? AND folder = ? AND uid = ?", (account_key, folder, uid)
        )
        conn.executemany(
            "INSERT INTO attachments (account, folder, uid, filename, content_type, payload) VALUES (?, ?, ?, ?, ?, ?)",
            [(account_key, folder, uid, a.filename, a.content_type, a.payload) for a in content.attachments],
        )
        conn.commit()
