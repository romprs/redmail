from __future__ import annotations

import html
import math
from collections.abc import Callable
import mimetypes
import re
import shutil
import tempfile
import zlib
from datetime import date, datetime, timedelta, timezone
from email.utils import getaddresses
from pathlib import Path

from PySide6.QtCore import (
    QByteArray,
    QDate,
    QDateTime,
    QObject,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QStringListModel,
    QThread,
    QTime,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QCursor,
    QDesktopServices,
    QFont,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCalendarWidget,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from redmail import archive_store, calendar_store, caldav_sync, contact_store, ews_client, itip
from redmail.config_store import (
    MailRule,
    default_archive_storage_dir,
    load_accounts,
    load_archive_storage_dir,
    load_caldav_url,
    load_ews_accounts,
    load_font_scale,
    load_mail_columns_state,
    load_mail_rules,
    load_open_archives,
    load_pane_orientation,
    load_poll_interval_minutes,
    load_window_geometry,
    save_accounts,
    save_archive_storage_dir,
    save_caldav_url,
    save_ews_accounts,
    save_font_scale,
    save_mail_columns_state,
    save_mail_rules,
    save_open_archives,
    save_pane_orientation,
    save_poll_interval_minutes,
    save_window_geometry,
)
from redmail.ews_client import EwsAccount, EwsConnectionError, EwsSession
from redmail.imap_client import Account, Attachment, FolderInfo, ImapSession, MessageContent, MessageSummary
from redmail.mailbox import ArchiveSource, CachedMailbox
from redmail.paths import app_dir
from redmail.smtp_client import (
    OutgoingAttachment,
    OutgoingMessage,
    SmtpAccount,
    build_email_message,
    send_message,
    test_connection as smtp_test_connection,
)

# Отправка через EWS идёт через сам EWS-сеанс (Exchange не нуждается в
# отдельном SMTP-релее), но весь остальной код по всему приложению
# проверяет "if not self.smtp_account", чтобы понять, настроена ли вообще
# исходящая почта для текущей учётной записи — этот маркер остаётся
# истинным (bool), не будучи настоящим SmtpAccount, чтобы все такие
# проверки продолжали работать без изменений для EWS-аккаунтов тоже.
_EWS_SEND_MARKER = object()
from redmail.ui.week_calendar import (
    AllDayRowWidget,
    MonthGridWidget,
    WeekGridWidget,
    WeekHeaderWidget,
    month_grid_range,
    week_start_for,
)

COL_CHECK = 0
COL_FLAG = 1
COL_IMPORTANCE = 2
COL_ATTACHMENT = 3
COL_SENDER = 4
COL_SUBJECT = 5
COL_DATE = 6

# Колонки, по которым имеет смысл искать текстом — по ним же переключается
# фильтр, когда пользователь встаёт в соответствующую колонку/заголовок.
_FILTER_COLUMNS: dict[int, str] = {COL_SENDER: "От кого", COL_SUBJECT: "Тема", COL_DATE: "Дата"}

_FLAG_MARK = "⚑"
_ATTACHMENT_MARK = "\U0001F4CE"  # 📎 — по запросу именно скрепка

# Gmail заворачивает Отправленные/Корзину и т.п. в служебный контейнер
# "[Gmail]" — сам по себе не открывается (см. \Noselect в list_folders),
# но его имя остаётся частью названий дочерних папок. В дереве эта
# служебная обёртка не нужна — реальную иерархию задаёт узел учётной записи.
_HIDDEN_PATH_SEGMENTS = {"[Gmail]", "[Google Mail]"}

# Протокольное имя папки остаётся "INBOX" (это то, что уходит в IMAP-команды);
# по-русски она подписывается иначе только в дереве папок.
_DISPLAY_NAMES: dict[str, str] = {"INBOX": "Входящие"}

_PARTICIPATION_LABELS: dict[str, str] = {
    "accepted": "Принял(а) участие",
    "declined": "Отклонил(а)",
    "tentative": "Участие под вопросом",
    "needs-action": "Ещё не ответил(а)",
}
_REPLY_VERBS: dict[str, str] = {"accepted": "Принято", "declined": "Отклонено", "tentative": "Под вопросом"}

# Не полагаемся на locale-зависимый strftime("%B") — на разных системах
# (Windows-разработка/RED OS) он может отдать разное, вплоть до английского.
_MONTH_NAMES = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)

def _shift_month(anchor: date, delta: int) -> date:
    total = anchor.year * 12 + (anchor.month - 1) + delta
    return date(total // 12, total % 12 + 1, 1)


_MARKER_ICON_SIZE = 16

# Значение self.marker_filter для "показать письма с любым маркером,
# независимо от цвета" — отличается от None (фильтр по маркеру выключен).
_ANY_MARKER_FILTER = "_any"

_MARKER_LABELS: dict[str, str] = {
    "red": "Красный",
    "orange": "Оранжевый",
    "yellow": "Жёлтый",
    "green": "Зелёный",
    "blue": "Синий",
    "purple": "Фиолетовый",
}
_MARKER_HEX: dict[str, str] = {
    "red": "#D64545",
    "orange": "#E08A2B",
    "yellow": "#C9A227",
    "green": "#4C9A5B",
    "blue": "#3B6FB6",
    "purple": "#8B5CB6",
}


def _format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} Б"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.0f} КБ"
    return f"{num_bytes / (1024 * 1024):.1f} МБ"


def _safe_attachment_filename(filename: str) -> str:
    """Только базовое имя файла — Attachment.filename приходит из письма
    как есть (никем не проверялось) и может быть "../../.ssh/authorized_keys"
    или абсолютным путём; мы дописываем его к temp_dir/пути сохранения
    напрямую, поэтому путевые компоненты нужно отбросить, иначе
    вредоносное вложение может записать файл вне предполагаемого каталога."""
    candidate = (filename or "").replace("\\", "/").split("/")[-1]
    candidate = candidate.rsplit(":", 1)[-1].strip()
    if candidate in ("", ".", ".."):
        return "attachment"
    return candidate


_URL_PATTERN = re.compile(r"https?://[^\s<>\"]+")

_SUBJECT_PREFIX_PATTERN = re.compile(r"^\s*(re|fw|fwd|ответ|отв|пересыл)\s*:\s*", re.IGNORECASE)


def _normalize_subject(subject: str) -> str:
    """Убирает префиксы "Re:"/"Fwd:"/"Ответ:"/"Пересыл:" (возможно
    несколько подряд — "Re: Fwd: Re: ...") и лишние пробелы, чтобы связать
    письма одной цепочки (вопрос-ответ) по теме. Используется только для
    группировки в интерфейсе (жалоба: "письма цепочки... не группируются
    и не схлапываются... не видно цепочки писем") — не трогает реальные
    заголовки References/In-Reply-To, которых архив/IMAP-сводки не несут."""
    normalized = subject.strip()
    while True:
        stripped = _SUBJECT_PREFIX_PATTERN.sub("", normalized)
        if stripped == normalized:
            return normalized
        normalized = stripped


# Сколько последних писем цепочки показываем подряд при последовательном
# просмотре — без ограничения на живом IMAP каждое письмо цепочки (кроме
# текущего) это отдельный сетевой запрос при первом открытии, а цепочка
# вопрос-ответ в рабочей переписке легко насчитывает десятки писем.
_THREAD_DEPTH_LIMIT = 8


def _build_thread_html(entries: list[tuple[MessageSummary, str]], current_uid: int) -> str:
    """Склеивает несколько писем цепочки в один прокручиваемый документ
    (жалоба: "есть только ссылки на другие письма, а надо просмотр цепочки
    последовательно"). entries — (summary, тело_как_html) по одному на
    письмо, в хронологическом порядке (от старого к новому).

    Тело НЕ-текущих писем цепочки специально передаётся уже как
    экранированный текст (см. _render_thread), а не их родной
    content.html: несколько ПОЛНЫХ HTML-документов (со своими <html>/
    <head>/<style>) нельзя просто склеить в один setHtml() — упрощённый
    рич-текстовый движок Qt не изолирует стили одного письма от
    следующих в том же документе. Только текущее (выбранное) письмо
    показывается его настоящим HTML."""
    parts = []
    for summary, body_html in entries:
        is_current = summary.uid == current_uid
        accent = "#1A73E8" if is_current else "#c9cdd1"
        header = (
            f'<div style="background:#f1f3f4;padding:6px 10px;margin-top:14px;'
            f'border-left:3px solid {accent};">'
            f"<b>{html.escape(summary.sender)}</b> — {html.escape(summary.date)}"
            f"</div>"
        )
        # &#8203; (нулевой ширины пробел) — не просто пустой тег: Qt
        # схлопывает и теряет полностью пустые <a name="..."></a> при
        # разборе HTML в QTextDocument (проверено эмпирически), а
        # scrollToAnchor() ищет именно якорь, сохранённый в документе.
        parts.append(
            f'<a name="msg-{summary.uid}">&#8203;</a>{header}'
            f'<div style="padding:6px 10px 4px 10px;">{body_html}</div>'
        )
    return "".join(parts)


def _populate_body_browser(browser: QTextBrowser, content: MessageContent) -> None:
    """HTML-письма показываем как есть (с внедрёнными картинками из
    cid:-вложений через addResource — без этого <img src="cid:..."> не
    отрисуется); письма с обычным текстом — тоже через setHtml, но
    экранированным и с активными ссылками (_linkify), чтобы голые
    http(s)-ссылки в теле письма были кликабельны, как и в HTML-версии.
    Внешние (не cid:) картинки Qt сам не подгружает — не течём в сеть на
    отрисовку письма. Общая для MainWindow.reading_pane и MessageWindow —
    открытие письма в отдельном окне должно выглядеть так же, как в
    основной панели чтения."""
    document = browser.document()
    document.clear()
    for content_id, (_content_type, payload) in content.inline_images.items():
        image = QImage.fromData(payload)
        if not image.isNull():
            document.addResource(QTextDocument.ResourceType.ImageResource, QUrl(f"cid:{content_id}"), image)
    if content.html:
        browser.setHtml(content.html)
    else:
        browser.setHtml(_linkify(content.text))


class MessageWindow(QWidget):
    """Письмо в отдельном окне (жалоба: "нельзя открыть письмо в отдельном
    окне") — независимое от основного окна, можно держать открытым рядом,
    пока просматриваешь другие письма в списке."""

    def __init__(self, summary: MessageSummary, content: MessageContent, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(content.subject or summary.subject or "(без темы)")
        self.resize(700, 500)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._summary = summary
        self._content = content

        sender = content.from_ or (f"{summary.sender} <{summary.sender_email}>" if summary.sender_email else summary.sender)
        lines = [f"<b>Тема:</b> {html.escape(content.subject or summary.subject or '(без темы)')}", f"<b>От:</b> {html.escape(sender)}"]
        if content.to:
            lines.append(f"<b>Кому:</b> {html.escape(content.to)}")
        if content.cc:
            lines.append(f"<b>Копия:</b> {html.escape(content.cc)}")
        if summary.date:
            lines.append(f"<b>Дата:</b> {html.escape(summary.date)}")
        header_label = QLabel("<br>".join(lines), self)
        header_label.setWordWrap(True)
        header_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        # Раньше здесь не было ни ответить, ни переслать вовсе — окно было
        # только для чтения (жалоба: "при открытии письма в отдельном окне
        # нет кнопок ответить, переслать"). Оба действия зовут обратно в
        # MainWindow (_start_reply/_start_forward) с ЭТИМ summary/content
        # явно, а не через self.selected_summary — письмо в этом окне
        # может быть вовсе не тем, что сейчас выделено в основном списке.
        reply_button = QPushButton("Ответить", self)
        reply_button.clicked.connect(self._on_reply)
        forward_button = QPushButton("Переслать", self)
        forward_button.clicked.connect(self._on_forward)
        button_row = QHBoxLayout()
        button_row.addWidget(reply_button)
        button_row.addWidget(forward_button)
        button_row.addStretch(1)

        body = QTextBrowser(self)
        body.setReadOnly(True)
        body.setOpenExternalLinks(True)
        _populate_body_browser(body, content)

        layout = QVBoxLayout(self)
        layout.addWidget(header_label)
        layout.addLayout(button_row)
        layout.addWidget(body)

    def _on_reply(self) -> None:
        main_window = self.parent()
        if main_window is not None:
            main_window._start_reply(self._summary, self._content.text)

    def _on_forward(self) -> None:
        main_window = self.parent()
        if main_window is not None:
            main_window._start_forward(self._summary, self._content)


def _linkify(text: str) -> str:
    """HTML-экранирует текст и оборачивает http(s)-ссылки в <a href>, чтобы
    их можно было открыть кликом в QTextBrowser."""
    escaped = html.escape(text)
    linked = _URL_PATTERN.sub(lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', escaped)
    return linked.replace("\n", "<br>")


def _format_event_time(event: calendar_store.Event) -> str:
    start_local = event.dtstart.astimezone()
    end_local = event.dtend.astimezone()
    if event.all_day:
        return f"{start_local.strftime('%d.%m.%Y')} (весь день)"
    if start_local.date() == end_local.date():
        return f"{start_local.strftime('%d.%m.%Y %H:%M')}–{end_local.strftime('%H:%M')}"
    return f"{start_local.strftime('%d.%m.%Y %H:%M')} – {end_local.strftime('%d.%m.%Y %H:%M')}"


def _format_recipient_candidate(name: str, email: str) -> str:
    """"Имя <email>", в кавычках, если имя само содержит запятую (частый
    формат "Фамилия, Имя") — без этого такое имя в поле "Кому" ломало
    разбор по запятой (жалоба: "адреса, импортированные... надо вручную
    добавлять разделитель — запятую, чтобы распознался второй адресат").
    Не email.utils.formataddr(): тот попутно кодирует не-ASCII имя в
    RFC 2047 (=?utf-8?...?=) — годится для реального заголовка письма, но
    в текстовом поле интерфейса пользователь увидел бы нечитаемую кашу
    вместо своего же кириллического имени."""
    if not name:
        return email
    if any(ch in name for ch in ',"<>'):
        escaped = name.replace('"', '\\"')
        return f'"{escaped}" <{email}>'
    return f"{name} <{email}>"


def _contact_candidates(contacts: list[contact_store.Contact]) -> list[str]:
    candidates = []
    for contact in contacts:
        for email in contact.emails:
            candidates.append(_format_recipient_candidate(contact.display_name, email))
    return candidates


def _parse_recipient_list(text: str) -> list[str]:
    """Достаёт голые адреса из поля через запятую — элементы могут быть как
    просто email, так и "Имя <email>" (так автодополнение по контактам
    вставляет выбранный вариант). getaddresses() (не parseaddr — тот не
    умеет список) разбирает полноценный список адресов сразу, включая
    случай, когда имя в кавычках само содержит запятую (см.
    _contact_candidates/_format_recipient_candidate) — раньше text.split(",") резал такое
    имя пополам, и второй адрес в списке переставал распознаваться."""
    return [addr for _name, addr in getaddresses([text]) if addr]


def _install_recipient_completer(line_edit: QLineEdit, contacts: list[contact_store.Contact]) -> QCompleter:
    """Автодополнение по адресной книге для поля со списком адресов через
    запятую. Обычный line_edit.setCompleter() достраивал бы ВСЁ поле
    целиком по одному совпадению — здесь достраивается только текущий
    (последний) сегмент после запятой, остальные не трогаются."""
    completer = QCompleter(_contact_candidates(contacts), line_edit)
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    completer.setFilterMode(Qt.MatchFlag.MatchContains)
    completer.setWidget(line_edit)

    # activated() срабатывает уже ПОСЛЕ клика по выпадающему списку —
    # на этот момент line_edit.cursorPosition() не всегда совпадает с
    # тем местом, где курсор реально стоял во время набора текста
    # (переключение фокуса на попап и обратно может его сместить).
    # Раньше insert_completion() запрашивал позицию курсора заново
    # именно в этот момент — жалоба: "лишний текст прилипал к
    # подставленному адресу" (набранный кусок оставался в поле вместо
    # замены). Запоминаем границы текущего сегмента в момент правки
    # текста (textEdited, надёжно привязан к нажатиям клавиш) и
    # используем именно их, а не переспрашиваем позицию заново.
    state = {"prefix_start": 0, "cursor_pos": 0}

    def insert_completion(text: str) -> None:
        content = line_edit.text()
        prefix_start = min(state["prefix_start"], len(content))
        cursor_pos = min(state["cursor_pos"], len(content))
        # content[:prefix_start] уже включает разделяющую запятую сам(ой)
        # предыдущей записи (prefix_start = позиция сразу за ней, см.
        # update_prefix) — раньше сюда всё равно безусловно дописывалась
        # ЕЩЁ одна ", ", получалась двойная запятая при вставке второго и
        # далее адресата. Снимаем её перед тем, как добавить одну свою.
        new_text = content[:prefix_start].rstrip()
        if new_text.endswith(","):
            new_text = new_text[:-1].rstrip()
        if new_text:
            new_text += ", "
        new_text += text
        rest = content[cursor_pos:]
        if not rest.strip():
            # Ничего не следует за курсором (обычный случай — дописывали
            # последнего адресата) — сразу ставим разделитель, чтобы можно
            # было печатать следующего адресата без ручного набора запятой.
            # Без этого второй адресат склеивался с первым в одну строку
            # без запятой, и update_prefix() читал всё как один (ни с чем
            # не совпадающий) префикс — автодополнение для второго
            # адресата просто не появлялось (жалоба: "при выборе 1
            # адресата подтягивается вариант..., но 2 адресат уже нет").
            new_text += ", "
            rest = ""
        line_edit.setText(new_text + rest)
        line_edit.setCursorPosition(len(new_text))

    def update_prefix(text: str) -> None:
        cursor_pos = line_edit.cursorPosition()
        last_comma = text.rfind(",", 0, cursor_pos)
        prefix_start = last_comma + 1
        prefix = text[prefix_start:cursor_pos].strip()
        state["prefix_start"] = prefix_start
        state["cursor_pos"] = cursor_pos
        if prefix:
            completer.setCompletionPrefix(prefix)
            completer.complete()
        else:
            completer.popup().hide()

    completer.activated.connect(insert_completion)
    line_edit.textEdited.connect(update_prefix)
    return completer


class ContactPickerDialog(QDialog):
    """Явный выбор адресов из адресной книги списком. Автодополнение по мере
    набора в поле «Кому»/«Участники» уже было, но по отзыву с реального
    использования оказалось незаметным — эта кнопка даёт то же самое явно."""

    def __init__(self, parent, contacts: list[contact_store.Contact]):
        super().__init__(parent)
        self.setWindowTitle("Адресная книга")
        self.resize(380, 420)

        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText("Поиск по имени или email")
        self.filter_edit.textChanged.connect(self._apply_filter)

        self.list_widget = QListWidget(self)
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        for candidate in _contact_candidates(contacts):
            self.list_widget.addItem(candidate)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.filter_edit)
        layout.addWidget(self.list_widget)
        layout.addWidget(buttons)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def selected_candidates(self) -> list[str]:
        return [item.text() for item in self.list_widget.selectedItems()]


def _open_contact_picker(parent, line_edit: QLineEdit, contacts: list[contact_store.Contact]) -> None:
    if not contacts:
        QMessageBox.information(parent, "Адресная книга", "Адресная книга пуста — сначала добавьте контакты.")
        return
    dialog = ContactPickerDialog(parent, contacts)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    picked = dialog.selected_candidates()
    if not picked:
        return
    # getaddresses (не наивный split(",")) при разборе уже введённого — иначе
    # имя в кавычках со своей запятой внутри (см. _contact_candidates)
    # резалось бы пополам при пересборке поля.
    #
    # "if addr" одного было недостаточно: если пользователь начал вручную
    # печатать второго адресата и не успел закончить (например, "Пе"),
    # getaddresses() на таком обрывке без "@" всё равно кладёт что-то в
    # "адрес" (некуда больше положить нераспознанный текст) — этот огрызок
    # раньше сохранялся как отдельный "адресат" при выборе из адресной
    # книги вместо того, чтобы быть замещённым (жалоба: "2 адресат не
    # всегда корректно распознаётся"). "@" в адресе — грубый, но
    # достаточный фильтр "похоже ли это вообще на email".
    existing_pairs = getaddresses([line_edit.text()]) if line_edit.text().strip() else []
    entries = [_format_recipient_candidate(name, addr) for name, addr in existing_pairs if addr and "@" in addr]
    existing_addrs = {addr for _name, addr in existing_pairs if addr and "@" in addr}
    for candidate in picked:
        picked_pairs = getaddresses([candidate])
        addr = picked_pairs[0][1] if picked_pairs else ""
        if addr and addr not in existing_addrs:
            entries.append(candidate)
            existing_addrs.add(addr)
    line_edit.setText(", ".join(entries))


def _importance_mark(importance: str) -> str:
    if importance == "high":
        return "!"
    if importance == "low":
        return "↓"
    return ""


def _dot_icon(hex_color: str, diameter: int = _MARKER_ICON_SIZE) -> QIcon:
    pixmap = QPixmap(diameter, diameter)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(hex_color))
    painter.drawEllipse(1, 1, diameter - 2, diameter - 2)
    painter.end()
    return QIcon(pixmap)


def _marker_icon(color: str, diameter: int = _MARKER_ICON_SIZE) -> QIcon:
    return _dot_icon(_MARKER_HEX[color], diameter)


_AVATAR_COLORS = ("#E53935", "#FB8C00", "#43A047", "#1E88E5", "#8E24AA", "#00897B", "#D81B60", "#6D4C41")


def _avatar_color(text: str) -> str:
    """Цвет кружка-аватара участника — детерминированный по email/имени,
    чтобы у одного и того же человека всегда был один и тот же цвет (как
    в референсе VK Mail), а не менялся между открытиями. Встроенный hash()
    для строк рандомизирован по процессам (PYTHONHASHSEED) — не годится,
    цвет "плавал" бы при каждом перезапуске приложения."""
    digest = zlib.crc32(text.encode("utf-8"))
    return _AVATAR_COLORS[digest % len(_AVATAR_COLORS)]


def _avatar_pixmap(letter: str, color: str, size: int = 24) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(0, 0, size, size)
    painter.setPen(QColor("white"))
    font = QFont()
    font.setPixelSize(int(size * 0.5))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, letter.upper())
    painter.end()
    return pixmap


def _attendee_avatar_letter(name: str, email: str) -> str:
    source = (name or email or "?").strip()
    return source[0] if source else "?"


_ICON_COLOR = "#5f6368"
_EVENT_COLOR_PALETTE: tuple[tuple[str, str], ...] = (
    ("Синий", "#3B6FB6"),
    ("Фиолетовый", "#8B5CB6"),
    ("Зелёный", "#2E7D32"),
    ("Оранжевый", "#E8710A"),
    ("Красный", "#D93025"),
    ("Бирюзовый", "#00897B"),
)


