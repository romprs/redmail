from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from redmail import profile
from redmail.imap_client import Attachment, MessageContent, MessageSummary

# Локальная копия ящика (аналог OST в Outlook) — <профиль>/mail.sqlite3.
#
# Версия 7 (переход на хранение «как в Outlook»): база хранит ВСЕ письма
# всех папок, а не последние 50; появились size (RFC822.SIZE — письма
# больше порога скачиваются только по запросу), body_state
# ('none' — только заголовок, 'full' — тело скачано, 'deferred' —
# отложено как большое), uidvalidity/headers_complete у папок. Список
# теперь упорядочен по UID (новые сверху), position больше не значим.
# Версии ниже 6 стирались целиком (иначе не совпадали поля); с 6 на 7
# данные сохраняются — только добавляются столбцы (миграции ниже), чтобы
# не терять цвета маркеров, которые сервер VK не хранит.
_SCHEMA_VERSION = 7
_MIN_COMPATIBLE_VERSION = 6

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

# Столбцы, добавленные после первого релиза — CREATE TABLE IF NOT EXISTS
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
    # v7
    "ALTER TABLE messages ADD COLUMN size INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE messages ADD COLUMN body_state TEXT NOT NULL DEFAULT 'none'",
    "ALTER TABLE folders ADD COLUMN uidvalidity INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE folders ADD COLUMN headers_complete INTEGER NOT NULL DEFAULT 0",
    "CREATE INDEX IF NOT EXISTS idx_messages_body_state ON messages (account, body_state, uid)",
    # автоархив: письмо перенесено в файл архива, тело читается оттуда
    "ALTER TABLE messages ADD COLUMN archive_path TEXT",
    "ALTER TABLE messages ADD COLUMN archive_uid INTEGER",
    "CREATE INDEX IF NOT EXISTS idx_attachments_msg ON attachments (account, folder, uid)",
    "CREATE INDEX IF NOT EXISTS idx_inline_msg ON inline_images (account, folder, uid)",
)

_SUMMARY_COLUMNS = (
    "uid, subject, sender, sender_email, date, message_id, has_attachments, marker_color, "
    "importance, is_read, recipients_to, is_answered, size"
)


def _db_path() -> Path:
    return profile.mail_db_path()


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # timeout: VACUUM/ужатие после автоархива держат базу минуты — другие
    # потоки (синхронизация, окно) должны ждать, а не падать с
    # "database is locked" через 5 секунд по умолчанию.
    conn = sqlite3.connect(path, timeout=600)
    conn.executescript(_SCHEMA)
    for migration in _MIGRATIONS:
        try:
            conn.execute(migration)
        except sqlite3.OperationalError:
            pass  # столбец/индекс уже есть

    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    stored = int(row[0]) if row and str(row[0]).isdigit() else 0
    if stored != _SCHEMA_VERSION:
        if stored < _MIN_COMPATIBLE_VERSION:
            # Формат закэшированных писем поменялся несовместимо — старые
            # строки не соответствуют текущим полям. Кэш безопасно стереть
            # целиком и заново набрать с сервера.
            conn.execute("DELETE FROM folders")
            conn.execute("DELETE FROM messages")
            conn.execute("DELETE FROM attachments")
            conn.execute("DELETE FROM inline_images")
        else:
            # 6 → 7: у уже скачанных тел проставляем body_state='full', у
            # старых строк без тела остаётся 'none' — их докачает фон.
            conn.execute("UPDATE messages SET body_state = 'full' WHERE body IS NOT NULL AND body_state = 'none'")
            conn.execute("UPDATE folders SET headers_complete = 0")
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(_SCHEMA_VERSION),),
        )
        conn.commit()
    return conn


def _row_to_summary(row) -> MessageSummary:
    (uid, subject, sender, sender_email, date, message_id, has_attachments, marker_color, importance, is_read,
     recipients_to, is_answered, size) = row
    return MessageSummary(
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
        size=size or 0,
    )


# ---- Папки -------------------------------------------------------------------


def get_folder_exists(account_key: str, folder: str) -> int | None:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT exists_count FROM folders WHERE account = ? AND folder = ?",
            (account_key, folder),
        ).fetchone()
    return row[0] if row else None


def get_folder_uidvalidity(account_key: str, folder: str) -> int:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT uidvalidity FROM folders WHERE account = ? AND folder = ?", (account_key, folder)
        ).fetchone()
    return int(row[0]) if row else 0


def is_folder_synced(account_key: str, folder: str) -> bool:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT headers_complete FROM folders WHERE account = ? AND folder = ?", (account_key, folder)
        ).fetchone()
    return bool(row and row[0])


