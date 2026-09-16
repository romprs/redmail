"""Выгрузка всей переписки в открытые форматы: mbox или отдельные файлы EML.

Нужна, когда сотрудник уходит и забирает переписку, или когда почту надо
открыть другой программой. Выгружается всё, что есть на этом компьютере:
локальная копия ящиков (mail.sqlite3) и архивы .rmarchive.

Локальная копия хранит письмо разобранным (текст, HTML, вложения, картинки),
а не исходным файлом — письмо собирается заново в MIME с теми же
реквизитами. Письма из архивов хранятся целиком и выгружаются как есть.
Письмо, тело которого ещё не скачано с сервера, выгружается с реквизитами и
пометкой X-Redmail-Export: body-not-downloaded — его число показывается в
итоге, чтобы не было иллюзии полной выгрузки.

Раскладка результата:

    <каталог>/<учётная запись>/<папка>.mbox
    <каталог>/Архивы/<архив>/<папка>.mbox
    (для EML — каталоги <папка>/ с файлами <номер> <тема>.eml)
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime, formataddr
from pathlib import Path
from typing import Callable

from redmail import profile
from redmail.applog import get_logger

_log = get_logger("mail_export")

FORMAT_MBOX = "mbox"
FORMAT_EML = "eml"
FORMATS = {
    FORMAT_MBOX: "mbox — один файл на папку (Thunderbird, Evolution, почтовые программы Linux)",
    FORMAT_EML: "EML — отдельный файл на письмо (открывается Outlook и любой почтовой программой)",
}

ARCHIVES_DIR_NAME = "Архивы"
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


class ExportCancelled(Exception):
    pass


@dataclass
class ExportResult:
    target: Path
    messages: int = 0
    headers_only: int = 0
    archives: int = 0
    folders: int = 0


def safe_name(value: str, limit: int = 80) -> str:
    name = _UNSAFE.sub("_", value or "").strip(" .")
    return name[:limit].rstrip(" .") or "_"


def _header(value: str | None) -> str:
    # Реквизиты из базы — данные с сервера: переводы строк в заголовке
    # позволили бы подмешать свои заголовки в выгруженное письмо.
    return re.sub(r"[\r\n]+", " ", value or "").strip()


def _split_type(content_type: str) -> tuple[str, str]:
    main, _, sub = (content_type or "").partition("/")
    main, sub = main.strip().lower(), sub.split(";")[0].strip().lower()
    return (main, sub) if main and sub else ("application", "octet-stream")


def _rfc_date(value: str) -> str:
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return format_datetime(datetime.strptime(value, pattern).astimezone())
        except (TypeError, ValueError):
            continue
    return ""


def build_message(
    *,
    subject: str,
    sender: str,
    sender_email: str,
    date: str,
    message_id: str,
    importance: str = "normal",
    to: str = "",
    body: str | None = None,
    html: str = "",
    content_from: str = "",
    content_to: str = "",
    content_cc: str = "",
    attachments: list[tuple[str, str, bytes]] = (),
    inline_images: list[tuple[str, str, bytes]] = (),
) -> bytes:
    """Письмо из локальной копии обратно в MIME."""
    message = EmailMessage()
    message["From"] = _header(content_from) or formataddr((_header(sender), _header(sender_email)))
    recipients = _header(content_to) or _header(to)
    if recipients:
        message["To"] = recipients
    if _header(content_cc):
        message["Cc"] = _header(content_cc)
    message["Subject"] = _header(subject)
    rfc_date = _rfc_date(date)
    if rfc_date:
        message["Date"] = rfc_date
    mid = _header(message_id)
    if mid:
        message["Message-ID"] = mid if mid.startswith("<") else f"<{mid}>"
    if importance == "high":
        message["Importance"] = "high"
        message["X-Priority"] = "1"
    elif importance == "low":
        message["Importance"] = "low"
        message["X-Priority"] = "5"
    if body is None:
        message["X-Redmail-Export"] = "body-not-downloaded"
        message.set_content("")
        return message.as_bytes()
    message.set_content(body or "")
    if html:
        message.add_alternative(html, subtype="html")
        if inline_images:
            html_part = message.get_body(("html",))
            for content_id, content_type, payload in inline_images:
                maintype, subtype = _split_type(content_type)
                html_part.add_related(payload, maintype=maintype, subtype=subtype, cid=f"<{_header(content_id)}>")
    for filename, content_type, payload in attachments:
        maintype, subtype = _split_type(content_type)
        message.add_attachment(payload, maintype=maintype, subtype=subtype, filename=_header(filename) or "file")
    return message.as_bytes()


def _mbox_entry(raw: bytes) -> bytes:
    """Одно письмо в формате mboxrd: строка-разделитель From и экранирование
    строк, начинающихся с From, — иначе программа-читатель разрежет письмо."""
    raw = raw.replace(b"\r\n", b"\n")
    body = re.sub(rb"(?m)^(>*From )", rb">\1", raw)
    if not body.endswith(b"\n"):
        body += b"\n"
    stamp = datetime.now().strftime("%a %b %d %H:%M:%S %Y").encode()
    return b"From MAILER-DAEMON " + stamp + b"\n" + body + b"\n"


class _Writer:
    def __init__(self, fmt: str) -> None:
        if fmt not in FORMATS:
            raise ValueError(f"неизвестный формат выгрузки: {fmt}")
        self.fmt = fmt
        self._file = None
        self._dir: Path | None = None
        self._counter = 0

    def open_folder(self, base: Path, folder: str) -> None:
        self.close()
        parts = [safe_name(part) for part in re.split(r"[/\\]", folder) if part] or ["_"]
        if self.fmt == FORMAT_MBOX:
            path = base.joinpath(*parts[:-1], parts[-1] + ".mbox")
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(path, "ab")
        else:
            self._dir = base.joinpath(*parts)
            self._dir.mkdir(parents=True, exist_ok=True)
            self._counter = 0

    def add(self, raw: bytes, subject: str) -> None:
        if self.fmt == FORMAT_MBOX:
            self._file.write(_mbox_entry(raw))
        else:
            self._counter += 1
            name = f"{self._counter:06d} {safe_name(subject, 60)}.eml"
            (self._dir / name).write_bytes(raw)

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


def _connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=600)


def archive_files(extra: list[Path] = ()) -> list[Path]:
    """Архивы профиля, подключённые архивы и архивы, на которые ссылается
    локальная копия (автоархив), — без повторов."""
    seen: dict[str, Path] = {}
    candidates = list(profile.archives_dir().glob("*.rmarchive")) if profile.archives_dir().exists() else []
    candidates += list(extra)
    mail_db = profile.mail_db_path()
    if mail_db.exists():
        try:
            with closing(_connect_ro(mail_db)) as conn:
                candidates += [Path(row[0]) for row in conn.execute(
                    "SELECT DISTINCT archive_path FROM messages WHERE archive_path IS NOT NULL"
                )]
        except sqlite3.Error as exc:
            _log.warning("Ссылки на архивы не прочитаны: %s", exc)
    for path in candidates:
        if path.is_file():
            seen.setdefault(str(path.resolve()), path)
    return list(seen.values())


def account_dir_name(account_key: str) -> str:
    """imap:host:user / host:user / ews:server:email → «user (host)»."""
    parts = [part for part in account_key.split(":") if part]
    if parts and parts[0] in ("imap", "ews"):
        parts = parts[1:]
    if len(parts) >= 2:
        return safe_name(f"{parts[-1]} ({':'.join(parts[:-1])})")
    return safe_name(account_key)


def export_mail(
    target: Path,
    fmt: str = FORMAT_MBOX,
    *,
    archives: list[Path] | None = None,
    progress: Callable[[int], None] | None = None,
    stop: Callable[[], bool] | None = None,
) -> ExportResult:
    """Выгружает всю переписку в новый каталог target (он не должен
    существовать или должен быть пуст — чтобы не смешать выгрузки)."""
    target = Path(target)
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"каталог выгрузки не пуст: {target}")
    target.mkdir(parents=True, exist_ok=True)
    result = ExportResult(target=target)
    writer = _Writer(fmt)
    archive_list = archive_files() if archives is None else [path for path in archives if path.is_file()]
    exported_archives = {str(path.resolve()) for path in archive_list}

    def tick() -> None:
        result.messages += 1
        if stop is not None and stop():
            raise ExportCancelled()
        if progress is not None and result.messages % 50 == 0:
            progress(result.messages)

    try:
        mail_db = profile.mail_db_path()
        if mail_db.exists():
            with closing(_connect_ro(mail_db)) as conn:
                current = None
                rows = conn.execute(
                    "SELECT account, folder, uid, subject, sender, sender_email, date, message_id, importance, "
                    "recipients_to, body, html, content_from, content_to, content_cc, archive_path "
                    "FROM messages ORDER BY account, folder, date, uid"
                )
                for (account, folder, uid, subject, sender, sender_email, date, message_id, importance, to, body,
                     html, content_from, content_to, content_cc, archive_path) in rows:
                    if archive_path and str(Path(archive_path).resolve()) in exported_archives:
                        continue  # письмо уже в архиве — выгрузится из него целиком
                    if (account, folder) != current:
                        current = (account, folder)
                        writer.open_folder(target / account_dir_name(account), folder)
                        result.folders += 1
                    attachments, inline = [], []
                    if body is not None:
                        attachments = conn.execute(
                            "SELECT filename, content_type, payload FROM attachments "
                            "WHERE account = ? AND folder = ? AND uid = ?", (account, folder, uid),
                        ).fetchall()
                        inline = conn.execute(
                            "SELECT content_id, content_type, payload FROM inline_images "
                            "WHERE account = ? AND folder = ? AND uid = ?", (account, folder, uid),
                        ).fetchall()
                    else:
                        result.headers_only += 1
                    raw = build_message(
                        subject=subject, sender=sender, sender_email=sender_email, date=date,
                        message_id=message_id, importance=importance, to=to, body=body, html=html or "",
                        content_from=content_from or "", content_to=content_to or "", content_cc=content_cc or "",
                        attachments=attachments, inline_images=inline,
                    )
                    writer.add(raw, subject)
                    tick()
        used_names: set[str] = set()
        for archive in archive_list:
            name = safe_name(archive.stem)
            while name in used_names:
                name += "_"
            used_names.add(name)
            base = target / ARCHIVES_DIR_NAME / name
            with closing(_connect_ro(archive)) as conn:
                current = None
                for folder, subject, raw in conn.execute(
                    "SELECT folder, subject, raw FROM messages ORDER BY folder, date, id"
                ):
                    if folder != current:
                        current = folder
                        writer.open_folder(base, folder)
                        result.folders += 1
                    writer.add(bytes(raw), subject)
                    tick()
            result.archives += 1
    finally:
        writer.close()
    if progress is not None:
        progress(result.messages)
    _log.info(
        "Выгрузка переписки (%s) в %s: писем %d, без тела %d, архивов %d",
        fmt, target, result.messages, result.headers_only, result.archives,
    )
    return result
