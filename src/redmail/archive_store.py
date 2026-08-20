from __future__ import annotations

import mailbox
import sqlite3
from contextlib import closing
from email import message_from_bytes
from email.header import decode_header
from email.message import EmailMessage, Message
from email.utils import formatdate, parseaddr
from pathlib import Path

from redmail.imap_client import MessageContent, MessageSummary, extract_content, parse_importance

# Свой формат: один файл SQLite на архив. Не PST — по назначению аналог
# (папка с письмами, которую можно отключить от сервера и открыть локально),
# но не побайтово совместим с ним. Реальный .pst слишком сложен, чтобы
# писать самим (см. обсуждение — читаем чужие через pypff, свои не пишем).
_FORMAT_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder TEXT NOT NULL,
    subject TEXT NOT NULL,
    sender TEXT NOT NULL,
    sender_email TEXT NOT NULL,
    date TEXT NOT NULL,
    message_id TEXT NOT NULL,
    has_attachments INTEGER NOT NULL DEFAULT 0,
    marker_color TEXT,
    importance TEXT NOT NULL DEFAULT 'normal',
    raw BLOB NOT NULL
);
"""


class NotAnArchiveError(Exception):
    """Файл существует, но это не наш формат архива (или он новее, чем мы умеем читать)."""


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def create_archive(path: Path) -> None:
    """Создаёт пустой файл архива. Не трогает уже существующий по этому пути."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(path)) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = 'format_version'").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('format_version', ?)", (str(_FORMAT_VERSION),)
            )
            conn.commit()


def is_archive_file(path: Path) -> bool:
    """Дешёвая проверка перед тем, как предлагать открыть файл как архив."""
    if not path.exists():
        return False
    try:
        with closing(sqlite3.connect(path)) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'format_version'").fetchone()
    except sqlite3.DatabaseError:
        return False
    return row is not None


def list_folders(path: Path) -> list[str]:
    with closing(_connect(path)) as conn:
        rows = conn.execute("SELECT DISTINCT folder FROM messages ORDER BY folder").fetchall()
    return [row[0] for row in rows]


def list_messages(path: Path, folder: str) -> list[MessageSummary]:
    with closing(_connect(path)) as conn:
        rows = conn.execute(
            "SELECT id, subject, sender, sender_email, date, message_id, has_attachments, marker_color, importance "
            "FROM messages WHERE folder = ? ORDER BY date DESC, id DESC",
            (folder,),
        ).fetchall()
    return [
        MessageSummary(
            uid=row[0], subject=row[1], sender=row[2], sender_email=row[3], date=row[4], message_id=row[5],
            has_attachments=bool(row[6]), marker_color=row[7], importance=row[8],
            is_read=True,  # архив — уже разобранная почта, не показываем "непрочитанным" вечно
        )
        for row in rows
    ]


def get_message_content(path: Path, message_id: int) -> MessageContent:
    return extract_content(message_from_bytes(get_message_raw(path, message_id)))


def get_message_raw(path: Path, message_id: int) -> bytes:
    with closing(_connect(path)) as conn:
        row = conn.execute("SELECT raw FROM messages WHERE id = ?", (message_id,)).fetchone()
    if row is None:
        raise KeyError(f"В архиве нет письма с id={message_id}")
    return row[0]


def set_marker(path: Path, message_id: int, color: str | None) -> None:
    with closing(_connect(path)) as conn:
        conn.execute("UPDATE messages SET marker_color = ? WHERE id = ?", (color, message_id))
        conn.commit()


def delete_messages(path: Path, message_ids: list[int]) -> None:
    if not message_ids:
        return
    placeholders = ",".join("?" * len(message_ids))
    with closing(_connect(path)) as conn:
        conn.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", message_ids)
        conn.commit()


def append_raw_message(path: Path, folder: str, raw: bytes) -> int:
    """Разбирает raw (полное письмо в формате RFC 822) и добавляет в архив."""
    with closing(_connect(path)) as conn:
        row_id = _insert_raw(conn, folder, raw)
        conn.commit()
    return row_id


def _insert_raw(conn: sqlite3.Connection, folder: str, raw: bytes) -> int:
    message = message_from_bytes(raw)
    sender_display, sender_email = _decode_from(message)
    content = extract_content(message)
    cursor = conn.execute(
        "INSERT INTO messages (folder, subject, sender, sender_email, date, message_id, "
        "has_attachments, marker_color, importance, raw) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            folder,
            _decode_mime_words(message.get("Subject")) or "(без темы)",
            sender_display,
            sender_email,
            _format_date(message.get("Date")),
            message.get("Message-Id", "") or "",
            int(bool(content.attachments)),
            None,
            parse_importance(message),
            raw,
        ),
    )
    return cursor.lastrowid