def set_folder_state(
    account_key: str, folder: str, *, uidvalidity: int, exists_count: int, headers_complete: bool
) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO folders (account, folder, exists_count, uidvalidity, headers_complete) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(account, folder) DO UPDATE SET exists_count = excluded.exists_count, "
            "uidvalidity = excluded.uidvalidity, headers_complete = excluded.headers_complete",
            (account_key, folder, exists_count, int(uidvalidity or 0), int(headers_complete)),
        )
        conn.commit()


def delete_folder(account_key: str, folder: str) -> None:
    with closing(_connect()) as conn:
        for table in ("messages", "attachments", "inline_images"):
            conn.execute(f"DELETE FROM {table} WHERE account = ? AND folder = ?", (account_key, folder))
        conn.execute("DELETE FROM folders WHERE account = ? AND folder = ?", (account_key, folder))
        conn.commit()


def list_folders(account_key: str) -> list[str]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT folder FROM folders WHERE account = ? ORDER BY folder", (account_key,)).fetchall()
    return [r[0] for r in rows]


# ---- Сводки ------------------------------------------------------------------


def get_folder_summaries(account_key: str, folder: str, limit: int | None = None) -> list[MessageSummary]:
    """Сводки папки, новые сверху (по UID — сервер выдаёт их по возрастанию
    по мере поступления). Строки без сводки (position = -1: тело
    закэшировано раньше, чем папка перечислена) не показываются."""
    sql = (
        f"SELECT {_SUMMARY_COLUMNS} FROM messages WHERE account = ? AND folder = ? AND position >= 0 "
        "ORDER BY uid DESC"
    )
    params: tuple = (account_key, folder)
    if limit is not None:
        sql += " LIMIT ?"
        params = (*params, limit)
    with closing(_connect()) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_summary(row) for row in rows]


def count_folder_summaries(account_key: str, folder: str) -> int:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE account = ? AND folder = ? AND position >= 0", (account_key, folder)
        ).fetchone()
    return int(row[0]) if row else 0


def get_folder_uids(account_key: str, folder: str, *, include_archived: bool = False) -> set[int]:
    """UID писем папки, известных локально. include_archived=True — вместе с
    перенесёнными в архив (их заголовки уже есть, качать заново незачем);
    False — только «живые» на сервере (для зеркала удалений)."""
    clause = "" if include_archived else " AND archive_path IS NULL"
    with closing(_connect()) as conn:
        rows = conn.execute(
            f"SELECT uid FROM messages WHERE account = ? AND folder = ? AND position >= 0{clause}",
            (account_key, folder),
        ).fetchall()
    return {r[0] for r in rows}


def get_folder_flags(account_key: str, folder: str) -> dict[int, tuple[bool, bool, str | None]]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT uid, is_read, is_answered, marker_color FROM messages WHERE account = ? AND folder = ? "
            "AND position >= 0 AND archive_path IS NULL",
            (account_key, folder),
        ).fetchall()
    return {uid: (bool(r), bool(a), m) for uid, r, a, m in rows}


def upsert_summaries(account_key: str, folder: str, summaries: list[MessageSummary]) -> None:
    """Добавляет/обновляет сводки, не трогая уже скачанные тела и не удаляя
    ничего (удаление — отдельно, по списку пропавших на сервере). Цвет
    маркера, уже известный локально, сохраняется, пока сервер говорит, что
    маркер есть (\\Flagged) — конкретный цвет сервер VK не хранит."""
    if not summaries:
        return
    with closing(_connect()) as conn:
        conn.executemany(
            "INSERT INTO messages "
            "(account, folder, uid, position, subject, sender, sender_email, date, message_id, "
            "has_attachments, marker_color, importance, is_read, recipients_to, is_answered, size) "
            "VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(account, folder, uid) DO UPDATE SET "
            "position = 0, subject = excluded.subject, sender = excluded.sender, "
            "sender_email = excluded.sender_email, date = excluded.date, message_id = excluded.message_id, "
            "has_attachments = excluded.has_attachments, "
            "marker_color = CASE WHEN excluded.marker_color IS NULL THEN NULL "
            "  WHEN messages.marker_color IS NOT NULL THEN messages.marker_color ELSE excluded.marker_color END, "
            "importance = excluded.importance, is_read = excluded.is_read, recipients_to = excluded.recipients_to, "
            "is_answered = excluded.is_answered, size = excluded.size",
            [
                (
                    account_key, folder, s.uid, s.subject, s.sender, s.sender_email, s.date,
                    s.message_id, int(s.has_attachments), s.marker_color, s.importance, int(s.is_read), s.to,
                    int(s.is_answered), int(getattr(s, "size", 0) or 0),
                )
                for s in summaries
            ],
        )
        conn.commit()


