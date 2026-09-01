from __future__ import annotations

import csv as csv_module
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import vobject

# Свой формат: один файл SQLite, тот же принцип, что у calendar_store.py и
# archive_store.py. Адресная книга — не источник живой синхронизации
# (закрытая корпоративная сеть без выхода к CardDAV/GAL, см. calendar_store —
# та же причина), а локальное хранилище, наполняемое вручную и импортом
# извне (vCard — стандартный формат экспорта из Outlook/Exchange/Evolution/
# телефонов; CSV — то, что реально экспортирует Outlook "на диск").
_FORMAT_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    emails TEXT NOT NULL DEFAULT '[]',
    phone TEXT NOT NULL DEFAULT '',
    organization TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT ''
);
"""

_COLUMNS = "id, uid, display_name, emails, phone, organization, notes"


@dataclass
class Contact:
    id: int | None = None
    uid: str = ""
    display_name: str = ""
    emails: list[str] = field(default_factory=list)
    phone: str = ""
    organization: str = ""
    notes: str = ""


def new_uid() -> str:
    return f"{uuid4()}@redmail"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def create_contacts_book(path: Path) -> None:
    """Создаёт пустой файл книги. Не трогает уже существующий по этому пути."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(path)) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = 'format_version'").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('format_version', ?)", (str(_FORMAT_VERSION),)
            )
            conn.commit()