def _calendar_icon(kind: str, size: int = 16) -> QIcon:
    """Простые монохромные значки для компактных строк в диалоге события
    (референс VK Mail — значок слева от каждого поля вместо подписи).
    Рисуются сами, а не берутся из системной темы/эмодзи-шрифта — на этой
    платформе уже был найден пробел в покрытии эмодзи-шрифтом (см.
    маркеры/аватарки), рисованные QPainter-иконки от этого не зависят."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(_ICON_COLOR))
    pen.setWidthF(1.3)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    m = size * 0.14
    r = size - 2 * m
    cx, cy = size / 2, size / 2

    if kind == "time":
        painter.drawEllipse(QRectF(m, m, r, r))
        painter.drawLine(QPointF(cx, cy), QPointF(cx, cy - r * 0.32))
        painter.drawLine(QPointF(cx, cy), QPointF(cx + r * 0.22, cy + r * 0.06))
    elif kind == "repeat":
        rect = QRectF(m, m, r, r)
        painter.drawArc(rect, 25 * 16, 260 * 16)
        angle = math.radians(25)
        ax = cx + (r / 2) * math.cos(angle)
        ay = cy - (r / 2) * math.sin(angle)
        painter.setBrush(QColor(_ICON_COLOR))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon([QPointF(ax - 3.2, ay - 0.8), QPointF(ax + 1.6, ay - 3.6), QPointF(ax + 0.8, ay + 3.0)])
    elif kind == "people":
        painter.drawEllipse(QRectF(size * 0.30, size * 0.16, size * 0.36, size * 0.36))
        painter.drawArc(QRectF(size * 0.06, size * 0.52, size * 0.84, size * 0.5), 0, 180 * 16)
    elif kind == "location":
        path = QPainterPath()
        path.moveTo(cx, size * 0.88)
        path.cubicTo(size * 0.16, size * 0.55, size * 0.16, size * 0.14, cx, size * 0.12)
        path.cubicTo(size * 0.84, size * 0.14, size * 0.84, size * 0.55, cx, size * 0.88)
        painter.drawPath(path)
        painter.setBrush(QColor(_ICON_COLOR))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(cx - size * 0.10, size * 0.27, size * 0.20, size * 0.20))
    elif kind == "description":
        for frac, shorten in ((0.28, 0.0), (0.5, 0.0), (0.72, size * 0.25)):
            y = m + frac * r
            painter.drawLine(QPointF(m, y), QPointF(size - m - shorten, y))
    painter.end()
    return QIcon(pixmap)


def _toolbar_icon(kind: str, size: int = 18) -> QIcon:
    """Монохромные значки для панелей инструментов (та же техника, что и
    _calendar_icon выше — рисуем сами через QPainter, не полагаясь на
    системную тему/эмодзи-шрифт). Команды переведены с текстовых подписей
    на иконки с подсказками по явной просьбе пользователя — экономит место
    в тулбаре (кроме почты/календаря/контактов/параметров/справки, которые
    намеренно оставлены текстом)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(_ICON_COLOR))
    pen.setWidthF(1.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    m = size * 0.16
    cx, cy = size / 2, size / 2

    if kind == "compose":
        painter.drawLine(QPointF(m, size - m), QPointF(size * 0.55, size * 0.45))
        painter.drawPolyline([
            QPointF(size * 0.55, size * 0.45), QPointF(size - m, m),
            QPointF(size - m * 0.4, m + m * 0.6), QPointF(size * 0.62, size * 0.52),
        ])
        painter.drawLine(QPointF(m, size - m), QPointF(m + m * 0.6, size - m * 1.4))
    elif kind in ("reply", "forward"):
        flip = kind == "forward"
        x0, x1 = (size - m, m) if flip else (m, size - m)
        painter.drawLine(QPointF(x0, cy), QPointF(x1, cy))
        dx = -1 if flip else 1
        painter.drawLine(QPointF(x0, cy), QPointF(x0 + dx * size * 0.30, cy - size * 0.22))
        painter.drawLine(QPointF(x0, cy), QPointF(x0 + dx * size * 0.30, cy + size * 0.22))
        painter.drawArc(QRectF(cx - size * 0.05, m, size * 0.42, size * 0.42), -20 * 16, 200 * 16)
    elif kind == "delete":
        painter.drawRect(QRectF(size * 0.24, size * 0.30, size * 0.52, size * 0.56))
        painter.drawLine(QPointF(size * 0.16, size * 0.30), QPointF(size * 0.84, size * 0.30))
        painter.drawLine(QPointF(size * 0.40, size * 0.16), QPointF(size * 0.60, size * 0.16))
        painter.drawLine(QPointF(size * 0.40, size * 0.16), QPointF(size * 0.40, size * 0.30))
        painter.drawLine(QPointF(size * 0.60, size * 0.16), QPointF(size * 0.60, size * 0.30))
        painter.drawLine(QPointF(size * 0.38, size * 0.42), QPointF(size * 0.38, size * 0.76))
        painter.drawLine(QPointF(size * 0.5, size * 0.42), QPointF(size * 0.5, size * 0.76))
        painter.drawLine(QPointF(size * 0.62, size * 0.42), QPointF(size * 0.62, size * 0.76))
    elif kind == "refresh":
        rect = QRectF(m, m, size - 2 * m, size - 2 * m)
        painter.drawArc(rect, 20 * 16, 280 * 16)
        angle = math.radians(20)
        ax = cx + (rect.width() / 2) * math.cos(angle)
        ay = cy - (rect.height() / 2) * math.sin(angle)
        painter.setBrush(QColor(_ICON_COLOR))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon([QPointF(ax - 3.4, ay - 0.8), QPointF(ax + 1.6, ay - 3.8), QPointF(ax + 0.8, ay + 3.2)])
    elif kind in ("open_archive", "archive_folder", "import", "archive"):
        painter.drawPolyline([
            QPointF(m, size * 0.34), QPointF(m, size - m), QPointF(size - m, size - m), QPointF(size - m, size * 0.34),
        ])
        painter.drawPolyline([
            QPointF(m, size * 0.34), QPointF(size * 0.40, size * 0.34), QPointF(size * 0.46, size * 0.22),
            QPointF(size * 0.60, size * 0.22), QPointF(size * 0.66, size * 0.34), QPointF(size - m, size * 0.34),
        ])
        if kind in ("import", "archive"):
            painter.drawLine(QPointF(cx, size * 0.42), QPointF(cx, size * 0.68))
            painter.drawLine(QPointF(cx - size * 0.12, size * 0.58), QPointF(cx, size * 0.70))
            painter.drawLine(QPointF(cx + size * 0.12, size * 0.58), QPointF(cx, size * 0.70))
    elif kind == "today":
        painter.drawRoundedRect(QRectF(m, size * 0.20, size - 2 * m, size - size * 0.20 - m), 2, 2)
        painter.drawLine(QPointF(size * 0.32, m), QPointF(size * 0.32, size * 0.28))
        painter.drawLine(QPointF(size * 0.68, m), QPointF(size * 0.68, size * 0.28))
        painter.setBrush(QColor(_ICON_COLOR))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(cx - size * 0.11, cy - size * 0.01, size * 0.22, size * 0.22))
    elif kind in ("prev", "next"):
        flip = kind == "next"
        x0, x1 = (size * 0.36, size * 0.64) if flip else (size * 0.64, size * 0.36)
        painter.drawLine(QPointF(x0, m), QPointF(x1, cy))
        painter.drawLine(QPointF(x1, cy), QPointF(x0, size - m))
    elif kind == "add":
        painter.drawLine(QPointF(cx, m), QPointF(cx, size - m))
        painter.drawLine(QPointF(m, cy), QPointF(size - m, cy))
    elif kind == "cancel_event":
        painter.drawRoundedRect(QRectF(m, size * 0.20, size - 2 * m, size - size * 0.20 - m), 2, 2)
        painter.drawLine(QPointF(size * 0.32, m), QPointF(size * 0.32, size * 0.28))
        painter.drawLine(QPointF(size * 0.68, m), QPointF(size * 0.68, size * 0.28))
        painter.drawLine(QPointF(size * 0.36, size * 0.46), QPointF(size * 0.64, size * 0.78))
        painter.drawLine(QPointF(size * 0.64, size * 0.46), QPointF(size * 0.36, size * 0.78))
    elif kind == "sync":
        rect1 = QRectF(m, m, size * 0.56, size * 0.56)
        rect2 = QRectF(size - m - size * 0.56, size - m - size * 0.56, size * 0.56, size * 0.56)
        painter.drawArc(rect1, 30 * 16, 240 * 16)
        painter.drawArc(rect2, 210 * 16, 240 * 16)
        painter.setBrush(QColor(_ICON_COLOR))
        painter.setPen(Qt.PenStyle.NoPen)
        a1 = math.radians(30)
        ax1 = rect1.center().x() + (rect1.width() / 2) * math.cos(a1)
        ay1 = rect1.center().y() - (rect1.height() / 2) * math.sin(a1)
        painter.drawPolygon([QPointF(ax1 - 3.2, ay1 - 0.6), QPointF(ax1 + 1.6, ay1 - 3.4), QPointF(ax1 + 0.8, ay1 + 2.8)])
        a2 = math.radians(210)
        ax2 = rect2.center().x() + (rect2.width() / 2) * math.cos(a2)
        ay2 = rect2.center().y() - (rect2.height() / 2) * math.sin(a2)
        painter.drawPolygon([QPointF(ax2 + 3.2, ay2 + 0.6), QPointF(ax2 - 1.6, ay2 + 3.4), QPointF(ax2 - 0.8, ay2 - 2.8)])
    painter.end()
    return QIcon(pixmap)