def save_folder_summaries(
    account_key: str, folder: str, exists_count: int, summaries: list[MessageSummary]
) -> None:
    """Полный список папки: всё, чего нет в списке, удаляется; тела
    оставшихся писем не трогаются."""
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
        conn.commit()
    upsert_summaries(account_key, folder, summaries)


def update_flags(account_key: str, folder: str, changes: list[tuple[int, bool, bool, str | None]]) -> None:
    if not changes:
        return
    with closing(_connect()) as conn:
        conn.executemany(
            "UPDATE messages SET is_read = ?, is_answered = ?, marker_color = ? WHERE account = ? AND folder = ? AND uid = ?",
            [(int(r), int(a), m, account_key, folder, uid) for uid, r, a, m in changes],
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
    with closing(_connect()) as conn:
        for part_start in range(0, len(uids), 500):
            part = uids[part_start:part_start + 500]
            placeholders = ",".join("?" * len(part))
            for table in ("messages", "attachments", "inline_images"):
                conn.execute(
                    f"DELETE FROM {table} WHERE account = ? AND folder = ? AND uid IN ({placeholders})",
                    (account_key, folder, *part),
                )
        conn.commit()


# ---- Тела писем --------------------------------------------------------------


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
            "body, html, content_from, content_to, content_cc, content_bcc, body_state) "
            "VALUES (?, ?, ?, -1, '', '', '', '', '', ?, ?, ?, ?, ?, ?, 'full') "
            "ON CONFLICT(account, folder, uid) DO UPDATE SET "
            "body = excluded.body, html = excluded.html, content_from = excluded.content_from, "
            "content_to = excluded.content_to, content_cc = excluded.content_cc, content_bcc = excluded.content_bcc, "
            "body_state = 'full'",
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


def set_body_state(account_key: str, folder: str, uid: int, state: str) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            "UPDATE messages SET body_state = ? WHERE account = ? AND folder = ? AND uid = ?",
            (state, account_key, folder, uid),
        )
        conn.commit()


def _skip_clause(skip_folders: tuple[str, ...]) -> tuple[str, tuple]:
    if not skip_folders:
        return "", ()
    placeholders = ",".join("?" * len(skip_folders))
    return f" AND folder NOT IN ({placeholders})", tuple(skip_folders)


def messages_without_body(
    account_key: str, max_bytes: int, *, limit: int = 50, skip_folders: tuple[str, ...] = ()
) -> list[tuple[str, int, int]]:
    """(папка, uid, размер) писем без тела, от новых к старым, не больше
    max_bytes (большие — по запросу, см. defer_large_messages)."""
    clause, params = _skip_clause(skip_folders)
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT folder, uid, size FROM messages WHERE account = ? AND body_state = 'none' AND position >= 0 "
            f"AND size <= ?{clause} ORDER BY uid DESC LIMIT ?",
            (account_key, max_bytes, *params, limit),
        ).fetchall()
    return [(f, u, s) for f, u, s in rows]


def count_messages_without_body(account_key: str, max_bytes: int, *, skip_folders: tuple[str, ...] = ()) -> int:
    clause, params = _skip_clause(skip_folders)
    with closing(_connect()) as conn:
        row = conn.execute(
            f"SELECT COUNT(*) FROM messages WHERE account = ? AND body_state = 'none' AND position >= 0 AND size <= ?{clause}",
            (account_key, max_bytes, *params),
        ).fetchone()
    return int(row[0]) if row else 0


def defer_large_messages(account_key: str, max_bytes: int) -> int:
    with closing(_connect()) as conn:
        cur = conn.execute(
            "UPDATE messages SET body_state = 'deferred' WHERE account = ? AND body_state = 'none' AND size > ?",
            (account_key, max_bytes),
        )
        conn.commit()
        return cur.rowcount


# ---- Автоархив (единый индекс: архивные письма остаются в списке) -------------


def oldest_messages(account_key: str, *, limit: int | None = None) -> list[tuple[str, int, int, str]]:
    """(папка, uid, размер, дата) кандидатов автоархива от старых к новым —
    только письма, тела которых реально лежат в базе (body_state='full'):
    у писем с одними заголовками локально ~1 КБ, и их перенос ничего не
    освобождает (реальная находка на .80: раунд из 300 самых старых писем
    ужал базу на 40 МБ вместо 248)."""
    sql = (
        "SELECT folder, uid, size, date FROM messages WHERE account = ? AND position >= 0 AND archive_path IS NULL "
        "AND body_state = 'full' ORDER BY date ASC, uid ASC"
    )
    params: tuple = (account_key,)
    if limit is not None:
        sql += " LIMIT ?"
        params = (*params, limit)
    with closing(_connect()) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [(f, u, int(s or 0), d or "") for f, u, s, d in rows]