def is_contacts_file(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with closing(sqlite3.connect(path)) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'format_version'").fetchone()
    except sqlite3.DatabaseError:
        return False
    return row is not None


def _row_to_contact(row) -> Contact:
    return Contact(
        id=row[0],
        uid=row[1],
        display_name=row[2],
        emails=json.loads(row[3]),
        phone=row[4],
        organization=row[5],
        notes=row[6],
    )


def list_contacts(path: Path) -> list[Contact]:
    create_contacts_book(path)
    with closing(_connect(path)) as conn:
        rows = conn.execute(f"SELECT {_COLUMNS} FROM contacts ORDER BY display_name COLLATE NOCASE").fetchall()
    return [_row_to_contact(row) for row in rows]


def get_contact(path: Path, contact_id: int) -> Contact | None:
    create_contacts_book(path)
    with closing(_connect(path)) as conn:
        row = conn.execute(f"SELECT {_COLUMNS} FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    return _row_to_contact(row) if row else None


def find_by_email(path: Path, email: str) -> Contact | None:
    create_contacts_book(path)
    needle = email.strip().lower()
    with closing(_connect(path)) as conn:
        rows = conn.execute(f"SELECT {_COLUMNS} FROM contacts").fetchall()
    for row in rows:
        contact = _row_to_contact(row)
        if any(e.strip().lower() == needle for e in contact.emails):
            return contact
    return None


def save_contact(path: Path, contact: Contact) -> Contact:
    """Вставляет или обновляет по UID; если UID не задан (новый контакт из
    UI), генерирует его. Импорт из внешних vCard использует их собственный
    UID — повторный импорт того же файла обновляет, а не дублирует."""
    create_contacts_book(path)
    uid = contact.uid or new_uid()
    with closing(_connect(path)) as conn:
        conn.execute(
            "INSERT INTO contacts (uid, display_name, emails, phone, organization, notes) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET "
            "display_name=excluded.display_name, emails=excluded.emails, phone=excluded.phone, "
            "organization=excluded.organization, notes=excluded.notes",
            (
                uid,
                contact.display_name,
                json.dumps(contact.emails, ensure_ascii=False),
                contact.phone,
                contact.organization,
                contact.notes,
            ),
        )
        conn.commit()
        row = conn.execute(f"SELECT {_COLUMNS} FROM contacts WHERE uid = ?", (uid,)).fetchone()
    return _row_to_contact(row)


def delete_contact(path: Path, contact_id: int) -> None:
    with closing(_connect(path)) as conn:
        conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        conn.commit()


def delete_all_contacts(path: Path) -> None:
    """Раньше можно было удалить только по одному — жалоба: "нет
    возможности удалить все контакты"."""
    with closing(_connect(path)) as conn:
        conn.execute("DELETE FROM contacts")
        conn.commit()


# ---------------------------------------------------------------------------
# Импорт из внешних форматов
# ---------------------------------------------------------------------------


def import_vcard(path: Path, vcf_bytes: bytes) -> int:
    """Импортирует все VCARD-записи из .vcf (vCard 2.1/3.0/4.0 — то, что
    экспортируют Outlook, Exchange, Evolution, телефоны)."""
    create_contacts_book(path)
    text = vcf_bytes.decode("utf-8", errors="replace")
    count = 0
    for card in vobject.readComponents(text, ignoreUnreadable=True):
        contact = _contact_from_vcard(card)
        if contact is None:
            continue
        save_contact(path, contact)
        count += 1
    return count


def _contact_from_vcard(card) -> Contact | None:
    emails = [e.value.strip() for e in card.contents.get("email", []) if e.value.strip()]
    display_name = str(card.fn.value).strip() if hasattr(card, "fn") else ""
    if not display_name and hasattr(card, "n"):
        # FN пуст, но структурированное имя (N) может быть заполнено —
        # соберём его из частей (Фамилия/Имя/Отчество и т.п.).
        name = card.n.value
        parts = (
            getattr(name, "prefix", ""), getattr(name, "given", ""),
            getattr(name, "additional", ""), getattr(name, "family", ""),
            getattr(name, "suffix", ""),
        )
        display_name = " ".join(p.strip() for p in parts if p and p.strip())
    if not display_name and hasattr(card, "nickname"):
        # Найдено на реальном экспорте: FN и N оба пустые, а полное ФИО
        # лежит в NICKNAME — не по стандарту (NICKNAME предназначен для
        # короткого прозвища), но это реальные данные экспортёра, и терять
        # ФИО молча из-за нестандартного размещения хуже, чем принять его
        # (жалоба: "импорт прошёл некорректно, не подгрузились ФИО").
        display_name = str(card.nickname.value).strip()
    if not display_name and emails:
        display_name = emails[0]
    if not display_name and not emails:
        return None  # пустая карточка — нечего сохранять

    uid = str(card.uid.value).strip() if hasattr(card, "uid") and card.uid.value else ""
    if not uid:
        uid = f"vcard-{emails[0]}" if emails else new_uid()

    phones = [t.value.strip() for t in card.contents.get("tel", []) if t.value.strip()]
    org_value = card.org.value if hasattr(card, "org") else None
    organization = ", ".join(part for part in org_value if part) if isinstance(org_value, list) else (org_value or "")
    notes = str(card.note.value).strip() if hasattr(card, "note") else ""

    return Contact(
        uid=uid,
        display_name=display_name,
        emails=emails,
        phone=phones[0] if phones else "",
        organization=organization,
        notes=notes,
    )


# Заголовки, под которыми Outlook (в т.ч. русская локаль) и другие клиенты
# обычно экспортируют CSV-контакты — ищем без учёта регистра, берём первое
# совпадение по каждой роли.
_CSV_NAME_HEADERS = ("display name", "полное имя", "name", "full name")
_CSV_FIRST_LAST_HEADERS = (("first name", "имя"), ("last name", "фамилия"))
_CSV_EMAIL_HEADERS = ("e-mail address", "email", "e-mail", "электронная почта", "email address")
_CSV_PHONE_HEADERS = ("business phone", "mobile phone", "телефон", "phone", "home phone")
_CSV_ORG_HEADERS = ("company", "организация", "company name")


def import_csv(path: Path, csv_bytes: bytes) -> int:
    """Импортирует контакты из CSV (Outlook: Файл → Открыть и экспортировать
    → Импорт/экспорт → Экспорт в файл → CSV). Формат столбцов заранее
    неизвестен (зависит от локали и версии Outlook) — ищем по распознанным
    названиям заголовков, а не по фиксированным позициям колонок."""
    create_contacts_book(path)
    text = csv_bytes.decode("utf-8-sig", errors="replace")
    reader = csv_module.DictReader(text.splitlines())
    if not reader.fieldnames:
        return 0
    header_map = {h.strip().lower(): h for h in reader.fieldnames}

    def _find(candidates: tuple[str, ...]) -> str | None:
        for candidate in candidates:
            if candidate in header_map:
                return header_map[candidate]
        return None

    name_col = _find(_CSV_NAME_HEADERS)
    first_col = _find(_CSV_FIRST_LAST_HEADERS[0])
    last_col = _find(_CSV_FIRST_LAST_HEADERS[1])
    email_col = _find(_CSV_EMAIL_HEADERS)
    phone_col = _find(_CSV_PHONE_HEADERS)
    org_col = _find(_CSV_ORG_HEADERS)

    count = 0
    for row in reader:
        email = (row.get(email_col) or "").strip() if email_col else ""
        if name_col:
            display_name = (row.get(name_col) or "").strip()
        else:
            first = (row.get(first_col) or "").strip() if first_col else ""
            last = (row.get(last_col) or "").strip() if last_col else ""
            display_name = " ".join(part for part in (first, last) if part)
        if not display_name:
            display_name = email
        if not display_name:
            continue  # ни имени, ни почты — не контакт, а пустая строка

        contact = Contact(
            uid=f"csv-{email}" if email else new_uid(),
            display_name=display_name,
            emails=[email] if email else [],
            phone=(row.get(phone_col) or "").strip() if phone_col else "",
            organization=(row.get(org_col) or "").strip() if org_col else "",
        )
        save_contact(path, contact)
        count += 1
    return count