def _icon_label(kind: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(parent)
    label.setPixmap(_calendar_icon(kind).pixmap(16, 16))
    label.setFixedWidth(22)
    label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
    return label


class SettingsDialog(QDialog):
    """Один диалог на всё: учётная запись (было отдельным «Подключиться…»),
    интервал проверки почты и расположение панели чтения."""

    def __init__(
        self,
        parent=None,
        *,
        account: Account | None = None,
        smtp: SmtpAccount | None = None,
        poll_interval_minutes: int = 5,
        pane_orientation: str = "vertical",
        caldav_url: str = "",
        archive_storage_dir: Path | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Параметры")

        self.host_edit = QLineEdit(account.host if account else "")
        self.port_edit = QSpinBox()
        self.port_edit.setRange(1, 65535)
        self.port_edit.setValue(account.port if account else 993)
        self.user_edit = QLineEdit(account.username if account else "")
        self.password_edit = QLineEdit(account.password if account else "")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ssl_check = QCheckBox("Использовать SSL")
        self.ssl_check.setChecked(account.use_ssl if account else True)

        self.auth_combo = QComboBox()
        self.auth_combo.addItem("Логин и пароль", "password")
        self.auth_combo.addItem("SSO (Kerberos, без пароля)", "kerberos")
        self.auth_combo.setToolTip(
            "SSO — почтовый сервер в домене; вход идёт по Kerberos-билету, "
            "который RED OS уже выдала при входе пользователя в домен "
            "(SSSD). Пароль в приложении не хранится и не используется — "
            "его смену на стороне домена обрабатывает сама SSO-инфраструктура."
        )
        self.auth_combo.setCurrentIndex(
            self.auth_combo.findData(account.auth_type if account else "password")
        )
        self.auth_combo.currentIndexChanged.connect(self._update_password_enabled)

        self.imap_test_button = QPushButton("Проверить подключение")
        self.imap_test_button.clicked.connect(self._on_test_imap)
        self.imap_test_status = QLabel("")
        self.imap_test_status.setWordWrap(True)
        self._imap_test_worker: object | None = None

        imap_form = QFormLayout()
        imap_form.addRow("Сервер", self.host_edit)
        imap_form.addRow("Порт", self.port_edit)
        imap_form.addRow("Способ входа", self.auth_combo)
        imap_form.addRow("Логин", self.user_edit)
        imap_form.addRow("Пароль", self.password_edit)
        imap_form.addRow(self.ssl_check)
        imap_form.addRow(self.imap_test_button)
        imap_form.addRow(self.imap_test_status)
        imap_group = QGroupBox("Входящая почта (IMAP)")
        imap_group.setLayout(imap_form)

        self.smtp_host_edit = QLineEdit(smtp.host if smtp else "")
        self.smtp_port_edit = QSpinBox()
        self.smtp_port_edit.setRange(1, 65535)
        self.smtp_port_edit.setValue(smtp.port if smtp else 587)
        self.smtp_ssl_check = QCheckBox("SSL напрямую (порт 465) вместо STARTTLS")
        self.smtp_ssl_check.setChecked(smtp.use_ssl if smtp else False)

        self.smtp_test_button = QPushButton("Проверить подключение")
        self.smtp_test_button.clicked.connect(self._on_test_smtp)
        self.smtp_test_status = QLabel("")
        self.smtp_test_status.setWordWrap(True)
        self._smtp_test_worker: object | None = None

        smtp_form = QFormLayout()
        smtp_form.addRow("Сервер", self.smtp_host_edit)
        smtp_form.addRow("Порт", self.smtp_port_edit)
        smtp_form.addRow(self.smtp_ssl_check)
        smtp_form.addRow(self.smtp_test_button)
        smtp_form.addRow(self.smtp_test_status)
        smtp_group = QGroupBox("Исходящая почта (SMTP) — тот же логин и пароль")
        smtp_group.setLayout(smtp_form)

        self.interval_edit = QSpinBox()
        self.interval_edit.setRange(1, 180)
        self.interval_edit.setSuffix(" мин.")
        self.interval_edit.setValue(poll_interval_minutes)

        self.orientation_vertical = QRadioButton("Список писем сверху, чтение снизу")
        self.orientation_horizontal = QRadioButton("Список писем слева, чтение справа")
        if pane_orientation == "horizontal":
            self.orientation_horizontal.setChecked(True)
        else:
            self.orientation_vertical.setChecked(True)

        general_form = QFormLayout()
        general_form.addRow("Проверять почту каждые", self.interval_edit)
        general_form.addRow("Панель чтения", self.orientation_vertical)
        general_form.addRow("", self.orientation_horizontal)
        general_group = QGroupBox("Общие")
        general_group.setLayout(general_form)

        self.caldav_url_edit = QLineEdit(caldav_url)
        self.caldav_url_edit.setPlaceholderText("https://calendar.example.corp/caldav/ (необязательно)")

        self.caldav_test_button = QPushButton("Проверить подключение")
        self.caldav_test_button.clicked.connect(self._on_test_caldav)
        self.caldav_test_status = QLabel("")
        self.caldav_test_status.setWordWrap(True)
        self._caldav_test_worker: object | None = None

        caldav_form = QFormLayout()
        caldav_form.addRow("Адрес сервера", self.caldav_url_edit)
        caldav_form.addRow(self.caldav_test_button)
        caldav_form.addRow(self.caldav_test_status)
        caldav_group = QGroupBox("Календарь (CalDAV) — логин и пароль те же, что для IMAP выше")
        caldav_group.setLayout(caldav_form)

        self.archive_dir_edit = QLineEdit(str(archive_storage_dir or default_archive_storage_dir()))
        archive_dir_browse = QPushButton("Обзор…", self)
        archive_dir_browse.clicked.connect(self._on_browse_archive_dir)
        archive_dir_row = QHBoxLayout()
        archive_dir_row.addWidget(self.archive_dir_edit)
        archive_dir_row.addWidget(archive_dir_browse)
        archive_dir_form = QFormLayout()
        archive_dir_form.addRow("Каталог для новых архивов", archive_dir_row)
        archive_dir_group = QGroupBox("Архивы почты")
        archive_dir_group.setLayout(archive_dir_form)

        # Редкие действия — перенесены сюда с панели инструментов почты,
        # чтобы не переполнять её (жалоба: "кнопка параметры пропала" —
        # оказалось, тулбар с длинными подписями кнопок не помещался в
        # окно, и Qt тихо прятал часть кнопок).
        add_account_button = QPushButton("Добавить учётную запись…", self)
        add_account_button.clicked.connect(self._on_add_account)
        add_ews_account_button = QPushButton("Подключить Exchange (EWS)…", self)
        add_ews_account_button.setToolTip(
            "Прямой доступ к Exchange по протоколу EWS — если IMAP отключён "
            "политикой безопасности, или нужен вход по Kerberos (SSO) без пароля"
        )
        add_ews_account_button.clicked.connect(self._on_add_ews_account)
        mail_rules_button = QPushButton("Правила сортировки почты…", self)
        mail_rules_button.clicked.connect(self._on_mail_rules)
        apply_rules_button = QPushButton("Применить правила к текущей папке", self)
        apply_rules_button.clicked.connect(self._on_apply_mail_rules)
        accounts_rules_layout = QVBoxLayout()
        accounts_rules_layout.addWidget(add_account_button)
        accounts_rules_layout.addWidget(add_ews_account_button)
        accounts_rules_layout.addWidget(mail_rules_button)
        accounts_rules_layout.addWidget(apply_rules_button)
        accounts_rules_group = QGroupBox("Учётные записи и правила почты", self)
        accounts_rules_group.setLayout(accounts_rules_layout)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(imap_group)
        layout.addWidget(smtp_group)
        layout.addWidget(general_group)
        layout.addWidget(caldav_group)
        layout.addWidget(archive_dir_group)
        layout.addWidget(accounts_rules_group)
        layout.addWidget(buttons)

        self._update_password_enabled()

    def _update_password_enabled(self) -> None:
        self.password_edit.setEnabled(self.auth_combo.currentData() != "kerberos")

    def _on_test_imap(self) -> None:
        account = self.account()
        if not account.host or not account.username:
            QMessageBox.warning(self, "Укажите параметры", "Сервер и логин обязательны для проверки.")
            return
        self.imap_test_button.setEnabled(False)
        self.imap_test_status.setText("Проверка подключения…")

        def connect_and_list_folders() -> int:
            with ImapSession(account) as session:
                return len(session.list_folders())

        worker = _CallableWorker(connect_and_list_folders, parent=self)

        def on_success(folder_count: object) -> None:
            self.imap_test_status.setText(f"Подключение успешно, папок найдено: {folder_count}")
            self.imap_test_button.setEnabled(True)
            self._imap_test_worker = None

        def on_failure(error_text: str) -> None:
            self.imap_test_status.setText("")
            QMessageBox.critical(self, "Не удалось подключиться (IMAP)", error_text)
            self.imap_test_button.setEnabled(True)
            self._imap_test_worker = None

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._imap_test_worker = worker
        worker.start()

    def _on_test_smtp(self) -> None:
        smtp = self.smtp_account()
        if not smtp.host or not smtp.username:
            QMessageBox.warning(self, "Укажите параметры", "Сервер и логин обязательны для проверки.")
            return
        self.smtp_test_button.setEnabled(False)
        self.smtp_test_status.setText("Проверка подключения…")

        worker = _CallableWorker(smtp_test_connection, smtp, parent=self)

        def on_success(_result: object) -> None:
            self.smtp_test_status.setText("Подключение и вход успешны")
            self.smtp_test_button.setEnabled(True)
            self._smtp_test_worker = None

        def on_failure(error_text: str) -> None:
            self.smtp_test_status.setText("")
            QMessageBox.critical(self, "Не удалось подключиться (SMTP)", error_text)
            self.smtp_test_button.setEnabled(True)
            self._smtp_test_worker = None

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._smtp_test_worker = worker
        worker.start()

    def _on_test_caldav(self) -> None:
        url = self.caldav_url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "Укажите адрес", "Адрес сервера CalDAV обязателен для проверки.")
            return
        # Логин/пароль — те же, что для IMAP (см. заголовок группы) — если
        # выбран SSO, пароля нет и здесь: у CalDAV в этом приложении нет
        # отдельной поддержки Kerberos, проверка честно покажет ошибку входа.
        caldav_account = caldav_sync.CalDavAccount(
            url=url, username=self.user_edit.text().strip(), password=self.password_edit.text()
        )
        self.caldav_test_button.setEnabled(False)
        self.caldav_test_status.setText("Проверка подключения…")

        def connect_and_list_calendars() -> int:
            session = caldav_sync.CalDavSession(caldav_account)
            try:
                return len(session.list_calendar_names())
            finally:
                session.close()

        worker = _CallableWorker(connect_and_list_calendars, parent=self)

        def on_success(calendar_count: object) -> None:
            self.caldav_test_status.setText(f"Подключение успешно, календарей найдено: {calendar_count}")
            self.caldav_test_button.setEnabled(True)
            self._caldav_test_worker = None

        def on_failure(error_text: str) -> None:
            self.caldav_test_status.setText("")
            QMessageBox.critical(self, "Не удалось подключиться (CalDAV)", error_text)
            self.caldav_test_button.setEnabled(True)
            self._caldav_test_worker = None

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._caldav_test_worker = worker
        worker.start()

    def _on_browse_archive_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Каталог для новых архивов", self.archive_dir_edit.text())
        if chosen:
            self.archive_dir_edit.setText(chosen)

    def archive_storage_dir(self) -> Path:
        text = self.archive_dir_edit.text().strip()
        return Path(text) if text else default_archive_storage_dir()

    def _on_add_account(self) -> None:
        if self.parent() is not None:
            self.parent().on_add_account()

    def _on_add_ews_account(self) -> None:
        if self.parent() is not None:
            self.parent().on_add_ews_account()

    def _on_mail_rules(self) -> None:
        if self.parent() is not None:
            self.parent().on_mail_rules()

    def _on_apply_mail_rules(self) -> None:
        if self.parent() is not None:
            self.parent().on_apply_mail_rules()

    def account(self) -> Account:
        auth_type = self.auth_combo.currentData()
        return Account(
            host=self.host_edit.text().strip(),
            username=self.user_edit.text().strip(),
            password="" if auth_type == "kerberos" else self.password_edit.text(),
            port=self.port_edit.value(),
            use_ssl=self.ssl_check.isChecked(),
            auth_type=auth_type,
        )

    def smtp_account(self) -> SmtpAccount:
        auth_type = self.auth_combo.currentData()
        return SmtpAccount(
            host=self.smtp_host_edit.text().strip(),
            username=self.user_edit.text().strip(),
            password="" if auth_type == "kerberos" else self.password_edit.text(),
            port=self.smtp_port_edit.value(),
            use_ssl=self.smtp_ssl_check.isChecked(),
            auth_type=auth_type,
        )

    def poll_interval_minutes(self) -> int:
        return self.interval_edit.value()

    def pane_orientation(self) -> str:
        return "horizontal" if self.orientation_horizontal.isChecked() else "vertical"

    def caldav_url(self) -> str:
        return self.caldav_url_edit.text().strip()


class EwsAccountDialog(QDialog):
    """Подключение к Exchange напрямую по EWS — отдельный диалог от
    обычного "Параметры" (IMAP/SMTP): модель входа принципиально другая —
    адрес сервера обычно не нужен вводить (автообнаружение по email), а
    для Kerberos/SSO вообще не нужен пароль в самом приложении (билет
    берётся из окружения ОС, см. ews_client.EwsSession)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Подключить Exchange (EWS)")

        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("ivan.ivanov@corp.example")

        self.auth_combo = QComboBox()
        self.auth_combo.addItem("Логин и пароль", "basic")
        self.auth_combo.addItem("NTLM (DOMAIN\\логин)", "ntlm")
        self.auth_combo.addItem("Kerberos (SSO, без пароля)", "kerberos")
        self.auth_combo.currentIndexChanged.connect(self._update_fields_enabled)

        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("для NTLM — DOMAIN\\логин; иначе обычно совпадает с email")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.server_edit = QLineEdit()
        self.server_edit.setPlaceholderText("необязательно — по умолчанию автообнаружение по email")

        form = QFormLayout()
        form.addRow("Email", self.email_edit)
        form.addRow("Способ входа", self.auth_combo)
        form.addRow("Логин", self.username_edit)
        form.addRow("Пароль", self.password_edit)
        form.addRow("Сервер EWS", self.server_edit)

        self.test_button = QPushButton("Проверить подключение")
        self.test_button.clicked.connect(self._on_test)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self._test_worker: object | None = None

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.test_button)
        layout.addWidget(self.status_label)
        layout.addWidget(buttons)

        self._update_fields_enabled()

    def _update_fields_enabled(self) -> None:
        is_kerberos = self.auth_combo.currentData() == "kerberos"
        self.username_edit.setEnabled(not is_kerberos)
        self.password_edit.setEnabled(not is_kerberos)

    def account(self) -> EwsAccount:
        return EwsAccount(
            email=self.email_edit.text().strip(),
            username=self.username_edit.text().strip(),
            password=self.password_edit.text(),
            server=self.server_edit.text().strip(),
            auth_type=self.auth_combo.currentData(),
        )

    def _on_test(self) -> None:
        account = self.account()
        if not account.email:
            QMessageBox.warning(
                self, "Укажите email", "Email обязателен — по нему автообнаружение ищет сервер."
            )
            return
        self.test_button.setEnabled(False)
        self.status_label.setText("Проверка подключения… (автообнаружение сервера может занять до минуты)")

        def connect_and_list_folders() -> int:
            session = EwsSession(account)
            return len(session.list_folders())

        worker = _CallableWorker(connect_and_list_folders, parent=self)

        def on_success(folder_count: object) -> None:
            self.status_label.setText(f"Подключение успешно, папок найдено: {folder_count}")
            self.test_button.setEnabled(True)
            self._test_worker = None

        def on_failure(error_text: str) -> None:
            self.status_label.setText("")
            QMessageBox.critical(self, "Не удалось подключиться", error_text)
            self.test_button.setEnabled(True)
            self._test_worker = None

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._test_worker = worker  # держим ссылку, пока поток жив
        worker.start()


class ComposeDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        title: str = "Новое письмо",
        to: str = "",
        cc: str = "",
        bcc: str = "",
        subject: str = "",
        body: str = "",
        contacts: list[contact_store.Contact] | None = None,
        attachments: list[OutgoingAttachment] | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 460)
        self.attachments: list[OutgoingAttachment] = list(attachments) if attachments else []

        self._contacts = contacts or []

        self.to_edit = QLineEdit(to)
        self.to_edit.setPlaceholderText("Через запятую, если получателей несколько")
        if contacts:
            _install_recipient_completer(self.to_edit, contacts)
        self.subject_edit = QLineEdit(subject)
        self.body_edit = QPlainTextEdit(body)

        address_book_button = QPushButton("Адресная книга…", self)
        address_book_button.clicked.connect(lambda: _open_contact_picker(self, self.to_edit, self._contacts))
        cc_bcc_button = QPushButton("Копия/Скрытая копия", self)
        cc_bcc_button.setFlat(True)
        cc_bcc_button.clicked.connect(self._show_cc_bcc)
        to_row = QHBoxLayout()
        to_row.addWidget(self.to_edit)
        to_row.addWidget(address_book_button)
        to_row.addWidget(cc_bcc_button)

        self.cc_edit = QLineEdit(cc, self)
        self.cc_edit.setPlaceholderText("Через запятую, если получателей несколько")
        if contacts:
            _install_recipient_completer(self.cc_edit, contacts)
        self.bcc_edit = QLineEdit(bcc, self)
        self.bcc_edit.setPlaceholderText("Через запятую, если получателей несколько")
        if contacts:
            _install_recipient_completer(self.bcc_edit, contacts)

        form = QFormLayout()
        form.addRow("Кому", to_row)
        self._cc_row_label = "Копия"
        form.addRow("Копия", self.cc_edit)
        form.addRow("Скрытая копия", self.bcc_edit)
        form.addRow("Тема", self.subject_edit)
        self._form = form
        # Если письмо открыто с уже заполненными Копия/Скрытая копия
        # (например, редактирование сохранённого черновика) — сразу
        # показываем эти поля, а не прячем данные от пользователя.
        self._show_cc_bcc_fields(bool(cc) or bool(bcc))

        self.attachments_list = QListWidget()
        self.attachments_list.setMaximumHeight(70)
        for attachment in self.attachments:
            self.attachments_list.addItem(f"{attachment.filename} ({_format_size(len(attachment.payload))})")

        attach_button = QPushButton("Прикрепить файл…")
        attach_button.clicked.connect(self._on_attach)
        remove_button = QPushButton("Убрать")
        remove_button.clicked.connect(self._on_remove_attachment)

        attach_row = QHBoxLayout()
        attach_row.addWidget(attach_button)
        attach_row.addWidget(remove_button)
        attach_row.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Отправить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        # ActionRole, а не Accepted/Rejected — сохранение черновика не
        # должно идти по тому же пути, что и "Отправить" (жалоба: "в
        # черновики новые письма не сохраняет" — раньше кнопки не было
        # вовсе, письмо можно было либо отправить, либо потерять при
        # закрытии окна).
        self._save_as_draft = False
        self.save_draft_button = buttons.addButton("Сохранить черновик", QDialogButtonBox.ButtonRole.ActionRole)
        self.save_draft_button.clicked.connect(self._on_save_draft)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(attach_row)
        layout.addWidget(self.attachments_list)
        layout.addWidget(self.body_edit)
        layout.addWidget(buttons)

    def _on_save_draft(self) -> None:
        self._save_as_draft = True
        self.accept()

    def save_as_draft_requested(self) -> bool:
        return self._save_as_draft

    def _show_cc_bcc(self) -> None:
        self._show_cc_bcc_fields(True)

    def _show_cc_bcc_fields(self, visible: bool) -> None:
        self._form.setRowVisible(self.cc_edit, visible)
        self._form.setRowVisible(self.bcc_edit, visible)

    def cc_recipients(self) -> list[str]:
        return _parse_recipient_list(self.cc_edit.text())

    def bcc_recipients(self) -> list[str]:
        return _parse_recipient_list(self.bcc_edit.text())

    def _on_attach(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Прикрепить файлы")
        for path in paths:
            data = Path(path).read_bytes()
            content_type, _ = mimetypes.guess_type(path)
            attachment = OutgoingAttachment(
                filename=Path(path).name,
                content_type=content_type or "application/octet-stream",
                payload=data,
            )
            self.attachments.append(attachment)
            self.attachments_list.addItem(f"{attachment.filename} ({_format_size(len(data))})")

    def _on_remove_attachment(self) -> None:
        row = self.attachments_list.currentRow()
        if row < 0:
            return
        self.attachments_list.takeItem(row)
        del self.attachments[row]

    def recipients(self) -> list[str]:
        return _parse_recipient_list(self.to_edit.text())

    def subject(self) -> str:
        return self.subject_edit.text().strip()

    def body(self) -> str:
        return self.body_edit.toPlainText()


class MailRuleEditDialog(QDialog):
    _FIELDS = (("from", "От кого"), ("subject", "Тема"))

    def __init__(self, parent, folder_names: list[str], rule: MailRule | None = None):
        super().__init__(parent)
        self.setWindowTitle("Изменить правило" if rule else "Новое правило")

        self.field_combo = QComboBox(self)
        for value, label in self._FIELDS:
            self.field_combo.addItem(label, value)
        self.contains_edit = QLineEdit(self)
        self.contains_edit.setPlaceholderText("Часть адреса или темы, регистр не важен")
        self.folder_combo = QComboBox(self)
        self.folder_combo.addItems(folder_names)
        self.folder_combo.setEditable(True)  # папка может ещё не существовать на момент создания правила

        if rule is not None:
            index = self.field_combo.findData(rule.field)
            self.field_combo.setCurrentIndex(index if index >= 0 else 0)
            self.contains_edit.setText(rule.contains)
            self.folder_combo.setCurrentText(rule.target_folder)

        form = QFormLayout()
        form.addRow("Если", self.field_combo)
        form.addRow("Содержит", self.contains_edit)
        form.addRow("Переместить в", self.folder_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def to_rule(self) -> MailRule:
        return MailRule(
            field=self.field_combo.currentData(),
            contains=self.contains_edit.text().strip(),
            target_folder=self.folder_combo.currentText().strip(),
        )


class MailRulesDialog(QDialog):
    """Правила сортировки почты по подпапкам — жалоба из реального
    пилота ("нет сортировки писем по подпапкам (разбор по правилам) и
    создание этих папок"), явно отложенная пользователем на отдельный
    раунд после мелких фиксов. Применяются только вручную (кнопка
    "Применить правила" на панели) — сервер ни разу не проверялся вживую
    с этой функцией, автоматическая тихая раскладка почты при получении
    была бы больше риском, чем пользой на первом этапе."""

    _FIELD_LABELS = dict(MailRuleEditDialog._FIELDS)

    def __init__(self, parent, rules: list[MailRule], folder_names: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Правила сортировки почты")
        self.resize(520, 360)
        self._rules = list(rules)
        self._folder_names = folder_names

        info_label = QLabel(
            "Правила применяются только вручную — кнопкой «Применить правила» к письмам "
            "текущей папки, не автоматически при получении.",
            self,
        )
        info_label.setWordWrap(True)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["Поле", "Содержит", "Папка"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # Раньше правило можно было только целиком удалить и создать
        # заново — жалоба: "правила нельзя редактировать". Двойной клик —
        # тот же путь, что и кнопка "Изменить…", для единообразия с
        # остальными таблицами в приложении.
        self.table.itemDoubleClicked.connect(lambda _item: self._on_edit())
        self._refresh_table()

        add_button = QPushButton("Добавить…", self)
        add_button.clicked.connect(self._on_add)
        edit_button = QPushButton("Изменить…", self)
        edit_button.clicked.connect(self._on_edit)
        remove_button = QPushButton("Удалить", self)
        remove_button.clicked.connect(self._on_remove)
        button_row = QHBoxLayout()
        button_row.addWidget(add_button)
        button_row.addWidget(edit_button)
        button_row.addWidget(remove_button)
        button_row.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(info_label)
        layout.addLayout(button_row)
        layout.addWidget(self.table)
        layout.addWidget(buttons)

    def _refresh_table(self) -> None:
        self.table.setRowCount(len(self._rules))
        for row, rule in enumerate(self._rules):
            self.table.setItem(row, 0, QTableWidgetItem(self._FIELD_LABELS.get(rule.field, rule.field)))
            self.table.setItem(row, 1, QTableWidgetItem(rule.contains))
            self.table.setItem(row, 2, QTableWidgetItem(rule.target_folder))

    def _on_add(self) -> None:
        dialog = MailRuleEditDialog(self, self._folder_names)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        rule = dialog.to_rule()
        if not rule.contains or not rule.target_folder:
            return
        self._rules.append(rule)
        self._refresh_table()

    def _on_edit(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        dialog = MailRuleEditDialog(self, self._folder_names, self._rules[row])
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        rule = dialog.to_rule()
        if not rule.contains or not rule.target_folder:
            return
        self._rules[row] = rule
        self._refresh_table()
        self.table.selectRow(row)

    def _on_remove(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        del self._rules[row]
        self._refresh_table()

    def rules(self) -> list[MailRule]:
        return self._rules


class ArchiveFolderScopeDialog(QDialog):
    """Что выгружать при архивировании ЦЕЛОЙ папки: всё целиком или только
    всё старше выбранной даты (жалоба: "в архив можно убрать письма, но не
    папку целиком или частично, например всё до определённой даты")."""

    def __init__(self, parent, folder_display_name: str):
        super().__init__(parent)
        self.setWindowTitle("Архивировать папку")

        self.info_label = QLabel(f"Папка: {folder_display_name}", self)

        self.whole_radio = QRadioButton("Всю папку целиком", self)
        self.before_radio = QRadioButton("Всё старше указанной даты", self)
        self.whole_radio.setChecked(True)

        self.date_edit = QDateEdit(QDate.currentDate().addMonths(-1), self)
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd.MM.yyyy")
        self.date_edit.setEnabled(False)
        self.before_radio.toggled.connect(self.date_edit.setEnabled)

        layout = QVBoxLayout(self)
        layout.addWidget(self.info_label)
        layout.addWidget(self.whole_radio)
        layout.addWidget(self.before_radio)
        layout.addWidget(self.date_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def before_date(self) -> date | None:
        if not self.before_radio.isChecked():
            return None
        qd = self.date_edit.date()
        return date(qd.year(), qd.month(), qd.day())


class ArchiveTargetDialog(QDialog):
    """Общий диалог выбора архива для выгрузки/импорта: архив (из уже
    открытых, либо «выбрать/создать другой»), опционально папка внутри
    архива, опционально копировать/переместить (для выгрузки из ящика)."""

    def __init__(
        self,
        parent,
        archive_names: dict[str, str],
        *,
        title: str,
        ask_folder: bool = False,
        default_folder: str = "",
        ask_move_copy: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)

        self.archive_combo = QComboBox()
        self._archive_keys: list[str] = list(archive_names.keys())
        for key in self._archive_keys:
            self.archive_combo.addItem(archive_names[key])
        self.archive_combo.addItem("Открыть или создать другой архив…")
        self._archive_keys.append("")

        form = QFormLayout()
        form.addRow("Архив", self.archive_combo)

        self.folder_edit: QLineEdit | None = None
        if ask_folder:
            self.folder_edit = QLineEdit(default_folder)
            form.addRow("Папка в архиве", self.folder_edit)

        self.copy_radio: QRadioButton | None = None
        self.move_radio: QRadioButton | None = None
        if ask_move_copy:
            self.copy_radio = QRadioButton("Копировать (оставить в ящике)")
            self.move_radio = QRadioButton("Переместить (удалить из ящика после выгрузки)")
            self.copy_radio.setChecked(True)
            form.addRow(self.copy_radio)
            form.addRow(self.move_radio)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def selected_archive_key(self) -> str:
        return self._archive_keys[self.archive_combo.currentIndex()]

    def folder_name(self) -> str:
        return (self.folder_edit.text().strip() if self.folder_edit else "") or "Импорт"

    def move(self) -> bool:
        return bool(self.move_radio and self.move_radio.isChecked())


_RECURRENCE_OPTIONS: list[tuple[str, str | None]] = [
    ("Не повторяется", None),
    ("Каждый день", "FREQ=DAILY"),
    ("Каждую неделю", "FREQ=WEEKLY"),
    ("Каждый месяц", "FREQ=MONTHLY"),
    ("Каждый год", "FREQ=YEARLY"),
]


class EventDialog(QDialog):
    """Создание встречи и редактирование своей — тот же диалог: правка
    существующей организованной встречи это и есть перенос (см.
    MainWindow._save_event_from_dialog: SEQUENCE растёт, участникам уходит
    обновлённый REQUEST)."""

    def __init__(
        self,
        parent=None,
        *,
        event: calendar_store.Event | None = None,
        my_email: str = "",
        contacts: list[contact_store.Contact] | None = None,
        default_start: datetime | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Изменить встречу" if event else "Новая встреча")
        self.resize(460, 560)
        self.attachments: list[Attachment] = list(event.attachments) if event else []
        self._color = event.color if event else None
        self._temp_dirs: list[Path] = []

        self.summary_edit = QLineEdit(event.summary if event else "")
        self.summary_edit.setPlaceholderText("Придумайте название")
        summary_font = self.summary_edit.font()
        summary_font.setPointSize(summary_font.pointSize() + 3)
        self.summary_edit.setFont(summary_font)

        self.location_edit = QLineEdit(event.location if event else "")
        self.location_edit.setPlaceholderText("Укажите место")
        self._contacts = contacts or []

        other_attendees = [a.email for a in event.attendees if a.email != my_email] if event else []
        self.attendees_edit = QLineEdit(", ".join(other_attendees))
        self.attendees_edit.setPlaceholderText("Выберите участников")
        if contacts:
            _install_recipient_completer(self.attendees_edit, contacts)
        self.description_edit = QPlainTextEdit(event.description if event else "")
        self.description_edit.setPlaceholderText("Добавьте описание")
        self.description_edit.setFixedHeight(70)
        # Жалоба "убери внутреннюю рамку" была про это поле — стандартная
        # рамка QPlainTextEdit вокруг текста описания, а не про карточку
        # события в недельной сетке (которую я по ошибке трогал раньше).
        self.description_edit.setFrameShape(QFrame.Shape.NoFrame)

        attendees_address_book_button = QPushButton("Адресная книга…", self)
        attendees_address_book_button.clicked.connect(
            lambda: _open_contact_picker(self, self.attendees_edit, self._contacts)
        )

        fallback_start = default_start or (
            datetime.now().astimezone().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        )
        start_local = event.dtstart.astimezone() if event else fallback_start
        end_local = event.dtend.astimezone() if event else start_local + timedelta(hours=1)

        self.start_edit = QDateTimeEdit(QDateTime(start_local.date(), start_local.time()), self)
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.end_edit = QDateTimeEdit(QDateTime(end_local.date(), end_local.time()), self)
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setDisplayFormat("dd.MM.yyyy HH:mm")

        self.all_day_check = QCheckBox("Весь день", self)
        self.all_day_check.setChecked(event.all_day if event else False)
        self.all_day_check.toggled.connect(self._on_all_day_toggled)
        self._on_all_day_toggled(self.all_day_check.isChecked())

        self.recurrence_combo = QComboBox(self)
        for label, value in _RECURRENCE_OPTIONS:
            self.recurrence_combo.addItem(label, value)
        if event and event.recurrence_rule:
            index = self.recurrence_combo.findData(event.recurrence_rule)
            self.recurrence_combo.setCurrentIndex(index if index >= 0 else 0)

        self.color_button = QPushButton(self)
        self.color_button.clicked.connect(self._open_color_menu)
        self._apply_color_button_style()

        self.attachments_list = QListWidget(self)
        self.attachments_list.setMaximumHeight(70)
        for attachment in self.attachments:
            self.attachments_list.addItem(f"{attachment.filename} ({_format_size(attachment.size)})")
        self.attachments_list.setVisible(bool(self.attachments))
        # Раньше вложения можно было только прикрепить/убрать, но не
        # открыть, пока событие ещё не сохранено (жалоба: "вложения в
        # событие не открываются") — EventDetailsDialog (просмотр ЧУЖОГО
        # события) уже умел это, здесь просто не было подключено вовсе.
        self.attachments_list.itemDoubleClicked.connect(self._open_attachment)
        self.attachments_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.attachments_list.customContextMenuRequested.connect(self._attachment_context_menu)

        attach_button = QPushButton("Прикрепить файл…", self)
        attach_button.clicked.connect(self._on_attach)
        remove_button = QPushButton("Убрать", self)
        remove_button.clicked.connect(self._on_remove_attachment)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        # Компактные строки "значок слева + поле" вместо подписанных полей
        # QFormLayout — так выглядит попап создания события в референсе
        # (VK Mail): "Придумайте название" присланный пользователем.
        start_time_button = QPushButton("▾", self)
        start_time_button.setFixedWidth(22)
        start_time_button.setToolTip("Выбрать время из списка")
        start_time_button.clicked.connect(lambda: self._open_time_menu(self.start_edit, start_time_button))
        end_time_button = QPushButton("▾", self)
        end_time_button.setFixedWidth(22)
        end_time_button.setToolTip("Выбрать время из списка")
        end_time_button.clicked.connect(lambda: self._open_time_menu(self.end_edit, end_time_button))

        time_row = QHBoxLayout()
        time_row.addWidget(_icon_label("time", self))
        time_row.addWidget(self.start_edit)
        time_row.addWidget(start_time_button)
        time_row.addWidget(QLabel("—", self))
        time_row.addWidget(self.end_edit)
        time_row.addWidget(end_time_button)
        time_row.addWidget(self.all_day_check)
        time_row.addStretch(1)

        repeat_row = QHBoxLayout()
        repeat_row.addWidget(_icon_label("repeat", self))
        repeat_row.addWidget(self.recurrence_combo)
        repeat_row.addStretch(1)

        attendees_row = QHBoxLayout()
        attendees_row.addWidget(_icon_label("people", self))
        attendees_row.addWidget(self.attendees_edit)
        attendees_row.addWidget(attendees_address_book_button)

        location_row = QHBoxLayout()
        location_row.addWidget(_icon_label("location", self))
        location_row.addWidget(self.location_edit)

        description_row = QHBoxLayout()
        description_row.addWidget(_icon_label("description", self))
        description_row.addWidget(self.description_edit)

        attach_row = QHBoxLayout()
        attach_row.addSpacing(22)
        attach_row.addWidget(attach_button)
        attach_row.addWidget(remove_button)
        attach_row.addStretch(1)

        attachments_list_row = QHBoxLayout()
        attachments_list_row.addSpacing(22)
        attachments_list_row.addWidget(self.attachments_list)

        color_row = QHBoxLayout()
        color_row.addSpacing(22)
        color_row.addWidget(self.color_button)
        color_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary_edit)
        layout.addLayout(time_row)
        layout.addLayout(repeat_row)
        layout.addLayout(attendees_row)
        layout.addLayout(location_row)
        layout.addLayout(description_row)
        layout.addLayout(attach_row)
        layout.addLayout(attachments_list_row)
        layout.addLayout(color_row)
        layout.addWidget(buttons)

    def _open_time_menu(self, target_edit: QDateTimeEdit, anchor_button: QPushButton) -> None:
        # Быстрый выбор времени списком (шаг 30 минут) — Qt не даёт
        # QDateTimeEdit всплывающий выбор времени "из коробки" (только
        # календарь для даты), а вручную набирать время неудобно (жалоба:
        # "время в событии всё ещё набирается вручную — нет выбора").
        # Сам QDateTimeEdit по-прежнему можно набрать/прокрутить вручную —
        # это меню лишь более быстрый путь, не замена.
        menu = QMenu(self)
        for hour in range(24):
            for minute in (0, 30):
                label = f"{hour:02d}:{minute:02d}"
                action = menu.addAction(label)
                action.setData((hour, minute))
        chosen = menu.exec(anchor_button.mapToGlobal(anchor_button.rect().bottomLeft()))
        if chosen is None:
            return
        hour, minute = chosen.data()
        current = target_edit.dateTime()
        target_edit.setDateTime(QDateTime(current.date(), QTime(hour, minute)))

    def _apply_color_button_style(self) -> None:
        if self._color:
            swatch = self._color
            label_text = next((name for name, hexval in _EVENT_COLOR_PALETTE if hexval == self._color), "Цвет события")
        else:
            swatch = "#3B6FB6"  # тот же синий, что и автоцвет организатора в week_calendar.py
            label_text = "Цвет события (авто)"
        self.color_button.setText(f"● {label_text} ▾")
        self.color_button.setStyleSheet(f"QPushButton {{ color: {swatch}; font-weight: 600; }}")

    def _open_color_menu(self) -> None:
        menu = QMenu(self)
        auto_action = menu.addAction("Авто (по роли)")
        menu.addSeparator()
        color_actions = {}
        for name, hexval in _EVENT_COLOR_PALETTE:
            action = menu.addAction(_dot_icon(hexval), name)
            color_actions[action] = hexval
        chosen = menu.exec(QCursor.pos())
        if chosen is None:
            return
        self._color = None if chosen is auto_action else color_actions[chosen]
        self._apply_color_button_style()

    def color(self) -> str | None:
        return self._color

    def _on_attach(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Прикрепить файлы")
        for path in paths:
            data = Path(path).read_bytes()
            content_type, _ = mimetypes.guess_type(path)
            attachment = Attachment(
                filename=Path(path).name, content_type=content_type or "application/octet-stream", payload=data
            )
            self.attachments.append(attachment)
            self.attachments_list.addItem(f"{attachment.filename} ({_format_size(len(data))})")
        self.attachments_list.setVisible(bool(self.attachments))

    def _on_remove_attachment(self) -> None:
        row = self.attachments_list.currentRow()
        if row < 0:
            return
        self.attachments_list.takeItem(row)
        del self.attachments[row]
        self.attachments_list.setVisible(bool(self.attachments))

    def _open_attachment(self, item: QListWidgetItem) -> None:
        attachment = self.attachments[self.attachments_list.row(item)]
        try:
            temp_dir = Path(tempfile.mkdtemp(prefix="redmail_event_"))
            temp_path = temp_dir / _safe_attachment_filename(attachment.filename)
            temp_path.write_bytes(attachment.payload)
        except OSError as exc:
            QMessageBox.critical(self, "Не удалось открыть вложение", str(exc))
            return
        self._temp_dirs.append(temp_dir)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(temp_path)))

    def _attachment_context_menu(self, pos) -> None:
        item = self.attachments_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        save_action = menu.addAction("Сохранить как…")
        chosen = menu.exec(self.attachments_list.mapToGlobal(pos))
        if chosen is not save_action:
            return
        attachment = self.attachments[self.attachments_list.row(item)]
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить вложение", _safe_attachment_filename(attachment.filename))
        if not path:
            return
        try:
            Path(path).write_bytes(attachment.payload)
        except OSError as exc:
            QMessageBox.critical(self, "Не удалось сохранить", str(exc))

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        for temp_dir in self._temp_dirs:
            shutil.rmtree(temp_dir, ignore_errors=True)
        super().closeEvent(event)

    def summary(self) -> str:
        return self.summary_edit.text().strip()

    def location(self) -> str:
        return self.location_edit.text().strip()

    def description(self) -> str:
        return self.description_edit.toPlainText()

    def attendee_emails(self) -> list[str]:
        return _parse_recipient_list(self.attendees_edit.text())

    def start_utc(self) -> datetime:
        return self._utc(self.start_edit.dateTime())

    def end_utc(self) -> datetime:
        return self._utc(self.end_edit.dateTime())

    @staticmethod
    def _utc(value: QDateTime) -> datetime:
        # QDateTimeEdit показывает местное время; интерпретируем как local
        # wall-clock и переводим в UTC для хранения (naive.astimezone() без
        # аргументов трактует наивное время как системный часовой пояс).
        date, time = value.date(), value.time()
        local = datetime(date.year(), date.month(), date.day(), time.hour(), time.minute()).astimezone()
        return local.astimezone(timezone.utc)

    def recurrence_rule(self) -> str | None:
        return self.recurrence_combo.currentData()

    def all_day(self) -> bool:
        return self.all_day_check.isChecked()

    def _on_all_day_toggled(self, checked: bool) -> None:
        # "Весь день" — время суток не имеет значения, только даты; прячем
        # часы/минуты в отображении, чтобы это было видно, а не только
        # угадывалось по галочке (жалоба на референс: "нет возможности
        # выбрать весь день" — раньше такого переключателя не было вовсе).
        fmt = "dd.MM.yyyy" if checked else "dd.MM.yyyy HH:mm"
        self.start_edit.setDisplayFormat(fmt)
        self.end_edit.setDisplayFormat(fmt)


class EventDetailsDialog(QDialog):
    """Просмотр встречи, которую организовал не я — со ссылками кликабельными
    и вложениями открываемыми/сохраняемыми, как в письме, плюс участники
    (с аватарками и статусом ответа, как в референсе VK Mail) и кнопки
    "Иду/Не иду/Может быть" прямо здесь — раньше участие можно было
    поменять только через приглашение в почте, что неудобно, если письмо
    уже прочитано/не под рукой."""

    def __init__(self, parent, event: calendar_store.Event):
        super().__init__(parent)
        self.setWindowTitle(event.summary or "(без темы)")
        self.resize(440, 460)
        self.event = event
        self._temp_dirs: list[Path] = []
        self.chosen_participation: str | None = None
        self.copy_requested = False

        info = QTextBrowser(self)
        info.setOpenExternalLinks(True)
        parts = [f"<b>{html.escape(event.summary or '(без темы)')}</b><br>{_format_event_time(event)}"]
        if event.location:
            parts.append(html.escape(event.location))

        avatar_index = 0
        attendee_rows = []

        def _avatar_row(name: str, email: str, suffix: str) -> str:
            nonlocal avatar_index
            letter = _attendee_avatar_letter(name, email)
            color = _avatar_color(email or name)
            url = f"avatar://{avatar_index}"
            info.document().addResource(
                QTextDocument.ResourceType.ImageResource, QUrl(url), _avatar_pixmap(letter, color)
            )
            avatar_index += 1
            display = html.escape(name or email)
            return f'<tr><td><img src="{url}"></td><td>&nbsp;{display}{suffix}</td></tr>'

        organizer_display = event.organizer_name or event.organizer_email
        attendee_rows.append(_avatar_row(organizer_display, event.organizer_email, " — организатор"))
        for attendee in event.attendees:
            label = _PARTICIPATION_LABELS.get(attendee.participation, "")
            suffix = f" — {label}" if attendee.participation != "needs-action" else ""
            attendee_rows.append(_avatar_row(attendee.name, attendee.email, suffix))
        parts.append(
            "<br><b>Участники</b><table cellspacing=\"4\">" + "".join(attendee_rows) + "</table>"
        )
        parts.append(
            f"Моё участие: {html.escape(_PARTICIPATION_LABELS.get(event.my_participation, event.my_participation))}"
        )
        if event.description:
            parts.append("<br>" + _linkify(event.description))
        info.setHtml("<br>".join(parts))

        layout = QVBoxLayout(self)
        layout.addWidget(info)

        if event.attachments:
            layout.addWidget(QLabel("Вложения", self))
            self.attachments_list = QListWidget(self)
            self.attachments_list.setMaximumHeight(90)
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
            for attachment in event.attachments:
                self.attachments_list.addItem(
                    QListWidgetItem(icon, f"{attachment.filename} ({_format_size(attachment.size)})")
                )
            self.attachments_list.itemDoubleClicked.connect(self._open_attachment)
            self.attachments_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.attachments_list.customContextMenuRequested.connect(self._attachment_context_menu)
            layout.addWidget(self.attachments_list)

        rsvp_row = QHBoxLayout()
        going_button = QPushButton("Иду", self)
        not_going_button = QPushButton("Не иду", self)
        maybe_button = QPushButton("Может быть", self)
        going_button.clicked.connect(lambda: self._respond("accepted"))
        not_going_button.clicked.connect(lambda: self._respond("declined"))
        maybe_button.clicked.connect(lambda: self._respond("tentative"))
        rsvp_row.addWidget(going_button)
        rsvp_row.addWidget(not_going_button)
        rsvp_row.addWidget(maybe_button)
        rsvp_row.addStretch(1)
        copy_button = QPushButton("Копировать событие", self)
        copy_button.clicked.connect(self._request_copy)
        rsvp_row.addWidget(copy_button)
        layout.addLayout(rsvp_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _respond(self, participation: str) -> None:
        self.chosen_participation = participation
        self.accept()

    def _request_copy(self) -> None:
        self.copy_requested = True
        self.accept()

    def _open_attachment(self, item: QListWidgetItem) -> None:
        attachment = self.event.attachments[self.attachments_list.row(item)]
        try:
            temp_dir = Path(tempfile.mkdtemp(prefix="redmail_event_"))
            temp_path = temp_dir / _safe_attachment_filename(attachment.filename)
            temp_path.write_bytes(attachment.payload)
        except OSError as exc:
            QMessageBox.critical(self, "Не удалось открыть вложение", str(exc))
            return
        self._temp_dirs.append(temp_dir)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(temp_path)))

    def _attachment_context_menu(self, pos) -> None:
        item = self.attachments_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        save_action = menu.addAction("Сохранить как…")
        chosen = menu.exec(self.attachments_list.mapToGlobal(pos))
        if chosen is not save_action:
            return
        attachment = self.event.attachments[self.attachments_list.row(item)]
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить вложение", _safe_attachment_filename(attachment.filename))
        if not path:
            return
        try:
            Path(path).write_bytes(attachment.payload)
        except OSError as exc:
            QMessageBox.critical(self, "Не удалось сохранить", str(exc))

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        for temp_dir in self._temp_dirs:
            shutil.rmtree(temp_dir, ignore_errors=True)
        super().closeEvent(event)


class ContactDialog(QDialog):
    def __init__(self, parent=None, *, contact: contact_store.Contact | None = None):
        super().__init__(parent)
        self.setWindowTitle("Изменить контакт" if contact else "Новый контакт")
        self.resize(420, 380)
        self._contact = contact

        self.name_edit = QLineEdit(contact.display_name if contact else "")
        self.emails_edit = QLineEdit(", ".join(contact.emails) if contact else "")
        self.emails_edit.setPlaceholderText("Через запятую, если несколько")
        self.phone_edit = QLineEdit(contact.phone if contact else "")
        self.org_edit = QLineEdit(contact.organization if contact else "")
        self.notes_edit = QPlainTextEdit(contact.notes if contact else "")

        form = QFormLayout()
        form.addRow("Имя", self.name_edit)
        form.addRow("Email", self.emails_edit)
        form.addRow("Телефон", self.phone_edit)
        form.addRow("Организация", self.org_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("Заметки", self))
        layout.addWidget(self.notes_edit)
        layout.addWidget(buttons)

    def to_contact(self) -> contact_store.Contact:
        return contact_store.Contact(
            id=self._contact.id if self._contact else None,
            uid=self._contact.uid if self._contact else "",
            display_name=self.name_edit.text().strip(),
            emails=[e.strip() for e in self.emails_edit.text().split(",") if e.strip()],
            phone=self.phone_edit.text().strip(),
            organization=self.org_edit.text().strip(),
            notes=self.notes_edit.toPlainText(),
        )


class _CallableWorker(QThread):
    """Выполняет одну функцию в отдельном потоке и сообщает результат через
    сигналы — без этого любая сетевая операция (SMTP-отправка, разбор
    .pst/.mbox/Maildir) выполнялась прямо в обработчике сигнала Qt, то есть
    в основном потоке интерфейса: окно переставало перерисовываться и
    реагировать на клики на всё время операции (для .pst на реальном файле
    — почти две минуты, для отправки приглашения по SMTP — пара секунд).
    Жалоба пользователя: "подвисает интерфейс... импорт pst не работает"
    (на деле работал, но окно выглядело замёршим так долго, что это
    читалось как сбой)."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, parent: QObject | None = None, **kwargs):
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:  # noqa: N802 - Qt override
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # передаём текст в основной поток — сам exc через границу потоков не тащим
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)


class _FolderTreeWidget(QTreeWidget):
    """Дерево папок (живые ящики + архивы) с перетаскиванием мышью — по
    просьбе пользователя ("нужно сделать возможность перетаскивать папки
    мышью"). Не пользуемся стандартным Qt-InternalMove как есть: он бы сам
    переставил узлы в дереве ДО того, как реальная папка на диске/сервере
    действительно переместилась — вместо этого dropEvent() полностью
    перехватывается, а фактическая перестройка дерева происходит только
    после успешного archive_store.rename_folder()/session.rename_folder()
    через folderDropped, которую слушает MainWindow."""

    folderDropped = Signal(object, object)  # (перетаскиваемый item, item под курсором или None)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._drag_source_item: QTreeWidgetItem | None = None

    def startDrag(self, supported_actions) -> None:  # noqa: N802 - Qt override
        self._drag_source_item = self.currentItem()
        super().startDrag(supported_actions)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        target_item = self.itemAt(pos)
        source_item = self._drag_source_item
        self._drag_source_item = None
        event.ignore()  # никогда не даём Qt самому переставить узлы визуально
        if source_item is not None and source_item is not target_item:
            self.folderDropped.emit(source_item, target_item)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Почтовый клиент RED OS — прототип")
        self.resize(1200, 760)  # выше умолчание помогает и календарной сетке, и списку писем; реальный размер запоминается между запусками

        # Несколько одновременно подключённых учётных записей (жалоба:
        # "несколько учётных записей одновременно"). self.account/
        # self.mailbox/self.smtp_account/self.account_root/
        # self.trash_folder_name остаются "текущей" учётной записью —
        # той, чья папка сейчас выбрана в дереве — ради обратной
        # совместимости со всем кодом почты/календаря/CalDAV, который их
        # читает напрямую; self.mailboxes и парные словари ниже — источник
        # истины на несколько записей сразу.
        self.mailboxes: dict[str, CachedMailbox] = {}
        self.mailbox_accounts: dict[str, Account | EwsAccount] = {}
        self.mailbox_smtp_accounts: dict[str, SmtpAccount | None] = {}
        self.mailbox_trash_folders: dict[str, str | None] = {}
        self.mailbox_sent_folders: dict[str, str | None] = {}
        self.mailbox_drafts_folders: dict[str, str | None] = {}
        self.mailbox_tree_roots: dict[str, QTreeWidgetItem] = {}
        # "imap" | "ews" на каждый ключ (username для IMAP, email для EWS) —
        # определяет, каким путём слать почту для ТЕКУЩЕЙ учётной записи:
        # через отдельный SMTP-аккаунт или через сам EWS-сеанс (см.
        # _send_message_in_background). self.account_protocol — алиас
        # "текущей" по тому же принципу, что self.account/self.mailbox.
        self.mailbox_protocols: dict[str, str] = {}
        self.account_protocol: str = "imap"

        self.account: Account | EwsAccount | None = None
        self.mailbox: CachedMailbox | None = None
        self.account_root: QTreeWidgetItem | None = None
        self.archives: dict[str, ArchiveSource] = {}
        self.archive_tree_roots: dict[str, QTreeWidgetItem] = {}
        self.active_source: CachedMailbox | ArchiveSource | None = None
        self.smtp_account: SmtpAccount | None = None
        self.trash_folder_name: str | None = None
        self.sent_folder_name: str | None = None
        self.drafts_folder_name: str | None = None
        self.current_folder: str | None = None
        self.current_summaries: list[MessageSummary] = []
        self.summaries_by_uid: dict[int, MessageSummary] = {}
        self.current_body: str = ""
        self.current_attachments: list[Attachment] = []
        self.current_content: MessageContent | None = None
        self.selected_summary: MessageSummary | None = None
        self._message_windows: list[QWidget] = []  # держим ссылки, пока окна открыты
        self.poll_interval_minutes = load_poll_interval_minutes()
        self.pane_orientation = load_pane_orientation()
        self.caldav_url = load_caldav_url()
        self.archive_storage_dir = load_archive_storage_dir()
        self.mail_rules: list[MailRule] = load_mail_rules()
        # Держим ссылки на фоновые потоки (импорт архивов, отправка
        # приглашений) — без этого Python может собрать QThread раньше, чем
        # он реально завершится, даже если у него есть родитель-QObject.
        self._background_workers: list[QThread] = []
        self.filter_column = COL_SUBJECT
        self.marker_filter: str | None = None
        self._temp_attachment_dirs: list[Path] = []
        self._base_font_point_size = QApplication.instance().font().pointSizeF() or 10.0

        # Один локальный календарь на пользователя (не на учётную запись —
        # как и почтовый кэш, это просто локальное состояние приложения).
        self.calendar_path = app_dir() / "calendar.rmcal"
        self.contacts_path = app_dir() / "contacts.rmcontacts"
        self.current_invite: itip.IncomingInvite | None = None
        self.selected_contact: contact_store.Contact | None = None
        self._contacts_by_row: list[contact_store.Contact] = []

        self.folder_tree = _FolderTreeWidget(self)
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.currentItemChanged.connect(self.on_folder_item_changed)
        self.folder_tree.folderDropped.connect(self.on_folder_dropped)
        self.folder_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.folder_tree.customContextMenuRequested.connect(self.on_folder_tree_context_menu)
        self._folder_delimiter = "/"

        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText(f"Фильтр: {_FILTER_COLUMNS[self.filter_column]}")
        self.filter_edit.textChanged.connect(self.on_filter_changed)

        self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels(["", _FLAG_MARK, "!", _ATTACHMENT_MARK, "От кого", "Тема", "Дата"])
        self._update_marker_filter_indicator()
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        # Тема — Interactive, а не Stretch: Qt не даёт вручную тянуть границу
        # у Stretch-колонки, а пользователю нужно было именно это (жалоба:
        # "не могу изменить ширину колонки тема"). Ширина по умолчанию —
        # просто разумная стартовая, реальная запоминается между запусками
        # через _restore_window_state()/mail_columns_state.
        header.setSectionResizeMode(COL_SUBJECT, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(COL_SUBJECT, 320)
        for col in (COL_CHECK, COL_FLAG, COL_IMPORTANCE, COL_ATTACHMENT):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionsMovable(True)
        header.sectionClicked.connect(self._set_filter_column)
        # Ни одна колонка не Stretch (см. комментарий выше про "Тема") — без
        # этого сумма ширин колонок не зависит от размера окна вовсе, и
        # таблица оставляет пустую полосу справа вместо того, чтобы занять
        # всё окно (жалоба: "таблица... не растягивается на все окно",
        # особенно заметно при чтении справа — там панели письма ещё и
        # делят с таблицей ширину, не только высоту). stretchLastSection
        # отдаёт лишнее место последней колонке (Дата), не мешая
        # Interactive-изменению ширины остальных колонок пользователем.
        header.setStretchLastSection(True)
        self.table.setIconSize(QSize(_MARKER_ICON_SIZE, _MARKER_ICON_SIZE))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.itemSelectionChanged.connect(self.on_message_selected)
        self.table.itemClicked.connect(self.on_table_item_clicked)
        self.table.itemDoubleClicked.connect(self.on_table_item_double_clicked)
        self.table.currentCellChanged.connect(self.on_current_cell_changed)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.on_mail_table_context_menu)

        # Команды над списком писем — по просьбе пользователя ("команды
        # связанные с письмами разместить в среднем окне над фильтрами"):
        # они действуют на текущее/отмеченные письма в этой же таблице, а
        # не на весь ящик/архив, так что логично разместить их прямо над
        # таблицей, а не в общем тулбаре наверху окна. Сами QAction создаём
        # здесь (а не в основном тулбаре ниже по __init__), потому что
        # именно здесь строится их видимая панель.
        compose_action = QAction(_toolbar_icon("compose"), "Написать письмо…", self)
        compose_action.triggered.connect(self.on_compose)

        self.reply_action = QAction(_toolbar_icon("reply"), "Ответить", self)
        self.reply_action.triggered.connect(self.on_reply)

        self.forward_action = QAction(_toolbar_icon("forward"), "Переслать", self)
        self.forward_action.triggered.connect(self.on_forward)

        self.delete_action = QAction(_toolbar_icon("delete"), "Удалить", self)
        self.delete_action.setToolTip("Удалить — в корзину. Shift+Удалить — безвозвратно.")
        self.delete_action.triggered.connect(self.on_delete_selected)

        self.archive_selected_action = QAction(_toolbar_icon("archive"), "В архив…", self)
        self.archive_selected_action.setToolTip("В архив — выгрузить отмеченные письма в архив (копия или перемещение)")
        self.archive_selected_action.triggered.connect(self.on_archive_selected)

        mail_actions_toolbar = QToolBar("Письмо", self)
        mail_actions_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        for action in (compose_action, self.reply_action, self.forward_action, self.delete_action, self.archive_selected_action):
            mail_actions_toolbar.addAction(action)

        table_container = QWidget(self)
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(mail_actions_toolbar)
        table_layout.addWidget(self.filter_edit)
        table_layout.addWidget(self.table)

        self.attachments_list = QListWidget(self)
        self.attachments_list.setMaximumHeight(110)
        self.attachments_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.attachments_list.itemDoubleClicked.connect(self.on_open_attachment)
        self.attachments_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.attachments_list.customContextMenuRequested.connect(self.on_attachment_context_menu)
        self.attachments_list.hide()

        self.invite_bar = QWidget(self)
        self.invite_bar.setAutoFillBackground(True)
        invite_layout = QHBoxLayout(self.invite_bar)
        invite_layout.setContentsMargins(8, 6, 8, 6)
        self.invite_label = QLabel(self.invite_bar)
        self.invite_label.setWordWrap(True)
        invite_layout.addWidget(self.invite_label, 1)
        self.invite_accept_button = QPushButton("Принять", self.invite_bar)
        self.invite_tentative_button = QPushButton("Предварительно", self.invite_bar)
        self.invite_decline_button = QPushButton("Отклонить", self.invite_bar)
        self.invite_accept_button.clicked.connect(lambda: self.on_invite_response("accepted"))
        self.invite_tentative_button.clicked.connect(lambda: self.on_invite_response("tentative"))
        self.invite_decline_button.clicked.connect(lambda: self.on_invite_response("declined"))
        for button in (self.invite_accept_button, self.invite_tentative_button, self.invite_decline_button):
            invite_layout.addWidget(button)
        self.invite_bar.hide()

        # Реквизиты письма (тема/отправитель/получатели) — раньше нигде не
        # отображались (жалоба: "при просмотре письма невидно его
        # реквизитов"). Отдельный виджет над телом, а не встроено в HTML
        # письма — тело часто само содержит полный <html>...</html>, и
        # примешивать туда наш текст means риск сломать вёрстку письма.
        self.message_header_widget = QWidget(self)
        header_layout = QHBoxLayout(self.message_header_widget)
        header_layout.setContentsMargins(6, 4, 6, 4)
        self.message_header_label = QLabel(self.message_header_widget)
        self.message_header_label.setWordWrap(True)
        self.message_header_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header_layout.addWidget(self.message_header_label, 1)
        self.open_message_window_button = QPushButton("Открыть в окне", self.message_header_widget)
        self.open_message_window_button.setToolTip("Открыть письмо в отдельном окне")
        self.open_message_window_button.clicked.connect(self.on_open_message_window)
        header_layout.addWidget(self.open_message_window_button)
        self.message_header_widget.hide()

        # Список остальных писем той же цепочки (по теме, без Re:/Fwd:) —
        # быстрый переход к письму внутри последовательной ленты (см.
        # _render_thread ниже), которая теперь и есть основной способ
        # просмотра цепочки — раньше клик по элементу списка просто менял
        # выбранную строку в таблице, а сама цепочка целиком нигде не
        # показывалась подряд (жалоба: "есть только ссылки на другие
        # письма, а надо просмотр цепочки последовательно").
        self.thread_list = QListWidget(self)
        self.thread_list.setMaximumHeight(90)
        self.thread_list.itemClicked.connect(self._on_thread_item_clicked)
        self.thread_list.hide()

        # Содержимое писем цепочки (кроме текущего) подгружается отдельными
        # запросами к active_source — на живом IMAP это реальный сетевой
        # round trip на каждое письмо цепочки. Кэш по uid, очищается при
        # смене папки (on_folder_item_changed), не даёт грузить одно и то
        # же письмо заново при каждом переключении между письмами одной
        # цепочки.
        self._thread_content_cache: dict[int, MessageContent] = {}
        # Растёт при каждом открытии письма/переключении папки — фоновый
        # результат подгрузки цепочки применяется, только если токен всё
        # ещё тот же (иначе пользователь уже открыл что-то другое, а
        # устаревший ответ из сети может прийти позже).
        self._thread_render_token = 0

        self.reading_pane = QTextBrowser(self)
        self.reading_pane.setReadOnly(True)
        self.reading_pane.setOpenExternalLinks(True)
        self.reading_pane.setPlaceholderText("Выберите письмо, чтобы увидеть текст")

        reading_container = QWidget(self)
        reading_layout = QVBoxLayout(reading_container)
        reading_layout.setContentsMargins(0, 0, 0, 0)
        reading_layout.addWidget(self.message_header_widget)
        reading_layout.addWidget(self.thread_list)
        reading_layout.addWidget(self.invite_bar)
        reading_layout.addWidget(self.attachments_list)
        reading_layout.addWidget(self.reading_pane)

        self.right_splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.right_splitter.addWidget(table_container)
        self.right_splitter.addWidget(reading_container)
        self.right_splitter.setStretchFactor(0, 2)
        self.right_splitter.setStretchFactor(1, 1)
        self._apply_pane_orientation()

        main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        main_splitter.addWidget(self.folder_tree)
        main_splitter.addWidget(self.right_splitter)
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([220, 980])

        self.calendar_week_start = week_start_for(date.today())
        self.selected_calendar_event: calendar_store.Event | None = None
        self._calendar_scrolled_to_now = False
        self.calendar_show_events = True

        self.calendar_month_label = QLabel(self)
        self.calendar_month_label.setStyleSheet("font-weight: 600; font-size: 13pt;")
        self.calendar_view_mode = "week"  # "week" | "month" — "день" пока не реализован
        self.calendar_month_anchor = date.today().replace(day=1)
        self.calendar_view_combo = QComboBox(self)
        self.calendar_view_combo.addItem("Неделя")
        self.calendar_view_combo.addItem("Месяц")
        self.calendar_view_combo.setMinimumWidth(90)
        self.calendar_view_combo.currentTextChanged.connect(self.on_calendar_view_mode_changed)

        # Раньше все команды календаря были текстовыми подписями — с
        # ростом их числа тулбар не помещался по ширине. Переведены на
        # иконки с исходным текстом в подсказке (по просьбе пользователя),
        # как уже сделано для писем/архива выше.
        calendar_toolbar = QToolBar("Календарь", self)
        calendar_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        today_action = QAction(_toolbar_icon("today"), "Сегодня", self)
        today_action.triggered.connect(self.on_calendar_today)
        calendar_toolbar.addAction(today_action)
        prev_week_action = QAction(_toolbar_icon("prev"), "Предыдущая неделя", self)
        prev_week_action.triggered.connect(self.on_calendar_prev_week)
        calendar_toolbar.addAction(prev_week_action)
        next_week_action = QAction(_toolbar_icon("next"), "Следующая неделя", self)
        next_week_action.triggered.connect(self.on_calendar_next_week)
        calendar_toolbar.addAction(next_week_action)
        calendar_toolbar.addWidget(self.calendar_month_label)
        calendar_toolbar.addSeparator()
        new_event_action = QAction(_toolbar_icon("add"), "Новая встреча…", self)
        new_event_action.triggered.connect(self.on_new_event)
        calendar_toolbar.addAction(new_event_action)
        cancel_event_action = QAction(_toolbar_icon("cancel_event"), "Отменить встречу", self)
        cancel_event_action.setToolTip("Отменить встречу — только для встреч, которые организовали вы сами")
        cancel_event_action.triggered.connect(self.on_cancel_event)
        calendar_toolbar.addAction(cancel_event_action)
        import_ics_action = QAction(_toolbar_icon("import"), "Импортировать .ics…", self)
        import_ics_action.setToolTip("Импортировать .ics — загрузить выгрузку календаря (VK Mail, Google, Outlook)")
        import_ics_action.triggered.connect(self.on_import_ics)
        calendar_toolbar.addAction(import_ics_action)
        calendar_refresh_action = QAction(_toolbar_icon("refresh"), "Обновить", self)
        calendar_refresh_action.triggered.connect(self.refresh_calendar_view)
        calendar_toolbar.addAction(calendar_refresh_action)
        caldav_sync_action = QAction(_toolbar_icon("sync"), "Синхронизировать с CalDAV", self)
        caldav_sync_action.setToolTip(
            "Синхронизировать с CalDAV — адрес сервера в Параметрах, логин/пароль от почты"
        )
        caldav_sync_action.triggered.connect(self.on_caldav_sync)
        calendar_toolbar.addAction(caldav_sync_action)
        calendar_toolbar.addWidget(self.calendar_view_combo)

        # Левая панель: мини-календарь для быстрого перехода к неделе +
        # список "календарей" — пока фактически один локальный, но чекбокс
        # реально скрывает/показывает события, а не просто для вида.
        self.calendar_mini_picker = QCalendarWidget(self)
        self.calendar_mini_picker.setGridVisible(False)
        self.calendar_mini_picker.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.calendar_mini_picker.clicked.connect(self.on_calendar_mini_picker_clicked)

        self.calendar_show_checkbox = QCheckBox("Мои встречи", self)
        self.calendar_show_checkbox.setChecked(True)
        self.calendar_show_checkbox.toggled.connect(self.on_calendar_visibility_toggled)
        calendars_group = QGroupBox("Мои календари", self)
        calendars_group_layout = QVBoxLayout(calendars_group)
        calendars_group_layout.addWidget(self.calendar_show_checkbox)

        calendar_sidebar = QWidget(self)
        calendar_sidebar.setFixedWidth(240)
        sidebar_layout = QVBoxLayout(calendar_sidebar)
        sidebar_layout.addWidget(self.calendar_mini_picker)
        sidebar_layout.addWidget(calendars_group)
        sidebar_layout.addStretch(1)

        self.calendar_selected_day: date | None = None
        self._mini_picker_target_day: date | None = None
        self.calendar_week_header = WeekHeaderWidget(self)
        self.calendar_week_header.dayClicked.connect(self.on_calendar_day_clicked)
        self.calendar_all_day_row = AllDayRowWidget(self)
        self.calendar_all_day_row.eventClicked.connect(self._on_calendar_event_clicked)
        self.calendar_all_day_row.eventDoubleClicked.connect(self._on_calendar_event_double_clicked)
        self.calendar_all_day_row.eventContextMenuRequested.connect(self.on_calendar_event_context_menu)
        self.calendar_week_grid = WeekGridWidget(self)
        self.calendar_week_grid.eventClicked.connect(self._on_calendar_event_clicked)
        self.calendar_week_grid.eventDoubleClicked.connect(self._on_calendar_event_double_clicked)
        self.calendar_week_grid.eventDragRescheduled.connect(self.on_calendar_event_drag_rescheduled)
        self.calendar_week_grid.eventContextMenuRequested.connect(self.on_calendar_event_context_menu)
        self.calendar_week_grid.emptySlotClicked.connect(self.on_calendar_empty_slot_clicked)
        self.calendar_week_grid.emptySlotContextMenuRequested.connect(self.on_calendar_empty_slot_context_menu)

        calendar_scroll = QScrollArea(self)
        calendar_scroll.setWidget(self.calendar_week_grid)
        calendar_scroll.setWidgetResizable(True)
        calendar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._calendar_scroll = calendar_scroll

        week_view = QWidget(self)
        week_view_layout = QVBoxLayout(week_view)
        week_view_layout.setContentsMargins(0, 0, 0, 0)
        week_view_layout.setSpacing(0)
        week_view_layout.addWidget(self.calendar_week_header)
        week_view_layout.addWidget(self.calendar_all_day_row)
        week_view_layout.addWidget(calendar_scroll)

        self.calendar_month_grid = MonthGridWidget(self)
        self.calendar_month_grid.eventClicked.connect(self._on_calendar_event_clicked)
        self.calendar_month_grid.eventDoubleClicked.connect(self._on_calendar_event_double_clicked)
        self.calendar_month_grid.eventContextMenuRequested.connect(self.on_calendar_event_context_menu)
        self.calendar_month_grid.dayClicked.connect(self.on_calendar_month_day_clicked)
        self.calendar_month_grid.dayDoubleClicked.connect(self.on_calendar_month_day_double_clicked)

        self.calendar_view_stack = QStackedWidget(self)
        self.calendar_view_stack.addWidget(week_view)
        self.calendar_view_stack.addWidget(self.calendar_month_grid)

        calendar_main = QWidget(self)
        calendar_main_layout = QVBoxLayout(calendar_main)
        calendar_main_layout.setContentsMargins(0, 0, 0, 0)
        calendar_main_layout.setSpacing(0)
        calendar_main_layout.addWidget(calendar_toolbar)
        calendar_main_layout.addWidget(self.calendar_view_stack)

        calendar_page = QWidget(self)
        calendar_layout = QHBoxLayout(calendar_page)
        calendar_layout.setContentsMargins(0, 0, 0, 0)
        calendar_layout.setSpacing(0)
        calendar_layout.addWidget(calendar_sidebar)
        calendar_layout.addWidget(calendar_main, 1)

        # --- Контакты ---
        self.contacts_table = QTableWidget(0, 4, self)
        self.contacts_table.setHorizontalHeaderLabels(["Имя", "Email", "Телефон", "Организация"])
        self.contacts_table.verticalHeader().setVisible(False)
        self.contacts_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.contacts_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # Раньше растягивался только первый столбец (Stretch стоял только
        # на нём) — при изменении размера окна таблица "перекашивалась",
        # остальные столбцы оставались фиксированной ширины (жалоба: "в
        # контактах таблица расширяется не пропорционально, а не каждый
        # столбец"). Растягиваем все столбцы поровну.
        contacts_header = self.contacts_table.horizontalHeader()
        for col in range(4):
            contacts_header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.contacts_table.itemSelectionChanged.connect(self.on_contact_selection_changed)
        self.contacts_table.itemDoubleClicked.connect(self.on_contact_double_clicked)

        contacts_toolbar = QToolBar("Контакты", self)
        contacts_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        new_contact_action = QAction(_toolbar_icon("add"), "Новый контакт…", self)
        new_contact_action.triggered.connect(self.on_new_contact)
        contacts_toolbar.addAction(new_contact_action)
        import_contacts_action = QAction(_toolbar_icon("import"), "Импортировать…", self)
        import_contacts_action.setToolTip("Импортировать — vCard (.vcf) или CSV (экспорт из Outlook)")
        import_contacts_action.triggered.connect(self.on_import_contacts)
        contacts_toolbar.addAction(import_contacts_action)
        delete_contact_action = QAction(_toolbar_icon("delete"), "Удалить", self)
        delete_contact_action.triggered.connect(self.on_delete_contact)
        contacts_toolbar.addAction(delete_contact_action)
        delete_all_contacts_action = QAction(_toolbar_icon("delete"), "Удалить все…", self)
        delete_all_contacts_action.triggered.connect(self.on_delete_all_contacts)
        contacts_toolbar.addAction(delete_all_contacts_action)
        contacts_refresh_action = QAction(_toolbar_icon("refresh"), "Обновить", self)
        contacts_refresh_action.triggered.connect(self.refresh_contacts_view)
        contacts_toolbar.addAction(contacts_refresh_action)

        contacts_page = QWidget(self)
        contacts_layout = QVBoxLayout(contacts_page)
        contacts_layout.setContentsMargins(0, 0, 0, 0)
        contacts_layout.setSpacing(0)
        contacts_layout.addWidget(contacts_toolbar)
        contacts_layout.addWidget(self.contacts_table)

        self.pages = QStackedWidget(self)
        self.pages.addWidget(main_splitter)  # 0: почта
        self.pages.addWidget(calendar_page)  # 1: календарь
        self.pages.addWidget(contacts_page)  # 2: контакты
        self.setCentralWidget(self.pages)

        toolbar = QToolBar("Основная", self)
        self.addToolBar(toolbar)

        mode_group = QActionGroup(self)
        mode_group.setExclusive(True)
        self.mail_mode_action = QAction("Почта", self)
        self.mail_mode_action.setCheckable(True)
        self.mail_mode_action.setChecked(True)
        self.mail_mode_action.triggered.connect(self._show_mail_page)
        self.calendar_mode_action = QAction("Календарь", self)
        self.calendar_mode_action.setCheckable(True)
        self.calendar_mode_action.triggered.connect(self._show_calendar_page)
        self.contacts_mode_action = QAction("Контакты", self)
        self.contacts_mode_action.setCheckable(True)
        self.contacts_mode_action.triggered.connect(self._show_contacts_page)
        mode_group.addAction(self.mail_mode_action)
        mode_group.addAction(self.calendar_mode_action)
        mode_group.addAction(self.contacts_mode_action)
        toolbar.addAction(self.mail_mode_action)
        toolbar.addAction(self.calendar_mode_action)
        toolbar.addAction(self.contacts_mode_action)

        toolbar.addSeparator()

        # Параметры и Справка — раньше жили отдельно (Параметры во втором
        # ряду тулбара, Справка отдельным пунктом нативного menuBar) — по
        # просьбе пользователя перенесены в один ряд с Почта/Календарь/
        # Контакты. Второй ряд тулбара (addToolBarBreak) стал не нужен: те
        # действия, что в нём были (архив/импорт), переехали к кнопкам над
        # списком писем (см. mail_actions_toolbar выше) — единственные
        # оставшиеся здесь пункты снова помещаются в один ряд.
        settings_action = QAction("Параметры…", self)
        settings_action.setToolTip(
            "Параметры — правит ТЕКУЩУЮ учётную запись (ту, чья папка сейчас выбрана); "
            "там же добавление учётных записей и правила почты"
        )
        settings_action.triggered.connect(self.on_settings)
        toolbar.addAction(settings_action)

        help_button = QToolButton(self)
        help_button.setText("Справка")
        help_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        help_menu = QMenu(help_button)
        about_action = QAction("О программе…", self)
        about_action.triggered.connect(self.on_about)
        help_menu.addAction(about_action)
        help_button.setMenu(help_menu)
        toolbar.addWidget(help_button)

        # Почта/Календарь/Контакты/Параметры/Справка — единственные подписи,
        # оставленные текстом по явной просьбе пользователя; весь остальной
        # тулбар переведён на иконки.
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        for mode_action in (self.mail_mode_action, self.calendar_mode_action, self.contacts_mode_action):
            button = toolbar.widgetForAction(mode_action)
            if button is not None:
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        settings_button = toolbar.widgetForAction(settings_action)
        if settings_button is not None:
            settings_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        help_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

        # Открыть архив/Импортировать/Архивировать папку — были во втором
        # ряду основного тулбара, перенесены к кнопкам над списком писем
        # (по просьбе пользователя — "разместить аналогично кнопкам над
        # списком писем"), в конец ряда через разделитель.
        mail_actions_toolbar.addSeparator()

        open_archive_action = QAction(_toolbar_icon("open_archive"), "Открыть архив…", self)
        open_archive_action.triggered.connect(self.on_open_archive)
        mail_actions_toolbar.addAction(open_archive_action)

        import_action = QAction(_toolbar_icon("import"), "Импортировать…", self)
        import_action.setToolTip("Импортировать — mbox/Maildir (Evolution) или .pst (Outlook) в архив")
        import_action.triggered.connect(self.on_import)
        mail_actions_toolbar.addAction(import_action)

        self.archive_folder_action = QAction(_toolbar_icon("archive_folder"), "Архивировать папку…", self)
        self.archive_folder_action.setToolTip("Архивировать папку — выгрузить в архив всю папку целиком или всё старше выбранной даты")
        self.archive_folder_action.triggered.connect(self.on_archive_folder)
        mail_actions_toolbar.addAction(self.archive_folder_action)

        # Обновить — была в основном тулбаре наверху, перенесена вниз к
        # написать/ответить/переслать и поставлена первой (по просьбе
        # пользователя). insertAction ставит её перед "Написать письмо…",
        # а не в конец, куда её добавил бы addAction.
        refresh_action = QAction(_toolbar_icon("refresh"), "Обновить", self)
        refresh_action.triggered.connect(self.on_refresh)
        mail_actions_toolbar.insertAction(compose_action, refresh_action)

        self.setStatusBar(QStatusBar(self))

        initial_font_scale = load_font_scale()
        self.font_scale_label = QLabel(f"{round(initial_font_scale * 100)}%", self)
        self.statusBar().addPermanentWidget(self.font_scale_label)
        self.font_scale_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.font_scale_slider.setRange(50, 200)
        self.font_scale_slider.setFixedWidth(120)
        self.font_scale_slider.setToolTip("Масштаб шрифта")
        self.font_scale_slider.setValue(round(initial_font_scale * 100))
        self.font_scale_slider.valueChanged.connect(self.on_font_scale_preview)
        self.font_scale_slider.sliderReleased.connect(self.on_font_scale_committed)
        self.statusBar().addPermanentWidget(self.font_scale_slider)
        self._apply_font_scale(initial_font_scale)

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._on_periodic_refresh)
        self._restart_poll_timer()

        QTimer.singleShot(0, self._restore_saved_account)
        QTimer.singleShot(0, self._restore_saved_archives)
        self._restore_window_state()

    def _restore_window_state(self) -> None:
        try:
            geometry = load_window_geometry()
            if geometry:
                self.restoreGeometry(QByteArray(geometry))
            columns_state = load_mail_columns_state()
            if columns_state:
                self.table.horizontalHeader().restoreState(QByteArray(columns_state))
        except Exception:
            pass  # сохранённое расположение не подошло (например, число колонок изменилось) — не критично

    def _restart_poll_timer(self) -> None:
        self.poll_timer.start(self.poll_interval_minutes * 60_000)

    def _restore_saved_archives(self) -> None:
        try:
            saved_paths = load_open_archives()
        except Exception:
            saved_paths = []
        missing: list[str] = []
        for path_str in saved_paths:
            path = Path(path_str)
            if not path.exists():
                missing.append(path_str)
                continue
            self._attach_archive(path, persist=False)
        if missing:
            self.statusBar().showMessage(
                f"Не найдены на диске и пропущены: {len(missing)} архив(ов)", 8000
            )
        # Если какие-то из сохранённых архивов пропали — не переписывать их
        # молча из списка навсегда (файл может быть на временно отключённом
        # диске); список на диске поправится сам при следующем "Открыть
        # архив…"/создании, а не будет тихо усечён прямо на старте.

    def _restore_saved_account(self) -> None:
        try:
            saved_accounts = load_accounts()
        except Exception as exc:
            # Отличаем от штатного "пароля в хранилище нет" (load_accounts
            # сам пропускает такие записи без исключения) — сюда попадает
            # поломка самого хранилища секретов (например, при запуске без
            # сессионной шины D-Bus keyring выдаёт NoKeyringError). Раньше
            # это тихо проглатывалось, и пользователю казалось, что все
            # настройки исчезли без причины.
            QMessageBox.warning(
                self,
                "Не удалось получить сохранённые данные входа",
                f"Хранилище паролей недоступно: {exc}\n\nПодключитесь заново вручную.",
            )
            return
        restored = []
        for account, smtp_account in saved_accounts:
            try:
                session = ImapSession(account)
                folders = session.list_folders()
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Не удалось войти с сохранёнными данными",
                    f"{account.username}: {exc}\n\nПодключитесь заново вручную.",
                )
                continue
            self._add_or_replace_account(account, smtp_account, session, folders)
            restored.append(account.username)

        try:
            saved_ews_accounts = load_ews_accounts()
        except Exception:
            saved_ews_accounts = []  # то же хранилище секретов, что и выше — если оно уже пожаловалось, не дублируем
        for ews_account in saved_ews_accounts:
            try:
                session = EwsSession(ews_account)
                folders = session.list_folders()
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Не удалось войти в Exchange с сохранёнными данными",
                    f"{ews_account.email}: {exc}\n\nПодключитесь заново вручную.",
                )
                continue
            self._add_or_replace_ews_account(ews_account, session, folders)
            restored.append(ews_account.email)

        if restored:
            self.statusBar().showMessage(f"Восстановлено подключений: {', '.join(restored)}", 5000)

    def _add_or_replace_account(
        self,
        account: Account,
        smtp_account: SmtpAccount | None,
        session: ImapSession,
        folders: list[FolderInfo],
    ) -> None:
        self._add_or_replace_account_generic(account, smtp_account, session, folders, protocol="imap")

    def _add_or_replace_ews_account(
        self, account: EwsAccount, session: EwsSession, folders: list[FolderInfo]
    ) -> None:
        self._add_or_replace_account_generic(account, _EWS_SEND_MARKER, session, folders, protocol="ews")

    def _add_or_replace_account_generic(
        self,
        account: Account | EwsAccount,
        smtp_account,
        session,
        folders: list[FolderInfo],
        *,
        protocol: str,
    ) -> None:
        """Подключает учётную запись, НЕ закрывая уже открытые (в отличие
        от старого однозаписевого _apply_connection) — та же логика "новый
        top-level узел в дереве", что уже применяется к архивам. Если
        запись с этим ключом уже была открыта (например, правка своих же
        настроек через "Параметры…"), она заменяется, а не дублируется.
        Ключ — username для IMAP, email для EWS (там username не всегда
        заполнен: при входе по Kerberos/SSO логин не нужен вовсе)."""
        key = account.email if protocol == "ews" else account.username
        old_mailbox = self.mailboxes.get(key)
        if old_mailbox is not None:
            old_mailbox.close()

        self.mailboxes[key] = CachedMailbox(session, account)
        self.mailbox_accounts[key] = account
        self.mailbox_smtp_accounts[key] = smtp_account
        self.mailbox_protocols[key] = protocol
        self.mailbox_trash_folders[key] = session.trash_folder()
        self.mailbox_sent_folders[key] = session.sent_folder()
        self.mailbox_drafts_folders[key] = session.drafts_folder()

        default_item = self._populate_account_folder_tree(key, folders)

        # Сделать только что добавленную/переподключённую запись "текущей"
        # для всего кода, который читает self.account/self.mailbox
        # напрямую (композер, ответ/пересылка, календарь, CalDAV,
        # "Параметры…").
        self.account = account
        self.account_protocol = protocol
        self.mailbox = self.mailboxes[key]
        self.smtp_account = smtp_account
        self.account_root = self.mailbox_tree_roots[key]
        self.trash_folder_name = self.mailbox_trash_folders[key]
        self.sent_folder_name = self.mailbox_sent_folders[key]
        self.drafts_folder_name = self.mailbox_drafts_folders[key]

        if default_item is not None:
            self.folder_tree.setCurrentItem(default_item)

    def _populate_account_folder_tree(self, key: str, folders: list[FolderInfo]) -> QTreeWidgetItem | None:
        old_root = self.mailbox_tree_roots.get(key)
        if old_root is not None:
            index = self.folder_tree.indexOfTopLevelItem(old_root)
            if index != -1:
                self.folder_tree.takeTopLevelItem(index)

        root = QTreeWidgetItem([key])
        root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.folder_tree.insertTopLevelItem(0, root)
        self.mailbox_tree_roots[key] = root

        nodes: dict[tuple[str, ...], QTreeWidgetItem] = {(): root}
        inbox_item: QTreeWidgetItem | None = None
        first_selectable: QTreeWidgetItem | None = None

        for info in folders:
            delimiter = info.delimiter or "/"
            self._folder_delimiter = delimiter
            parts = [p for p in info.name.split(delimiter) if p not in _HIDDEN_PATH_SEGMENTS] or [info.name]
            path: tuple[str, ...] = ()
            parent = root
            for part in parts:
                path = path + (part,)
                node = nodes.get(path)
                if node is None:
                    node = QTreeWidgetItem([_DISPLAY_NAMES.get(part, part)])
                    parent.addChild(node)
                    nodes[path] = node
                parent = node
            parent.setData(0, Qt.ItemDataRole.UserRole, (key, info.name))
            if first_selectable is None:
                first_selectable = parent
            if info.name == "INBOX":
                inbox_item = parent

        self.folder_tree.expandAll()
        return inbox_item or first_selectable

    def on_open_archive(self) -> None:
        dialog = QFileDialog(self, "Открыть или создать архив")
        dialog.setNameFilter("Архивы RedMail (*.rmarchive)")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        paths = dialog.selectedFiles()
        if not paths:
            return
        path = self._normalize_archive_path(paths[0])
        source = self._attach_archive(path)
        if source is not None:
            self.folder_tree.setCurrentItem(self.archive_tree_roots[str(path)])

    @staticmethod
    def _normalize_archive_path(path_str: str) -> Path:
        path = Path(path_str)
        if path.suffix != ".rmarchive":
            path = path.with_suffix(".rmarchive")
        return path

    def _prompt_new_archive_path(self) -> Path | None:
        # Раньше здесь был полный диалог "Сохранить как" — пользователь
        # каждый раз сам выбирал каталог на диске (жалоба: "при импорте pst
        # просит создать архив" — воспринималось как лишний обязательный
        # шаг). Теперь спрашиваем только имя, каталог берём из настроек
        # (self.archive_storage_dir, "Архивы почты" в "Параметры…") и
        # создаём его при необходимости.
        name, ok = QInputDialog.getText(self, "Новый архив", "Название архива:")
        name = name.strip()
        if not ok or not name:
            return None
        try:
            self.archive_storage_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "Не удалось создать каталог архивов", str(exc))
            return None
        return self._normalize_archive_path(str(self.archive_storage_dir / name))

    def _attach_archive(self, path: Path, *, persist: bool = True) -> ArchiveSource | None:
        key = str(path)
        if key in self.archives:
            return self.archives[key]
        if path.exists() and not archive_store.is_archive_file(path):
            QMessageBox.critical(self, "Не архив RedMail", f"Файл «{path}» — не архив RedMail.")
            return None
        try:
            archive_store.create_archive(path)
        except OSError as exc:
            QMessageBox.critical(self, "Не удалось открыть архив", str(exc))
            return None
        source = ArchiveSource(path)
        self.archives[key] = source
        self._add_archive_to_tree(key, path)
        if persist:
            self._save_open_archives()
        return source

    def _save_open_archives(self) -> None:
        try:
            save_open_archives(list(self.archives.keys()))
        except Exception:
            pass  # список открытых архивов не запомнится между запусками — не критично

    def _add_archive_to_tree(self, key: str, path: Path) -> None:
        root = QTreeWidgetItem([path.stem])
        root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.folder_tree.addTopLevelItem(root)
        self.archive_tree_roots[key] = root
        self._refresh_archive_folders(key)
        self.folder_tree.expandItem(root)

    def _close_archive(self, key: str) -> None:
        # Раньше открытые архивы нельзя было отключить вообще — жалоба
        # пользователя: "нет возможности отключить импортированный или
        # открытый архив". Сам файл архива на диске не трогаем — просто
        # убираем его из списка открытых (аналог "закрыть файл", не "удалить").
        root = self.archive_tree_roots.pop(key, None)
        if root is not None:
            index = self.folder_tree.indexOfTopLevelItem(root)
            if index != -1:
                self.folder_tree.takeTopLevelItem(index)
        was_active = self.active_source is self.archives.get(key)
        self.archives.pop(key, None)
        if was_active:
            self.active_source = None
            self.current_folder = None
            self._clear_reading_pane()
            self._render_folder([])
        self._save_open_archives()
        self.statusBar().showMessage("Архив отключён", 3000)

    def _disconnect_account(self, key: str) -> None:
        # Раньше живую учётную запись (IMAP/EWS) нельзя было отключить
        # вообще, только закрыть всё приложение (жалоба: "нет возможности
        # отключить ящик") — по аналогии с "Закрыть архив" выше, но здесь
        # ещё нужно перевыбрать "текущую" запись, если отключаем именно её.
        mailbox = self.mailboxes.pop(key, None)
        if mailbox is not None:
            mailbox.close()
        self.mailbox_accounts.pop(key, None)
        self.mailbox_smtp_accounts.pop(key, None)
        self.mailbox_protocols.pop(key, None)
        self.mailbox_trash_folders.pop(key, None)
        self.mailbox_sent_folders.pop(key, None)
        self.mailbox_drafts_folders.pop(key, None)
        root = self.mailbox_tree_roots.pop(key, None)
        if root is not None:
            index = self.folder_tree.indexOfTopLevelItem(root)
            if index != -1:
                self.folder_tree.takeTopLevelItem(index)

        if self.active_source is mailbox:
            self.active_source = None
            self.current_folder = None
            self._clear_reading_pane()
            self._render_folder([])

        if self.mailbox is mailbox:
            remaining_key = next(iter(self.mailboxes), None)
            if remaining_key is not None:
                self.account = self.mailbox_accounts[remaining_key]
                self.mailbox = self.mailboxes[remaining_key]
                self.smtp_account = self.mailbox_smtp_accounts[remaining_key]
                self.account_protocol = self.mailbox_protocols[remaining_key]
                self.account_root = self.mailbox_tree_roots[remaining_key]
                self.trash_folder_name = self.mailbox_trash_folders[remaining_key]
                self.sent_folder_name = self.mailbox_sent_folders[remaining_key]
                self.drafts_folder_name = self.mailbox_drafts_folders[remaining_key]
            else:
                self.account = None
                self.mailbox = None
                self.smtp_account = None
                self.account_protocol = "imap"
                self.account_root = None
                self.trash_folder_name = None
                self.sent_folder_name = None
                self.drafts_folder_name = None

        self._save_all_accounts()
        self.statusBar().showMessage("Ящик отключён", 3000)

    def _empty_trash(self, mailbox: CachedMailbox, folder: str) -> None:
        confirm = QMessageBox.question(
            self,
            "Очистить корзину",
            "Удалить безвозвратно ВСЕ письма в корзине? Это действие нельзя отменить.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        def do_empty() -> int:
            uids = mailbox.session.search_uids(folder)
            if uids:
                mailbox.delete_messages(folder, uids)
            return len(uids)

        worker = _CallableWorker(do_empty, parent=self)

        def on_success(result: object) -> None:
            count = result
            if self.active_source is mailbox and self.current_folder == folder:
                self._render_folder([])
            self.statusBar().showMessage(f"Корзина очищена: удалено {count}", 5000)
            self._background_workers.remove(worker)

        def on_failure(error_text: str) -> None:
            QMessageBox.critical(self, "Не удалось очистить корзину", error_text)
            self._background_workers.remove(worker)

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._background_workers.append(worker)
        worker.start()

    def _refresh_archive_folders(self, key: str) -> None:
        root = self.archive_tree_roots.get(key)
        source = self.archives.get(key)
        if root is None or source is None:
            return
        root.takeChildren()
        # Раньше каждая папка архива становилась ПРЯМЫМ ребёнком корня с
        # ПОЛНЫМ путём в качестве подписи (например, "Top of Personal
        # Folders/Входящие/Подпапка") — у PST с общим длинным префиксом у
        # всех папок ("Top of Personal Folders/...") они все визуально
        # выглядели одинаково обрезанными в узкой колонке дерева (жалоба:
        # "загрузка из pst все ещё некорректно загружает имена папок").
        # Строим настоящее вложенное дерево тем же способом, что и для
        # живого ящика (_populate_account_folder_tree) — на каждом уровне
        # подпись это только сегмент имени, не весь путь.
        nodes: dict[tuple[str, ...], QTreeWidgetItem] = {(): root}
        for folder_name in archive_store.list_folders(source.path):
            parts = [p for p in folder_name.split("/") if p] or [folder_name]
            path: tuple[str, ...] = ()
            parent = root
            for part in parts:
                path = path + (part,)
                node = nodes.get(path)
                if node is None:
                    node = QTreeWidgetItem([part])
                    # Раньше UserRole ставился только на "листовой" узел (тот,
                    # что совпадает с конкретной строкой folder в БД) — клик
                    # правой кнопкой на промежуточном сегменте пути (например,
                    # "Работа" для писем в "Работа/Проекты") не находил
                    # никаких данных и меню вообще не открывалось (жалоба:
                    # "папки в архиве не даёт переименовать"). Ставим сразу на
                    # каждый узел — archive_store.rename_folder теперь
                    # переименовывает и вложенные "путь/..." тем же
                    # префиксом, так что промежуточный узел тоже осмысленно
                    # переименовывается.
                    node.setData(0, Qt.ItemDataRole.UserRole, (key, "/".join(path)))
                    parent.addChild(node)
                    nodes[path] = node
                parent = node
        self.folder_tree.expandItem(root)

    def _pick_archive_target(
        self, *, title: str, ask_folder: bool = False, default_folder: str = "", ask_move_copy: bool = False
    ) -> tuple[str, str, bool] | None:
        archive_names = {key: Path(source.path).stem for key, source in self.archives.items()}
        if not archive_names:
            # Пока не открыто ни одного архива, выбирать в диалоге
            # действительно не из чего — там был бы ровно один пункт
            # «Открыть или создать другой архив…» (жалоба пользователя при
            # первом импорте .pst: "нет возможности выбрать архив"). Сразу
            # переходим к созданию нового архива, без бесполезного шага.
            new_path = self._prompt_new_archive_path()
            if new_path is None:
                return None
            source = self._attach_archive(new_path)
            if source is None:
                return None
            return str(new_path), default_folder or "Импорт", False
        dialog = ArchiveTargetDialog(
            self,
            archive_names,
            title=title,
            ask_folder=ask_folder,
            default_folder=default_folder,
            ask_move_copy=ask_move_copy,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        archive_key = dialog.selected_archive_key()
        if not archive_key:
            new_path = self._prompt_new_archive_path()
            if new_path is None:
                return None
            source = self._attach_archive(new_path)
            if source is None:
                return None
            archive_key = str(new_path)
        return archive_key, dialog.folder_name(), dialog.move()

    def on_import(self) -> None:
        menu = QMenu(self)
        mbox_action = menu.addAction("mbox (Evolution/Thunderbird)…")
        maildir_action = menu.addAction("Maildir (Evolution)…")
        pst_action = menu.addAction(".pst (Outlook)…")
        chosen = menu.exec(QCursor.pos())
        if chosen is None:
            return
        if chosen is mbox_action:
            self._import_mbox_or_maildir(is_maildir=False)
        elif chosen is maildir_action:
            self._import_mbox_or_maildir(is_maildir=True)
        else:
            self._import_pst()

    def _import_mbox_or_maildir(self, *, is_maildir: bool) -> None:
        if is_maildir:
            source_path_str = QFileDialog.getExistingDirectory(self, "Выбрать каталог Maildir")
        else:
            source_path_str, _ = QFileDialog.getOpenFileName(self, "Выбрать mbox-файл")
        if not source_path_str:
            return

        result = self._pick_archive_target(
            title="Импорт Maildir" if is_maildir else "Импорт mbox", ask_folder=True, default_folder="Импорт"
        )
        if result is None:
            return
        archive_key, folder_name, _move = result
        archive_path = self.archives[archive_key].path
        importer = archive_store.import_maildir if is_maildir else archive_store.import_mbox
        self._run_archive_import(
            archive_key, importer, archive_path, Path(source_path_str), folder_name,
            progress_text="Импорт письма…",
        )

    def _import_pst(self) -> None:
        source_path_str, _ = QFileDialog.getOpenFileName(self, "Выбрать файл .pst", filter="Outlook PST (*.pst)")
        if not source_path_str:
            return

        result = self._pick_archive_target(title="Импорт .pst")
        if result is None:
            return
        archive_key, _folder_name, _move = result
        archive_path = self.archives[archive_key].path
        self._run_archive_import(
            archive_key, archive_store.import_pst, archive_path, Path(source_path_str),
            progress_text="Импорт .pst — для больших файлов может занять пару минут…",
        )

    def _run_archive_import(self, archive_key: str, importer, *args, progress_text: str) -> None:
        # Разбор .pst/.mbox/Maildir раньше выполнялся прямо здесь, в
        # обработчике клика — то есть в основном потоке интерфейса. На
        # реальном .pst (586 писем) это занимало почти две минуты, всё это
        # время окно не перерисовывалось и не отвечало на клики: выглядело
        # так, будто импорт не работает вовсе (жалоба пользователя после
        # реального теста). Отмену на середине разбора .pst не поддерживаем
        # (нет промежуточных точек для безопасной остановки), поэтому кнопки
        # отмены у индикатора нет — только факт, что окно живое.
        progress = QProgressDialog(progress_text, None, 0, 0, self)
        progress.setWindowTitle("Импорт")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setCancelButton(None)
        progress.show()

        worker = _CallableWorker(importer, *args, parent=self)

        def on_success(count: object) -> None:
            progress.close()
            self._refresh_archive_folders(archive_key)
            self.statusBar().showMessage(f"Импортировано писем: {count}", 5000)
            self._background_workers.remove(worker)

        def on_failure(message: str) -> None:
            progress.close()
            QMessageBox.critical(self, "Ошибка импорта", message)
            self._background_workers.remove(worker)

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._background_workers.append(worker)
        worker.start()

    def on_archive_selected(self) -> None:
        if self.active_source is not self.mailbox or not self.mailbox or not self.current_folder:
            QMessageBox.information(
                self, "Недоступно", "Выгрузка в архив работает только из папок живого ящика."
            )
            return
        checked_uids = self._checked_uids()
        if not checked_uids:
            QMessageBox.information(self, "Нечего выгружать", "Отметьте галочками письма для выгрузки в архив.")
            return

        result = self._pick_archive_target(
            title="Выгрузить в архив",
            ask_folder=True,
            default_folder=_DISPLAY_NAMES.get(self.current_folder, self.current_folder),
            ask_move_copy=True,
        )
        if result is None:
            return
        archive_key, folder_name, move = result
        archive_path = self.archives[archive_key].path
        source_folder = self.current_folder

        exported = 0
        try:
            for uid in checked_uids:
                raw = self.mailbox.message_raw(source_folder, uid)
                archive_store.append_raw_message(archive_path, folder_name, raw)
                exported += 1
            if move:
                self.mailbox.delete_messages(source_folder, checked_uids)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка выгрузки в архив", str(exc))
            return

        self._refresh_archive_folders(archive_key)
        if move and self.active_source is self.mailbox and self.current_folder == source_folder:
            try:
                summaries = self.mailbox.refresh_folder(source_folder)
            except Exception:
                summaries = None
            if summaries is not None:
                self._render_folder(summaries)
        verb = "Перемещено" if move else "Скопировано"
        self.statusBar().showMessage(f"{verb} в архив: {exported}", 5000)

    def on_mail_rules(self) -> None:
        folder_names = []
        if self.mailbox:
            try:
                folder_names = [info.name for info in self.mailbox.session.list_folders()]
            except Exception:
                folder_names = []
        dialog = MailRulesDialog(self, self.mail_rules, folder_names)
        dialog.exec()
        self.mail_rules = dialog.rules()
        try:
            save_mail_rules(self.mail_rules)
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось сохранить правила", str(exc))

    def on_apply_mail_rules(self) -> None:
        if self.active_source is not self.mailbox or not self.mailbox or not self.current_folder:
            QMessageBox.information(self, "Недоступно", "Применение правил работает только в папках живого ящика.")
            return
        if not self.mail_rules:
            QMessageBox.information(
                self, "Нет правил",
                "Сначала добавьте хотя бы одно правило: Параметры → «Правила сортировки почты…».",
            )
            return

        source_folder = self.current_folder
        moves: dict[str, list[int]] = {}
        for summary in self.current_summaries:
            for rule in self.mail_rules:
                haystack = summary.sender_email if rule.field == "from" else summary.subject
                if rule.contains.lower() in (haystack or "").lower():
                    moves.setdefault(rule.target_folder, []).append(summary.uid)
                    break  # первое подходящее правило — не проверяем остальные для этого письма
        if not moves:
            self.statusBar().showMessage("Правила не подошли ни к одному письму в этой папке", 5000)
            return

        moved_total = 0
        try:
            for target_folder, uids in moves.items():
                self.mailbox.move_to_folder(source_folder, uids, target_folder)
                moved_total += len(uids)
        except Exception as exc:
            QMessageBox.critical(
                self, "Ошибка применения правил", f"{exc}\n\nПеремещено до сбоя: {moved_total}."
            )

        try:
            summaries = self.mailbox.refresh_folder(source_folder)
        except Exception:
            summaries = None
        if summaries is not None:
            self._render_folder(summaries)
        self.statusBar().showMessage(f"По правилам перемещено писем: {moved_total}", 5000)

    def on_archive_folder(self) -> None:
        if self.active_source is not self.mailbox or not self.mailbox or not self.current_folder:
            QMessageBox.information(
                self, "Недоступно", "Выгрузка в архив работает только из папок живого ящика."
            )
            return

        source_folder = self.current_folder
        scope_dialog = ArchiveFolderScopeDialog(self, _DISPLAY_NAMES.get(source_folder, source_folder))
        if scope_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        before = scope_dialog.before_date()

        try:
            uids = self.mailbox.search_uids(source_folder, before=before)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка поиска писем", str(exc))
            return
        if not uids:
            QMessageBox.information(self, "Нечего выгружать", "В папке нет подходящих писем.")
            return

        result = self._pick_archive_target(
            title="Выгрузить папку в архив",
            ask_folder=True,
            default_folder=_DISPLAY_NAMES.get(source_folder, source_folder),
            ask_move_copy=True,
        )
        if result is None:
            return
        archive_key, folder_name, move = result

        scope_text = f"всё старше {before.strftime('%d.%m.%Y')}" if before else "всю папку целиком"
        confirm = QMessageBox.question(
            self,
            "Архивировать папку",
            f"{'Переместить' if move else 'Скопировать'} в архив {scope_text} ({len(uids)} писем)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        archive_path = self.archives[archive_key].path
        exported = 0
        try:
            for uid in uids:
                raw = self.mailbox.message_raw(source_folder, uid)
                archive_store.append_raw_message(archive_path, folder_name, raw)
                exported += 1
            if move:
                self.mailbox.delete_messages(source_folder, uids)
        except Exception as exc:
            QMessageBox.critical(
                self, "Ошибка выгрузки в архив", f"{exc}\n\nВыгружено до сбоя: {exported} из {len(uids)}."
            )
            return

        self._refresh_archive_folders(archive_key)
        if move and self.active_source is self.mailbox and self.current_folder == source_folder:
            try:
                summaries = self.mailbox.refresh_folder(source_folder)
            except Exception:
                summaries = None
            if summaries is not None:
                self._render_folder(summaries)
        verb = "Перемещено" if move else "Скопировано"
        self.statusBar().showMessage(f"{verb} в архив: {exported}", 5000)

    def on_about(self) -> None:
        try:
            from importlib.metadata import PackageNotFoundError, version

            app_version = version("redmail")
        except PackageNotFoundError:
            app_version = "?"
        QMessageBox.about(
            self,
            "О программе",
            "<h3>RedMail</h3>"
            "<p>Почтовый клиент для RED OS — функциональный аналог Microsoft "
            "Outlook: почта по IMAP/SMTP, локальные архивы писем, календарь "
            "с приглашениями по электронной почте (iTIP), адресная книга.</p>"
            f"<p>Версия: {app_version}</p>"
            "<p>Автор: Пономарев Роман Сергеевич</p>"
            "<p><i>Программа разработана с использованием искусственного "
            "интеллекта.</i></p>",
        )

    def on_settings(self) -> None:
        dialog = SettingsDialog(
            self,
            account=self.account,
            smtp=self.smtp_account,
            poll_interval_minutes=self.poll_interval_minutes,
            pane_orientation=self.pane_orientation,
            caldav_url=self.caldav_url,
            archive_storage_dir=self.archive_storage_dir,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.poll_interval_minutes = dialog.poll_interval_minutes()
        self.pane_orientation = dialog.pane_orientation()
        self.caldav_url = dialog.caldav_url()
        self.archive_storage_dir = dialog.archive_storage_dir()
        try:
            save_poll_interval_minutes(self.poll_interval_minutes)
            save_pane_orientation(self.pane_orientation)
            save_caldav_url(self.caldav_url)
            save_archive_storage_dir(self.archive_storage_dir)
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось сохранить параметры", str(exc))
        self._restart_poll_timer()
        self._apply_pane_orientation()

        new_account = dialog.account()
        if not new_account.host or not new_account.username:
            return
        new_smtp = dialog.smtp_account()
        new_smtp = new_smtp if new_smtp.host else None
        if new_account == self.account and new_smtp == self.smtp_account:
            return  # данные подключения не менялись — незачем переподключаться

        try:
            session = ImapSession(new_account)
            folders = session.list_folders()
        except Exception as exc:  # показываем пользователю любую ошибку подключения как есть
            QMessageBox.critical(self, "Ошибка подключения", str(exc))
            return
        self._add_or_replace_account(new_account, new_smtp, session, folders)
        self._save_all_accounts()

    def on_add_account(self) -> None:
        """Добавить ЕЩЁ одну учётную запись, не закрывая уже открытые
        (жалоба: "несколько учётных записей одновременно — сейчас клиент
        держит только одно подключение") — в отличие от "Параметры…",
        который правит текущую."""
        dialog = SettingsDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_account = dialog.account()
        if not new_account.host or not new_account.username:
            return
        new_smtp = dialog.smtp_account()
        new_smtp = new_smtp if new_smtp.host else None
        if new_account.username in self.mailboxes:
            QMessageBox.information(self, "Уже подключено", f"Учётная запись {new_account.username} уже открыта.")
            return

        try:
            session = ImapSession(new_account)
            folders = session.list_folders()
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка подключения", str(exc))
            return
        self._add_or_replace_account(new_account, new_smtp, session, folders)
        self._save_all_accounts()
        self.statusBar().showMessage(f"Добавлена учётная запись: {new_account.username}", 5000)

    def on_add_ews_account(self) -> None:
        """Подключение к Exchange напрямую по EWS — отдельный путь от
        обычного IMAP (кнопка выше): нужен, когда IMAP отключён политикой
        безопасности организации, или когда вход должен идти по Kerberos
        (SSO) без ввода пароля в самом приложении."""
        dialog = EwsAccountDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_account = dialog.account()
        if not new_account.email:
            return
        if new_account.email in self.mailboxes:
            QMessageBox.information(self, "Уже подключено", f"Учётная запись {new_account.email} уже открыта.")
            return
        try:
            session = EwsSession(new_account)
            folders = session.list_folders()
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось подключиться к Exchange", str(exc))
            return
        self._add_or_replace_ews_account(new_account, session, folders)
        self._save_all_accounts()
        self.statusBar().showMessage(f"Exchange подключён: {new_account.email}", 5000)

    def _save_all_accounts(self) -> None:
        try:
            save_accounts(
                [
                    (self.mailbox_accounts[key], self.mailbox_smtp_accounts[key])
                    for key in self.mailboxes
                    if self.mailbox_protocols[key] == "imap"
                ]
            )
            save_ews_accounts(
                [self.mailbox_accounts[key] for key in self.mailboxes if self.mailbox_protocols[key] == "ews"]
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Не удалось сохранить настройки",
                f"Подключение работает, но запомнить его для следующего запуска не вышло: {exc}",
            )

    def _apply_pane_orientation(self) -> None:
        orientation = (
            Qt.Orientation.Horizontal if self.pane_orientation == "horizontal" else Qt.Orientation.Vertical
        )
        self.right_splitter.setOrientation(orientation)

    def on_font_scale_preview(self, value: int) -> None:
        self.font_scale_label.setText(f"{value}%")
        self._apply_font_scale(value / 100)

    def on_font_scale_committed(self) -> None:
        try:
            save_font_scale(self.font_scale_slider.value() / 100)
        except Exception:
            pass  # масштаб не запомнится между запусками — не критично

    def _apply_font_scale(self, scale: float) -> None:
        app = QApplication.instance()
        font = app.font()
        font.setPointSizeF(self._base_font_point_size * scale)
        app.setFont(font)

    def _set_filter_column(self, column: int) -> None:
        if column == COL_FLAG:
            self._open_marker_filter_menu()
            return
        if column not in _FILTER_COLUMNS:
            return
        self.filter_column = column
        self.filter_edit.setPlaceholderText(f"Фильтр: {_FILTER_COLUMNS[column]}")
        self.on_filter_changed(self.filter_edit.text())

    def _open_marker_filter_menu(self) -> None:
        menu = QMenu(self)
        all_action = menu.addAction("Все письма")
        any_action = menu.addAction("С любым маркером")
        menu.addSeparator()
        color_actions: dict[QAction, str] = {}
        for color, label in _MARKER_LABELS.items():
            action = menu.addAction(_marker_icon(color), label)
            color_actions[action] = color

        chosen = menu.exec(QCursor.pos())
        if chosen is None:
            return
        if chosen is all_action:
            self.marker_filter = None
        elif chosen is any_action:
            self.marker_filter = _ANY_MARKER_FILTER
        else:
            self.marker_filter = color_actions[chosen]
        self._update_marker_filter_indicator()
        self.on_filter_changed(self.filter_edit.text())

    def _update_marker_filter_indicator(self) -> None:
        header_item = self.table.horizontalHeaderItem(COL_FLAG)
        if self.marker_filter is None:
            header_item.setIcon(QIcon())
            header_item.setToolTip("Клик — фильтр по маркеру")
        elif self.marker_filter == _ANY_MARKER_FILTER:
            header_item.setIcon(QIcon())
            header_item.setToolTip("Фильтр: письма с любым маркером (клик — изменить)")
        else:
            header_item.setIcon(_marker_icon(self.marker_filter))
            header_item.setToolTip(f"Фильтр: {_MARKER_LABELS[self.marker_filter]} (клик — изменить)")

    def on_current_cell_changed(
        self, current_row: int, current_column: int, _previous_row: int, _previous_column: int
    ) -> None:
        # Только переключение активной колонки текстового фильтра — не
        # трогаем колонку маркера здесь. Раньше это шло через тот же
        # _set_filter_column(), что и клик по ЗАГОЛОВКУ, а для COL_FLAG это
        # открывает всплывающее меню фильтра по цвету; из-за этого клик по
        # ячейке маркера в строке письма (чтобы поставить маркер САМОМУ
        # письму, см. on_table_item_clicked/_open_marker_menu) сначала
        # открывал не то меню, и приходилось кликать дважды.
        if current_column not in _FILTER_COLUMNS:
            return
        self.filter_column = current_column
        self.filter_edit.setPlaceholderText(f"Фильтр: {_FILTER_COLUMNS[current_column]}")
        self.on_filter_changed(self.filter_edit.text())

    def on_folder_item_changed(self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None) -> None:
        if current is None:
            return
        data = current.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return  # промежуточный узел иерархии (учётная запись/архив), не настоящая папка
        source_key, folder_name = data
        source = self.mailboxes.get(source_key) or self.archives.get(source_key)
        if source is None:
            return
        self.active_source = source
        self.current_folder = folder_name
        if source_key in self.mailboxes:
            # Переключение на папку ДРУГОЙ учётной записи — обновляем
            # "текущие" алиасы (self.account/self.mailbox/...), которые
            # читает весь остальной код (композер, календарь, CalDAV,
            # "Параметры…", удаление/корзина).
            self.account = self.mailbox_accounts[source_key]
            self.mailbox = self.mailboxes[source_key]
            self.smtp_account = self.mailbox_smtp_accounts[source_key]
            self.account_protocol = self.mailbox_protocols[source_key]
            self.account_root = self.mailbox_tree_roots[source_key]
            self.trash_folder_name = self.mailbox_trash_folders[source_key]
            self.sent_folder_name = self.mailbox_sent_folders[source_key]
            self.drafts_folder_name = self.mailbox_drafts_folders[source_key]
        self._clear_reading_pane()
        self._thread_content_cache.clear()
        self._thread_render_token += 1
        try:
            summaries = source.folder_summaries(folder_name)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка загрузки папки", str(exc))
            return
        self._render_folder(summaries)

    def on_folder_tree_context_menu(self, pos) -> None:
        item = self.folder_tree.itemAt(pos)
        if item is None:
            return
        archive_key = next((key for key, root in self.archive_tree_roots.items() if root is item), None)
        if archive_key is not None:
            menu = QMenu(self)
            close_action = menu.addAction("Закрыть архив")
            chosen = menu.exec(self.folder_tree.mapToGlobal(pos))
            if chosen is close_action:
                self._close_archive(archive_key)
            return

        data = item.data(0, Qt.ItemDataRole.UserRole)
        if bool(data) and data[0] in self.archives:
            self._rename_archive_folder(data[0], data[1], pos)
            return

        # Только для живого ящика — у архивов своя (плоская) структура папок
        # без создания через сервер, и IMAP-иерархия им не подходит. Узел
        # может принадлежать ЛЮБОЙ из открытых учётных записей, не только
        # "текущей" — определяем, какой именно, по самому узлу.
        account_key = next((key for key, root in self.mailbox_tree_roots.items() if root is item), None)
        is_root = account_key is not None
        if not is_root and bool(data) and data[0] in self.mailboxes:
            account_key = data[0]
        is_live_folder = bool(data) and data[0] in self.mailboxes
        if not is_root and not is_live_folder:
            return
        mailbox = self.mailboxes[account_key]
        is_trash = not is_root and data[1] == self.mailbox_trash_folders.get(account_key)

        menu = QMenu(self)
        create_action = menu.addAction("Создать папку…" if is_root else "Создать вложенную папку…")
        rename_action = None if is_root else menu.addAction("Переименовать папку…")
        empty_trash_action = menu.addAction("Очистить корзину…") if is_trash else None
        disconnect_action = menu.addAction("Отключить ящик") if is_root else None
        chosen = menu.exec(self.folder_tree.mapToGlobal(pos))

        if rename_action is not None and chosen is rename_action:
            self._rename_live_folder(account_key, mailbox, data[1])
            return
        if empty_trash_action is not None and chosen is empty_trash_action:
            self._empty_trash(mailbox, data[1])
            return
        if disconnect_action is not None and chosen is disconnect_action:
            self._disconnect_account(account_key)
            return
        if chosen is not create_action:
            return

        parent_path = "" if is_root else data[1]
        name, ok = QInputDialog.getText(self, "Новая папка", "Название папки:")
        name = name.strip()
        if not ok or not name:
            return
        full_name = f"{parent_path}{self._folder_delimiter}{name}" if parent_path else name
        try:
            mailbox.session.create_folder(full_name)
            folders = mailbox.session.list_folders()
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось создать папку", str(exc))
            return
        self.mailbox_trash_folders[account_key] = mailbox.session.trash_folder()
        if account_key == next((k for k, m in self.mailboxes.items() if m is self.mailbox), None):
            self.trash_folder_name = self.mailbox_trash_folders[account_key]
        self._populate_account_folder_tree(account_key, folders)
        self.statusBar().showMessage(f"Папка создана: {full_name}", 5000)

    def _rename_archive_folder(self, archive_key: str, folder_name: str, pos) -> None:
        menu = QMenu(self)
        rename_action = menu.addAction("Переименовать папку…")
        chosen = menu.exec(self.folder_tree.mapToGlobal(pos))
        if chosen is not rename_action:
            return
        new_name, ok = QInputDialog.getText(self, "Переименовать папку", "Новое название:", text=folder_name)
        new_name = new_name.strip()
        if not ok or not new_name or new_name == folder_name:
            return
        archive = self.archives[archive_key]
        try:
            archive_store.rename_folder(archive.path, folder_name, new_name)
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось переименовать папку", str(exc))
            return
        self._refresh_archive_folders(archive_key)
        if self.active_source is archive and self.current_folder == folder_name:
            self.current_folder = new_name
        self.statusBar().showMessage(f"Папка переименована: {new_name}", 5000)

    def _rename_live_folder(self, account_key: str, mailbox: CachedMailbox, old_full_name: str) -> None:
        short_name = old_full_name.rsplit(self._folder_delimiter, 1)[-1]
        new_short, ok = QInputDialog.getText(self, "Переименовать папку", "Новое название:", text=short_name)
        new_short = new_short.strip()
        if not ok or not new_short or new_short == short_name:
            return
        parent_path = (
            old_full_name.rsplit(self._folder_delimiter, 1)[0] if self._folder_delimiter in old_full_name else ""
        )
        new_full_name = f"{parent_path}{self._folder_delimiter}{new_short}" if parent_path else new_short
        try:
            mailbox.session.rename_folder(old_full_name, new_full_name)
            folders = mailbox.session.list_folders()
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось переименовать папку", str(exc))
            return
        self.mailbox_trash_folders[account_key] = mailbox.session.trash_folder()
        if account_key == next((k for k, m in self.mailboxes.items() if m is self.mailbox), None):
            self.trash_folder_name = self.mailbox_trash_folders[account_key]
        self._populate_account_folder_tree(account_key, folders)
        if self.active_source is mailbox and self.current_folder == old_full_name:
            self.current_folder = new_full_name
        self.statusBar().showMessage(f"Папка переименована: {new_full_name}", 5000)

    def on_folder_dropped(self, source_item: QTreeWidgetItem, target_item: QTreeWidgetItem | None) -> None:
        """Перетаскивание папки мышью в дереве (по просьбе пользователя) —
        и для архивов, и для живых ящиков; в обоих случаях "переместить" —
        это то же самое переименование в новый полный путь, что уже есть у
        кнопки "Переименовать папку…", просто путь вычисляется из точки
        сброса, а не спрашивается текстом."""
        if target_item is None:
            return  # брошено в пустое место дерева — неоднозначно, к какому корню отнести

        is_source_root = any(root is source_item for root in (*self.archive_tree_roots.values(), *self.mailbox_tree_roots.values()))
        if is_source_root:
            return  # тащили целый архив/учётную запись, а не папку внутри неё

        source_data = source_item.data(0, Qt.ItemDataRole.UserRole)
        if not source_data:
            return
        key, source_path = source_data

        target_archive_key = next((k for k, root in self.archive_tree_roots.items() if root is target_item), None)
        target_account_key = next((k for k, root in self.mailbox_tree_roots.items() if root is target_item), None)
        if target_archive_key is not None:
            target_key, target_path = target_archive_key, ""
        elif target_account_key is not None:
            target_key, target_path = target_account_key, ""
        else:
            target_data = target_item.data(0, Qt.ItemDataRole.UserRole)
            if not target_data:
                return
            target_key, target_path = target_data

        if target_key != key:
            QMessageBox.information(
                self, "Нельзя переместить", "Нельзя перетащить папку в другую учётную запись или другой архив."
            )
            return

        is_archive = key in self.archives
        delimiter = "/" if is_archive else self._folder_delimiter
        short_name = source_path.rsplit(delimiter, 1)[-1]
        new_path = f"{target_path}{delimiter}{short_name}" if target_path else short_name

        if new_path == source_path:
            return  # бросили туда же, откуда взяли
        if target_path == source_path or target_path.startswith(source_path + delimiter):
            QMessageBox.warning(self, "Нельзя переместить", "Нельзя перетащить папку саму в себя или в свою же подпапку.")
            return

        confirm = QMessageBox.question(
            self, "Переместить папку",
            f"Переместить «{source_path}» в «{target_path or '(верхний уровень)'}»?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        if is_archive:
            archive = self.archives[key]
            try:
                archive_store.rename_folder(archive.path, source_path, new_path)
            except Exception as exc:
                QMessageBox.critical(self, "Не удалось переместить папку", str(exc))
                return
            self._refresh_archive_folders(key)
            if self.active_source is archive and self.current_folder == source_path:
                self.current_folder = new_path
        else:
            mailbox = self.mailboxes[key]
            try:
                mailbox.session.rename_folder(source_path, new_path)
                folders = mailbox.session.list_folders()
            except Exception as exc:
                QMessageBox.critical(self, "Не удалось переместить папку", str(exc))
                return
            self.mailbox_trash_folders[key] = mailbox.session.trash_folder()
            if key == next((k for k, m in self.mailboxes.items() if m is self.mailbox), None):
                self.trash_folder_name = self.mailbox_trash_folders[key]
            self._populate_account_folder_tree(key, folders)
            if self.active_source is mailbox and self.current_folder == source_path:
                self.current_folder = new_path
        self.statusBar().showMessage(f"Папка перемещена: {new_path}", 5000)

    def on_refresh(self) -> None:
        if not self.active_source or not self.current_folder:
            return
        try:
            summaries = self.active_source.refresh_folder(self.current_folder)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка обновления", str(exc))
            return
        self._render_folder(summaries)
        self.statusBar().showMessage(f"Обновлено: {self.current_folder}", 3000)

    def _on_periodic_refresh(self) -> None:
        # Тихая фоновая проверка по таймеру — без модальных окон об ошибках,
        # чтобы не перебивать пользователя, если тот занят (например, пишет письмо).
        # Архивы локальны и статичны — опрашивать их по таймеру незачем.
        if self.active_source is not self.mailbox or not self.mailbox or not self.current_folder:
            return
        try:
            summaries = self.mailbox.refresh_folder(self.current_folder)
        except Exception:
            return
        self._render_folder(summaries)

    def _clear_reading_pane(self) -> None:
        self.reading_pane.clear()
        self.selected_summary = None
        self.current_content = None
        self.current_attachments = []
        self.attachments_list.clear()
        self.attachments_list.hide()
        self.current_invite = None
        self.invite_bar.hide()
        self.message_header_widget.hide()
        self.thread_list.clear()
        self.thread_list.hide()

    def on_filter_changed(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.table.rowCount()):
            visible = True
            if needle:
                value = self.table.item(row, self.filter_column).text().lower()
                visible = needle in value
            if visible and self.marker_filter is not None:
                summary = self._summary_for_row(row)
                marker = summary.marker_color if summary else None
                visible = marker is not None if self.marker_filter == _ANY_MARKER_FILTER else marker == self.marker_filter
            self.table.setRowHidden(row, not visible)

    def _render_folder(self, summaries: list[MessageSummary]) -> None:
        previously_selected_uid = self.selected_summary.uid if self.selected_summary else None

        self.current_summaries = summaries
        self.summaries_by_uid = {s.uid: s for s in summaries}

        # Сортировку на время заполнения отключаем: иначе Qt переставляет
        # строки после каждого setItem(), и индекс row перестаёт совпадать
        # с тем, что мы только что туда положили.
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(summaries))
        for row, summary in enumerate(summaries):
            check_item = QTableWidgetItem()
            check_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check_item.setCheckState(Qt.CheckState.Unchecked)
            check_item.setData(Qt.ItemDataRole.UserRole, summary.uid)
            self.table.setItem(row, COL_CHECK, check_item)

            flag_item = self._readonly_item("")
            if summary.marker_color:
                flag_item.setIcon(_marker_icon(summary.marker_color))
            self.table.setItem(row, COL_FLAG, flag_item)

            self.table.setItem(row, COL_IMPORTANCE, self._readonly_item(_importance_mark(summary.importance)))
            self.table.setItem(
                row, COL_ATTACHMENT, self._readonly_item(_ATTACHMENT_MARK if summary.has_attachments else "")
            )
            sender_item = QTableWidgetItem(summary.sender)
            subject_item = QTableWidgetItem(summary.subject)
            if not summary.is_read:
                # Непрочитанное — жирным, как в любом другом почтовом клиенте.
                bold_font = sender_item.font()
                bold_font.setBold(True)
                sender_item.setFont(bold_font)
                subject_item.setFont(bold_font)
            self.table.setItem(row, COL_SENDER, sender_item)
            self.table.setItem(row, COL_SUBJECT, subject_item)
            self.table.setItem(row, COL_DATE, QTableWidgetItem(summary.date))
        self.table.setSortingEnabled(True)

        self.statusBar().showMessage(f"{self.current_folder}: писем {len(summaries)}", 5000)
        self.on_filter_changed(self.filter_edit.text())

        if previously_selected_uid is not None:
            for row in range(self.table.rowCount()):
                if self.table.item(row, COL_CHECK).data(Qt.ItemDataRole.UserRole) == previously_selected_uid:
                    self.table.selectRow(row)
                    break

    @staticmethod
    def _readonly_item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return item

    def _summary_for_row(self, row: int) -> MessageSummary | None:
        uid = self.table.item(row, COL_CHECK).data(Qt.ItemDataRole.UserRole)
        return self.summaries_by_uid.get(uid)

    def on_mail_table_context_menu(self, pos) -> None:
        item = self.table.itemAt(pos)
        if item is None:
            return
        summary = self._summary_for_row(item.row())
        if summary is None:
            return
        menu = QMenu(self)
        toggle_read_action = menu.addAction(
            "Отметить как непрочитанное" if summary.is_read else "Отметить как прочитанное"
        )
        restore_action = None
        in_trash = (
            self.active_source is self.mailbox
            and self.trash_folder_name is not None
            and self.current_folder == self.trash_folder_name
        )
        if in_trash:
            restore_action = menu.addAction("Восстановить из корзины")
        menu.addSeparator()
        add_contact_action = menu.addAction("Добавить отправителя в контакты…")
        chosen = menu.exec(self.table.mapToGlobal(pos))

        if chosen is toggle_read_action:
            self._set_message_read(item.row(), summary, not summary.is_read)
            return
        if restore_action is not None and chosen is restore_action:
            self.on_restore_from_trash()
            return
        if chosen is not add_contact_action:
            return

        existing = None
        if summary.sender_email:
            try:
                existing = contact_store.find_by_email(self.contacts_path, summary.sender_email)
            except Exception:
                existing = None
        if existing is not None:
            dialog = ContactDialog(self, contact=existing)
        else:
            prefilled = contact_store.Contact(
                display_name=summary.sender, emails=[summary.sender_email] if summary.sender_email else []
            )
            dialog = ContactDialog(self, contact=prefilled)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            contact_store.save_contact(self.contacts_path, dialog.to_contact())
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось сохранить контакт", str(exc))
            return
        self.statusBar().showMessage(f"Сохранено в контактах: {dialog.name_edit.text()}", 5000)

    def on_table_item_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() != COL_FLAG or not self.active_source or not self.current_folder:
            return
        summary = self._summary_for_row(item.row())
        if summary is None:
            return
        self._open_marker_menu(item, summary)

    def on_table_item_double_clicked(self, item: QTableWidgetItem) -> None:
        if not self.active_source or not self.current_folder:
            return
        summary = self._summary_for_row(item.row())
        if summary is None:
            return
        try:
            content = self.active_source.message_content(self.current_folder, summary.uid)
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось загрузить письмо", str(exc))
            return

        # Двойной клик по письму в "Черновиках" — сразу продолжить его
        # редактирование, а не просто показать (жалоба: "из черновиков не
        # отправляет"); во всех остальных папках — открыть в отдельном
        # окне (жалоба: "нельзя открыть письмо в отдельном окне").
        is_draft = (
            self.active_source is self.mailbox
            and self.drafts_folder_name is not None
            and self.current_folder == self.drafts_folder_name
        )
        if is_draft:
            dialog = ComposeDialog(
                self,
                title="Черновик",
                to=content.to,
                cc=content.cc,
                bcc=content.bcc,
                subject=content.subject or summary.subject,
                body=content.text,
                contacts=self._load_contacts(),
                attachments=[
                    OutgoingAttachment(filename=a.filename, content_type=a.content_type, payload=a.payload)
                    for a in content.attachments
                ],
            )
            self._exec_compose(dialog, source_draft=(self.current_folder, summary.uid))
            return

        self._open_message_window(summary, content)

    def _open_marker_menu(self, item: QTableWidgetItem, summary: MessageSummary) -> None:
        menu = QMenu(self)
        none_action = menu.addAction("Без маркера")
        menu.addSeparator()
        action_colors: dict[QAction, str] = {}
        for color, label in _MARKER_LABELS.items():
            action = menu.addAction(_marker_icon(color), label)
            action_colors[action] = color

        chosen = menu.exec(QCursor.pos())
        if chosen is None:
            return
        new_color = None if chosen is none_action else action_colors[chosen]

        try:
            self.active_source.set_marker(
                self.current_folder, summary.uid, new_color, previous_color=summary.marker_color
            )
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось изменить маркер", str(exc))
            return

        summary.marker_color = new_color
        item.setIcon(_marker_icon(new_color) if new_color else QIcon())

    def _checked_uids(self) -> list[int]:
        checked = [
            self.table.item(row, COL_CHECK).data(Qt.ItemDataRole.UserRole)
            for row in range(self.table.rowCount())
            if self.table.item(row, COL_CHECK).checkState() == Qt.CheckState.Checked
        ]
        if checked:
            return checked
        # Ничего не отмечено галочками — при ДВУХ и более выделенных
        # строках (обычный Ctrl+клик/Shift+клик) считаем это тем же самым:
        # раньше массовые действия (удаление, восстановление из корзины)
        # требовали ставить галочку на каждое письмо по отдельности, даже
        # если уже была выделена целая группа строк (жалоба: "невозможно
        # удалить письма из корзины сразу (только выбор по 1)"). Одно
        # выделение НЕ считаем — это обычно просто открытое для чтения
        # письмо (клик по строке = его выделение), а не намерение
        # массового действия; так одиночный клик по письму по-прежнему не
        # рискует случайно попасть под "Удалить".
        selected_rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        if len(selected_rows) < 2:
            return []
        return [self.table.item(row, COL_CHECK).data(Qt.ItemDataRole.UserRole) for row in selected_rows]

    def on_restore_from_trash(self) -> None:
        if self.active_source is not self.mailbox or not self.mailbox or not self.current_folder:
            return
        checked_uids = self._checked_uids()
        if not checked_uids:
            QMessageBox.information(
                self, "Нечего восстанавливать", "Выделите (или отметьте галочками) письма для восстановления."
            )
            return
        # "INBOX" — единственное протокольное имя папки, гарантированное
        # на любом IMAP-сервере (RFC 3501); у нас нет сведений, из какой
        # именно папки письмо когда-то попало в корзину, поэтому
        # восстанавливаем во "Входящие" как разумное значение по умолчанию.
        try:
            self.mailbox.move_to_folder(self.current_folder, checked_uids, "INBOX")
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось восстановить", str(exc))
            return
        try:
            summaries = self.mailbox.refresh_folder(self.current_folder)
        except Exception:
            summaries = [s for s in self.current_summaries if s.uid not in checked_uids]
        self._render_folder(summaries)
        self.statusBar().showMessage(f"Восстановлено во «Входящие»: {len(checked_uids)}", 5000)

    def on_delete_selected(self) -> None:
        if not self.active_source or not self.current_folder:
            return
        checked_uids = self._checked_uids()
        if not checked_uids:
            QMessageBox.information(self, "Нечего удалять", "Отметьте галочками письма, которые нужно удалить.")
            return

        if self.active_source is self.mailbox:
            shift_held = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
            already_in_trash = self.trash_folder_name is not None and self.current_folder == self.trash_folder_name
            permanent = shift_held or already_in_trash or not self.trash_folder_name

            if permanent:
                confirm = QMessageBox.question(
                    self,
                    "Удалить безвозвратно",
                    f"Удалить выбранные письма насовсем ({len(checked_uids)})? Это действие нельзя отменить.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if confirm != QMessageBox.StandardButton.Yes:
                    return
                try:
                    self.mailbox.delete_messages(self.current_folder, checked_uids)
                except Exception as exc:
                    QMessageBox.critical(self, "Ошибка удаления", str(exc))
                    return
                status_text = f"Удалено безвозвратно: {len(checked_uids)}"
            else:
                try:
                    self.mailbox.move_to_trash(self.current_folder, checked_uids, self.trash_folder_name)
                except Exception as exc:
                    QMessageBox.critical(self, "Ошибка удаления", str(exc))
                    return
                status_text = f"Перемещено в корзину: {len(checked_uids)}"
        else:
            confirm = QMessageBox.question(
                self,
                "Удалить из архива",
                f"Удалить выбранные письма из архива насовсем ({len(checked_uids)})? Это действие нельзя отменить.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
            try:
                self.active_source.delete_messages(self.current_folder, checked_uids)
            except Exception as exc:
                QMessageBox.critical(self, "Ошибка удаления", str(exc))
                return
            status_text = f"Удалено из архива: {len(checked_uids)}"

        if self.selected_summary and self.selected_summary.uid in checked_uids:
            self._clear_reading_pane()
        try:
            summaries = self.active_source.refresh_folder(self.current_folder)
        except Exception as exc:
            QMessageBox.warning(self, "Письма удалены, но обновить список не удалось", str(exc))
            return
        self._render_folder(summaries)
        self.statusBar().showMessage(status_text, 5000)

    def on_message_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or not self.active_source or not self.current_folder:
            return
        summary = self._summary_for_row(rows[0].row())
        if summary is None:
            return
        self.selected_summary = summary
        self.current_invite = None
        self.invite_bar.hide()
        try:
            content = self.active_source.message_content(self.current_folder, summary.uid)
        except Exception as exc:
            self.current_body = ""
            self.current_attachments = []
            self.current_content = None
            self.reading_pane.setPlainText(f"Не удалось загрузить письмо: {exc}")
            self.attachments_list.clear()
            self.attachments_list.hide()
            self.message_header_widget.hide()
            return
        self.current_body = content.text
        self.current_attachments = content.attachments
        self.current_content = content
        self._render_message_header(summary, content)
        self._render_thread(summary, content)
        self._update_invite_bar(content)
        self._refresh_attachments_list()

        if not summary.is_read:
            # Отложено на следующий цикл событий: сама отметка "прочитано"
            # на сервере — это блокирующий сетевой запрос (STORE), и раньше
            # он выполнялся прямо здесь, ДО того как письмо успевало
            # отрисоваться — на реальной корпоративной сети с заметной
            # задержкой это ощущалось как "переключение между письмами
            # тормозит" при каждом непрочитанном письме. Так письмо сначала
            # показывается, а запрос уходит следом.
            row = rows[0].row()
            QTimer.singleShot(0, lambda: self._set_message_read(row, summary, True))

    def _render_message_header(self, summary: MessageSummary, content: MessageContent) -> None:
        subject = content.subject or summary.subject or "(без темы)"
        sender = content.from_ or (f"{summary.sender} <{summary.sender_email}>" if summary.sender_email else summary.sender)
        lines = [
            f"<b>Тема:</b> {html.escape(subject)}",
            f"<b>От:</b> {html.escape(sender)}",
        ]
        if content.to:
            lines.append(f"<b>Кому:</b> {html.escape(content.to)}")
        if content.cc:
            lines.append(f"<b>Копия:</b> {html.escape(content.cc)}")
        if summary.date:
            lines.append(f"<b>Дата:</b> {html.escape(summary.date)}")
        self.message_header_label.setText("<br>".join(lines))
        self.message_header_widget.show()
        self._render_thread_list(summary)

    def _thread_summaries_for(self, summary: MessageSummary) -> list[MessageSummary]:
        """Остальные письма текущей папки с той же темой (без Re:/Fwd:/
        Ответ:/Пересыл:), отсортированные от старых к новым — не включает
        само summary."""
        normalized = _normalize_subject(summary.subject)
        return sorted(
            (s for s in self.current_summaries if s.uid != summary.uid and _normalize_subject(s.subject) == normalized),
            key=lambda s: s.date,
        )

    def _render_thread_list(self, summary: MessageSummary) -> None:
        thread = self._thread_summaries_for(summary)
        self.thread_list.clear()
        if not thread:
            self.thread_list.hide()
            return
        for other in thread:
            item = QListWidgetItem(f"{other.date} — {other.sender}: {other.subject}")
            item.setData(Qt.ItemDataRole.UserRole, other.uid)
            self.thread_list.addItem(item)
        self.thread_list.show()

    def _on_thread_item_clicked(self, item: QListWidgetItem) -> None:
        # Вся цепочка уже отрисована последовательно в reading_pane (см.
        # _render_thread) — переход к письму это просто прокрутка к его
        # якорю, а не смена выбранной строки в таблице.
        uid = item.data(Qt.ItemDataRole.UserRole)
        self.reading_pane.scrollToAnchor(f"msg-{uid}")

    def _render_thread(self, summary: MessageSummary, content: MessageContent) -> None:
        """Показывает письмо вместе со всей его цепочкой подряд, одной
        прокручиваемой лентой (жалоба: "есть только ссылки на другие
        письма, а надо просмотр цепочки последовательно"). Если у письма
        нет цепочки — обычный показ одного письма, без изменений."""
        thread = self._thread_summaries_for(summary)
        if not thread:
            self._render_body(content)
            return

        all_in_thread = sorted([summary, *thread], key=lambda s: s.date)[-_THREAD_DEPTH_LIMIT:]
        self._thread_content_cache[summary.uid] = content
        self._thread_render_token += 1
        token = self._thread_render_token

        # Показываем то, что уже есть (текущее письмо — сразу, остальные —
        # из кэша, если уже когда-то грузились), не дожидаясь сети —
        # раньше загрузка недостающих писем цепочки шла синхронно прямо
        # здесь, и окно "подвисало" на каждое письмо цепочки, которого ещё
        # не было в кэше (жалоба: "при просмотре цепочки клиент зависает").
        self._paint_thread(summary, all_in_thread, content)

        missing = [s for s in all_in_thread if s.uid != summary.uid and s.uid not in self._thread_content_cache]
        if not missing:
            return

        folder = self.current_folder
        source = self.active_source

        def fetch_missing() -> dict[int, MessageContent | None]:
            results: dict[int, MessageContent | None] = {}
            for other in missing:
                try:
                    results[other.uid] = source.message_content(folder, other.uid)
                except Exception:
                    results[other.uid] = None
            return results

        worker = _CallableWorker(fetch_missing, parent=self)

        def on_success(results: object) -> None:
            self._background_workers.remove(worker)
            if token != self._thread_render_token:
                return  # пользователь уже открыл другое письмо — этот ответ больше не актуален
            for uid, other_content in results.items():
                if other_content is not None:
                    self._thread_content_cache[uid] = other_content
            self._paint_thread(summary, all_in_thread, content)

        def on_failure(_message: str) -> None:
            self._background_workers.remove(worker)

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._background_workers.append(worker)
        worker.start()

    def _paint_thread(
        self, summary: MessageSummary, all_in_thread: list[MessageSummary], content: MessageContent
    ) -> None:
        """Собирает и показывает HTML цепочки из того, что сейчас есть в
        _thread_content_cache — письма, ещё не подгруженные из сети,
        показываются заглушкой "Загрузка…" (заменяется на реальный текст,
        когда придёт фоновый ответ, см. _render_thread)."""
        entries: list[tuple[MessageSummary, str]] = []
        for other in all_in_thread:
            if other.uid == summary.uid:
                entries.append((other, content.html or _linkify(content.text)))
                continue
            other_content = self._thread_content_cache.get(other.uid)
            if other_content is None:
                entries.append((other, "<i>Загрузка…</i>"))
                continue
            # Тело остальных писем цепочки — только текстом, не их родным
            # HTML: см. подробное объяснение в _build_thread_html.
            body = _linkify(other_content.text) if other_content.text.strip() else "<i>(письмо в формате HTML — предпросмотр недоступен в цепочке)</i>"
            entries.append((other, body))

        document = self.reading_pane.document()
        document.clear()
        for content_id, (_content_type, payload) in content.inline_images.items():
            image = QImage.fromData(payload)
            if not image.isNull():
                document.addResource(QTextDocument.ResourceType.ImageResource, QUrl(f"cid:{content_id}"), image)
        self.reading_pane.setHtml(_build_thread_html(entries, summary.uid))
        self.reading_pane.scrollToAnchor(f"msg-{summary.uid}")

    def on_open_message_window(self) -> None:
        if self.selected_summary is None or self.current_content is None:
            return
        self._open_message_window(self.selected_summary, self.current_content)

    def _open_message_window(self, summary: MessageSummary, content: MessageContent) -> None:
        window = MessageWindow(summary, content, parent=self)
        window.destroyed.connect(lambda: self._message_windows.remove(window) if window in self._message_windows else None)
        self._message_windows.append(window)
        window.show()

    def _render_body(self, content: MessageContent) -> None:
        _populate_body_browser(self.reading_pane, content)

    def _set_message_read(self, row: int, summary: MessageSummary, read: bool) -> None:
        try:
            self.active_source.set_read(self.current_folder, summary.uid, read)
        except Exception:
            pass  # необязательная операция — письмо и так уже открыто/помечено локально
        summary.is_read = read
        for col in (COL_SENDER, COL_SUBJECT):
            item = self.table.item(row, col)
            font = item.font()
            font.setBold(not read)
            item.setFont(font)

    def _update_invite_bar(self, content: MessageContent) -> None:
        calendar_part = next((a for a in content.attachments if a.content_type == "text/calendar"), None)
        if calendar_part is None or not self.account:
            return
        try:
            invite = itip.parse_invite(calendar_part.payload, my_email=self.account.username)
        except Exception:
            return  # повреждённый или непонятный .ics — просто не показываем панель

        try:
            self._apply_invite_to_calendar(invite)
        except Exception as exc:
            # Не проглатывать молча — иначе панель приглашения просто не
            # появляется без единого следа, почему (так уже терялось видимое
            # состояние календаря один раз — см. миграцию схемы в
            # calendar_store.py).
            QMessageBox.critical(self, "Не удалось обработать приглашение", str(exc))

    def _apply_invite_to_calendar(self, invite: itip.IncomingInvite) -> None:
        if invite.method == "REQUEST":
            event = calendar_store.apply_invite(self.calendar_path, "REQUEST", invite.event)
            self.current_invite = invite
            when = _format_event_time(event)
            text = f"Приглашение: «{event.summary}» — {when}"
            if event.location:
                text += f", {event.location}"
            text += f"\nОрганизатор: {event.organizer_name or event.organizer_email}"
            if event.my_participation != "needs-action":
                text += f" · {_PARTICIPATION_LABELS[event.my_participation]}"
            self.invite_label.setText(text)
            can_respond = bool(self.smtp_account) and event.my_participation == "needs-action"
            for button in (self.invite_accept_button, self.invite_tentative_button, self.invite_decline_button):
                button.setEnabled(can_respond)
            self.invite_bar.show()
        elif invite.method == "CANCEL":
            calendar_store.apply_invite(self.calendar_path, "CANCEL", invite.event)
            self.current_invite = None
            self.invite_label.setText(f"Встреча отменена: «{invite.event.summary}»")
            for button in (self.invite_accept_button, self.invite_tentative_button, self.invite_decline_button):
                button.setEnabled(False)
            self.invite_bar.show()
        elif invite.method == "REPLY" and invite.replying_attendee_email:
            participation = next(
                (a.participation for a in invite.event.attendees if a.email == invite.replying_attendee_email),
                "needs-action",
            )
            calendar_store.apply_reply(
                self.calendar_path, invite.event.uid, invite.replying_attendee_email, participation
            )
            self.current_invite = None
            label = _PARTICIPATION_LABELS.get(participation, participation)
            self.invite_label.setText(f"{invite.replying_attendee_email}: {label} — «{invite.event.summary}»")
            for button in (self.invite_accept_button, self.invite_tentative_button, self.invite_decline_button):
                button.setEnabled(False)
            self.invite_bar.show()

    def on_invite_response(self, participation: str) -> None:
        if self.current_invite is None or not self.account:
            return
        event = self._respond_to_invite(self.current_invite.event.uid, participation)
        if event is None:
            return
        self.current_invite = None
        self.invite_label.setText(f"«{event.summary}» — {_PARTICIPATION_LABELS[participation]}")
        for button in (self.invite_accept_button, self.invite_tentative_button, self.invite_decline_button):
            button.setEnabled(False)
        # Само письмо с ответом уходит в фоне (см. _send_message_in_background) —
        # окончательное "отправлен" покажет её собственный колбэк успеха.
        self.statusBar().showMessage(f"Ответ сохранён, отправляется: {_REPLY_VERBS[participation].lower()}", 3000)

    def on_calendar_rsvp(self, event: calendar_store.Event, participation: str) -> None:
        """То же самое, что on_invite_response, но для встречи, открытой
        прямо из календаря (EventDetailsDialog), а не из панели приглашения
        в почте — раньше поменять участие можно было только через письмо."""
        updated = self._respond_to_invite(event.uid, participation)
        if updated is None:
            return
        self.selected_calendar_event = updated
        self.refresh_calendar_view()
        self.statusBar().showMessage(f"Ответ сохранён, отправляется: {_REPLY_VERBS[participation].lower()}", 3000)

    def _respond_to_invite(self, uid: str, participation: str) -> calendar_store.Event | None:
        if not self.account:
            return None
        if not self.smtp_account:
            QMessageBox.warning(
                self, "Нет исходящей почты", "Укажите сервер SMTP в настройках, чтобы ответить на приглашение."
            )
            return None
        event = calendar_store.set_my_participation(self.calendar_path, uid, participation)
        if event is None:
            return None

        ics = itip.build_reply_ics(event, self.account.username, self.account.username, participation)
        verb = _REPLY_VERBS[participation]
        message = OutgoingMessage(
            sender=self.account.username,
            to=[event.organizer_email],
            subject=f"{verb}: {event.summary}",
            body=f"{verb}: «{event.summary}»",
            attachments=[
                OutgoingAttachment(
                    filename="reply.ics",
                    content_type="text/calendar",
                    payload=ics,
                    content_type_params={"method": "REPLY"},
                )
            ],
        )
        self._send_message_in_background(
            message,
            success_status=f"Ответ на приглашение отправлен: {verb.lower()}",
            failure_title="Не удалось отправить ответ",
        )
        return event

    def _show_mail_page(self) -> None:
        self.pages.setCurrentIndex(0)
        self._update_mail_actions_enabled()

    def _update_mail_actions_enabled(self) -> None:
        # Раньше "Ответить"/"Переслать"/"Удалить" (и выгрузка в архив)
        # оставались нажимаемыми и на вкладках "Контакты"/"Календарь", хотя
        # там нет ни выбранного письма, ни текущей папки почты — жалоба:
        # "при нахождении в контактах или календаре можно нажать кнопку
        # переслать, удалить, ответить".
        is_mail_page = self.pages.currentIndex() == 0
        for action in (
            self.reply_action,
            self.forward_action,
            self.delete_action,
            self.archive_selected_action,
            self.archive_folder_action,
        ):
            action.setEnabled(is_mail_page)

    def _show_calendar_page(self) -> None:
        self.pages.setCurrentIndex(1)
        self._update_mail_actions_enabled()
        self.refresh_calendar_view()
        if not self._calendar_scrolled_to_now:
            self._calendar_scrolled_to_now = True
            self._calendar_scroll.verticalScrollBar().setValue(self.calendar_week_grid.scroll_position_for_now())

    def on_calendar_prev_week(self) -> None:
        if self.calendar_view_mode == "month":
            self.calendar_month_anchor = _shift_month(self.calendar_month_anchor, -1)
        else:
            self.calendar_week_start -= timedelta(days=7)
        self.refresh_calendar_view()

    def on_calendar_next_week(self) -> None:
        if self.calendar_view_mode == "month":
            self.calendar_month_anchor = _shift_month(self.calendar_month_anchor, 1)
        else:
            self.calendar_week_start += timedelta(days=7)
        self.refresh_calendar_view()

    def on_calendar_today(self) -> None:
        self.calendar_week_start = week_start_for(date.today())
        self.calendar_month_anchor = date.today().replace(day=1)
        self.refresh_calendar_view()

    def on_calendar_view_mode_changed(self, text: str) -> None:
        self.calendar_view_mode = "month" if text == "Месяц" else "week"
        self.calendar_view_stack.setCurrentIndex(1 if self.calendar_view_mode == "month" else 0)
        self.refresh_calendar_view()

    def on_calendar_month_day_clicked(self, day: date) -> None:
        self.calendar_selected_day = day
        self.calendar_month_grid.set_selected_day(day)

    def on_calendar_month_day_double_clicked(self, day: date) -> None:
        # Двойной клик по дню в месячном виде — перейти к недельному виду,
        # показывающему этот день (в месячном виде время не выбрать, только
        # дату; для создания/просмотра события с точным временем нужна
        # неделя).
        self.calendar_week_start = week_start_for(day)
        self.calendar_selected_day = day
        self.calendar_view_combo.setCurrentText("Неделя")

    def on_import_ics(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(self, "Выбрать файл .ics", filter="iCalendar (*.ics)")
        if not path_str:
            return
        try:
            data = Path(path_str).read_bytes()
            count = itip.import_ics(self.calendar_path, data, self.account.username if self.account else "")
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка импорта", str(exc))
            return
        self.refresh_calendar_view()
        self.statusBar().showMessage(f"Импортировано событий: {count}", 5000)

    def on_caldav_sync(self) -> None:
        """Ручная синхронизация по кнопке — не по таймеру. Сервер ни разу
        не проверялся вживую (закрытая корпоративная сеть, доступа отсюда
        нет), поэтому автоматический фоновый опрос пока не включаем —
        только явный запуск, чтобы первые реальные проблемы были видны
        сразу пользователю, а не тихо повторялись каждые несколько минут.

        Раньше вся синхронизация (создание CalDAV-соединения, отправка
        каждой встречи по одной, затем получение всех с сервера) шла
        синхронно прямо здесь — в основном потоке интерфейса. На реальном
        сервере это могло занять заметное время, а без таймаута на HTTP
        (см. caldav_sync.CalDavSession) — зависнуть надолго при сетевых
        проблемах: жалоба "после настройки CalDav сломалась отправка,
        просмотр, переход между папками и получение почты" — окно было не
        сломано, а всё это время ждало одного зависшего запроса к CalDAV,
        полностью замёрзшее. Теперь вся сетевая часть уходит в фоновый
        поток, как и импорт/отправка почты."""
        if not self.account:
            QMessageBox.warning(self, "Нет учётной записи", "Сначала подключитесь к почте в настройках.")
            return
        if not self.caldav_url:
            QMessageBox.information(
                self, "CalDAV не настроен", "Укажите адрес CalDAV-сервера в Параметрах (раздел «Календарь»)."
            )
            return

        account = caldav_sync.CalDavAccount(
            url=self.caldav_url, username=self.account.username, password=self.account.password
        )
        calendar_path = self.calendar_path
        window_start = datetime.now(timezone.utc) - timedelta(days=30)
        window_end = datetime.now(timezone.utc) + timedelta(days=180)

        def do_sync() -> tuple[int, int]:
            session = caldav_sync.CalDavSession(account)
            try:
                # Сначала отправляем локальные изменения (свои встречи),
                # потом забираем с сервера — если сделать наоборот, свежая
                # локальная правка, ещё не отправленная, могла бы затереться
                # устаревшей версией с сервера при получении.
                pushed = 0
                local_events = calendar_store.list_events(calendar_path, start=window_start, end=window_end)
                for event in local_events:
                    if event.is_organizer and event.status != "cancelled":
                        session.push_event(event, account.username, account.username)
                        pushed += 1

                pulled = 0
                server_events = session.fetch_events(window_start, window_end, account.username)
                for event in server_events:
                    # CalDAV-сервер ничего не знает о наших полях, которых
                    # нет в стандартном iCalendar (ручной цвет события) и
                    # может не хранить произвольные вложения — не даём
                    # синхронизации тихо стереть то, что есть только локально.
                    existing_local = calendar_store.get_event(calendar_path, event.uid)
                    if existing_local:
                        if not event.attachments and existing_local.attachments:
                            event.attachments = existing_local.attachments
                        if not event.color and existing_local.color:
                            event.color = existing_local.color
                    calendar_store.save_event(calendar_path, event)
                    pulled += 1
                return pushed, pulled
            finally:
                session.close()

        self.statusBar().showMessage("Синхронизация с CalDAV…")
        worker = _CallableWorker(do_sync, parent=self)

        def on_success(result: object) -> None:
            pushed, pulled = result
            self.refresh_calendar_view()
            self.statusBar().showMessage(f"CalDAV: отправлено {pushed}, получено {pulled}", 7000)
            self._background_workers.remove(worker)

        def on_failure(error_text: str) -> None:
            QMessageBox.critical(self, "Ошибка CalDAV", error_text)
            self._background_workers.remove(worker)

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._background_workers.append(worker)
        worker.start()

    def refresh_calendar_view(self) -> None:
        # Локальный календарь ничего не опрашивает по сети — "Обновить"
        # здесь просто перечитывает файл (например, после того как в
        # почте были приняты новые приглашения).
        if self.calendar_view_mode == "month":
            window_start_date, window_end_date = month_grid_range(self.calendar_month_anchor)
        else:
            window_start_date = self.calendar_week_start
            window_end_date = self.calendar_week_start + timedelta(days=7)
        window_start_local = datetime(
            window_start_date.year, window_start_date.month, window_start_date.day
        ).astimezone()
        window_end_local = datetime(window_end_date.year, window_end_date.month, window_end_date.day).astimezone()
        window_start = window_start_local.astimezone(timezone.utc)
        window_end = window_end_local.astimezone(timezone.utc)
        try:
            events = calendar_store.list_events(self.calendar_path, start=window_start, end=window_end)
        except Exception as exc:
            # Без этого исключение из слота Qt тихо проглатывалось — сетка
            # просто оставалась пустой без единого сообщения об ошибке
            # (так был найден баг миграции схемы calendar.rmcal).
            QMessageBox.critical(self, "Не удалось загрузить календарь", str(exc))
            return
        events = [e for e in events if e.status != "cancelled"]
        if not self.calendar_show_events:
            events = []

        if self.calendar_view_mode == "month":
            self.calendar_month_label.setText(
                f"{_MONTH_NAMES[self.calendar_month_anchor.month - 1]} {self.calendar_month_anchor.year}"
            )
            self.calendar_month_grid.set_month(self.calendar_month_anchor, events)
            self.calendar_month_grid.set_selected_day(self.calendar_selected_day)
            highlighted_day = self._mini_picker_target_day or self.calendar_selected_day or self.calendar_month_anchor
        else:
            timed_events = [e for e in events if not e.all_day]
            all_day_events = [e for e in events if e.all_day]
            # Неделя может задевать два месяца — подписываем по четвергу этой
            # недели (тот же принцип, что и у номера недели ISO: у какого
            # месяца больше дней в неделе, тот и "её" месяц).
            anchor = self.calendar_week_start + timedelta(days=3)
            self.calendar_month_label.setText(f"{_MONTH_NAMES[anchor.month - 1]} {anchor.year}")
            self.calendar_week_header.set_week_start(self.calendar_week_start)
            self.calendar_all_day_row.set_week(self.calendar_week_start, all_day_events)
            self.calendar_week_grid.set_week(self.calendar_week_start, timed_events)
            # Раньше здесь всегда подставлялся понедельник недели — если
            # пользователь кликал в мини-календаре не по понедельнику
            # (например, 21.08 — пятница), тот же refresh_calendar_view()
            # тут же откатывал выделение обратно на 17.08 и день визуально
            # "не выбирался".
            highlighted_day = self._mini_picker_target_day or self.calendar_week_start

        self.calendar_mini_picker.setSelectedDate(
            QDate(highlighted_day.year, highlighted_day.month, highlighted_day.day)
        )
        self._apply_calendar_selection_highlight()

    def _apply_calendar_selection_highlight(self) -> None:
        selected_uid = self.selected_calendar_event.uid if self.selected_calendar_event else None
        all_blocks = (*self.calendar_week_grid._blocks, *self.calendar_all_day_row._blocks, *self.calendar_month_grid.blocks)
        for block in all_blocks:
            block.set_selected(selected_uid is not None and block.calendar_event.uid == selected_uid)

    def on_calendar_mini_picker_clicked(self, qdate: QDate) -> None:
        picked = date(qdate.year(), qdate.month(), qdate.day())
        self.calendar_selected_day = picked
        self.calendar_week_header.set_selected_day(picked)
        self.calendar_week_grid.set_selected_day(picked)
        if self.calendar_view_mode == "month":
            self.calendar_month_anchor = picked.replace(day=1)
        else:
            self.calendar_week_start = week_start_for(picked)
        self._mini_picker_target_day = picked
        try:
            self.refresh_calendar_view()
        finally:
            self._mini_picker_target_day = None

    def on_calendar_visibility_toggled(self, checked: bool) -> None:
        self.calendar_show_events = checked
        self.refresh_calendar_view()

    def _on_calendar_event_clicked(self, event: calendar_store.Event) -> None:
        self.selected_calendar_event = event
        self._apply_calendar_selection_highlight()

    def _on_calendar_event_double_clicked(self, event: calendar_store.Event) -> None:
        self.selected_calendar_event = event
        self._apply_calendar_selection_highlight()
        if not event.is_organizer:
            dialog = EventDetailsDialog(self, event)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                if dialog.chosen_participation is not None:
                    self.on_calendar_rsvp(event, dialog.chosen_participation)
                elif dialog.copy_requested:
                    self.on_copy_event(event)
            return
        dialog = EventDialog(
            self, event=event, my_email=self.account.username if self.account else "", contacts=self._load_contacts()
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_event_from_dialog(dialog, existing=event)

    def on_new_event(self, *, default_start: datetime | None = None) -> None:
        if not self.account:
            QMessageBox.warning(self, "Нет учётной записи", "Сначала подключитесь к почте в настройках.")
            return
        if default_start is None and self.calendar_selected_day is not None:
            # Кнопка "Новое событие" на панели — если пользователь кликом
            # выбрал день в шапке календаря, событие по умолчанию ставим
            # туда, а не всегда на "сейчас+час".
            default_start = self._slot_to_datetime(self.calendar_selected_day, 9 * 60)
        dialog = EventDialog(
            self, my_email=self.account.username, contacts=self._load_contacts(), default_start=default_start
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_event_from_dialog(dialog, existing=None)

    def on_calendar_day_clicked(self, day: date) -> None:
        self.calendar_selected_day = day
        self.calendar_week_header.set_selected_day(day)
        self.calendar_week_grid.set_selected_day(day)

    def on_calendar_empty_slot_clicked(self, day: date, minutes: int) -> None:
        self.on_new_event(default_start=self._slot_to_datetime(day, minutes))

    def on_calendar_empty_slot_context_menu(self, day: date, minutes: int, global_pos) -> None:
        menu = QMenu(self)
        new_action = menu.addAction("Новое событие…")
        chosen = menu.exec(global_pos)
        if chosen is new_action:
            self.on_new_event(default_start=self._slot_to_datetime(day, minutes))

    @staticmethod
    def _slot_to_datetime(day: date, minutes: int) -> datetime:
        return datetime(day.year, day.month, day.day, minutes // 60, minutes % 60).astimezone()

    def on_calendar_event_context_menu(self, event: calendar_store.Event, global_pos) -> None:
        self.selected_calendar_event = event
        self._apply_calendar_selection_highlight()
        menu = QMenu(self)
        open_action = menu.addAction("Изменить" if event.is_organizer else "Просмотреть")
        copy_action = menu.addAction("Копировать")
        cancel_action = None
        if event.is_organizer:
            menu.addSeparator()
            cancel_action = menu.addAction("Отменить встречу")
        chosen = menu.exec(global_pos)
        if chosen is open_action:
            self._on_calendar_event_double_clicked(event)
        elif chosen is copy_action:
            self.on_copy_event(event)
        elif chosen is not None and chosen is cancel_action:
            self.on_cancel_event()

    def on_copy_event(self, event: calendar_store.Event) -> None:
        if not self.account:
            QMessageBox.warning(self, "Нет учётной записи", "Сначала подключитесь к почте в настройках.")
            return
        dialog = EventDialog(self, event=event, my_email=self.account.username, contacts=self._load_contacts())
        dialog.setWindowTitle("Копия встречи")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_event_from_dialog(dialog, existing=None)

    def _load_contacts(self) -> list[contact_store.Contact]:
        try:
            return contact_store.list_contacts(self.contacts_path)
        except Exception:
            return []

    def _show_contacts_page(self) -> None:
        self.pages.setCurrentIndex(2)
        self._update_mail_actions_enabled()
        self.refresh_contacts_view()

    def refresh_contacts_view(self) -> None:
        try:
            contacts = contact_store.list_contacts(self.contacts_path)
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось загрузить контакты", str(exc))
            return
        self._contacts_by_row = contacts
        self.contacts_table.setRowCount(len(contacts))
        for row, contact in enumerate(contacts):
            self.contacts_table.setItem(row, 0, QTableWidgetItem(contact.display_name))
            self.contacts_table.setItem(row, 1, QTableWidgetItem(", ".join(contact.emails)))
            self.contacts_table.setItem(row, 2, QTableWidgetItem(contact.phone))
            self.contacts_table.setItem(row, 3, QTableWidgetItem(contact.organization))

    def on_contact_selection_changed(self) -> None:
        rows = self.contacts_table.selectionModel().selectedRows()
        self.selected_contact = self._contacts_by_row[rows[0].row()] if rows else None

    def on_contact_double_clicked(self, item: QTableWidgetItem) -> None:
        contact = self._contacts_by_row[item.row()]
        dialog = ContactDialog(self, contact=contact)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            contact_store.save_contact(self.contacts_path, dialog.to_contact())
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось сохранить контакт", str(exc))
            return
        self.refresh_contacts_view()

    def on_new_contact(self) -> None:
        dialog = ContactDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_contact = dialog.to_contact()
        if not new_contact.display_name and not new_contact.emails:
            return  # пустая форма — нечего сохранять
        try:
            contact_store.save_contact(self.contacts_path, new_contact)
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось сохранить контакт", str(exc))
            return
        self.refresh_contacts_view()

    def on_delete_contact(self) -> None:
        if self.selected_contact is None:
            QMessageBox.information(self, "Нечего удалять", "Выберите контакт в списке.")
            return
        confirm = QMessageBox.question(
            self,
            "Удалить контакт",
            f"Удалить «{self.selected_contact.display_name}» из адресной книги?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        contact_store.delete_contact(self.contacts_path, self.selected_contact.id)
        self.selected_contact = None
        self.refresh_contacts_view()

    def on_delete_all_contacts(self) -> None:
        count = self.contacts_table.rowCount()
        if count == 0:
            QMessageBox.information(self, "Адресная книга пуста", "Удалять нечего.")
            return
        confirm = QMessageBox.question(
            self,
            "Удалить все контакты",
            f"Удалить все контакты ({count})? Это действие нельзя отменить.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        contact_store.delete_all_contacts(self.contacts_path)
        self.selected_contact = None
        self.refresh_contacts_view()
        self.statusBar().showMessage(f"Удалено контактов: {count}", 5000)

    def on_import_contacts(self) -> None:
        menu = QMenu(self)
        vcard_action = menu.addAction("vCard (.vcf)…")
        csv_action = menu.addAction("CSV (экспорт из Outlook)…")
        chosen = menu.exec(QCursor.pos())
        if chosen is None:
            return

        if chosen is vcard_action:
            path_str, _ = QFileDialog.getOpenFileName(self, "Выбрать файл vCard", filter="vCard (*.vcf)")
            importer = contact_store.import_vcard
        elif chosen is csv_action:
            path_str, _ = QFileDialog.getOpenFileName(self, "Выбрать CSV-файл", filter="CSV (*.csv)")
            importer = contact_store.import_csv
        else:
            return
        if not path_str:
            return

        try:
            data = Path(path_str).read_bytes()
            count = importer(self.contacts_path, data)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка импорта", str(exc))
            return

        self.refresh_contacts_view()
        self.statusBar().showMessage(f"Импортировано контактов: {count}", 5000)

    def _save_event_from_dialog(self, dialog: EventDialog, *, existing: calendar_store.Event | None) -> None:
        start = dialog.start_utc()
        end = dialog.end_utc()
        all_day = dialog.all_day()
        if all_day:
            # "Весь день" — время суток из полей не важно (оно скрыто в
            # интерфейсе); границы всегда полночь-в-полночь по местному
            # времени, и минимум сутки, даже если начало/конец выбраны
            # на один день (иначе end<=start и ниже сработала бы проверка).
            start = start.astimezone().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
            end_local_date = end.astimezone().date()
            start_local_date = start.astimezone().date()
            if end_local_date <= start_local_date:
                end_local_date = start_local_date + timedelta(days=1)
            end = (
                datetime(end_local_date.year, end_local_date.month, end_local_date.day)
                .astimezone()
                .astimezone(timezone.utc)
            )
        if end <= start:
            QMessageBox.warning(self, "Некорректное время", "Окончание должно быть позже начала.")
            return

        # Только для НОВОГО начала — если существующее событие просто
        # редактируют (описание, участники), не трогая время, не блокируем
        # сохранение из-за того, что встреча объективно уже была в прошлом
        # (жалоба касалась именно возможности НАЗНАЧИТЬ встречу задним
        # числом, а не запрета редактировать историю). Сравниваем с
        # точностью до минуты, а не миллисекунды в миллисекунду — поле
        # start_edit (QDateTimeEdit) не хранит секунды/микросекунды вообще,
        # так что простое round-trip события через диалог без единого
        # изменения даты всё равно давало бы start != existing.dtstart
        # из-за обрезанных микросекунд и ложно блокировало бы сохранение.
        start_unchanged = existing is not None and start.replace(second=0, microsecond=0) == existing.dtstart.replace(
            second=0, microsecond=0
        )
        if not start_unchanged:
            now = datetime.now(timezone.utc)
            past_cutoff = (
                now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
                if all_day
                else now
            )
            if start < past_cutoff:
                QMessageBox.warning(self, "Прошедшее время", "Нельзя запланировать встречу на прошедшую дату/время.")
                return

        attendee_emails = dialog.attendee_emails()
        event = calendar_store.Event(
            uid=existing.uid if existing else calendar_store.new_uid(),
            summary=dialog.summary() or "(без темы)",
            description=dialog.description(),
            location=dialog.location(),
            dtstart=start,
            dtend=end,
            all_day=all_day,
            organizer_email=self.account.username,
            organizer_name=self.account.username,
            is_organizer=True,
            my_participation="accepted",
            sequence=(existing.sequence + 1) if existing else 0,
            recurrence_rule=dialog.recurrence_rule(),
            color=dialog.color(),
            attendees=[calendar_store.Attendee(email=addr) for addr in attendee_emails],
            attachments=list(dialog.attachments),
        )
        calendar_store.save_event(self.calendar_path, event)
        self._send_request_to_attendees(event, subject_prefix="Приглашение", body_prefix="Вас приглашают на встречу")
        self.refresh_calendar_view()

    def _send_request_to_attendees(self, event: calendar_store.Event, *, subject_prefix: str, body_prefix: str) -> None:
        """Общая часть для «создали/изменили встречу» и «перенесли
        перетаскиванием» — обе ветки должны разослать один и тот же
        обновлённый REQUEST (SEQUENCE уже увеличен к этому моменту)."""
        attendee_emails = [a.email for a in event.attendees]
        if not attendee_emails:
            return
        if not self.smtp_account:
            QMessageBox.warning(
                self,
                "Встреча сохранена, но не разослана",
                "Событие сохранено локально, но SMTP не настроен — приглашения участникам не отправлены.",
            )
            return
        ics = itip.build_request_ics(event, self.account.username, self.account.username)
        message = OutgoingMessage(
            sender=self.account.username,
            to=attendee_emails,
            subject=f"{subject_prefix}: {event.summary}",
            body=f"{body_prefix} «{event.summary}».\n{_format_event_time(event)}",
            attachments=[
                OutgoingAttachment(
                    filename="invite.ics",
                    content_type="text/calendar",
                    payload=ics,
                    content_type_params={"method": "REQUEST"},
                )
            ],
        )
        self._send_message_in_background(
            message,
            success_status=f"Приглашения разосланы: «{event.summary}»",
            failure_title="Встреча сохранена, но не разослана",
        )

    def _send_message_in_background(
        self,
        message: OutgoingMessage,
        *,
        success_status: str,
        failure_title: str,
        severity: str = "critical",
        on_success_extra: Callable[[], None] | None = None,
    ) -> None:
        # Событие/ответ на приглашение уже сохранены локально к моменту
        # вызова — сама отправка (SMTP или EWS, обычно 1-2 секунды на
        # реальный сервер) больше не блокирует интерфейс: раньше "Новая
        # встреча" и перенос мышью на пару секунд подвешивали всё окно
        # ровно на время сетевого разговора (жалоба: "подвисает при
        # создании события"). Для EWS-учётной записи отдельного
        # SMTP-релея нет — письмо уходит через тот же EWS-сеанс, что и
        # чтение (см. self.account_protocol/_EWS_SEND_MARKER выше).
        if self.account_protocol == "ews" and self.mailbox is not None:
            worker = _CallableWorker(ews_client.send_message, self.mailbox.session, message, parent=self)
        else:
            worker = _CallableWorker(send_message, self.smtp_account, message, parent=self)

        def on_success(_result: object) -> None:
            self.statusBar().showMessage(success_status, 5000)
            self._background_workers.remove(worker)
            if on_success_extra is not None:
                on_success_extra()

        def on_failure(error_text: str) -> None:
            dialog = QMessageBox.warning if severity == "warning" else QMessageBox.critical
            dialog(self, failure_title, error_text)
            self._background_workers.remove(worker)

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._background_workers.append(worker)
        worker.start()

    def on_calendar_event_drag_rescheduled(
        self, event: calendar_store.Event, day_delta: int, minute_delta: int
    ) -> None:
        # Перетаскивание доступно только для своих встреч (см.
        # _EventBlock._draggable), но событие в сигнале — снимок с момента
        # начала перетаскивания; на всякий случай проверяем ещё раз перед
        # тем, как от чужого имени разослать письмо всем участникам.
        if not event.is_organizer:
            self.refresh_calendar_view()
            return

        delta = timedelta(days=day_delta, minutes=minute_delta)
        new_start = event.dtstart + delta
        new_end = event.dtend + delta
        if new_start < datetime.now(timezone.utc):
            QMessageBox.warning(self, "Прошедшее время", "Нельзя перенести встречу на прошедшую дату/время.")
            self.refresh_calendar_view()  # вернуть блок на исходное место
            return
        when_text = new_start.astimezone().strftime("%d.%m.%Y %H:%M")
        confirm = QMessageBox.question(
            self,
            "Перенести встречу",
            f"Перенести «{event.summary}» на {when_text} и уведомить участников?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            self.refresh_calendar_view()  # вернуть блок на исходное место
            return

        updated = calendar_store.reschedule_event(self.calendar_path, event.uid, new_start, new_end)
        if updated is None:
            self.refresh_calendar_view()
            return
        self._send_request_to_attendees(updated, subject_prefix="Перенесено", body_prefix="Встреча перенесена:")
        self.refresh_calendar_view()
        self.statusBar().showMessage(f"Перенесено: «{updated.summary}» → {when_text}", 5000)

    def on_cancel_event(self) -> None:
        event = self.selected_calendar_event
        if event is None:
            QMessageBox.information(self, "Нечего отменять", "Выберите встречу в сетке (клик по блоку).")
            return
        if not event.is_organizer:
            QMessageBox.information(
                self, "Недоступно", "Отменить можно только встречу, которую организовали вы сами."
            )
            return
        confirm = QMessageBox.question(
            self,
            "Отменить встречу",
            f"Отменить «{event.summary}» и уведомить участников?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        attendee_emails = [a.email for a in event.attendees]
        if attendee_emails and self.smtp_account:
            ics = itip.build_cancel_ics(event, self.account.username, self.account.username)
            message = OutgoingMessage(
                sender=self.account.username,
                to=attendee_emails,
                subject=f"Отменено: {event.summary}",
                body=f"Встреча «{event.summary}» отменена.",
                attachments=[
                    OutgoingAttachment(
                        filename="cancel.ics",
                        content_type="text/calendar",
                        payload=ics,
                        content_type_params={"method": "CANCEL"},
                    )
                ],
            )
            self._send_message_in_background(
                message,
                success_status=f"Участники уведомлены об отмене: «{event.summary}»",
                failure_title="Не удалось уведомить участников",
                severity="warning",
            )

        calendar_store.delete_event(self.calendar_path, event.uid)
        self.selected_calendar_event = None
        self.refresh_calendar_view()

    def _refresh_attachments_list(self) -> None:
        self.attachments_list.clear()
        if not self.current_attachments:
            self.attachments_list.hide()
            return
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        for attachment in self.current_attachments:
            item = QListWidgetItem(icon, f"{attachment.filename} ({_format_size(attachment.size)})")
            self.attachments_list.addItem(item)
        self.attachments_list.show()

    def on_open_attachment(self, item: QListWidgetItem) -> None:
        attachment = self.current_attachments[self.attachments_list.row(item)]
        try:
            temp_dir = Path(tempfile.mkdtemp(prefix="redmail_"))
            temp_path = temp_dir / _safe_attachment_filename(attachment.filename)
            temp_path.write_bytes(attachment.payload)
        except OSError as exc:
            QMessageBox.critical(self, "Не удалось открыть вложение", str(exc))
            return
        self._temp_attachment_dirs.append(temp_dir)
        # Открываем той программой, что у ОС зарегистрирована для этого типа
        # файла — как двойной клик по файлу в файловом менеджере.
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(temp_path)))

    def on_attachment_context_menu(self, pos) -> None:
        item = self.attachments_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        save_action = menu.addAction("Сохранить как…")
        chosen = menu.exec(self.attachments_list.mapToGlobal(pos))
        if chosen is save_action:
            self._save_attachment(item)

    def _save_attachment(self, item: QListWidgetItem) -> None:
        attachment = self.current_attachments[self.attachments_list.row(item)]
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить вложение", _safe_attachment_filename(attachment.filename))
        if not path:
            return
        try:
            Path(path).write_bytes(attachment.payload)
        except OSError as exc:
            QMessageBox.critical(self, "Не удалось сохранить", str(exc))
            return
        self.statusBar().showMessage(f"Сохранено: {path}", 5000)

    def on_compose(self) -> None:
        if not self.smtp_account:
            QMessageBox.warning(
                self,
                "Нет исходящей почты",
                "Сначала подключитесь и укажите сервер SMTP в настройках учётной записи.",
            )
            return
        dialog = ComposeDialog(self, title="Новое письмо", contacts=self._load_contacts())
        self._exec_compose(dialog)

    def on_reply(self) -> None:
        if not self.selected_summary:
            QMessageBox.warning(self, "Нет письма", "Выберите письмо, на которое хотите ответить.")
            return
        self._start_reply(self.selected_summary, self.current_body)

    def _start_reply(self, summary: MessageSummary, body_text: str) -> None:
        # Вынесено из on_reply() отдельно — тем же путём пользуется кнопка
        # "Ответить" в окне отдельно открытого письма (MessageWindow),
        # где нет self.selected_summary/self.current_body (жалоба: "при
        # открытии письма в отдельном окне нет кнопок ответить, переслать").
        if not self.smtp_account:
            QMessageBox.warning(
                self,
                "Нет исходящей почты",
                "Сначала подключитесь и укажите сервер SMTP в настройках учётной записи.",
            )
            return
        subject = summary.subject if summary.subject.lower().startswith("re:") else f"Re: {summary.subject}"
        quote_header = f"{summary.date}, {summary.sender} писал(а):"
        quoted = "\n".join(f"> {line}" for line in body_text.splitlines())
        body = f"\n\n{quote_header}\n{quoted}"

        dialog = ComposeDialog(
            self, title="Ответить", to=summary.sender_email, subject=subject, body=body, contacts=self._load_contacts()
        )
        self._exec_compose(dialog, in_reply_to=summary.message_id or None)

    def on_forward(self) -> None:
        if not self.selected_summary or not self.active_source or not self.current_folder:
            QMessageBox.warning(self, "Нет письма", "Выберите письмо, которое хотите переслать.")
            return
        summary = self.selected_summary
        try:
            content = self.active_source.message_content(self.current_folder, summary.uid)
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось загрузить письмо", str(exc))
            return
        self._start_forward(summary, content)

    def _start_forward(self, summary: MessageSummary, content: MessageContent) -> None:
        # См. _start_reply — то же самое: общая часть для тулбара и для
        # кнопки "Переслать" в отдельном окне письма.
        if not self.smtp_account:
            QMessageBox.warning(
                self,
                "Нет исходящей почты",
                "Сначала подключитесь и укажите сервер SMTP в настройках учётной записи.",
            )
            return
        subject = summary.subject if summary.subject.lower().startswith("fwd:") else f"Fwd: {summary.subject}"
        forward_header = (
            f"---------- Пересланное сообщение ----------\n"
            f"От: {summary.sender} <{summary.sender_email}>\n"
            f"Дата: {summary.date}\n"
            f"Тема: {summary.subject}\n"
        )
        body = f"\n\n{forward_header}\n{content.text}"

        dialog = ComposeDialog(
            self,
            title="Переслать",
            subject=subject,
            body=body,
            contacts=self._load_contacts(),
            attachments=[
                OutgoingAttachment(
                    filename=a.filename, content_type=a.content_type, payload=a.payload
                )
                for a in content.attachments
            ],
        )
        self._exec_compose(dialog)

    def _exec_compose(
        self,
        dialog: ComposeDialog,
        *,
        in_reply_to: str | None = None,
        source_draft: tuple[str, int] | None = None,
    ) -> None:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        if dialog.save_as_draft_requested():
            self._save_draft(dialog, source_draft=source_draft)
            return

        recipients = dialog.recipients()
        if not recipients:
            QMessageBox.warning(self, "Нет получателя", "Укажите хотя бы одного получателя.")
            return

        message = OutgoingMessage(
            sender=self.account.username,
            to=recipients,
            cc=dialog.cc_recipients(),
            bcc=dialog.bcc_recipients(),
            subject=dialog.subject(),
            body=dialog.body(),
            in_reply_to=in_reply_to,
            attachments=dialog.attachments,
        )

        def after_send() -> None:
            self._append_sent_copy(message)
            if source_draft is not None:
                self._delete_draft(source_draft)

        # Раньше отправка шла синхронно прямо здесь — окно подвисало на
        # время SMTP-разговора с сервером, как и у календарных приглашений
        # (см. _send_message_in_background); теперь то же самое одним
        # общим путём, вместе с поддержкой EWS-аккаунтов.
        self._send_message_in_background(
            message,
            success_status=f"Письмо отправлено: {', '.join(recipients)}",
            failure_title="Ошибка отправки",
            on_success_extra=after_send,
        )

    def _append_sent_copy(self, message: OutgoingMessage) -> None:
        """Кладёт копию только что отправленного письма в "Отправленные"
        через IMAP APPEND — VK Mail (и не только) не всегда сам сохраняет
        копию исходящих (жалоба: "не отображается отправка почты, не
        появляется в папке отправленные"). Для EWS не нужно: там копия
        сохраняется автоматически самим Message.send() (см. ews_client)."""
        if self.account_protocol != "imap" or not self.sent_folder_name or self.mailbox is None:
            return
        try:
            raw = build_email_message(message).as_bytes()
            self.mailbox.append_message(self.sent_folder_name, raw, flags=(b"\\Seen",))
        except Exception:
            pass  # письмо уже реально ушло получателю — копия в "Отправленные" необязательна

    def _save_draft(self, dialog: ComposeDialog, *, source_draft: tuple[str, int] | None) -> None:
        # Жалоба: "в черновики новые письма не сохраняет" — раньше кнопки
        # сохранения черновика не было вовсе, при закрытии окна
        # недописанное письмо просто терялось.
        if self.account_protocol != "imap":
            QMessageBox.warning(
                self,
                "Пока не поддерживается",
                "Сохранение черновиков для учётных записей Exchange (EWS) пока не реализовано.",
            )
            return
        if not self.drafts_folder_name or self.mailbox is None:
            QMessageBox.warning(
                self, "Нет папки «Черновики»", "У этой учётной записи не найдена папка «Черновики» на сервере."
            )
            return
        message = OutgoingMessage(
            sender=self.account.username,
            to=dialog.recipients(),
            cc=dialog.cc_recipients(),
            bcc=dialog.bcc_recipients(),
            subject=dialog.subject(),
            body=dialog.body(),
            attachments=dialog.attachments,
        )
        try:
            raw = build_email_message(message).as_bytes()
            self.mailbox.append_message(self.drafts_folder_name, raw, flags=(b"\\Draft",))
            if source_draft is not None and source_draft[0] == self.drafts_folder_name:
                # Правка уже существующего черновика — старую копию убираем,
                # иначе при каждом "Сохранить" в Черновиках копилось бы по
                # дубликату письма.
                self.mailbox.delete_messages(source_draft[0], [source_draft[1]])
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось сохранить черновик", str(exc))
            return
        self.statusBar().showMessage("Черновик сохранён", 5000)
        if self.active_source is self.mailbox and self.current_folder == self.drafts_folder_name:
            self.on_refresh()

    def _delete_draft(self, source_draft: tuple[str, int]) -> None:
        if self.mailbox is None:
            return
        try:
            self.mailbox.delete_messages(source_draft[0], [source_draft[1]])
        except Exception:
            pass  # письмо уже отправлено — то, что старая черновая копия не убралась, не критично

    def closeEvent(self, event) -> None:
        self.poll_timer.stop()
        for mailbox in self.mailboxes.values():
            mailbox.close()
        for archive in self.archives.values():
            archive.close()
        for temp_dir in self._temp_attachment_dirs:
            shutil.rmtree(temp_dir, ignore_errors=True)
        try:
            save_window_geometry(bytes(self.saveGeometry()))
            save_mail_columns_state(bytes(self.table.horizontalHeader().saveState()))
        except Exception:
            pass  # расположение окна/колонок не запомнится между запусками — не критично
        super().closeEvent(event)