def mark_archived(account_key: str, folder: str, uid: int, archive_path: str, archive_uid: int) -> None:
    """Письмо перенесено в архив: тело и вложения из основной базы убираются,
    строка остаётся с указателем на файл архива."""
    with closing(_connect()) as conn:
        conn.execute(
            "UPDATE messages SET archive_path = ?, archive_uid = ?, body = NULL, html = NULL, body_state = 'archived' "
            "WHERE account = ? AND folder = ? AND uid = ?",
            (archive_path, archive_uid, account_key, folder, uid),
        )
        for table in ("attachments", "inline_images"):
            conn.execute(f"DELETE FROM {table} WHERE account = ? AND folder = ? AND uid = ?", (account_key, folder, uid))
        conn.commit()


def get_archive_ref(account_key: str, folder: str, uid: int) -> tuple[str, int] | None:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT archive_path, archive_uid FROM messages WHERE account = ? AND folder = ? AND uid = ? "
            "AND archive_path IS NOT NULL",
            (account_key, folder, uid),
        ).fetchone()
    return (row[0], int(row[1])) if row else None


def relocate_archive_path(old_path: str, new_path: str) -> int:
    """Файл архива переехал (например, из старого каталога в профиль) —
    переписать указатели в индексе."""
    with closing(_connect()) as conn:
        cur = conn.execute("UPDATE messages SET archive_path = ? WHERE archive_path = ?", (new_path, old_path))
        conn.commit()
        return cur.rowcount


def count_archived(account_key: str) -> int:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE account = ? AND archive_path IS NOT NULL", (account_key,)
        ).fetchone()
    return int(row[0]) if row else 0


VACUUM_STEP_PAGES = 20000  # ≈ 80 МБ за шаг: замок на базе — доли секунды, окно не замирает


def needs_initial_vacuum(min_bytes: int = 200 * 1024 * 1024) -> bool:
    """Базу нужно один раз перевести в режим auto_vacuum=INCREMENTAL полным
    VACUUM (минуты на нескольких ГБ) — делается при старте программы под
    заставкой, а не во время работы (окно замирало: жалоба "почему завис
    почтовый клиент")."""
    path = _db_path()
    if not path.exists() or path.stat().st_size < min_bytes:
        return False
    with closing(_connect()) as conn:
        mode = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
    return int(mode or 0) != 2


def initial_vacuum() -> None:
    with closing(_connect()) as conn:
        conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
        conn.execute("VACUUM")


def vacuum(stop=None) -> int:
    """Вернуть место после автоархива — SQLite сам файл не ужимает.
    Только инкрементально, порциями по VACUUM_STEP_PAGES страниц: каждый
    шаг держит базу доли секунды, между шагами окно и синхронизация
    успевают поработать. Если база ещё не переведена в режим
    auto_vacuum=INCREMENTAL — ничего не делает (переведёт старт программы).
    Возвращает число освобождённых страниц."""
    freed = 0
    with closing(_connect()) as conn:
        mode = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
    if int(mode or 0) != 2:
        return 0
    while stop is None or not stop.is_set():
        with closing(_connect()) as conn:
            before = conn.execute("PRAGMA freelist_count").fetchone()[0]
            if not before:
                break
            conn.execute(f"PRAGMA incremental_vacuum({VACUUM_STEP_PAGES})")
            after = conn.execute("PRAGMA freelist_count").fetchone()[0]
        freed += max(0, int(before) - int(after))
        if after >= before:
            break
    return freed


def storage_stats(account_key: str | None = None) -> dict:
    """Размер базы и счётчики — для настроек и автоархива по размеру."""
    path = _db_path()
    size = path.stat().st_size if path.exists() else 0
    with closing(_connect()) as conn:
        where = "WHERE account = ?" if account_key else ""
        params: tuple = (account_key,) if account_key else ()
        total = conn.execute(f"SELECT COUNT(*) FROM messages {where}", params).fetchone()[0]
        with_body = conn.execute(
            f"SELECT COUNT(*) FROM messages {where + ' AND' if where else 'WHERE'} body_state = 'full'", params
        ).fetchone()[0]
    return {"db_bytes": size, "messages": int(total), "with_body": int(with_body)}