def _decode_mime_words(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    return "".join(
        chunk.decode(encoding or "utf-8", errors="replace") if isinstance(chunk, bytes) else chunk
        for chunk, encoding in parts
    )


def _decode_from(message: Message) -> tuple[str, str]:
    name, addr = parseaddr(message.get("From", ""))
    display = _decode_mime_words(name) or addr or "(неизвестно)"
    return display, addr


def _format_date(raw_date: str | None) -> str:
    if not raw_date:
        return ""
    from email.utils import parsedate_to_datetime

    try:
        parsed = parsedate_to_datetime(raw_date)
    except (TypeError, ValueError):
        return ""
    if parsed is None:
        return ""
    return parsed.strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# Импорт из внешних форматов
# ---------------------------------------------------------------------------


def import_mbox(path: Path, mbox_file: Path, folder: str) -> int:
    """Импортирует все письма из mbox-файла (Evolution, Thunderbird и т.п.)."""
    create_archive(path)
    count = 0
    box = mailbox.mbox(str(mbox_file))
    try:
        with closing(_connect(path)) as conn:
            for message in box:
                _insert_raw(conn, folder, message.as_bytes())
                count += 1
            conn.commit()
    finally:
        box.close()
    return count


def import_maildir(path: Path, maildir_dir: Path, folder: str) -> int:
    """Импортирует Maildir-каталог (формат хранения почты Evolution на диске)."""
    create_archive(path)
    count = 0
    box = mailbox.Maildir(str(maildir_dir), create=False)
    try:
        with closing(_connect(path)) as conn:
            for message in box:
                _insert_raw(conn, folder, message.as_bytes())
                count += 1
            conn.commit()
    finally:
        box.close()
    return count


_ATTACHMENT_FILENAME_TAGS = (0x3707, 0x3704)  # PidTagAttachLongFilename, PidTagAttachFilename


def import_pst(path: Path, pst_file: Path) -> int:
    """Импортирует .pst (Outlook), сохраняя структуру папок исходного файла.

    PST хранит письма как MAPI-свойства, не как готовый RFC 822 — для
    писем, полученных по почте, часто есть исходные "сырые" заголовки
    (transport_headers), но не для писем, составленных прямо в Outlook.
    Поэтому raw для архива собираем сами через email.message.EmailMessage
    из текста/отправителя/темы/вложений — так работает единообразно для
    обоих случаев, а остальной код архива (чтение, отображение) не должен
    знать, что письмо изначально не было "настоящим" RFC 822.
    """
    try:
        import pypff
    except ImportError as exc:
        raise RuntimeError(
            "Для импорта .pst нужен пакет libpff-python (модуль pypff) — "
            "он не установлен в этом окружении."
        ) from exc

    create_archive(path)
    count = 0
    pst = pypff.file()
    pst.open(str(pst_file))
    try:
        with closing(_connect(path)) as conn:
            count = _import_pst_folder(conn, pst.get_root_folder(), "")
            conn.commit()
    finally:
        pst.close()
    return count


def _import_pst_folder(conn: sqlite3.Connection, folder, path_prefix: str) -> int:
    count = 0
    folder_name = folder.get_name() or ""
    current_path = f"{path_prefix}/{folder_name}" if path_prefix else folder_name
    for message in folder.sub_messages:
        raw = _pst_message_to_raw(message)
        _insert_raw(conn, current_path or "Импорт из PST", raw)
        count += 1
    for sub_folder in folder.sub_folders:
        count += _import_pst_folder(conn, sub_folder, current_path)
    return count


def _pst_message_to_raw(message) -> bytes:
    email_message = EmailMessage()
    email_message["Subject"] = message.get_subject() or ""
    email_message["From"] = message.get_sender_name() or ""

    delivery_time = message.get_delivery_time()
    if delivery_time is not None:
        email_message["Date"] = formatdate(delivery_time.timestamp())

    transport_headers = message.get_transport_headers()
    if transport_headers:
        # Письмо реально пришло по почте — там уже есть настоящий Message-Id
        # и полный набор заголовков; переносим то, чего нет в EmailMessage.
        parsed_headers = message_from_bytes(transport_headers.encode("utf-8", errors="replace"))
        for header in ("Message-Id", "To", "Cc"):
            value = parsed_headers.get(header)
            if value:
                email_message[header] = value

    body = message.get_plain_text_body()
    if not body:
        html_body = message.get_html_body()
        body = "(письмо в формате HTML — предпросмотр текста недоступен)" if html_body else "(нет текстового содержимого)"
    elif isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    email_message.set_content(body)

    attachment_count = message.get_number_of_attachments()
    for index in range(attachment_count):
        attachment = message.get_attachment(index)
        data = attachment.read_buffer(attachment.get_size())
        filename = _pst_attachment_filename(attachment, index)
        email_message.add_attachment(data, maintype="application", subtype="octet-stream", filename=filename)

    return bytes(email_message)


def _pst_attachment_filename(attachment, index: int) -> str:
    for i in range(attachment.number_of_record_sets):
        record_set = attachment.get_record_set(i)
        for j in range(record_set.number_of_entries):
            entry = record_set.get_entry(j)
            if entry.get_entry_type() in _ATTACHMENT_FILENAME_TAGS:
                name = entry.get_data_as_string()
                if name:
                    return name
    return f"attachment-{index + 1}"
