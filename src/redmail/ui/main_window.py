from __future__ import annotations

import base64
import json
from contextlib import contextmanager
import html
import math
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
import mimetypes
import re
import shutil
import tempfile
import threading
import zlib
from datetime import date, datetime, time, timedelta, timezone
from email import message_from_bytes
from email.policy import default as email_default_policy
from email.utils import getaddresses
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QDate,
    QDateTime,
    QEvent,
    QIODevice,
    QItemSelectionModel,
    QObject,
    QPointF,
    QRect,
    QRectF,
    QSignalBlocker,
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
    QFontDatabase,
    QCursor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCalendarWidget,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QCompleter,
    QDateEdit,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QDoubleSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QStyleOptionViewItem,
    QToolTip,
    QTableWidget,
    QTabWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from redmail import archive_store, branding, calendar_store, caldav_sync, contact_store, ews_client, itip
from redmail import keyboard_layout, mail_export, memory_report, profile_transfer
from redmail.ui.message_source import MessageSourceWindow
from redmail.applog import get_logger, log_dir, log_path, tail_text
from redmail.config_store import (
    MailRule,
    Signature,
    default_archive_storage_dir,
    load_accounts,
    load_archive_storage_dir,
    load_caldav_url,
    load_default_signature_id,
    load_ews_accounts,
    load_auto_archive_confirmed,
    delete_on_server_for,
    load_auto_archive_enabled,
    load_maintenance_window,
    load_others_reminder,
    load_calendar_compact,
    load_disabled_accounts,
    load_domain_rewrites,
    load_tls_ca_file,
    in_maintenance_window,
    load_auto_archive_size_mb,
    load_body_max_size_mb,
    load_font_scale,
    load_list_view_states,
    save_list_view_states,
    plugin_enabled,
    save_plugin_enabled,
    load_greeting_mode,
    greeting_text,
    save_greeting_mode,
    GREETING_HELLO,
    GREETING_LIST,
    GREETING_NONE,
    GREETING_TIME_OF_DAY,
    Greeting,
    greeting_choices,
    load_greetings,
    save_greetings,
    validate_greeting,
    load_profile_dir,
    load_contacts_view_mode,
    load_mail_view_mode,
    load_thread_grouping,
    load_mail_columns_state,
    load_mail_date_column_pinned,
    load_mail_rules,
    load_mail_splitters_state,
    load_open_archives,
    load_pane_orientation,
    load_poll_interval_minutes,
    load_signatures,
    load_theme,
    load_window_geometry,
    load_compose_geometry,
    merge_accounts,
    recover_legacy_account,
    merge_ews_accounts,
    save_archive_storage_dir,
    save_caldav_url,
    save_default_signature_id,
    save_auto_archive_confirmed,
    save_delete_on_server_accounts,
    set_delete_on_server,
    set_domain_rewrites,
    save_auto_archive_enabled,
    save_maintenance_window,
    save_others_reminder,
    save_calendar_compact,
    save_disabled_accounts,
    save_domain_rewrites_by_account,
    save_tls_ca_file,
    save_auto_archive_size_mb,
    save_body_max_size_mb,
    save_font_scale,
    save_profile_dir,
    save_contacts_view_mode,
    save_mail_view_mode,
    save_thread_grouping,
    save_mail_columns_state,
    save_mail_date_column_pinned,
    save_mail_rules,
    save_mail_splitters_state,
    save_open_archives,
    save_pane_orientation,
    save_poll_interval_minutes,
    save_signatures,
    save_theme,
    save_window_geometry,
    save_compose_geometry,
)
from redmail.ews_client import (
    EwsAccount,
    EwsConnectionError,
    EwsSession,
    excluded_shared_folders,
    is_shared_folder,
)
from redmail import ics_subscription, remote_images
from redmail.imap_client import (
    HTML_ONLY_PLACEHOLDER,
    Account,
    Attachment,
    FolderInfo,
    ImapSession,
    MessageContent,
    MessageSummary,
    extract_content,
    html_to_text,
    join_markers,
    split_markers,
)
from redmail.ipc_server import IpcServer
from redmail.mailbox import ArchiveSource, CachedMailbox
from redmail import (
    address_rules,
    autoarchive,
    calendar_mail,
    calendar_names,
    calendar_sync,
    ews_calendar,
    html_cleanup,
    profile,
    secret_store,
    sync_engine,
    tls_trust,
    voice_assistant,
)
from redmail.paths import app_dir
from redmail import plugins as mail_plugins
from redmail.plugins import categories as mail_categories
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
from redmail.ui import theme as app_theme
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
COL_CATEGORY = 5
COL_SUBJECT = 6
COL_DATE = 7
MAIL_COLUMN_COUNT = 8

# Колонки, по которым имеет смысл искать текстом — по ним же переключается
# фильтр, когда пользователь встаёт в соответствующую колонку/заголовок.
_FILTER_COLUMNS: dict[int, str] = {COL_SENDER: "От кого", COL_SUBJECT: "Тема", COL_DATE: "Дата"}


class _ThinCheckboxDelegate(QStyledItemDelegate):
    """Свой рисунок чекбокса отметки письма вместо нативного индикатора
    Fusion-стиля — у QSS (border: 0.5px) нет реального эффекта, дробная
    ширина рамки в стилях Qt округляется до целого пикселя (проверено
    эмпирически: border: 0.5px и border: 1px дают идентичный результат) —
    единственный способ нарисовать рамку тоньше 1px это самому рисовать
    линию через QPainter с сглаживанием (pen.setWidthF < 1) — только так
    получается по-настоящему более тонкая (не просто того же 1px) линия."""

    _SIZE = 13
    _PEN_WIDTH = 0.6

    def paint(self, painter, option, index) -> None:  # noqa: N802 - Qt override
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        # Через QModelIndex.data() значение может прийти как int (0/1/2), а
        # не как сам enum Qt.CheckState — сравнение по .value работает в
        # обоих случаях.
        state = index.data(Qt.ItemDataRole.CheckStateRole)
        checked = int(state) == Qt.CheckState.Checked.value if state is not None else False
        rect = option.rect
        size = self._SIZE
        square = QRectF(
            rect.center().x() - size / 2 + 0.5, rect.center().y() - size / 2 + 0.5, size, size
        )
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = option.palette.color(QPalette.ColorRole.Highlight)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        # На выделенной строке фон и так цвета акцента — отмеченный квадрат
        # сливался с ним (жалоба: "непонятно, что выбрал письмо галочкой,
        # цвета совпадают"): там квадрат рисуем цветом текста выделения, а
        # галочку внутри — акцентом.
        fill = option.palette.color(QPalette.ColorRole.HighlightedText) if selected else accent
        if checked:
            painter.setBrush(fill)
            painter.setPen(QPen(fill, self._PEN_WIDTH))
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            # Серый, а не палитровый Text (белый в тёмной теме/почти чёрный
            # в светлой — слишком контрастно на фоне и без того тонкой
            # линии) — светло-серый на тёмном фоне, тёмно-серый на светлом.
            # На тёмном фоне светлее прежнего (жалоба: "рамку почти не
            # видно, сделай на пару тонов ярче").
            border_color = fill if selected else (QColor("#a9adb3") if app_theme.is_dark() else QColor("#5f6368"))
            painter.setPen(QPen(border_color, self._PEN_WIDTH))
        painter.drawRoundedRect(square, 3, 3)
        if checked:
            # Галочка внутри квадрата — видна и на обычной, и на выделенной строке.
            mark_color = accent if selected else option.palette.color(QPalette.ColorRole.HighlightedText)
            painter.setPen(QPen(mark_color, 1.6))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            x0, y0, w, h = square.x(), square.y(), square.width(), square.height()
            path = QPainterPath()
            path.moveTo(x0 + w * 0.22, y0 + h * 0.52)
            path.lineTo(x0 + w * 0.43, y0 + h * 0.73)
            path.lineTo(x0 + w * 0.78, y0 + h * 0.30)
            painter.drawPath(path)
        painter.restore()

_FLAG_MARK = "⚑"
# Сколько последних писем папки показывать в таблице (полная локальная
# копия может быть на десятки тысяч писем; QTableWidget на таком объёме
# заметно тормозит).
MAX_LIST_ROWS = 3000

_ATTACHMENT_MARK = "\U0001F4CE"  # 📎 — по запросу именно скрепка
_REPLIED_MARK = "↩"  # ↩ — письмо, на которое уже отправлен ответ (флаг \Answered)


# Сколько ждать закрытия сетевых соединений при выходе из программы.
CLOSE_WAIT_SECONDS = 5


def _close_quietly(source) -> None:
    try:
        source.close()
    except Exception as exc:
        _log.warning("Закрытие соединения: %s", exc)


def _subject_display_text(summary: MessageSummary) -> str:
    return f"{_REPLIED_MARK} {summary.subject}" if summary.is_answered else summary.subject


_THREAD_COLLAPSED_MARK = "▸"
_THREAD_EXPANDED_MARK = "▾"
_THREAD_CHILD_INDENT = "      "
_THREAD_TOGGLE_WIDTH = 22  # px слева в ячейке темы / строке плитки, где клик сворачивает/раскрывает


@dataclass
class _ThreadInfo:
    """Место письма в цепочке по теме: ключ группы, головное ли оно
    (самое новое — оно и остаётся видимым в свёрнутой цепочке), сколько
    писем в цепочке, uid головного."""

    key: str
    is_head: bool
    count: int
    head_uid: int


def _thread_infos(summaries: list[MessageSummary]) -> dict[int, _ThreadInfo]:
    """Группировка списка по нормализованной теме (без Re:/Fwd:). Головное
    письмо — самое новое по дате (при равенстве — с большим uid). Письма
    без темы не группируются (пожелание: "надо скрывать более ранние
    письма и показывать символ группировки типа раскрывающегося списка")."""
    groups: dict[str, list[MessageSummary]] = {}
    for summary in summaries:
        key = _normalize_subject(summary.subject or "").casefold()
        if not key:
            continue
        groups.setdefault(key, []).append(summary)
    infos: dict[int, _ThreadInfo] = {}
    for key, members in groups.items():
        head = max(members, key=lambda s: (s.date, s.uid))
        for summary in members:
            infos[summary.uid] = _ThreadInfo(key, summary is head, len(members), head.uid)
    return infos


def _thread_subject_text(summary: MessageSummary, info: _ThreadInfo | None, expanded: bool) -> str:
    text = _subject_display_text(summary)
    if info is None or info.count < 2:
        return text
    if info.is_head:
        mark = _THREAD_EXPANDED_MARK if expanded else _THREAD_COLLAPSED_MARK
        return f"{mark} {text} ({info.count})"
    return _THREAD_CHILD_INDENT + text


class _ThreadSortItem(QTableWidgetItem):
    """Ячейка списка писем, при сортировке держащая письма цепочки вместе
    (пожелание: "все письма с одной темой выстраиваются рядом; сортировка
    по теме и дате работает только с верхним уровнем, а внутри цепочки —
    всегда по дате на убывание"): сначала сравнивается значение головного
    письма цепочки (для всех её писем одно), затем ключ цепочки, затем
    головное письмо ставится первым в любом направлении сортировки, а
    дочерние — по дате, новые выше, тоже независимо от направления."""

    def __init__(self, text: str, *, group_value: str, group_key: str, rank: int, own: str, date: str = "") -> None:
        super().__init__(text)
        self.group_value = group_value
        self.group_key = group_key
        self.rank = rank
        self.own = own
        self.date = date

    def _descending(self) -> bool:
        table = self.tableWidget()
        return table is not None and table.horizontalHeader().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder

    def __lt__(self, other) -> bool:  # noqa: D105 - Qt sort hook
        if not isinstance(other, _ThreadSortItem):
            return super().__lt__(other)
        if self.group_value != other.group_value:
            return self.group_value < other.group_value
        if self.group_key != other.group_key:
            return self.group_key < other.group_key
        if self.rank != other.rank:
            return self.rank > other.rank if self._descending() else self.rank < other.rank
        if self.rank == 1 and self.group_key:
            # Дочерние письма одной цепочки: новые выше при любом направлении.
            return self.date < other.date if self._descending() else self.date > other.date
        if self.own == other.own and self.date != other.date:
            # Одинаковые категория, отправитель или тема: новые выше.
            return self.date < other.date if self._descending() else self.date > other.date
        return self.own < other.own


_SORTABLE_COLUMNS = (COL_SENDER, COL_SUBJECT, COL_DATE, COL_CATEGORY)


def _category_sort_key(name: str) -> str:
    """Письма без категории — в конце списка при сортировке по возрастанию."""
    return name.casefold() if name else "\uffff"

# Gmail заворачивает Отправленные/Корзину и т.п. в служебный контейнер
# "[Gmail]" — сам по себе не открывается (см. \Noselect в list_folders),
# но его имя остаётся частью названий дочерних папок. В дереве эта
# служебная обёртка не нужна — реальную иерархию задаёт узел учётной записи.
_HIDDEN_PATH_SEGMENTS = {"[Gmail]", "[Google Mail]"}

# Протокольное имя папки остаётся "INBOX" (это то, что уходит в IMAP-команды);
# по-русски она подписывается иначе только в дереве папок.
_DISPLAY_NAMES: dict[str, str] = {"INBOX": "Входящие"}

# Название папки в дереве без суффикса "(N непрочитанных)" — хранится
# отдельным data-слотом, чтобы при каждом обновлении счётчика не пытаться
# распарсить/отрезать предыдущий суффикс из текста элемента.
_FOLDER_BASE_LABEL_ROLE = Qt.ItemDataRole.UserRole + 1

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


def _mail_rule_moves(summaries: list[MessageSummary], rules: list[MailRule]) -> dict[str, list[int]]:
    """Какие письма куда переехали бы по правилам сортировки: целевая
    папка → список UID. Вынесено из MainWindow.on_apply_mail_rules отдельной
    чистой функцией, потому что тем же подбором пользуется команда
    apply_mail_rules локального канала управления (см. ipc_server.py) — и
    заодно её можно проверить тестом без единого виджета."""
    moves: dict[str, list[int]] = {}
    for summary in summaries:
        for rule in rules:
            haystack = summary.sender_email if rule.field == "from" else summary.subject
            if rule.contains.lower() in (haystack or "").lower():
                moves.setdefault(rule.target_folder, []).append(summary.uid)
                break  # первое подходящее правило — не проверяем остальные для этого письма
    return moves


# Сколько последних писем цепочки показываем подряд при последовательном
# просмотре — без ограничения на живом IMAP каждое письмо цепочки (кроме
# текущего) это отдельный сетевой запрос при первом открытии, а цепочка
# вопрос-ответ в рабочей переписке легко насчитывает десятки писем.
_THREAD_DEPTH_LIMIT = 8
# Сколько писем цепочки держать в памяти. Кэш нужен только для
# предпросмотра соседних писем, поэтому вложения и встроенные картинки из
# него выбрасываются: письмо с десятком вложений по 5 МБ раздувало память
# программы на сотни мегабайт (жалоба: "почему так много памяти съедает?").
_THREAD_CACHE_LIMIT = 12


def _lightweight_content(content: MessageContent) -> MessageContent:
    """Копия письма без тяжёлых данных — для кэша цепочки."""
    if not content.attachments and not content.inline_images:
        return content
    return replace(content, attachments=[], inline_images={})


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


_CID_IMG_SRC_RE = re.compile(r'(\bsrc\s*=\s*["\'])cid:([^"\']+)(["\'])', re.IGNORECASE)

_mail_web_profile: QWebEngineProfile | None = None

# Пути временных html-файлов писем, которым разрешено грузиться как
# file:// (см. _MailRequestInterceptor и _render_mail_html). Без этой
# проверки письмо с <img src="file:///home/user/..."> или скрытым
# <iframe src="file://...">, отрисованное через file://, могло бы читать
# и показывать ЛЮБОЙ другой файл, доступный пользователю системы — file://
# в Chromium имеет доступ к другим file://-ресурсам без ограничений
# same-origin, а acceptNavigationRequest эту проверку не покрывает вовсе
# (он видит только навигацию по фреймам, не подгрузку картинок/скриптов
# как подресурсов).
_allowed_local_html_paths: set[str] = set()


class _MailRequestInterceptor(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:  # noqa: N802
        url = info.requestUrl()
        if url.scheme() == "file" and url.toLocalFile() not in _allowed_local_html_paths:
            info.block(True)


def _get_mail_web_profile() -> QWebEngineProfile:
    """Общий профиль для всех окон чтения писем (основная панель +
    "Открыть в окне") — жёстко заблокирован под просмотр чужого HTML:
    без JavaScript (в письме ему взяться неоткуда для добросовестной цели,
    зато это первый вектор эксплойтов/трекинга), без localStorage и без
    постоянных cookie (только диск-кэш самих картинок — тот же профиль
    между письмами даёт настоящее кэширование HTTP, а не самодельное, как
    было до перехода на QWebEngineView). Именованный (не off-the-record)
    профиль специально — иначе Qt не включает дисковый кэш вовсе."""
    global _mail_web_profile
    if _mail_web_profile is None:
        profile = QWebEngineProfile("redmail_html_render")
        cache_dir = app_dir() / "webengine_cache"
        profile.setCachePath(str(cache_dir))
        profile.setPersistentStoragePath(str(cache_dir / "storage"))
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        # NoPersistentCookies не сохраняет их на диск, но всё равно разрешает
        # cookie в памяти — рекламная сеть может так связать открытие ДВУХ
        # РАЗНЫХ писем в одном сеансе программы через один и тот же
        # tracking-пиксель. Своим ящиком пользователь уже согласился на
        # автозагрузку внешних картинок без баннера подтверждения, но cookie
        # для этого не нужны вовсе — блокируем полностью, а не просто не
        # сохраняем.
        profile.cookieStore().setCookieFilter(lambda _request: False)
        interceptor = _MailRequestInterceptor(profile)
        profile.setUrlRequestInterceptor(interceptor)
        profile._redmail_interceptor = interceptor  # держим ссылку — иначе Python соберёт объект
        settings = profile.settings()
        for attribute in (
            QWebEngineSettings.WebAttribute.JavascriptEnabled,
            QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard,
            QWebEngineSettings.WebAttribute.LocalStorageEnabled,
            QWebEngineSettings.WebAttribute.PluginsEnabled,
            QWebEngineSettings.WebAttribute.AllowRunningInsecureContent,
            QWebEngineSettings.WebAttribute.AllowWindowActivationFromJavaScript,
            QWebEngineSettings.WebAttribute.FullScreenSupportEnabled,
        ):
            settings.setAttribute(attribute, False)
        # Тело письма грузится с file:// (см. _render_mail_html) — Chromium
        # по умолчанию НЕ разрешает содержимому с file:// обращаться к
        # удалённым http(s)-адресам вовсе (защита от локальных файлов,
        # ворующих данные по сети), из-за чего внешние картинки в письме
        # молча не грузились (жалоба: "картинки не подгружаются"). Явно
        # включаем — доступ к file:// ДРУГИХ файлов при этом всё равно
        # закрыт отдельно, через _MailRequestInterceptor.
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        _mail_web_profile = profile
    return _mail_web_profile


class _MailWebPage(QWebEnginePage):
    """Клик по ссылке в письме открывает системный браузер, а не уводит
    саму панель чтения на чужой сайт (та осталась бы без кнопок
    ответить/переслать и без нашего профиля с выключенным JS) — это же
    предотвращает и programmatic-навигацию, если бы JS всё-таки был
    включён. Всплывающие окна (createWindow) тоже блокируются — письму
    незачем их открывать."""

    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame: bool) -> bool:  # noqa: N802
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            QDesktopServices.openUrl(url)
            return False
        return True

    def createWindow(self, _web_window_type):  # noqa: N802 - Qt override
        return None


def _create_mail_browser(parent: QWidget) -> QWebEngineView:
    view = QWebEngineView(parent)
    view.setPage(_MailWebPage(_get_mail_web_profile(), view))
    # Жалоба: "масштаб в правом нижнем углу не работает" — слайдер
    # масштаба (_apply_font_scale) двигает только QApplication.font(),
    # которого движок Chromium внутри QWebEngineView вообще не видит (у
    # него собственный, полностью отдельный стек рендеринга/шрифтов) — то
    # есть текст самого письма, самое заметное место в интерфейсе, никак
    # не реагировал. setZoomFactor — уже собственный, chromium-овый способ
    # масштабирования, применяем сохранённое значение сразу при создании
    # (для окна "Открыть в письмо в окне" оно больше нигде не обновляется).
    view.setZoomFactor(load_font_scale())
    return view


_IMAGE_EXTENSIONS = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".bmp": "image/bmp", ".webp": "image/webp",
}


def _inline_images_to_data_uris(
    html_content: str, inline_images: dict[str, tuple[str, bytes]], attachments=None,
) -> str:
    """Заменяет <img src="cid:xxx"> на data:-URI прямо в разметке.

    QWebEngineView не понимает QTextDocument.addResource() (это API
    рич-текстового движка, у веб-движка нет такого понятия "ресурс
    документа") — вместо реестра ресурсов картинку нужно встроить прямо в
    HTML как data:-URI, единственный способ показать её без реального
    HTTP-сервера, отдающего cid:-ссылки.

    Ссылка ищется не только точным совпадением: в письмах из Outlook и
    Exchange адрес бывает закодирован (%40 вместо @), в другом регистре, а
    картинка — без Content-Id и с типом application/octet-stream, только с
    именем «image001.png». Раньше такие подписи и логотипы показывались
    пустой рамкой (жалоба: "часть содержимого писем не отображается")."""
    by_key: dict[str, tuple[str, bytes]] = {}

    def remember(key: str, entry: tuple[str, bytes]) -> None:
        key = (key or "").strip().strip("<>").casefold()
        if key and key not in by_key:
            by_key[key] = entry

    for content_id, entry in (inline_images or {}).items():
        remember(content_id, entry)
        remember(content_id.split("@", 1)[0], entry)
    for attachment in attachments or []:
        filename = getattr(attachment, "filename", "") or ""
        content_type = (getattr(attachment, "content_type", "") or "").lower()
        extension = os.path.splitext(filename)[1].lower()
        if not content_type.startswith("image/") and extension not in _IMAGE_EXTENSIONS:
            continue
        if not content_type.startswith("image/"):
            content_type = _IMAGE_EXTENSIONS[extension]
        remember(filename, (content_type, getattr(attachment, "payload", b"") or b""))
    if not by_key:
        return html_content

    def lookup(content_id: str):
        from urllib.parse import unquote

        raw = unquote(content_id).strip().strip("<>").casefold()
        return by_key.get(raw) or by_key.get(raw.split("@", 1)[0])

    def replace(match: re.Match[str]) -> str:
        prefix, content_id, suffix = match.group(1), match.group(2), match.group(3)
        entry = lookup(content_id)
        if entry is None:
            return match.group(0)
        content_type, payload = entry
        encoded = base64.b64encode(payload).decode("ascii")
        return f"{prefix}data:{content_type};base64,{encoded}{suffix}"

    return _CID_IMG_SRC_RE.sub(replace, html_content)


def _render_mail_html(view: QWebEngineView, html_content: str, *, anchor: str | None = None) -> None:
    """Показывает готовый HTML в панели чтения через временный файл, а не
    view.setHtml() напрямую — у setHtml() задокументированный потолок
    размера около 2 МБ (для писем со встроенными через data:-URI
    картинками легко превышается), и он не умеет прокрутку к якорю.
    Локальный файл снимает оба ограничения: обычная навигация браузера,
    включая "#msg-N" во фрагменте URL, работает без единой строчки
    JavaScript (JS в этом профиле выключен нарочно, см.
    _get_mail_web_profile)."""
    fd, path_str = tempfile.mkstemp(suffix=".html", prefix="redmail_body_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        # Файл пишется в UTF-8 — объявление другой кодировки внутри письма
        # (Qt при локали KOI8-R пишет charset=koi8-r) превращало текст в
        # кракозябры.
        f.write(html_cleanup.force_utf8_charset(html_content))
    path = Path(path_str)
    url = QUrl.fromLocalFile(str(path))
    # В белый список для _MailRequestInterceptor кладём то же значение,
    # которое сам Chromium вернёт из url.toLocalFile() при проверке запроса
    # — иначе строковое несовпадение (нормализация пути при round-trip
    # через QUrl) заблокировало бы и наш ЖЕ собственный документ.
    canonical_path = url.toLocalFile()
    _allowed_local_html_paths.add(canonical_path)

    old_path_str = view.property("_redmail_temp_html_path")
    view.setProperty("_redmail_temp_html_path", str(path))
    if old_path_str:
        old_path = Path(old_path_str)
        _allowed_local_html_paths.discard(QUrl.fromLocalFile(str(old_path)).toLocalFile())
        if old_path.exists():
            try:
                old_path.unlink()
            except OSError:
                pass  # уже мог быть удалён/занят — не критично, это временный файл

    if anchor:
        url.setFragment(anchor)
    view.load(url)


_BODY_WRAP_TEMPLATE = (
    '<html><head><meta charset="utf-8"></head>'
    '<body style="background:#ffffff;color:#202124;margin:14px 12px;'
    'font-family:sans-serif;">{content}</body></html>'
)


def _populate_body_browser(view: QWebEngineView, content: MessageContent, *, anchor: str | None = None) -> None:
    """HTML-письма показываем как есть (cid:-вложения превращены в
    data:-URI, см. _inline_images_to_data_uris — без этого <img
    src="cid:..."> не отрисуется); письма с обычным текстом — тоже как
    HTML, но экранированным и с активными ссылками (_linkify), чтобы
    голые http(s)-ссылки в теле письма были кликабельны, как и в
    HTML-версии. Внешние (не cid:) картинки веб-движок загружает сам, как
    обычный браузер — отдельная догрузка/кэш здесь не нужны. Общая для
    MainWindow.reading_pane и MessageWindow — открытие письма в отдельном
    окне должно выглядеть так же, как в основной панели чтения."""
    if content.html:
        html_content = _inline_images_to_data_uris(content.html, content.inline_images, content.attachments)
    else:
        html_content = _BODY_WRAP_TEMPLATE.format(content=_linkify(content.text))
    _render_mail_html(view, html_content, anchor=anchor)


def is_eml_attachment(filename: str, content_type: str = "") -> bool:
    """Вложение — само письмо? Такие открываем своим окном, а не отдаём
    системе: в RED OS для .eml обычно нет зарегистрированной программы, и
    двойной щелчок раньше просто ничего не делал."""
    if (content_type or "").split(";", 1)[0].strip().lower() == "message/rfc822":
        return True
    return Path(filename or "").suffix.lower() in (".eml", ".mht")


class EmlAttachmentWindow(QWidget):
    """Вложенное письмо (.eml) — читается прямо в программе: заголовки,
    тело и собственные вложения, включая новые письма внутри."""

    def __init__(self, raw: bytes, filename: str, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(760, 560)
        self._raw = raw
        self._temp_dirs: list[Path] = []
        date_text = ""
        try:
            message = message_from_bytes(raw, policy=email_default_policy)
            content = extract_content(message)
            # Дата письма в MessageContent не попадает (там только заголовки
            # для разбора рассылок) — берём из самого письма, иначе в шапке
            # вложенного письма даты не было бы вовсе.
            date_text = " ".join(str(message.get("Date", "")).split())
        except Exception as exc:
            _log.warning("Вложенное письмо %s не разобрано: %s", filename, exc)
            content = MessageContent(text=f"Письмо не удалось разобрать: {exc}")
        self._content = content
        self.setWindowTitle(content.subject or filename or "Вложенное письмо")

        header_label = QLabel(
            _build_message_header_html(
                content.subject or filename or "(без темы)", content.from_, content.to, content.cc, date_text
            ),
            self,
        )
        header_label.setWordWrap(True)
        header_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        source_button = QPushButton("Исходный текст", self)
        source_button.clicked.connect(self._show_source)
        save_button = QPushButton("Сохранить как…", self)
        save_button.clicked.connect(self._save_as)
        buttons = QHBoxLayout()
        buttons.addWidget(source_button)
        buttons.addWidget(save_button)
        buttons.addStretch(1)

        body = _create_mail_browser(self)
        _populate_body_browser(body, content)

        self.attachments_list = QListWidget(self)
        self.attachments_list.setMaximumHeight(90)
        for attachment in content.attachments:
            self.attachments_list.addItem(f"{attachment.filename} ({_format_size(attachment.size)})")
        self.attachments_list.setVisible(bool(content.attachments))
        self.attachments_list.itemDoubleClicked.connect(self._open_attachment)

        layout = QVBoxLayout(self)
        layout.addWidget(header_label)
        layout.addLayout(buttons)
        layout.addWidget(body)
        layout.addWidget(self.attachments_list)

    def _show_source(self) -> None:
        window = MessageSourceWindow(self.windowTitle(), self._raw, original=True, parent=self)
        window.show()

    def _save_as(self) -> None:
        name = _safe_attachment_filename(f"{self._content.subject or 'письмо'}.eml")
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить письмо", name, "Письмо (*.eml)")
        if not path:
            return
        try:
            Path(path).write_bytes(self._raw)
        except OSError as exc:
            QMessageBox.critical(self, "Не удалось сохранить", str(exc))

    def _open_attachment(self, item: QListWidgetItem) -> None:
        attachment = self._content.attachments[self.attachments_list.row(item)]
        open_attachment_payload(self, attachment)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        for temp_dir in self._temp_dirs:
            shutil.rmtree(temp_dir, ignore_errors=True)
        super().closeEvent(event)


def open_attachment_payload(parent: QWidget, attachment: Attachment) -> None:
    """Открыть вложение: письмо — своим окном, остальное — программой,
    которую ОС назначила этому типу файла."""
    if is_eml_attachment(attachment.filename, getattr(attachment, "content_type", "")):
        window = EmlAttachmentWindow(attachment.payload, attachment.filename, parent)
        window.show()
        return
    try:
        temp_dir = Path(tempfile.mkdtemp(prefix="redmail_"))
        temp_path = temp_dir / _safe_attachment_filename(attachment.filename)
        temp_path.write_bytes(attachment.payload)
    except OSError as exc:
        QMessageBox.critical(parent, "Не удалось открыть вложение", str(exc))
        return
    holder = getattr(parent, "_temp_attachment_dirs", None)
    if holder is None:
        holder = getattr(parent, "_temp_dirs", None)
    if isinstance(holder, list):
        holder.append(temp_dir)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(temp_path)))


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
        header_label = QLabel(
            _build_message_header_html(
                content.subject or summary.subject or "(без темы)", sender, content.to, content.cc, summary.date
            ),
            self,
        )
        header_label.setWordWrap(True)
        header_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        header_label.linkActivated.connect(
            lambda href: _show_full_recipient_list(self, "Кому" if href == "recipients:to" else "Копия",
                                                    content.to if href == "recipients:to" else content.cc)
            if href in ("recipients:to", "recipients:cc") else None
        )
        header_label.linkHovered.connect(
            lambda href: QToolTip.showText(
                QCursor.pos(), _full_recipient_list_text(content.to if href == "recipients:to" else content.cc), header_label
            )
            if href in ("recipients:to", "recipients:cc") else QToolTip.hideText()
        )
        header_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        # Раньше здесь не было ни ответить, ни переслать вовсе — окно было
        # только для чтения (жалоба: "при открытии письма в отдельном окне
        # нет кнопок ответить, переслать"). Оба действия зовут обратно в
        # MainWindow (_start_reply/_start_forward) с ЭТИМ summary/content
        # явно, а не через self.selected_summary — письмо в этом окне
        # может быть вовсе не тем, что сейчас выделено в основном списке.
        reply_button = QPushButton("Ответить", self)
        reply_button.clicked.connect(self._on_reply)
        reply_all_button = QPushButton("Ответить всем", self)
        reply_all_button.clicked.connect(self._on_reply_all)
        forward_button = QPushButton("Переслать", self)
        forward_button.clicked.connect(self._on_forward)
        button_row = QHBoxLayout()
        button_row.addWidget(reply_button)
        button_row.addWidget(reply_all_button)
        button_row.addWidget(forward_button)
        button_row.addStretch(1)

        body = _create_mail_browser(self)
        _populate_body_browser(body, content)

        layout = QVBoxLayout(self)
        layout.addWidget(header_label)
        layout.addLayout(button_row)
        layout.addWidget(body)

    def _on_reply(self) -> None:
        main_window = self.parent()
        if main_window is not None:
            main_window._start_reply(self._summary, self._content.text)

    def _on_reply_all(self) -> None:
        main_window = self.parent()
        if main_window is not None:
            main_window._start_reply(self._summary, self._content.text, content=self._content, reply_all=True)

    def _on_forward(self) -> None:
        main_window = self.parent()
        if main_window is not None:
            main_window._start_forward(self._summary, self._content)


def _linkify(text: str) -> str:
    """HTML-экранирует текст и оборачивает http(s)-ссылки в <a href>, чтобы
    их можно было открыть кликом в QTextBrowser.

    Метки картинок «[cid:image001.png@…]», которые Outlook вставляет в
    текстовую версию письма вместо изображений, убираются — в ленте цепочки
    они выглядели обрывками служебного текста."""
    text = re.sub(r"\[cid:[^\]\s]+\]", "", text or "")
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


def _truncate_recipient_field(value: str, max_items: int = 3) -> tuple[str, int]:
    """(сокращённый HTML-список, число скрытых адресов) — не более
    max_items, остальные разворачиваются по клику на "и ещё N" (жалоба:
    "если много адресатов, поле растягивается на всю ширину" — реальные
    рассылки на полсотни человек делали заголовок письма выше самого
    письма)."""
    pairs = [(name, addr) for name, addr in getaddresses([value]) if addr]
    if len(pairs) <= max_items:
        return html.escape(value), 0
    shown_text = ", ".join(html.escape(_format_recipient_candidate(name, addr)) for name, addr in pairs[:max_items])
    return shown_text, len(pairs) - max_items


def _html_to_preview_text(html_content: str, limit: int = 1200) -> str:
    """Короткий текст из HTML для предпросмотра в цепочке."""
    return html_to_text(html_content, limit=limit)


def _content_preview_text(content: MessageContent, limit: int = 1200) -> str:
    """Текст письма для цепочки: текстовая часть, а если её нет или в кэше
    лежит старая заглушка — текст из HTML."""
    text = (content.text or "").strip()
    if text and text != HTML_ONLY_PLACEHOLDER:
        return text[:limit] + ("…" if len(text) > limit else "")
    return html_to_text(content.html, limit=limit)


def _build_message_header_html(subject: str, sender: str, to: str, cc: str, date: str) -> str:
    lines = [f"<b>Тема:</b> {html.escape(subject)}", f"<b>От:</b> {html.escape(sender)}"]
    if to:
        shown, hidden = _truncate_recipient_field(to)
        suffix = f' <a href="recipients:to">и ещё {hidden}…</a>' if hidden else ""
        lines.append(f"<b>Кому:</b> {shown}{suffix}")
    if cc:
        shown, hidden = _truncate_recipient_field(cc)
        suffix = f' <a href="recipients:cc">и ещё {hidden}…</a>' if hidden else ""
        lines.append(f"<b>Копия:</b> {shown}{suffix}")
    if date:
        lines.append(f"<b>Дата:</b> {html.escape(date)}")
    return "<br>".join(lines)


def _full_recipient_list_text(value: str) -> str:
    pairs = [(name, addr) for name, addr in getaddresses([value]) if addr]
    return "\n".join(_format_recipient_candidate(name, addr) for name, addr in pairs)


def _show_full_recipient_list(parent: QWidget, title: str, value: str) -> None:
    QMessageBox.information(parent, title, _full_recipient_list_text(value))


def _mark_primary(button: QPushButton) -> None:
    """Визуально выделяет ГЛАВНОЕ действие диалога (Отправить/Сохранить и
    т.п.) акцентным цветом — вместо того, чтобы все кнопки в ряду выглядели
    одинаково значимыми (компонент "Primary Button" дизайн-системы: одно
    чёткое основное действие, остальные — второстепенные)."""
    button.setProperty("primary", True)
    button.style().unpolish(button)
    button.style().polish(button)


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
    """Варианты для автодополнения и окна выбора. Человек — один адрес
    («Имя <адрес>»); группа — короткая ссылка «[Имя группы]»: в поле
    «Кому» остаётся читаемое имя, а адреса участников подставляются при
    отправке (_parse_recipient_list с адресной книгой). Жалоба: "выбрал
    группу — подставились адреса, просмотреть их невозможно"."""
    candidates = []
    for contact in contacts:
        if contact.is_group:
            if contact.emails:
                candidates.append(_format_group_candidate(contact.display_name))
        elif contact.emails:
            candidates.append(_format_recipient_candidate(contact.display_name, contact.emails[0]))
    return candidates


_GROUP_TOKEN_RE = re.compile(r"\[([^\[\]]+)\]")


def _format_group_candidate(name: str) -> str:
    safe_name = name.replace("[", "(").replace("]", ")").replace(",", " ").strip() or "Группа"
    return f"[{safe_name}]"


def _find_group(contacts: list[contact_store.Contact] | None, name: str) -> contact_store.Contact | None:
    needle = name.strip().casefold()
    for contact in contacts or ():
        if contact.is_group and _format_group_candidate(contact.display_name).strip("[]").casefold() == needle:
            return contact
    return None


def _split_recipient_text(text: str) -> tuple[list[str], str]:
    """(имена групп из ссылок «[…]», остальной текст без них)."""
    groups = [m.group(1) for m in _GROUP_TOKEN_RE.finditer(text)]
    rest = _GROUP_TOKEN_RE.sub("", text)
    return groups, rest


def _expand_recipients(text: str, contacts: list[contact_store.Contact] | None) -> list[str]:
    """Адреса из поля: ссылки на группы раскрываются по адресной книге,
    остальное — обычный разбор списка адресов. Порядок и уникальность
    сохраняются."""
    groups, rest = _split_recipient_text(text)
    result: list[str] = []
    for name in groups:
        group = _find_group(contacts, name)
        for addr in (group.emails if group is not None else []):
            if addr not in result:
                result.append(addr)
    for _name, addr in getaddresses([rest]):
        if addr and "@" in addr and addr not in result:
            result.append(addr)
    return result


def _contact_name_for(email: str, contacts: list[contact_store.Contact] | None) -> str:
    """Имя из адресной книги по адресу («» — нет такого контакта)."""
    needle = email.strip().casefold()
    for contact in contacts or ():
        if not contact.is_group and any(addr.casefold() == needle for addr in contact.emails):
            return contact.display_name
    return ""


def _recipient_entry(email: str, contacts: list[contact_store.Contact] | None, name: str = "") -> str:
    """"Фамилия Имя Отчество <email>" для поля адресатов — имя из книги,
    если не задано: голый email в поле нечитаем (жалоба: "добавляется не
    ФИО, а email — непонятно, кого добавили")."""
    return _format_recipient_candidate(name or _contact_name_for(email, contacts), email)


def _recipients_tooltip(text: str, contacts: list[contact_store.Contact] | None, limit: int = 40) -> str:
    """Подсказка к полю адресатов: все адреса, в которые раскроется поле,
    с именами из книги («Фамилия Имя Отчество — email»)."""
    addrs = _expand_recipients(text, contacts)
    if not addrs:
        return ""
    lines = []
    for addr in addrs[:limit]:
        name = _contact_name_for(addr, contacts)
        lines.append(f"{name} — {addr}" if name else addr)
    shown = "\n".join(lines)
    if len(addrs) > limit:
        shown += f"\n… и ещё {len(addrs) - limit}"
    return f"Адресатов: {len(addrs)}\n{shown}"


def _install_recipient_tooltip(line_edit: QLineEdit, contacts: list[contact_store.Contact] | None) -> None:
    def update(text: str) -> None:
        line_edit.setToolTip(_recipients_tooltip(text, contacts))

    line_edit.textChanged.connect(update)
    update(line_edit.text())


class RecipientListView(QListWidget):
    """Читаемый список адресатов под полем «Кому»/«Участники»: одна строка
    на человека — «Фамилия Имя Отчество — email» (группы раскрываются по
    книге). Само поле остаётся источником данных, список лишь отражает его
    содержимое и даёт убрать адресата клавишей Delete или из контекстного
    меню. Жалоба: "всё в одну строку — непонятно, кого добавили"."""

    def __init__(self, line_edit: QLineEdit, contacts: list[contact_store.Contact] | None, parent=None):
        super().__init__(parent)
        self._line_edit = line_edit
        self._contacts = contacts or []
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setIconSize(QSize(24, 24))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self.setToolTip("Выбранные адресаты. Delete или правая кнопка — убрать.")
        line_edit.textChanged.connect(self._refresh)
        self._refresh(line_edit.text())

    def _refresh(self, _text: str) -> None:
        self.clear()
        addrs = _expand_recipients(self._line_edit.text(), self._contacts)
        typed_names = {addr.casefold(): name for name, addr in getaddresses([self._line_edit.text()]) if addr}
        by_email = {
            (contact.emails[0].casefold() if contact.emails else ""): contact
            for contact in self._contacts if not contact.is_group and contact.emails
        }
        for addr in addrs:
            contact = by_email.get(addr.casefold())
            name = _contact_name_for(addr, self._contacts) or typed_names.get(addr.casefold(), "")
            item = QListWidgetItem(f"{name} — {addr}" if name else addr)
            item.setData(Qt.ItemDataRole.UserRole, addr)
            # Фотография адресата из книги (пожелание: "нет фото у получателей").
            item.setIcon(_contact_avatar(contact, 24) if contact is not None
                         else QIcon(_avatar_pixmap(_message_initials(name or addr), _avatar_color(addr), 24)))
            if contact is not None and (contact.title or contact.department):
                item.setToolTip("\n".join(p for p in (name, contact.title, contact.department, addr) if p))
            self.addItem(item)
        rows = len(addrs)
        self.setVisible(rows > 0)
        row_height = max(self.sizeHintForRow(0), 24) if rows else 0
        # Минимум — до трёх строк, дальше список растягивается вместе с окном
        # (пожелание: "растягивай список получателей и текст письма").
        self.setMinimumHeight(min(rows, 3) * row_height + 6 if rows else 0)
        self.setMaximumHeight(16777215 if rows else 0)

    def _remove_selected(self) -> None:
        drop = {item.data(Qt.ItemDataRole.UserRole).casefold() for item in self.selectedItems()}
        if not drop:
            return
        groups, rest = _split_recipient_text(self._line_edit.text())
        kept = [
            _format_recipient_candidate(name, addr)
            for name, addr in getaddresses([rest])
            if addr and "@" in addr and addr.casefold() not in drop
        ]
        # Ссылки на группы оставляем как есть: убрать одного человека из
        # группы через этот список нельзя, только всю группу — из поля.
        group_tokens = [f"[{name}]" for name in groups]
        self._line_edit.setText(", ".join(group_tokens + kept))

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._remove_selected()
            return
        super().keyPressEvent(event)

    def _context_menu(self, pos) -> None:
        if self.itemAt(pos) is None:
            return
        menu = QMenu(self)
        remove_action = menu.addAction("Убрать")
        if menu.exec(self.mapToGlobal(pos)) is remove_action:
            self._remove_selected()


def _parse_recipient_list(text: str, contacts: list[contact_store.Contact] | None = None) -> list[str]:
    """Достаёт голые адреса из поля через запятую — элементы могут быть как
    просто email, так и "Имя <email>" (так автодополнение по контактам
    вставляет выбранный вариант), так и ссылка на группу «[Имя группы]»
    (раскрывается по адресной книге contacts). getaddresses() (не
    parseaddr — тот не умеет список) разбирает полноценный список адресов
    сразу, включая случай, когда имя в кавычках само содержит запятую (см.
    _contact_candidates/_format_recipient_candidate) — раньше text.split(",") резал такое
    имя пополам, и второй адрес в списке переставал распознаваться."""
    if "[" in text:
        return _expand_recipients(text, contacts)
    return [addr for _name, addr in getaddresses([text]) if addr]


def _recipient_search_prefix(prefix: str, candidates: list[str]) -> str:
    """Строка поиска адресата. Набрали не в той раскладке («bdfyjd») и в
    адресной книге ничего нет, а в другой раскладке («иванов») есть —
    ищем по ней; выбранный адрес заменит набранное."""
    folded = prefix.casefold()
    if any(folded in candidate.casefold() for candidate in candidates):
        return prefix
    for variant in keyboard_layout.alternatives(prefix):
        if any(variant.casefold() in candidate.casefold() for candidate in candidates):
            return variant
    return prefix


def fix_search_layout(line_edit: QLineEdit, matches) -> str:
    """Строка поиска, набранная не в той раскладке: «bdfyjd» → «иванов».

    Если по набранному не находится ничего, а в другой раскладке находится,
    исправляем текст прямо в поле — человек видит, по чему на самом деле
    идёт поиск, и может продолжать печатать уже правильно. Возвращает
    строку, по которой надо фильтровать."""
    typed = line_edit.text()
    if not typed.strip() or matches(typed):
        return typed
    for variant in keyboard_layout.alternatives(typed):
        if matches(variant):
            blocker = QSignalBlocker(line_edit)
            try:
                line_edit.setText(variant)
                line_edit.setCursorPosition(len(variant))
            finally:
                del blocker
            return variant
    return typed


def switch_layout_in_widget(widget) -> bool:
    """Выделенный текст — или слово перед курсором — в другую раскладку.
    Работает в теле письма (QTextEdit) и в однострочных полях (тема)."""
    if isinstance(widget, QTextEdit):
        cursor = widget.textCursor()
        if not cursor.hasSelection():
            # Слово перед курсором — до пробела, а не до знака препинания:
            # «ж», «э», «б», «ю» в английской раскладке — это ; ' , .
            block_text = cursor.block().text()
            start = cursor.positionInBlock()
            while start > 0 and not block_text[start - 1].isspace():
                start -= 1
            cursor.setPosition(cursor.block().position() + start, QTextCursor.MoveMode.KeepAnchor)
        selected = cursor.selectedText()
        if not selected.strip():
            return False
        start, end = sorted((cursor.anchor(), cursor.position()))
        cursor.insertText(keyboard_layout.switch_layout(selected))
        cursor.setPosition(start)
        cursor.setPosition(start + (end - start), QTextCursor.MoveMode.KeepAnchor)
        widget.setTextCursor(cursor)
        return True
    if isinstance(widget, QLineEdit) and not widget.isReadOnly():
        text = widget.text()
        if widget.hasSelectedText():
            start = widget.selectionStart()
            end = start + len(widget.selectedText())
        else:
            end = widget.cursorPosition()
            start = end
            while start > 0 and not text[start - 1].isspace() and text[start - 1] != ",":
                start -= 1
        if start == end:
            return False
        widget.setText(text[:start] + keyboard_layout.switch_layout(text[start:end]) + text[end:])
        widget.setSelection(start, end - start)
        return True
    return False


def _install_recipient_completer(line_edit: QLineEdit, contacts: list[contact_store.Contact]) -> QCompleter:
    """Автодополнение по адресной книге для поля со списком адресов через
    запятую. Обычный line_edit.setCompleter() достраивал бы ВСЁ поле
    целиком по одному совпадению — здесь достраивается только текущий
    (последний) сегмент после запятой, остальные не трогаются."""
    candidates = _contact_candidates(contacts)
    completer = QCompleter(candidates, line_edit)
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
            search = _recipient_search_prefix(prefix, candidates)
            if search != prefix:
                # Набрано не в той раскладке, а в другой адресат находится:
                # исправляем прямо в поле (жалоба: «при выборе из книги
                # есть, но не меняется введённый текст») — дальше человек
                # печатает уже правильными буквами.
                head, tail = text[:prefix_start], text[cursor_pos:]
                spaces = len(text[prefix_start:cursor_pos]) - len(text[prefix_start:cursor_pos].lstrip())
                fixed = head + text[prefix_start:prefix_start + spaces] + search
                line_edit.setText(fixed + tail)
                cursor_pos = len(fixed)
                line_edit.setCursorPosition(cursor_pos)
                state["cursor_pos"] = cursor_pos
            completer.setCompletionPrefix(search)
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

    def __init__(self, parent, contacts: list[contact_store.Contact], *, preselected: str = "", persons_only: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Адресная книга")
        self.resize(420, 480)
        self._contacts = contacts
        # Что уже стоит в поле — отмечаем (жалоба: "провалился в адресную
        # книгу — не видно, кто выбран").
        pre_groups, pre_rest = _split_recipient_text(preselected)
        pre_groups_cf = {g.strip().casefold() for g in pre_groups}
        pre_addrs = {addr.lower() for _n, addr in getaddresses([pre_rest]) if addr and "@" in addr}

        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText("Поиск по имени или email")
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.filter_edit.setObjectName("searchField")
        self.filter_edit.addAction(_toolbar_icon("search", 14), QLineEdit.ActionPosition.LeadingPosition)

        self.list_widget = QListWidget(self)
        self.list_widget.setIconSize(QSize(28, 28))
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        # Жалоба: "нельзя выбрать сразу несколько" — множественный выбор
        # был, но только через Ctrl/Shift, о чём никто не догадывался.
        # Галочки очевидны и, в отличие от выделения, переживают фильтрацию
        # списка (выделение скрытых строк Qt сбрасывает, отмеченные
        # галочки — нет).
        for contact in contacts:
            if not contact.emails or (persons_only and contact.is_group):
                continue
            if contact.is_group:
                candidate = _format_group_candidate(contact.display_name)
                label = f"{candidate}  — группа, адресов: {len(contact.emails)}"
                checked = candidate.strip("[]").casefold() in pre_groups_cf
            else:
                candidate = _format_recipient_candidate(contact.display_name, contact.emails[0])
                label = f"{contact.display_name} — {contact.title}" if contact.title else candidate
                checked = contact.emails[0].lower() in pre_addrs
            item = QListWidgetItem(label)
            if not contact.is_group:
                item.setIcon(_contact_avatar(contact, 28))
                item.setToolTip(
                    "\n".join(part for part in (contact.display_name, contact.title, contact.department, contact.emails[0]) if part)
                )
            item.setData(Qt.ItemDataRole.UserRole, candidate)
            item.setData(Qt.ItemDataRole.UserRole + 1, len(contact.emails))
            item.setData(Qt.ItemDataRole.UserRole + 2, label)  # подпись без номера
            item.setData(Qt.ItemDataRole.UserRole + 3, contact.display_name)
            item.setData(Qt.ItemDataRole.UserRole + 4, ", ".join(contact.emails))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            if contact.is_group:
                item.setToolTip("\n".join(contact.emails[:40]) + ("\n…" if len(contact.emails) > 40 else ""))
            self.list_widget.addItem(item)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self.accept())
        self.list_widget.itemChanged.connect(lambda _item: self._update_summary())
        self._renumber()

        self.summary_label = QLabel("", self)
        self._update_summary()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.filter_edit)
        layout.addWidget(self.list_widget)
        layout.addWidget(self.summary_label)
        layout.addWidget(buttons)

    def _update_summary(self) -> None:
        checked = 0
        addresses = 0
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                checked += 1
                addresses += int(item.data(Qt.ItemDataRole.UserRole + 1) or 0)
        self.summary_label.setText(f"Отмечено: {checked}, адресов: {addresses}")

    def _labels(self) -> list[str]:
        return [
            str(self.list_widget.item(row).data(Qt.ItemDataRole.UserRole + 2) or self.list_widget.item(row).text()).lower()
            for row in range(self.list_widget.count())
        ]

    def _matches(self, text: str) -> bool:
        needle = text.strip().lower()
        return not needle or any(needle in label for label in self._labels())

    def _apply_filter(self, text: str) -> None:
        # Набрали не в той раскладке — поле исправляется само, как и в
        # поиске писем и контактов.
        needle = fix_search_layout(self.filter_edit, self._matches).strip().lower()
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            label = str(item.data(Qt.ItemDataRole.UserRole + 2) or item.text()).lower()
            item.setHidden(bool(needle) and needle not in label)
        self._renumber()

    # ---- голосовое управление (канал управления, contact_picker_*) ----------
    # Видимые строки нумеруются «1. Имя <адрес>» — помощник называет номер
    # или имя, redmail отмечает строку; «принять» = ОК.

    def _visible_items(self) -> list[QListWidgetItem]:
        return [self.list_widget.item(r) for r in range(self.list_widget.count()) if not self.list_widget.item(r).isHidden()]

    def _renumber(self) -> None:
        self.list_widget.blockSignals(True)
        try:
            for number, item in enumerate(self._visible_items(), start=1):
                item.setText(f"{number}. {item.data(Qt.ItemDataRole.UserRole + 2)}")
        finally:
            self.list_widget.blockSignals(False)

    def set_filter(self, text: str) -> None:
        self.filter_edit.setText(text)

    def candidates(self) -> list[dict]:
        return [
            {
                "number": number,
                "name": item.data(Qt.ItemDataRole.UserRole + 3),
                "email": item.data(Qt.ItemDataRole.UserRole + 4),
                "checked": item.checkState() == Qt.CheckState.Checked,
            }
            for number, item in enumerate(self._visible_items(), start=1)
        ]

    def picker_state(self) -> dict:
        return {"query": self.filter_edit.text(), "candidates": self.candidates()}

    def select(self, *, number: int | None = None, query: str | None = None, all_visible: bool = False, checked: bool = True) -> int:
        """Отметить (снять) строки: по номеру на экране, по словам имени
        (все слова должны встретиться) или все видимые. Возвращает число
        затронутых строк."""
        visible = self._visible_items()
        targets: list[QListWidgetItem] = []
        if all_visible:
            targets = visible
        elif number is not None:
            if 1 <= number <= len(visible):
                targets = [visible[number - 1]]
        elif query:
            words = [w for w in query.casefold().split() if w]
            for item in visible:
                label = str(item.data(Qt.ItemDataRole.UserRole + 2) or "").casefold()
                if words and all(w in label for w in words):
                    targets.append(item)
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for item in targets:
            item.setCheckState(state)
            self.list_widget.scrollToItem(item)
        return len(targets)

    def selected_entries(self) -> list[dict]:
        return [
            {"name": item.data(Qt.ItemDataRole.UserRole + 3), "email": item.data(Qt.ItemDataRole.UserRole + 4)}
            for item in (self.list_widget.item(r) for r in range(self.list_widget.count()))
            if item.checkState() == Qt.CheckState.Checked
        ]

    def selected_candidates(self) -> list[str]:
        # Галочки + текущее выделение: одиночный клик по строке (без
        # галочки) по-прежнему считается выбором, как и раньше.
        picked: list[str] = []
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.checkState() == Qt.CheckState.Checked or item.isSelected():
                picked.append(item.data(Qt.ItemDataRole.UserRole) or item.text())
        return picked


def _open_contact_picker(parent, line_edit: QLineEdit, contacts: list[contact_store.Contact]) -> None:
    if not contacts:
        QMessageBox.information(parent, "Адресная книга", "Адресная книга пуста — сначала добавьте контакты.")
        return
    dialog = ContactPickerDialog(parent, contacts, preselected=line_edit.text())
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    _apply_picker_to_field(line_edit, contacts, dialog.selected_candidates())
    return


def _apply_picker_to_field(line_edit: QLineEdit, contacts: list[contact_store.Contact], picked: list[str]) -> None:
    """Окно показывало текущий выбор — его результат и есть новое содержимое
    поля (снятые галочки убирают адресата); вручную набранные адреса,
    которых нет в книге, сохраняются."""
    known_addrs = {c.emails[0].lower() for c in contacts if not c.is_group and c.emails}
    _groups_in_field, rest = _split_recipient_text(line_edit.text())
    manual = [
        _format_recipient_candidate(name, addr)
        for name, addr in getaddresses([rest]) if addr and "@" in addr and addr.lower() not in known_addrs
    ]
    line_edit.setText(", ".join(manual + picked))
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


def _facts_from_summary(summary: MessageSummary, text: str = "") -> mail_categories.MessageFacts:
    return mail_categories.MessageFacts(
        sender_email=summary.sender_email or "", sender_name=summary.sender or "",
        subject=summary.subject or "", text=text or "", to=summary.to or "",
    )


def _facts_from_content(content: MessageContent) -> mail_categories.MessageFacts:
    from email.utils import parseaddr

    name, email = parseaddr(content.from_ or "")
    return mail_categories.MessageFacts(
        sender_email=email, sender_name=name, subject=content.subject or "", text=content.text or "",
        headers=dict(getattr(content, "mail_headers", {}) or {}), to=content.to or "",
    )


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


#: Готовые аватары контактов. Раньше фотография заново раскодировалась и
#: скруглялась при каждой отрисовке плитки и при каждом открытии раздела —
#: на корпоративной книге с фотографиями переход в «Контакты» заметно тормозил.
_AVATAR_CACHE: dict[tuple, QIcon] = {}
_AVATAR_CACHE_LIMIT = 8000


def _shrink_photo_bytes(data: bytes, max_side: int = 128) -> tuple[bytes, str] | None:
    """Фото контакта — уменьшенной JPEG-копией (крупнейший показ — 96 точек).
    QImage можно использовать и в фоновом потоке."""
    image = QImage.fromData(data)
    if image.isNull():
        return None
    if max(image.width(), image.height()) > max_side:
        image = image.scaled(max_side, max_side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.convertToFormat(QImage.Format.Format_RGB32).save(buffer, "JPEG", 85):
        return None
    return bytes(buffer.data()), "image/jpeg"


def _contact_avatar(contact, size: int = 32) -> QIcon:
    """Аватар контакта: фотография из адресной книги (в корпоративной
    выгрузке она есть у части сотрудников), иначе кружок с инициалами."""
    photo = getattr(contact, "photo", b"") or b""
    name = getattr(contact, "display_name", "") or ""
    emails = getattr(contact, "emails", None) or []
    cache_key = (size, name, emails[0] if emails else "", len(photo), photo[:32], photo[-32:])
    cached = _AVATAR_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if len(_AVATAR_CACHE) >= _AVATAR_CACHE_LIMIT:
        _AVATAR_CACHE.clear()
    icon = _render_contact_avatar(contact, size)
    _AVATAR_CACHE[cache_key] = icon
    return icon


def _render_contact_avatar(contact, size: int) -> QIcon:
    photo = getattr(contact, "photo", b"")
    if photo:
        image = QImage.fromData(photo)
        if not image.isNull():
            scaled = QPixmap.fromImage(image).scaled(
                size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
            )
            rounded = QPixmap(size, size)
            rounded.fill(Qt.GlobalColor.transparent)
            painter = QPainter(rounded)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addEllipse(0, 0, size, size)
            painter.setClipPath(path)
            painter.drawPixmap(
                int((size - scaled.width()) / 2), int((size - scaled.height()) / 2), scaled
            )
            painter.end()
            return QIcon(rounded)
    name = getattr(contact, "display_name", "") or ""
    key = (getattr(contact, "emails", None) or [name])[0] if getattr(contact, "emails", None) else name
    return QIcon(_avatar_pixmap(_message_initials(name), _avatar_color(key or name), size))


def _message_initials(name: str) -> str:
    """Инициалы для аватара плитки: первые буквы двух первых слов имени
    ("Иванов Пётр" → "ИП"), для голого email — первая буква."""
    words = [w for w in re.split(r"[\s,<>\"']+", name or "") if w and w[0].isalnum()]
    if not words:
        return "?"
    if len(words) == 1 or "@" in words[0]:
        return words[0][0].upper()
    return (words[0][0] + words[1][0]).upper()


def _markers_icon(value: str | None) -> QIcon:
    """Иконка маркеров в таблице: один цвет — кружок как раньше; несколько
    (пожелание: "на письмо можно поставить несколько маркеров") — до
    четырёх кружков поменьше сеткой 2×2 в том же квадрате, чтобы не
    ломать фиксированный размер иконки колонки."""
    colors = split_markers(value)
    if not colors:
        return QIcon()
    if len(colors) == 1:
        return _marker_icon(colors[0])
    size = _MARKER_ICON_SIZE
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    dot = size // 2 - 1
    for index, color in enumerate(colors[:4]):
        x = (index % 2) * (size // 2) + 1
        y = (index // 2) * (size // 2) + 1
        painter.setBrush(QColor(_MARKER_HEX[color]))
        painter.drawEllipse(x, y, dot, dot)
    painter.end()
    return QIcon(pixmap)


class _ContactCardDelegate(QStyledItemDelegate):
    """Карточка контакта: круглый аватар (фотография сотрудника или
    инициалы), имя, должность с подразделением, адрес и телефон —
    пожелание «хочу карточки с аватаром»."""

    AVATAR = 44
    PAD = 10

    def __init__(self, contacts_provider, parent=None) -> None:
        super().__init__(parent)
        self._contacts = contacts_provider

    def _contact(self, index):
        contacts = self._contacts()
        row = index.data(Qt.ItemDataRole.UserRole)
        return contacts[row] if isinstance(row, int) and 0 <= row < len(contacts) else None

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt override
        line = QFontMetrics(option.font).height()
        return QSize(option.rect.width(), max(self.AVATAR + 2 * self.PAD, 2 * line + 2 * self.PAD + 6))

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802 - Qt override
        contact = self._contact(index)
        if contact is None:
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        palette = option.palette
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.fillRect(rect, palette.highlight())
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect, palette.alternateBase())
        text_color = palette.highlightedText().color() if selected else palette.text().color()
        muted = palette.highlightedText().color() if selected else palette.placeholderText().color()

        avatar_x = rect.left() + self.PAD
        avatar_y = rect.center().y() - self.AVATAR // 2
        icon = _toolbar_icon("group", self.AVATAR) if contact.is_group else _contact_avatar(contact, self.AVATAR)
        painter.drawPixmap(avatar_x, avatar_y, icon.pixmap(self.AVATAR, self.AVATAR))

        normal = QFont(option.font)
        bold = QFont(option.font)
        bold.setBold(True)
        line_h = QFontMetrics(bold).height()
        x = avatar_x + self.AVATAR + self.PAD
        right = rect.right() - self.PAD
        top = rect.center().y() - line_h - 2

        name = contact.display_name + (" (группа)" if contact.is_group else "")
        contacts_text = ", ".join(contact.emails[:1]) if not contact.is_group else f"адресов: {len(contact.emails)}"
        if contact.phone:
            contacts_text = f"{contacts_text}   тел. {contact.phone}" if contacts_text else f"тел. {contact.phone}"
        second_line = ", ".join(part for part in (contact.title, contact.department) if part) or contact.organization

        painter.setFont(normal)
        contacts_width = min(painter.fontMetrics().horizontalAdvance(contacts_text), rect.width() // 3)
        painter.setPen(muted)
        painter.drawText(
            QRect(right - contacts_width, top, contacts_width, line_h),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(contacts_text, Qt.TextElideMode.ElideMiddle, contacts_width),
        )
        painter.setFont(bold)
        painter.setPen(text_color)
        name_rect = QRect(x, top, max(10, right - contacts_width - self.PAD - x), line_h)
        painter.drawText(
            name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, name_rect.width()),
        )
        if second_line:
            painter.setFont(normal)
            painter.setPen(muted)
            second_rect = QRect(x, top + line_h + 4, max(10, right - x), line_h)
            painter.drawText(
                second_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                painter.fontMetrics().elidedText(second_line, Qt.TextElideMode.ElideRight, second_rect.width()),
            )
        painter.setPen(QPen(palette.mid().color()))
        painter.drawLine(rect.left() + self.PAD, rect.bottom(), rect.right() - self.PAD, rect.bottom())
        painter.restore()


class _MessageCardDelegate(QStyledItemDelegate):
    """Плитка письма (второй режим списка, по дизайн-референсу): галочка,
    кружок-аватар с инициалами, отправитель + дата, тема + признаки
    (важность, вложение, отвечено, маркеры). Рисует прямо из
    MessageSummary — данные не дублируются, после смены флагов достаточно
    перерисовать список."""

    CHECK = 16
    AVATAR = 36
    PAD = 8

    def __init__(self, summaries_by_uid, parent=None, *, thread_info=None, on_thread_toggle=None, category_for=None) -> None:
        super().__init__(parent)
        self._summaries_by_uid = summaries_by_uid
        self._category_for = category_for or (lambda uid: None)
        self._thread_info = thread_info or (lambda uid: None)
        self._on_thread_toggle = on_thread_toggle
        self.expanded_keys: set[str] = set()
        self.sent_mode = False

    def _summary(self, index) -> MessageSummary | None:
        return self._summaries_by_uid().get(index.data(Qt.ItemDataRole.UserRole))

    def _check_rect(self, rect: QRect) -> QRect:
        return QRect(rect.left() + self.PAD, rect.center().y() - self.CHECK // 2, self.CHECK, self.CHECK)

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt override
        line_h = QFontMetrics(option.font).height()
        return QSize(option.rect.width(), max(self.AVATAR + 2 * self.PAD, 2 * line_h + 2 * self.PAD + 4))

    def paint(self, painter: QPainter, option, index) -> None:
        summary = self._summary(index)
        if summary is None:
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        palette = option.palette
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.fillRect(rect, palette.highlight())
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect, palette.alternateBase())
        text_color = palette.highlightedText().color() if selected else palette.text().color()
        muted = palette.highlightedText().color() if selected else palette.placeholderText().color()

        check_rect = self._check_rect(rect)
        check_option = QStyleOptionButton()
        check_option.rect = check_rect
        check_option.palette = palette
        checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
        check_option.state = QStyle.StateFlag.State_Enabled | (
            QStyle.StateFlag.State_On if checked else QStyle.StateFlag.State_Off
        )
        QApplication.style().drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorCheckBox, check_option, painter)

        name = (summary.to if self.sent_mode else summary.sender) or summary.sender_email or "(без имени)"
        key = (summary.to if self.sent_mode else (summary.sender_email or summary.sender)) or "?"
        avatar_x = check_rect.right() + self.PAD + 2
        avatar_y = rect.center().y() - self.AVATAR // 2
        painter.drawPixmap(avatar_x, avatar_y, _avatar_pixmap(_message_initials(name), _avatar_color(key), self.AVATAR))
        if not summary.is_read:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(palette.highlightedText() if selected else palette.highlight())
            painter.drawEllipse(avatar_x + self.AVATAR - 9, avatar_y - 1, 10, 10)

        normal = QFont(option.font)
        bold = QFont(option.font)
        bold.setBold(True)
        line_h = QFontMetrics(bold).height()
        x = avatar_x + self.AVATAR + self.PAD + 2
        right = rect.right() - self.PAD
        top = rect.top() + (rect.height() - 2 * line_h - 4) // 2

        painter.setFont(normal)
        date_w = painter.fontMetrics().horizontalAdvance(summary.date)
        painter.setPen(muted)
        painter.drawText(QRect(right - date_w, top, date_w, line_h), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, summary.date)
        painter.setPen(text_color)
        painter.setFont(normal if summary.is_read else bold)
        sender_rect = QRect(x, top, max(10, right - date_w - self.PAD - x), line_h)
        painter.drawText(
            sender_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, sender_rect.width()),
        )

        y2 = top + line_h + 4
        indicator_x = right
        dot = 10
        for color in reversed(split_markers(summary.marker_color)):
            if color not in _MARKER_HEX:
                continue
            indicator_x -= dot + 3
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(_MARKER_HEX[color]))
            painter.drawEllipse(indicator_x, y2 + (line_h - dot) // 2, dot, dot)
        chip = self._category_for(summary.uid)
        if chip is not None:
            chip_name, chip_color = chip
            small = QFont(option.font)
            small.setPointSizeF(max(6.0, option.font.pointSizeF() * 0.85))
            painter.setFont(small)
            chip_w = painter.fontMetrics().horizontalAdvance(chip_name) + 10
            indicator_x -= chip_w + 6
            chip_rect = QRect(indicator_x, y2 + 1, chip_w, line_h - 2)
            painter.setPen(Qt.PenStyle.NoPen)
            fill = QColor(chip_color)
            fill.setAlpha(40)
            painter.setBrush(fill)
            painter.drawRoundedRect(chip_rect, 6, 6)
            painter.setPen(palette.highlightedText().color() if selected else QColor(chip_color))
            painter.drawText(chip_rect, Qt.AlignmentFlag.AlignCenter, chip_name)
        marks = []
        if summary.importance == "high":
            marks.append("!")
        if summary.has_attachments:
            marks.append(_ATTACHMENT_MARK)
        if summary.is_answered:
            marks.append(_REPLIED_MARK)
        painter.setFont(normal)
        if marks:
            text = " ".join(marks)
            w = painter.fontMetrics().horizontalAdvance(text)
            indicator_x -= w + 6
            painter.setPen(muted)
            painter.drawText(QRect(indicator_x, y2, w, line_h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        # Цепочка по теме: у головного письма — значок раскрытия и число
        # писем, у дочерних — отступ.
        info = self._thread_info(summary.uid)
        subject_x = x
        if info is not None and info.count > 1:
            if info.is_head:
                mark = _THREAD_EXPANDED_MARK if info.key in self.expanded_keys else _THREAD_COLLAPSED_MARK
                painter.setPen(muted)
                painter.setFont(normal)
                painter.drawText(QRect(x, y2, _THREAD_TOGGLE_WIDTH, line_h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{mark} {info.count}")
                subject_x = x + _THREAD_TOGGLE_WIDTH + painter.fontMetrics().horizontalAdvance(str(info.count)) + 2
            else:
                subject_x = x + _THREAD_TOGGLE_WIDTH
        painter.setPen(text_color)
        painter.setFont(normal if summary.is_read else bold)
        subject_rect = QRect(subject_x, y2, max(10, indicator_x - self.PAD - subject_x), line_h)
        painter.drawText(
            subject_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(summary.subject or "(без темы)", Qt.TextElideMode.ElideRight, subject_rect.width()),
        )

        painter.setPen(QPen(palette.mid().color()))
        painter.drawLine(rect.left() + self.PAD, rect.bottom(), rect.right() - self.PAD, rect.bottom())
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802 - Qt override
        # Клик по галочке — переключить отметку письма (для массовых
        # действий), а не выбрать плитку.
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            hit = self._check_rect(option.rect).adjusted(-4, -4, 4, 4)
            if hit.contains(event.position().toPoint()):
                current = index.data(Qt.ItemDataRole.CheckStateRole)
                new_state = Qt.CheckState.Unchecked if current == Qt.CheckState.Checked else Qt.CheckState.Checked
                model.setData(index, new_state, Qt.ItemDataRole.CheckStateRole)
                return True
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            # Клик по значку цепочки — свернуть/раскрыть, не меняя выбор.
            uid = index.data(Qt.ItemDataRole.UserRole)
            info = self._thread_info(uid)
            if info is not None and info.count > 1 and info.is_head and self._on_thread_toggle is not None:
                toggle_rect = self._thread_toggle_rect(option.rect)
                if toggle_rect.contains(event.position().toPoint()):
                    self._on_thread_toggle(info.key)
                    return True
        return super().editorEvent(event, model, option, index)

    def _thread_toggle_rect(self, rect: QRect) -> QRect:
        x = self._check_rect(rect).right() + self.PAD + 2 + self.AVATAR + self.PAD + 2
        return QRect(x - 4, rect.top(), _THREAD_TOGGLE_WIDTH + 18, rect.height())


def _icon_color() -> str:
    """Жалоба: 'на тёмном фоне значки поярче нужно' — фиксированный
    тёмно-серый (#5f6368) был рассчитан на светлую тему и еле виден на
    тёмном фоне тулбаров/диалогов. В тёмной теме используется светлый
    оттенок, в светлой — прежний."""
    return "#c4c7c5" if app_theme.is_dark() else "#5f6368"
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
    pen = QPen(QColor(_icon_color()))
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
        painter.setBrush(QColor(_icon_color()))
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
        painter.setBrush(QColor(_icon_color()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(cx - size * 0.10, size * 0.27, size * 0.20, size * 0.20))
    elif kind == "description":
        for frac, shorten in ((0.28, 0.0), (0.5, 0.0), (0.72, size * 0.25)):
            y = m + frac * r
            painter.drawLine(QPointF(m, y), QPointF(size - m - shorten, y))
    elif kind == "bell":
        path = QPainterPath()
        path.moveTo(size * 0.22, size * 0.68)
        path.cubicTo(size * 0.30, size * 0.60, size * 0.26, size * 0.18, cx, size * 0.16)
        path.cubicTo(size * 0.74, size * 0.18, size * 0.70, size * 0.60, size * 0.78, size * 0.68)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawArc(QRectF(cx - size * 0.12, size * 0.70, size * 0.24, size * 0.18), 180 * 16, 180 * 16)
    elif kind == "calendar":
        painter.drawRoundedRect(QRectF(m, size * 0.20, size - 2 * m, size - size * 0.20 - m), 2, 2)
        painter.drawLine(QPointF(size * 0.32, m), QPointF(size * 0.32, size * 0.28))
        painter.drawLine(QPointF(size * 0.68, m), QPointF(size * 0.68, size * 0.28))
        painter.drawLine(QPointF(m, size * 0.42), QPointF(size - m, size * 0.42))
    painter.end()
    return QIcon(pixmap)


# Иконки — Material Symbols (Google, Apache License 2.0,
# https://github.com/google/material-design-icons), контур ("d" у <path>)
# каждого глифа скопирован как есть из официального npm-пакета
# @material-symbols/svg-400 (вариант "outlined", viewBox "0 -960 960 960").
# По мотивам дизайн-ревью ("самодельные иконки выглядят убого на фоне
# референса") — вместо геометрических фигур, нарисованных вручную через
# QPainter, используется тот же узнаваемый набор, что и на скриншотах-
# референсах, отрисованный через QSvgRenderer и перекрашенный в цвет
# _icon_color().
_MATERIAL_ICON_PATHS: dict[str, str] = {
    "code": "M320-240 80-480l240-240 57 57-184 184 183 183-56 56Zm320 0-57-57 184-184-183-183 56-56 240 240-240 240Z",
    "open_in_new": "M200-120q-33 0-56.5-23.5T120-200v-560q0-33 23.5-56.5T200-840h280v80H200v560h560v-280h80v280q0 33-23.5 56.5T760-120H200Zm188-212-56-56 372-372H560v-80h280v280h-80v-144L388-332Z",
    "edit": "M180-180h44l472-471-44-44-472 471v44Zm-60 60v-128l575-574q8-8 19-12.5t23-4.5q11 0 22 4.5t20 12.5l44 44q9 9 13 20t4 22q0 11-4.5 22.5T823-694L248-120H120Zm659-617-41-41 41 41Zm-105 64-22-22 44 44-22-22Z",
    "reply": "M780-200v-156q0-60-39-99t-99-39H236l163 163-43 43-236-236 236-236 43 43-163 163h406q85 0 141.5 56.5T840-356v156h-60Z",
    "forward": "m644-288-43-43 193-193-193-193 43-43 236 236-236 236ZM81-200v-156q0-85 56.5-141.5T279-554h305L421-717l43-43 236 236-236 236-43-43 163-163H279q-60 0-99 39t-39 99v156H81Z",
    "reply_all": "M316-288 80-524l236-236 43 43-193 193 193 193-43 43Zm503 88v-156q0-60-39-99t-99-39H376l163 163-43 43-236-236 236-236 43 43-163 163h305q85 0 141.5 56.5T879-356v156h-60Z",
    "filter_list": "M400-240v-60h160v60H400ZM240-450v-60h480v60H240ZM120-660v-60h720v60H120Z",
    "sort": "M120-240v-60h240v60H120Zm0-210v-60h480v60H120Zm0-210v-60h720v60H120Z",
    "group": "M40-160v-112q0-34 17.5-62.5T104-378q62-31 126-46.5T360-440q66 0 130 15.5T616-378q29 15 46.5 43.5T680-272v112H40Zm720 0v-120q0-44-24.5-84.5T666-434q51 6 96 20.5t84 35.5q36 20 55 44.5t19 53.5v120H760ZM360-480q-66 0-113-47t-47-113q0-66 47-113t113-47q66 0 113 47t47 113q0 66-47 113t-113 47Zm400-160q0 66-47 113t-113 47q-11 0-28-2.5t-28-5.5q27-32 41.5-71t14.5-81q0-42-14.5-81T544-792q14-5 28-6.5t28-1.5q66 0 113 47t47 113ZM120-240h480v-32q0-11-5.5-20T580-306q-54-27-109-40.5T360-360q-56 0-111 13.5T140-306q-9 5-14.5 14t-5.5 20v32Zm240-320q33 0 56.5-23.5T440-640q0-33-23.5-56.5T360-720q-33 0-56.5 23.5T280-640q0 33 23.5 56.5T360-560Zm0 320Zm0-400Z",
    "view_agenda": "M180-510q-24 0-42-18t-18-42v-210q0-24 18-42t42-18h600q24 0 42 18t18 42v210q0 24-18 42t-42 18H180Zm0-60h600v-210H180v210Zm0 450q-24 0-42-18t-18-42v-210q0-24 18-42t42-18h600q24 0 42 18t18 42v210q0 24-18 42t-42 18H180Zm0-60h600v-210H180v210Zm0-600v210-210Zm0 390v210-210Z",
    "view_list": "M350-220h470v-137H350v137ZM140-603h150v-137H140v137Zm0 187h150v-127H140v127Zm0 196h150v-137H140v137Zm210-196h470v-127H350v127Zm0-187h470v-137H350v137ZM140-160q-24 0-42-18t-18-42v-520q0-24 18-42t42-18h680q24 0 42 18t18 42v520q0 24-18 42t-42 18H140Z",
    "delete": "M261-120q-24.75 0-42.37-17.63Q201-155.25 201-180v-570h-41v-60h188v-30h264v30h188v60h-41v570q0 24-18 42t-42 18H261Zm438-630H261v570h438v-570ZM367-266h60v-399h-60v399Zm166 0h60v-399h-60v399ZM261-750v570-570Z",
    "refresh": "M480-160q-133 0-226.5-93.5T160-480q0-133 93.5-226.5T480-800q85 0 149 34.5T740-671v-129h60v254H546v-60h168q-38-60-97-97t-137-37q-109 0-184.5 75.5T220-480q0 109 75.5 184.5T480-220q83 0 152-47.5T728-393h62q-29 105-115 169t-195 64Z",
    "archive": "m480-270 156-156-40-40-86 86v-201h-60v201l-86-86-40 40 156 156ZM180-674v494h600v-494H180Zm0 554q-24.75 0-42.37-17.63Q120-155.25 120-180v-529q0-9.88 3-19.06 3-9.18 9-16.94l52-71q8-11 20.94-17.5Q217.88-840 232-840h495q14.12 0 27.06 6.5T775-816l53 71q6 7.76 9 16.94 3 9.18 3 19.06v529q0 24.75-17.62 42.37Q804.75-120 780-120H180Zm17-614h565l-36.41-46H233l-36 46Zm283 307Z",
    "download": "M480-313 287-506l43-43 120 120v-371h60v371l120-120 43 43-193 193ZM220-160q-24 0-42-18t-18-42v-143h60v143h520v-143h60v143q0 24-18 42t-42 18H220Z",
    "today": "M180-80q-24 0-42-18t-18-42v-620q0-24 18-42t42-18h65v-60h65v60h340v-60h65v60h65q24 0 42 18t18 42v620q0 24-18 42t-42 18H180Zm0-60h600v-430H180v430Zm0-490h600v-130H180v130Zm0 0v-130 130Z",
    "chevron_left": "M561-240 320-481l241-241 43 43-198 198 198 198-43 43Z",
    "chevron_right": "M530-481 332-679l43-43 241 241-241 241-43-43 198-198Z",
    "add": "M450-450H200v-60h250v-250h60v250h250v60H510v250h-60v-250Z",
    "event_busy": "m381-218-43-43 100-99-100-99 43-43 99 100 99-100 43 43-100 99 100 99-43 43-99-100-99 100ZM180-80q-24 0-42-18t-18-42v-620q0-24 18-42t42-18h65v-60h65v60h340v-60h65v60h65q24 0 42 18t18 42v620q0 24-18 42t-42 18H180Zm0-60h600v-430H180v430Zm0-490h600v-130H180v130Zm0 0v-130 130Z",
    "sync": "M167-160v-60h130l-15-12q-64-51-93-111t-29-134q0-106 62.5-190.5T387-784v62q-75 29-121 96.5T220-477q0 63 23.5 109.5T307-287l30 21v-124h60v230H167Zm407-15v-63q76-29 121-96.5T740-483q0-48-23.5-97.5T655-668l-29-26v124h-60v-230h230v60H665l15 14q60 56 90 120t30 123q0 106-62 191T574-175Z",
    "search": "M796-121 533-384q-30 26-70 40.5T378-329q-108 0-183-75t-75-181q0-106 75-181t182-75q106 0 180.5 75T632-585q0 43-14 83t-42 75l264 262-44 44ZM377-389q81 0 138-57.5T572-585q0-81-57-138.5T377-781q-82 0-139.5 57.5T180-585q0 81 57.5 138.5T377-389Z",
    "mail": "M140-160q-24 0-42-18t-18-42v-520q0-24 18-42t42-18h680q24 0 42 18t18 42v520q0 24-18 42t-42 18H140Zm340-302L140-685v465h680v-465L480-462Zm0-60 336-218H145l335 218ZM140-685v-55 520-465Z",
    "inbox": "M180-120q-24 0-42-18t-18-42v-600q0-24 18-42t42-18h600q24 0 42 18t18 42v600q0 24-18 42t-42 18H180Zm0-60h600v-136H634q-26 40-67.5 61.5T480-233q-45 0-86.5-21.5T326-316H180v136Zm374-136.5q33-23.5 56-59.5h170v-404H180v404h170q23 36 56.25 59.5 33.24 23.5 74 23.5Q521-293 554-316.5ZM180-180h600-600Z",
    "all_inbox": "M260-260h560v-163H676q-18 40-54.5 63.5T540-336q-45 0-81-23.5T404-423H260v163Zm280-136q38 0 65.02-25.56 27.02-25.55 27.02-61.44H820v-337H260v337h188q0 35.89 27.02 61.44Q502.05-396 540-396ZM260-200q-24 0-42-18t-18-42v-560q0-24 18-42t42-18h560q24 0 42 18t18 42v560q0 24-18 42t-42 18H260ZM140-80q-24 0-42-18t-18-42v-620h60v620h620v60H140Zm120-180h560-560Z",
    "send": "M120-160v-640l760 320-760 320Zm60-93 544-227-544-230v168l242 62-242 60v167Zm0 0v-457 457Z",
    "drafts": "m480-920 371 222q17 9 23 24.5t6 30.5v463q0 24-18 42t-42 18H140q-24 0-42-18t-18-42v-463q0-15 6.5-30.5T109-698l371-222Zm0 466 336-197-336-202-336 202 336 197Zm0 67L140-587v407h680v-407L480-387Zm0 207h340-680 340Z",
    "report": "M480-281q14 0 24.5-10.5T515-316q0-14-10.5-24.5T480-351q-14 0-24.5 10.5T445-316q0 14 10.5 24.5T480-281Zm-30-144h60v-263h-60v263ZM330-120 120-330v-300l210-210h300l210 210v300L630-120H330Zm25-60h250l175-175v-250L605-780H355L180-605v250l175 175Zm125-300Z",
    "star": "m323-245 157-94 157 95-42-178 138-120-182-16-71-168-71 167-182 16 138 120-42 178Zm-90 125 65-281L80-590l288-25 112-265 112 265 288 25-218 189 65 281-247-149-247 149Zm247-355Z",
    "flag": "M200-120v-680h343l19 86h238v370H544l-18.93-85H260v309h-60Zm300-452Zm95 168h145v-250H511l-19-86H260v251h316l19 85Z",
    "folder": "M140-160q-24 0-42-18.5T80-220v-520q0-23 18-41.5t42-18.5h281l60 60h339q23 0 41.5 18.5T880-680v460q0 23-18.5 41.5T820-160H140Zm0-60h680v-460H456l-60-60H140v520Zm0 0v-520 520Z",
    "more_vert": "M479.86-160Q460-160 446-174.14t-14-34Q432-228 446.14-242t34-14Q500-256 514-241.86t14 34Q528-188 513.86-174t-34 14Zm0-272Q460-432 446-446.14t-14-34Q432-500 446.14-514t34-14Q500-528 514-513.86t14 34Q528-460 513.86-446t-34 14Zm0-272Q460-704 446-718.14t-14-34Q432-772 446.14-786t34-14Q500-800 514-785.86t14 34Q528-732 513.86-718t-34 14Z",
}

# redmail-овое имя команды/роли -> имя глифа Material Symbols выше.
_TOOLBAR_ICON_MATERIAL: dict[str, str] = {
    "compose": "edit",
    "reply": "reply",
    "forward": "forward",
    "reply_all": "reply_all",
    "filter": "filter_list",
    "sort": "sort",
    "group": "group",
    "view_table": "view_list",
    "view_cards": "view_agenda",
    "delete": "delete",
    "refresh": "refresh",
    "open_archive": "archive",
    "archive_folder": "archive",
    "archive": "archive",
    "import": "download",
    "today": "today",
    "prev": "chevron_left",
    "next": "chevron_right",
    "add": "add",
    "cancel_event": "event_busy",
    "sync": "sync",
    "search": "search",
    "more": "more_vert",
    "open_window": "open_in_new",
    "source": "code",
}

_FOLDER_ICON_MATERIAL: dict[str, str] = {
    "inbox": "inbox",
    "sent": "send",
    "drafts": "drafts",
    "trash": "delete",
    "spam": "report",
    "important": "star",
    "flagged": "flag",
    "all": "all_inbox",
}


def _material_icon(name: str, size: int, color: str | None = None) -> QIcon:
    if color is None:
        color = _icon_color()
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -960 960 960">'
        f'<path d="{_MATERIAL_ICON_PATHS[name]}" fill="{color}"/></svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


def _toolbar_icon(kind: str, size: int = 18) -> QIcon:
    """Иконки панелей инструментов — см. _MATERIAL_ICON_PATHS выше. Команды
    переведены с текстовых подписей на иконки с подсказками по явной
    просьбе пользователя — экономит место в тулбаре (кроме почты/
    календаря/контактов/параметров/справки, которые намеренно оставлены
    текстом)."""
    return _material_icon(_TOOLBAR_ICON_MATERIAL[kind], size)


# Роль папки по её "сырому" IMAP-имени — по мотивам дизайн-ревью ("у каждой
# папки свой значок") и жалобы "подписывать количество писем тоже".
# Trash/Sent/Drafts уже надёжно определяются отдельно через SPECIAL-USE
# (см. ImapSession._special_folder) и используются в другой логике
# (перемещение в корзину, копия в отправленные и т.п.) — здесь то же самое
# распознавание по подстроке имени нужно ТОЛЬКО для выбора иконки, поэтому
# не завязано на self.trash_folder_name и т.п., а работает по каждому
# имени независимо, включая роли, которых больше нигде в приложении нет
# (спам/важное/помеченные/вся почта).
_FOLDER_ROLE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sent", ("sent", "отправленн")),
    ("drafts", ("draft", "черновик")),
    ("trash", ("trash", "корзин")),
    ("spam", ("spam", "junk", "спам")),
    ("important", ("important", "важн")),
    ("flagged", ("flagged", "starred", "помеч")),
    ("all", ("all mail", "вся почта")),
)


def _folder_role(raw_name: str) -> str | None:
    if raw_name == "INBOX":
        return "inbox"
    lowered = raw_name.lower()
    for role, hints in _FOLDER_ROLE_HINTS:
        if any(hint in lowered for hint in hints):
            return role
    return None


def _folder_icon(role: str | None, size: int = 16) -> QIcon:
    return _material_icon(_FOLDER_ICON_MATERIAL.get(role, "folder"), size)


def _icon_label(kind: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(parent)
    label.setPixmap(_calendar_icon(kind).pixmap(16, 16))
    label.setFixedWidth(22)
    label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
    return label


def _settings_tab(*groups: QWidget) -> QWidget:
    """Страница вкладки параметров: группы сверху, свободное место снизу."""
    page = QWidget()
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(8, 8, 8, 8)
    for group in groups:
        page_layout.addWidget(group)
    page_layout.addStretch(1)
    return page


class CategoryEditDialog(QDialog):
    """Одна категория: название, цвет, описание и правила — адресаты и слова в теме."""

    def __init__(self, parent, category: mail_categories.Category | None = None):
        super().__init__(parent)
        self.setWindowTitle("Категория" if category is None else f"Категория «{category.name}»")
        self._category = category
        self._color = category.color if category is not None else "#5C6BC0"
        self.name_edit = QLineEdit(category.name if category is not None else "", self)
        self.color_button = QPushButton(self)
        self.color_button.clicked.connect(self._on_color)
        self._paint_color_button()
        self.description_edit = QPlainTextEdit(category.description if category is not None else "", self)
        self.description_edit.setPlaceholderText("Что попадает в категорию — для себя и коллег")
        self.description_edit.setFixedHeight(60)
        self.senders_edit = QPlainTextEdit("\n".join(category.senders) if category is not None else "", self)
        self.senders_edit.setPlaceholderText("nalog.ru\n*@gosuslugi.ru\nnoreply@*\nИванов")
        self.keywords_edit = QPlainTextEdit("\n".join(category.keywords) if category is not None else "", self)
        self.keywords_edit.setPlaceholderText("счёт на оплату\nакт сверки")
        hint = QLabel(
            "Адресаты — по одному в строке: домен («nalog.ru»), адрес или маска со звёздочкой "
            "(«*@gosuslugi.ru», «noreply@*») или часть имени отправителя («Иванов»). Слова — ищутся "
            "в теме письма. Остальное модуль узнаёт сам, когда вы выбираете категорию письмам вручную.",
            self,
        )
        hint.setWordWrap(True)
        form = QFormLayout()
        form.addRow("Название", self.name_edit)
        form.addRow("Цвет", self.color_button)
        form.addRow("Описание", self.description_edit)
        form.addRow("Адресаты", self.senders_edit)
        form.addRow("Слова в теме", self.keywords_edit)
        form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.resize(520, 460)

    def _paint_color_button(self) -> None:
        self.color_button.setIcon(_dot_icon(self._color))
        self.color_button.setText(self._color)

    def _on_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._color), self, "Цвет категории")
        if color.isValid():
            self._color = color.name()
            self._paint_color_button()

    def accept(self) -> None:  # noqa: N802 - Qt override
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Нет названия", "Укажите название категории.")
            return
        super().accept()

    def to_category(self) -> mail_categories.Category:
        lines = lambda edit: [line.strip() for line in edit.toPlainText().splitlines() if line.strip()]  # noqa: E731
        base = self._category
        return mail_categories.Category(
            id=base.id if base is not None else "",
            name=self.name_edit.text().strip(),
            color=self._color,
            description=self.description_edit.toPlainText().strip(),
            senders=lines(self.senders_edit),
            keywords=lines(self.keywords_edit),
            builtin=base.builtin if base is not None else False,
            position=base.position if base is not None else 0,
        )


class CategoriesDialog(QDialog):
    """Список категорий модуля: добавить, изменить, удалить, разметить заново."""

    def __init__(self, parent, store: mail_categories.CategoryStore):
        super().__init__(parent)
        self.setWindowTitle("Категории писем")
        self._store = store
        self.list_widget = QListWidget(self)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._on_edit())
        add_button = QPushButton("Добавить…", self)
        add_button.clicked.connect(self._on_add)
        edit_button = QPushButton("Изменить…", self)
        edit_button.clicked.connect(self._on_edit)
        self.delete_button = QPushButton("Удалить", self)
        self.delete_button.clicked.connect(self._on_delete)
        relabel_button = QPushButton("Разметить письма заново", self)
        relabel_button.setToolTip(
            "Сбросить автоматическую разметку — письма получат категории заново по текущим правилам. "
            "Выбранное вручную сохраняется."
        )
        relabel_button.clicked.connect(self._on_relabel)
        side = QVBoxLayout()
        for button in (add_button, edit_button, self.delete_button):
            side.addWidget(button)
        side.addStretch(1)
        side.addWidget(relabel_button)
        body = QHBoxLayout()
        body.addWidget(self.list_widget, 1)
        body.addLayout(side)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(body)
        layout.addWidget(close)
        self.list_widget.currentRowChanged.connect(self._update_buttons)
        self._reload()
        self.resize(560, 360)

    def _reload(self) -> None:
        self.list_widget.clear()
        for category in self._store.categories():
            item = QListWidgetItem(_dot_icon(category.color), category.name)
            item.setData(Qt.ItemDataRole.UserRole, category.id)
            item.setToolTip(category.description)
            self.list_widget.addItem(item)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        self._update_buttons()

    def _current(self) -> mail_categories.Category | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return next((c for c in self._store.categories() if c.id == item.data(Qt.ItemDataRole.UserRole)), None)

    def _update_buttons(self, *_args) -> None:
        current = self._current()
        # Стандартные категории правятся, но не удаляются.
        self.delete_button.setEnabled(current is not None and not current.builtin)

    def _on_add(self) -> None:
        dialog = CategoryEditDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._store.save_category(dialog.to_category())
            self._reload()

    def _on_edit(self) -> None:
        current = self._current()
        if current is None:
            return
        dialog = CategoryEditDialog(self, current)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._store.save_category(dialog.to_category())
            self._reload()

    def _on_delete(self) -> None:
        current = self._current()
        if current is None or current.builtin:
            return
        answer = QMessageBox.question(
            self, "Удалить категорию",
            f"Удалить категорию «{current.name}»? Письма останутся на месте, пропадут только их метки этой категории и обучение.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._store.delete_category(current.id)
            self._reload()

    def _on_relabel(self) -> None:
        self._store.forget_automatic()
        QMessageBox.information(self, "Разметка", "Автоматическая разметка сброшена — письма получат категории заново при открытии папок.")


class BrandEditDialog(QDialog):
    """Оформление одной организации: цвета программы, шрифт, оформление
    письма и шаблон стандартной подписи. Содержимое задаёт заказчик."""

    _COLOR_LABELS = (
        ("accent", "Основной цвет (выделение, кнопки)"),
        ("accent_text", "Текст на основном цвете"),
        ("window", "Фон окна"),
        ("base", "Фон полей и списков"),
        ("alt_base", "Фон панелей"),
        ("text", "Текст"),
        ("border", "Рамки"),
        ("disabled_text", "Второстепенный текст"),
    )

    def __init__(self, parent, brand: branding.Brand | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Оформление «{brand.name}»" if brand else "Новое оформление")
        self._brand = brand
        self.id_edit = QLineEdit(brand.id if brand else "", self)
        self.id_edit.setPlaceholderText("gpp-blagoveshchensk")
        self.id_edit.setEnabled(brand is None)
        self.name_edit = QLineEdit(brand.name if brand else "", self)
        self.organization_edit = QLineEdit(brand.organization if brand else "", self)
        self.base_combo = QComboBox(self)
        self.base_combo.addItem("Светлая основа", "light")
        self.base_combo.addItem("Тёмная основа", "dark")
        self.base_combo.setCurrentIndex(max(0, self.base_combo.findData(brand.base if brand else "light")))
        self.default_check = QCheckBox("Оформление по умолчанию для тех, кто тему ещё не выбирал", self)
        self.default_check.setChecked(bool(brand and brand.default))
        self.export_combo = QComboBox(self)
        self.export_combo.addItem("Сотрудник сам", "user")
        self.export_combo.addItem("Только администратор", "admin")
        self.export_combo.setCurrentIndex(max(0, self.export_combo.findData(brand.data_export if brand else "user")))
        self.export_combo.setToolTip(
            "Кто выгружает переписку и переносит профиль. Действует, когда оформление разложено "
            "администратором в /etc/redmail/brands"
        )

        self._colors: dict[str, str] = dict(brand.colors) if brand else {}
        self._color_buttons: dict[str, QPushButton] = {}
        colors_form = QFormLayout()
        for key, label in self._COLOR_LABELS:
            button = QPushButton(self)
            button.clicked.connect(lambda _checked=False, k=key: self._pick_color(k))
            self._color_buttons[key] = button
            self._paint_color(key)
            colors_form.addRow(label, button)
        colors_group = QGroupBox("Цвета программы", self)
        colors_group.setLayout(colors_form)

        self.font_combo = QFontComboBox(self)
        self.font_check = QCheckBox("Свой шрифт программы", self)
        self.font_check.setChecked(bool(brand and brand.font_family))
        if brand and brand.font_family:
            self.font_combo.setCurrentFont(QFont(brand.font_family))
        self.font_size_spin = QDoubleSpinBox(self)
        self.font_size_spin.setRange(0, 30)
        self.font_size_spin.setSpecialValueText("как в системе")
        self.font_size_spin.setValue(brand.font_size if brand else 0)
        font_form = QFormLayout()
        font_form.addRow(self.font_check)
        font_form.addRow("Гарнитура", self.font_combo)
        font_form.addRow("Размер", self.font_size_spin)
        font_group = QGroupBox("Шрифт программы", self)
        font_group.setLayout(font_form)

        self.letter_font_check = QCheckBox("Свой шрифт письма", self)
        self.letter_font_check.setChecked(bool(brand and brand.letter_font_family))
        self.letter_font_combo = QFontComboBox(self)
        if brand and brand.letter_font_family:
            self.letter_font_combo.setCurrentFont(QFont(brand.letter_font_family))
        self.letter_size_spin = QDoubleSpinBox(self)
        self.letter_size_spin.setRange(0, 40)
        self.letter_size_spin.setSpecialValueText("как в программе")
        self.letter_size_spin.setValue(brand.letter_font_size if brand else 0)
        self._letter_color = brand.letter_text_color if brand else ""
        self.letter_color_button = QPushButton(self)
        self.letter_color_button.clicked.connect(self._pick_letter_color)
        self._paint_letter_color()
        self.footer_edit = QPlainTextEdit(brand.letter_footer_html if brand else "", self)
        self.footer_edit.setPlaceholderText("<p style=\"color:#777\">Сообщение и вложения конфиденциальны…</p>")
        self.footer_edit.setFixedHeight(70)
        letter_form = QFormLayout()
        letter_form.addRow(self.letter_font_check)
        letter_form.addRow("Гарнитура", self.letter_font_combo)
        letter_form.addRow("Размер", self.letter_size_spin)
        letter_form.addRow("Цвет текста", self.letter_color_button)
        letter_form.addRow("Колонтитул (HTML)", self.footer_edit)
        letter_group = QGroupBox("Оформление письма", self)
        letter_group.setLayout(letter_form)

        self.signature_edit = QPlainTextEdit(brand.signature_html if brand else "", self)
        self.signature_edit.setPlaceholderText(
            "<p>С уважением,<br>{name}<br>{title}<br>{department}<br>{organization}<br>Тел.: {phone}<br>{email}</p>"
        )
        signature_hint = QLabel(
            "Шаблон стандартной подписи (HTML). Подстановки: {name}, {title}, {department}, {organization}, "
            "{phone}, {email} — из карточки сотрудника в адресной книге. Строка с пустой подстановкой в подпись "
            "не попадает.", self,
        )
        signature_hint.setWordWrap(True)
        signature_layout = QVBoxLayout()
        signature_layout.addWidget(self.signature_edit)
        signature_layout.addWidget(signature_hint)
        signature_group = QGroupBox("Стандартная подпись", self)
        signature_group.setLayout(signature_layout)

        main_form = QFormLayout()
        main_form.addRow("Код (латиницей)", self.id_edit)
        main_form.addRow("Название", self.name_edit)
        main_form.addRow("Организация", self.organization_edit)
        main_form.addRow("Основа", self.base_combo)
        main_form.addRow(self.default_check)
        main_form.addRow("Выгрузка переписки", self.export_combo)

        left = QVBoxLayout()
        left.addLayout(main_form)
        left.addWidget(colors_group)
        left.addStretch(1)
        right = QVBoxLayout()
        right.addWidget(font_group)
        right.addWidget(letter_group)
        right.addWidget(signature_group, 1)
        columns = QHBoxLayout()
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(columns)
        layout.addWidget(buttons)
        self.resize(980, 720)

    def _paint_color(self, key: str) -> None:
        value = self._colors.get(key, "")
        button = self._color_buttons[key]
        button.setText(value or "как в основе")
        button.setIcon(_dot_icon(value) if value else QIcon())

    def _pick_color(self, key: str) -> None:
        color = QColorDialog.getColor(QColor(self._colors.get(key, "#ffffff")), self, "Цвет")
        if color.isValid():
            self._colors[key] = color.name()
            self._paint_color(key)

    def _paint_letter_color(self) -> None:
        self.letter_color_button.setText(self._letter_color or "как в программе")
        self.letter_color_button.setIcon(_dot_icon(self._letter_color) if self._letter_color else QIcon())

    def _pick_letter_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._letter_color or "#202124"), self, "Цвет текста письма")
        if color.isValid():
            self._letter_color = color.name()
            self._paint_letter_color()

    def to_brand(self) -> branding.Brand:
        return branding.parse_brand({
            "id": self.id_edit.text().strip(),
            "name": self.name_edit.text().strip(),
            "organization": self.organization_edit.text().strip(),
            "base": self.base_combo.currentData(),
            "colors": dict(self._colors),
            "font": {
                "family": self.font_combo.currentFont().family() if self.font_check.isChecked() else "",
                "size": self.font_size_spin.value(),
            },
            "letter": {
                "font_family": self.letter_font_combo.currentFont().family() if self.letter_font_check.isChecked() else "",
                "font_size": self.letter_size_spin.value(),
                "text_color": self._letter_color,
                "footer_html": self.footer_edit.toPlainText().strip(),
            },
            "signature_html": self.signature_edit.toPlainText().strip(),
            "default": self.default_check.isChecked(),
            "data_export": self.export_combo.currentData(),
        })

    def accept(self) -> None:  # noqa: N802 - Qt override
        try:
            self.to_brand()
        except ValueError as exc:
            QMessageBox.warning(self, "Оформление", f"Проверьте поля: {exc}")
            return
        super().accept()


class BrandsDialog(QDialog):
    """Оформления организаций: список, правка, выгрузка файла для
    администратора. Системные оформления (/etc/redmail/brands) только
    показываются — их меняет администратор."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Оформления организаций")
        self.list_widget = QListWidget(self)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._on_edit())
        add_button = QPushButton("Добавить…", self)
        add_button.clicked.connect(self._on_add)
        self.edit_button = QPushButton("Изменить…", self)
        self.edit_button.clicked.connect(self._on_edit)
        self.delete_button = QPushButton("Удалить", self)
        self.delete_button.clicked.connect(self._on_delete)
        self.export_button = QPushButton("Выгрузить файл…", self)
        self.export_button.setToolTip(
            f"Сохранить оформление файлом, чтобы администратор разложил его по машинам в {branding.SYSTEM_BRANDS_DIR}"
        )
        self.export_button.clicked.connect(self._on_export)
        import_button = QPushButton("Загрузить файл…", self)
        import_button.clicked.connect(self._on_import)
        side = QVBoxLayout()
        for button in (add_button, self.edit_button, self.delete_button, self.export_button, import_button):
            side.addWidget(button)
        side.addStretch(1)
        body = QHBoxLayout()
        body.addWidget(self.list_widget, 1)
        body.addLayout(side)
        hint = QLabel(
            f"Оформления из {branding.SYSTEM_BRANDS_DIR} разложены администратором и здесь не меняются. "
            "Выбрать оформление — в списке «Тема оформления».", self,
        )
        hint.setWordWrap(True)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(body)
        layout.addWidget(hint)
        layout.addWidget(close)
        self.list_widget.currentRowChanged.connect(self._update_buttons)
        self._reload()
        self.resize(620, 360)

    def _reload(self) -> None:
        self.list_widget.clear()
        for brand in branding.load_brands():
            system = brand.source.startswith(str(branding.SYSTEM_BRANDS_DIR))
            item = QListWidgetItem(_dot_icon(brand.colors.get("accent", "#1a73e8")), brand.name + (" — от администратора" if system else ""))
            item.setData(Qt.ItemDataRole.UserRole, brand.id)
            item.setData(Qt.ItemDataRole.UserRole + 1, system)
            self.list_widget.addItem(item)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        self._update_buttons()

    def _current(self) -> tuple[branding.Brand | None, bool]:
        item = self.list_widget.currentItem()
        if item is None:
            return None, False
        brand = next((b for b in branding.load_brands() if b.id == item.data(Qt.ItemDataRole.UserRole)), None)
        return brand, bool(item.data(Qt.ItemDataRole.UserRole + 1))

    def _update_buttons(self, *_args) -> None:
        brand, system = self._current()
        self.edit_button.setEnabled(brand is not None and not system)
        self.delete_button.setEnabled(brand is not None and not system)
        self.export_button.setEnabled(brand is not None)

    def _on_add(self) -> None:
        dialog = BrandEditDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            branding.save_user_brand(dialog.to_brand())
            self._reload()

    def _on_edit(self) -> None:
        brand, system = self._current()
        if brand is None or system:
            return
        dialog = BrandEditDialog(self, brand)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            branding.save_user_brand(dialog.to_brand())
            self._reload()

    def _on_delete(self) -> None:
        brand, system = self._current()
        if brand is None or system:
            return
        if QMessageBox.question(self, "Удалить оформление", f"Удалить оформление «{brand.name}»?") == QMessageBox.StandardButton.Yes:
            branding.delete_user_brand(brand.id)
            self._reload()

    def _on_export(self) -> None:
        brand, _system = self._current()
        if brand is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Выгрузить оформление", f"{brand.id}.json", "Оформление (*.json)")
        if path:
            Path(path).write_text(json.dumps(branding.brand_to_dict(brand), ensure_ascii=False, indent=2), encoding="utf-8")

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить оформление", "", "Оформление (*.json)")
        if not path:
            return
        try:
            brand = branding.parse_brand(json.loads(Path(path).read_text(encoding="utf-8")), path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Оформление", f"Файл не подходит: {exc}")
            return
        branding.save_user_brand(brand)
        self._reload()


class GreetingsDialog(QDialog):
    """Свой список приветствий: текст и время, когда оно подходит. В письмо
    ставится первое подходящее по времени; строка без времени — всегда."""

    def __init__(self, parent, greetings: list[Greeting]):
        super().__init__(parent)
        self.setWindowTitle("Приветствия")
        self.resize(640, 420)
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["Приветствие", "С (ЧЧ:ММ)", "До (ЧЧ:ММ)"])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        for greeting in greetings:
            self._add_row(greeting)
        add_button = QPushButton("Добавить", self)
        add_button.clicked.connect(lambda: self._add_row(Greeting(""), edit=True))
        remove_button = QPushButton("Удалить", self)
        remove_button.clicked.connect(self._remove_row)
        up_button = QPushButton("Выше", self)
        up_button.clicked.connect(lambda: self._move_row(-1))
        down_button = QPushButton("Ниже", self)
        down_button.clicked.connect(lambda: self._move_row(1))
        reset_button = QPushButton("По времени суток", self)
        reset_button.setToolTip("Вернуть «Доброе утро!», «Добрый день!», «Добрый вечер!», «Здравствуйте!»")
        reset_button.clicked.connect(self._reset)
        side = QVBoxLayout()
        for button in (add_button, remove_button, up_button, down_button, reset_button):
            side.addWidget(button)
        side.addStretch(1)
        body = QHBoxLayout()
        body.addWidget(self.table, 1)
        body.addLayout(side)
        hint = QLabel(
            "В письмо ставится первое приветствие сверху, подходящее по времени. Время можно не указывать — "
            "тогда приветствие подходит всегда (поставьте такое последним). Интервал может переходить через "
            "полночь: с 22:00 до 04:00. В окне письма приветствие можно сменить или убрать.", self,
        )
        hint.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(body)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    def _add_row(self, greeting: Greeting, *, edit: bool = False) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column, value in enumerate((greeting.text, greeting.start, greeting.end)):
            self.table.setItem(row, column, QTableWidgetItem(value))
        if edit:
            self.table.setCurrentCell(row, 0)
            self.table.editItem(self.table.item(row, 0))

    def _remove_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def _move_row(self, step: int) -> None:
        row = self.table.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self.table.rowCount():
            return
        values = [self.table.item(row, c).text() if self.table.item(row, c) else "" for c in range(3)]
        self.table.removeRow(row)
        self.table.insertRow(target)
        for column, value in enumerate(values):
            self.table.setItem(target, column, QTableWidgetItem(value))
        self.table.setCurrentCell(target, 0)

    def _reset(self) -> None:
        from redmail.config_store import GREETINGS_TIME_OF_DAY

        self.table.setRowCount(0)
        for greeting in GREETINGS_TIME_OF_DAY:
            self._add_row(greeting)

    def greetings(self) -> list[Greeting]:
        result = []
        for row in range(self.table.rowCount()):
            text, start, end = (self.table.item(row, c).text().strip() if self.table.item(row, c) else "" for c in range(3))
            if text or start or end:
                result.append(Greeting(text, start, end))
        return result

    def accept(self) -> None:  # noqa: N802 - Qt override
        greetings = self.greetings()
        if not greetings:
            QMessageBox.warning(self, "Приветствия", "Добавьте хотя бы одно приветствие.")
            return
        try:
            for greeting in greetings:
                validate_greeting(greeting)
        except ValueError as exc:
            QMessageBox.warning(self, "Приветствия", f"Проверьте строку: {exc}")
            return
        super().accept()


class SettingsDialog(QDialog):
    """Один диалог на всё: учётная запись (было отдельным «Подключиться…»),
    интервал проверки почты и расположение панели чтения."""

    def __init__(
        self,
        parent=None,
        *,
        poll_interval_minutes: int = 5,
        pane_orientation: str = "vertical",
        archive_storage_dir: Path | None = None,
        theme: str = "light",
        profile_dir: Path | None = None,
        body_max_size_mb: int = 25,
        auto_archive_size_mb: int = 500,
        storage_stats: dict | None = None,
        auto_archive_enabled: bool = True,
        maintenance_window: tuple[bool, int, int] = (False, 22, 7),
        others_reminder: tuple[str, int, tuple[str, ...]] = ("window", 15, ()),
        tls_ca_file: str = "",
        font_scale: float = 1.0,
        greeting_mode: str = GREETING_NONE,
        accounts: list[tuple[str, str]] | None = None,
        disabled_accounts: tuple[str, ...] = (),
        plugins_enabled: dict[str, bool] | None = None,
        category_store=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Параметры")


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

        self.theme_combo = QComboBox()
        self._fill_theme_combo(theme)
        brands_button = QPushButton("Оформления организаций…", self)
        brands_button.setToolTip(
            "Цвета, шрифты, оформление письма и стандартная подпись для каждой организации — "
            "их задаёт заказчик; файл оформления можно выгрузить и разослать администратору"
        )
        brands_button.clicked.connect(self._on_manage_brands)
        theme_row = QHBoxLayout()
        theme_row.addWidget(self.theme_combo, 1)
        theme_row.addWidget(brands_button)

        # Корневой сертификат организации: браузер берёт его из системного
        # хранилища Windows, а программа на RED OS — из своего набора, где
        # внутреннего ЦС нет (жалоба: "в браузере открывается, а тут
        # сертификат"). Пусто — системное хранилище.
        self.tls_ca_edit = QLineEdit(tls_ca_file, self)
        self.tls_ca_edit.setPlaceholderText("Пусто — системное хранилище сертификатов")
        self.tls_ca_edit.setClearButtonEnabled(True)
        tls_ca_browse = QPushButton("Обзор…", self)
        tls_ca_browse.clicked.connect(self._on_browse_tls_ca)
        tls_ca_row = QHBoxLayout()
        tls_ca_row.addWidget(self.tls_ca_edit, 1)
        tls_ca_row.addWidget(tls_ca_browse)

        # Масштаб шрифта: раньше был только ползунок в строке состояния и
        # его никто не находил (пожелание: "может в параметры вынести
        # настройку?"). Меняет размер во всём окне, включая календарь.
        self.font_scale_spin = QSpinBox(self)
        self.font_scale_spin.setRange(50, 200)
        self.font_scale_spin.setSingleStep(5)
        self.font_scale_spin.setSuffix(" %")
        self.font_scale_spin.setValue(int(round(font_scale * 100)))

        # Приветствие первой строкой нового письма, ответа и пересылки.
        self.greeting_combo = QComboBox(self)
        self.greeting_combo.addItem("Не добавлять", GREETING_NONE)
        self.greeting_combo.addItem("«Здравствуйте!»", GREETING_HELLO)
        self.greeting_combo.addItem("По времени суток: «Доброе утро!», «Добрый день!», «Добрый вечер!»", GREETING_TIME_OF_DAY)
        self.greeting_combo.addItem("Свой список", GREETING_LIST)
        greeting_index = self.greeting_combo.findData(greeting_mode)
        self.greeting_combo.setCurrentIndex(greeting_index if greeting_index >= 0 else 0)
        greetings_button = QPushButton("Список приветствий…", self)
        greetings_button.setToolTip("Свои приветствия и время, когда каждое подходит")
        greetings_button.clicked.connect(self._on_edit_greetings)
        greeting_row = QHBoxLayout()
        greeting_row.addWidget(self.greeting_combo, 1)
        greeting_row.addWidget(greetings_button)

        general_form = QFormLayout()
        general_form.addRow("Проверять почту каждые", self.interval_edit)
        general_form.addRow("Приветствие в начале письма", greeting_row)
        general_form.addRow("Масштаб шрифта", self.font_scale_spin)
        general_form.addRow("Панель чтения", self.orientation_vertical)
        general_form.addRow("", self.orientation_horizontal)
        general_form.addRow("Тема оформления", theme_row)
        general_form.addRow("Корневой сертификат (PEM)", tls_ca_row)
        general_group = QGroupBox("Общие")
        general_group.setLayout(general_form)

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
        signatures_button = QPushButton("Подписи…", self)
        signatures_button.setToolTip("Одна или несколько подписей для писем — выбираются при написании письма")
        signatures_button.clicked.connect(self._on_manage_signatures)
        # Список подключённых записей с выключателем: на время переезда
        # приходится держать две системы, но два одинаковых ящика сразу —
        # это двойные письма (жалоба: "логично было бы переключать их").
        self.accounts_list = QListWidget(self)
        self.accounts_list.setFixedHeight(110)
        for key, title in (accounts or []):
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked if key in (disabled_accounts or ()) else Qt.CheckState.Checked)
            self.accounts_list.addItem(item)
        accounts_hint = QLabel(
            "Снятая галочка — запись выключена: она не подключается, её письма не загружаются, "
            "но настройки и уже скачанная почта сохраняются. Вернули галочку — запись подключается сразу.",
            self,
        )
        accounts_hint.setWordWrap(True)
        # Всё, что относится к одной записи — подключение, удаление писем с
        # сервера, замена доменов, — в её собственном окне (пожелание: «лучше
        # открывать отдельное окно для выбранной учётки по кнопке»).
        self.edit_account_button = QPushButton("Настроить…", self)
        self.edit_account_button.setToolTip(
            "Подключение, удаление писем с сервера и замена доменов получателей — для выбранной учётной записи"
        )
        self.edit_account_button.clicked.connect(self._on_edit_account)
        self.accounts_list.itemDoubleClicked.connect(lambda _item: self._on_edit_account())
        self.accounts_list.currentRowChanged.connect(lambda row: self.edit_account_button.setEnabled(row >= 0))


        accounts_rules_layout = QVBoxLayout()
        accounts_rules_layout.addWidget(self.accounts_list)
        accounts_rules_layout.addWidget(accounts_hint)
        accounts_rules_layout.addWidget(self.edit_account_button, 0, Qt.AlignmentFlag.AlignLeft)
        if self.accounts_list.count():
            self.accounts_list.setCurrentRow(0)
        else:
            self.edit_account_button.setEnabled(False)
        accounts_rules_layout.addWidget(add_account_button)
        accounts_rules_layout.addWidget(add_ews_account_button)
        accounts_rules_layout.addWidget(mail_rules_button)
        accounts_rules_layout.addWidget(apply_rules_button)
        accounts_rules_layout.addWidget(signatures_button)
        accounts_rules_group = QGroupBox("Учётные записи и правила почты", self)
        accounts_rules_group.setLayout(accounts_rules_layout)

        # Напоминания о ЧУЖИХ встречах: в приглашении с сервера способ
        # выбирать некому (пожелание: «про чужие события — упоминать
        # автоматом или включить настройку, например по автору»).
        others_mode, others_minutes, others_authors = others_reminder
        self.others_remind_mode_combo = QComboBox(self)
        for label, value in _REMIND_MODE_OPTIONS:
            self.others_remind_mode_combo.addItem(label, value)
        mode_index = self.others_remind_mode_combo.findData(others_mode)
        self.others_remind_mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        self.others_remind_when_combo = QComboBox(self)
        for label, value in _REMIND_WHEN_OPTIONS:
            self.others_remind_when_combo.addItem(label, value)
        when_index = self.others_remind_when_combo.findData(int(others_minutes))
        self.others_remind_when_combo.setCurrentIndex(when_index if when_index >= 0 else 2)
        self.others_remind_authors_edit = QLineEdit(", ".join(others_authors), self)
        self.others_remind_authors_edit.setPlaceholderText("пусто — обо всех; иначе: Орлов, petrov@corp.ru")
        others_row = QHBoxLayout()
        others_row.addWidget(self.others_remind_mode_combo)
        others_row.addWidget(self.others_remind_when_combo)
        others_row.addStretch(1)
        others_form = QFormLayout()
        others_form.addRow("Напоминать", others_row)
        others_form.addRow("Только от авторов", self.others_remind_authors_edit)
        others_hint = QLabel(
            "О своих встречах напоминание выбирается в самом окне встречи. Здесь — про чужие: "
            "приглашения коллег и встречи из их календарей. В напоминании называется автор.", self,
        )
        others_hint.setWordWrap(True)
        others_form.addRow(others_hint)
        others_group = QGroupBox("Напоминания о чужих встречах", self)
        others_group.setLayout(others_form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        # Вкладки вместо одного длинного столбца: окно не помещалось на
        # экран (жалоба: "окно параметров не входит на экран — сделай
        # вкладки, разнеси функционал").
        tabs = QTabWidget(self)
        tabs.addTab(_settings_tab(general_group, others_group, archive_dir_group), "Общие")
        layout = QVBoxLayout(self)

        # Хранилище (переход на хранение «как в Outlook»): каталог профиля с
        # базами почты/календаря/контактов, порог размера письма для фоновой
        # загрузки, порог автоархива по размеру базы.
        self.profile_dir_edit = QLineEdit(str(profile_dir or profile.default_profile_dir()))
        profile_dir_browse = QPushButton("Обзор…", self)
        profile_dir_browse.clicked.connect(self._on_browse_profile_dir)
        profile_dir_row = QHBoxLayout()
        profile_dir_row.addWidget(self.profile_dir_edit)
        profile_dir_row.addWidget(profile_dir_browse)
        self.body_max_size_edit = QSpinBox(self)
        self.body_max_size_edit.setRange(1, 2000)
        self.body_max_size_edit.setSuffix(" МБ")
        self.body_max_size_edit.setValue(int(body_max_size_mb))
        self.auto_archive_check = QCheckBox("Автоархив по размеру базы: самые старые письма — в файл архива", self)
        self.auto_archive_check.setChecked(bool(auto_archive_enabled))
        self.auto_archive_size_edit = QSpinBox(self)
        self.auto_archive_size_edit.setRange(50, 100000)
        self.auto_archive_size_edit.setSuffix(" МБ")
        self.auto_archive_size_edit.setValue(int(auto_archive_size_mb))
        maintenance_enabled, maintenance_start, maintenance_end = maintenance_window
        self.maintenance_check = QCheckBox(
            "Обслуживать базу (автоархив, докачка писем, сжатие) только в указанные часы", self
        )
        self.maintenance_check.setChecked(bool(maintenance_enabled))
        self.maintenance_start_edit = QSpinBox(self)
        self.maintenance_start_edit.setRange(0, 23)
        self.maintenance_start_edit.setSuffix(":00")
        self.maintenance_start_edit.setValue(int(maintenance_start))
        self.maintenance_end_edit = QSpinBox(self)
        self.maintenance_end_edit.setRange(0, 23)
        self.maintenance_end_edit.setSuffix(":00")
        self.maintenance_end_edit.setValue(int(maintenance_end))
        maintenance_row = QHBoxLayout()
        maintenance_row.addWidget(QLabel("с", self))
        maintenance_row.addWidget(self.maintenance_start_edit)
        maintenance_row.addWidget(QLabel("до", self))
        maintenance_row.addWidget(self.maintenance_end_edit)
        maintenance_row.addStretch(1)
        stats = storage_stats or {}
        stats_text = (
            f"База почты: {stats.get('db_bytes', 0) / (1024 * 1024):.1f} МБ, писем {stats.get('messages', 0)}, "
            f"с телом {stats.get('with_body', 0)}"
        ) if stats else "База почты ещё не создана"
        storage_stats_label = QLabel(stats_text, self)
        storage_hint = QLabel("Смена каталога профиля вступает в силу после перезапуска программы.", self)
        storage_hint.setWordWrap(True)
        storage_form = QFormLayout()
        storage_form.addRow("Каталог профиля", profile_dir_row)
        storage_form.addRow("Не скачивать фоном письма больше", self.body_max_size_edit)
        storage_form.addRow(self.auto_archive_check)
        storage_form.addRow("Автоархив при размере базы", self.auto_archive_size_edit)
        storage_form.addRow(self.maintenance_check)
        storage_form.addRow("Часы обслуживания", maintenance_row)
        storage_form.addRow(storage_stats_label)
        storage_form.addRow(storage_hint)
        storage_group = QGroupBox("Хранилище")
        storage_group.setLayout(storage_form)
        self._transfer_workers: list[_CallableWorker] = []
        tabs.addTab(_settings_tab(storage_group, self._build_transfer_group()), "Хранилище")

        # Голосовой помощник (audioreferent) — отдельный продукт, здесь только
        # управление им: включить/выключить сервис, состояние, его настройки,
        # журнал, проверка связи по локальному каналу.
        self.voice_status_label = QLabel("", self)
        self.voice_status_label.setWordWrap(True)
        self.voice_enable_check = QCheckBox("Включён — служба помощника запускается при входе в систему", self)
        self.voice_enable_check.toggled.connect(self._on_voice_toggle)
        self.voice_settings_button = QPushButton("Настройки помощника…", self)
        self.voice_settings_button.clicked.connect(self._on_voice_settings)
        self.voice_log_button = QPushButton("Журнал помощника…", self)
        self.voice_log_button.clicked.connect(self._on_voice_log)
        self.voice_check_button = QPushButton("Проверить связь", self)
        self.voice_check_button.clicked.connect(self._on_voice_check)
        voice_buttons = QHBoxLayout()
        voice_buttons.addWidget(self.voice_settings_button)
        voice_buttons.addWidget(self.voice_log_button)
        voice_buttons.addWidget(self.voice_check_button)
        voice_buttons.addStretch(1)
        self._voice_updating = False
        self._refresh_voice_state()
        tabs.addTab(_settings_tab(accounts_rules_group), "Учётные записи")

        # Подключаемые модули: включаются и выключаются без перезапуска.
        self._category_store = category_store
        # Голосовой помощник — тоже модуль (пожелание: «голосовой помощник тоже
        # модуль, в закладку модули»), отдельной вкладки у него больше нет.
        self.plugin_checks: dict[str, QCheckBox] = {}
        module_groups: list[QGroupBox] = []
        for plugin in mail_plugins.available_plugins():
            description = QLabel(plugin.description, self)
            description.setWordWrap(True)
            module_layout = QVBoxLayout()
            module_layout.addWidget(description)
            if plugin.id == mail_plugins.VOICE_ASSISTANT.id:
                # Включение помощника — это его служба, а не флаг в настройках почты.
                module_layout.addWidget(self.voice_status_label)
                module_layout.addWidget(self.voice_enable_check)
                module_layout.addLayout(voice_buttons)
            else:
                check = QCheckBox("Включён", self)
                check.setChecked((plugins_enabled or {}).get(plugin.id, plugin.enabled_by_default))
                self.plugin_checks[plugin.id] = check
                module_layout.addWidget(check)
                if plugin.id == mail_plugins.CATEGORIES.id:
                    categories_button = QPushButton("Категории…", self)
                    categories_button.setToolTip("Список категорий, их цвета и описание: адресаты и слова в теме")
                    categories_button.clicked.connect(self._on_manage_categories)
                    categories_button.setEnabled(category_store is not None)
                    check.toggled.connect(lambda on, button=categories_button: button.setEnabled(on and self._category_store is not None))
                    module_layout.addWidget(categories_button, 0, Qt.AlignmentFlag.AlignLeft)
            group = QGroupBox(plugin.title, self)
            group.setLayout(module_layout)
            module_groups.append(group)
        tabs.addTab(_settings_tab(*module_groups), "Модули")
        layout.addWidget(tabs)
        layout.addWidget(buttons)
        self.resize(760, 560)

    # ---- Голосовой помощник ---------------------------------------------------

    def _refresh_voice_state(self) -> None:
        try:
            st = voice_assistant.state()
        except Exception as exc:  # настройки не должны падать из-за помощника
            st = voice_assistant.AssistantState(installed=False, detail=str(exc))
        self._voice_updating = True
        try:
            if not st.installed:
                self.voice_status_label.setText(
                    "Помощник не установлен. Установите пакет audioreferent (ставится вместе с почтой, "
                    "если лежит рядом при установке), затем откройте этот раздел снова."
                )
                self.voice_enable_check.setChecked(False)
            else:
                parts = [f"Состояние: {st.status_text}"]
                if st.version:
                    parts.append(f"версия {st.version}")
                if st.wake_word:
                    parts.append(f"ключевое слово «{st.wake_word}»")
                if st.detail:
                    parts.append(st.detail)
                self.voice_status_label.setText("; ".join(parts))
                self.voice_enable_check.setChecked(st.enabled or st.active)
            for widget in (self.voice_enable_check, self.voice_settings_button, self.voice_log_button, self.voice_check_button):
                widget.setEnabled(st.installed)
        finally:
            self._voice_updating = False

    def _on_voice_toggle(self, checked: bool) -> None:
        if self._voice_updating:
            return
        ok, message = voice_assistant.set_enabled(checked)
        if not ok:
            QMessageBox.warning(self, "Голосовой помощник", message)
        self._refresh_voice_state()

    def _on_voice_settings(self) -> None:
        if not voice_assistant.open_settings():
            QMessageBox.warning(self, "Голосовой помощник", "Не удалось открыть настройки помощника.")

    def _on_voice_log(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Журнал голосового помощника")
        dialog.resize(800, 500)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit(dialog)
        text.setReadOnly(True)
        text.setPlainText(voice_assistant.recent_log())
        layout.addWidget(text)
        close_button = QPushButton("Закрыть", dialog)
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.exec()

    def _on_voice_check(self) -> None:
        st = voice_assistant.state()
        channel = voice_assistant.ipc_endpoint_published()
        lines = [
            f"Помощник: {st.status_text}.",
            "Канал управления почты: " + ("опубликован, помощник сможет открывать письма и встречи." if channel else "не опубликован — перезапустите почту."),
        ]
        if st.installed and not st.active:
            lines.append("Включите помощника галочкой выше, чтобы команды обрабатывались.")
        QMessageBox.information(self, "Проверка связи", "\n".join(lines))

    def _on_browse_profile_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Каталог профиля", self.profile_dir_edit.text())
        if chosen:
            self.profile_dir_edit.setText(chosen)

    def profile_dir(self) -> Path:
        text = self.profile_dir_edit.text().strip()
        return Path(text) if text else profile.default_profile_dir()

    def body_max_size_mb(self) -> int:
        return self.body_max_size_edit.value()

    def auto_archive_size_mb(self) -> int:
        return self.auto_archive_size_edit.value()

    def auto_archive_enabled(self) -> bool:
        return self.auto_archive_check.isChecked()


    def tls_ca_file(self) -> str:
        return self.tls_ca_edit.text().strip()

    def font_scale(self) -> float:
        return self.font_scale_spin.value() / 100

    def plugins_enabled(self) -> dict[str, bool]:
        return {plugin_id: check.isChecked() for plugin_id, check in self.plugin_checks.items()}

    def _on_manage_categories(self) -> None:
        if self._category_store is not None:
            CategoriesDialog(self, self._category_store).exec()

    def greeting_mode(self) -> str:
        return self.greeting_combo.currentData() or GREETING_NONE

    def disabled_accounts(self) -> list[str]:
        return [
            self.accounts_list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.accounts_list.count())
            if self.accounts_list.item(row).checkState() != Qt.CheckState.Checked
        ]

    def _on_browse_tls_ca(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Корневой сертификат организации", self.tls_ca_edit.text() or str(Path.home()),
            "Сертификаты (*.pem *.crt *.cer);;Все файлы (*)",
        )
        if path:
            self.tls_ca_edit.setText(path)

    def others_reminder(self) -> tuple[str, int, list[str]]:
        authors = [
            part.strip()
            for part in self.others_remind_authors_edit.text().replace(";", ",").split(",")
            if part.strip()
        ]
        return (
            self.others_remind_mode_combo.currentData() or calendar_store.REMIND_NONE,
            int(self.others_remind_when_combo.currentData() or 15),
            authors,
        )

    def maintenance_window(self) -> tuple[bool, int, int]:
        return (
            self.maintenance_check.isChecked(),
            self.maintenance_start_edit.value(),
            self.maintenance_end_edit.value(),
        )

    def _on_browse_archive_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Каталог для новых архивов", self.archive_dir_edit.text())
        if chosen:
            self.archive_dir_edit.setText(chosen)

    def archive_storage_dir(self) -> Path:
        text = self.archive_dir_edit.text().strip()
        return Path(text) if text else default_archive_storage_dir()

    def _on_add_account(self) -> None:
        if self.parent() is not None:
            self._add_account_item(self.parent().on_add_account())

    def _on_add_ews_account(self) -> None:
        if self.parent() is not None:
            self._add_account_item(self.parent().on_add_ews_account())

    def _add_account_item(self, added) -> None:
        if not added:
            return
        key, title = added
        item = QListWidgetItem(title)
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        self.accounts_list.addItem(item)
        self.accounts_list.setCurrentItem(item)

    def _on_edit_account(self) -> None:
        item = self.accounts_list.currentItem()
        if item is None or self.parent() is None:
            return
        result = self.parent().on_edit_account(item.data(Qt.ItemDataRole.UserRole))
        if result:
            key, title = result
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setText(title)

    def _on_mail_rules(self) -> None:
        if self.parent() is not None:
            self.parent().on_mail_rules()

    def _on_apply_mail_rules(self) -> None:
        if self.parent() is not None:
            self.parent().on_apply_mail_rules()

    def _on_manage_signatures(self) -> None:
        if self.parent() is not None:
            self.parent().on_manage_signatures()

    def poll_interval_minutes(self) -> int:
        return self.interval_edit.value()

    def pane_orientation(self) -> str:
        return "horizontal" if self.orientation_horizontal.isChecked() else "vertical"

    def _on_edit_greetings(self) -> None:
        dialog = GreetingsDialog(self, load_greetings())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        save_greetings(dialog.greetings())
        self.greeting_combo.setCurrentIndex(self.greeting_combo.findData(GREETING_LIST))

    def _build_transfer_group(self) -> QGroupBox:
        """Перенос профиля на другой компьютер и выгрузка всей переписки.
        Кто это делает — сотрудник сам или администратор — задаёт оформление
        организации; без оформлений решает пользователь."""
        mode, brand = profile_transfer.export_policy()
        allowed = profile_transfer.allowed_here()
        self.export_mail_button = QPushButton("Выгрузить переписку…", self)
        self.export_mail_button.setToolTip("Вся переписка и архивы — в mbox или файлы EML, для другой почтовой программы")
        self.export_mail_button.clicked.connect(self._on_export_mail)
        self.export_profile_button = QPushButton("Выгрузить профиль…", self)
        self.export_profile_button.setToolTip("Настройки, почта, календарь, контакты и архивы одним файлом .rmprofile")
        self.export_profile_button.clicked.connect(self._on_export_profile)
        self.import_profile_button = QPushButton("Загрузить профиль…", self)
        self.import_profile_button.setToolTip("Профиль с другого компьютера; прежние данные сохраняются в резервную копию")
        self.import_profile_button.clicked.connect(self._on_import_profile)
        buttons = QHBoxLayout()
        for button in (self.export_mail_button, self.export_profile_button, self.import_profile_button):
            button.setEnabled(allowed)
            buttons.addWidget(button)
        buttons.addStretch(1)
        if not allowed:
            text = (
                f"Выгрузку переписки и перенос профиля выполняет администратор — так задано в оформлении "
                f"организации «{brand.name}». Обратитесь к администратору."
            )
        elif mode == profile_transfer.EXPORT_BY_USER and brand is not None:
            text = f"Разрешено сотруднику (оформление «{brand.name}»). Выгрузки записываются в системный журнал."
        else:
            text = "Пароли не переносятся — на новом компьютере их вводят заново. Выгрузки записываются в журнал."
        policy_label = QLabel(text, self)
        policy_label.setWordWrap(True)
        layout = QVBoxLayout()
        layout.addLayout(buttons)
        layout.addWidget(policy_label)
        pending = profile_transfer.pending_import()
        if pending is not None:
            pending_label = QLabel(f"Профиль из {pending} загрузится при следующем запуске программы.", self)
            pending_label.setWordWrap(True)
            cancel_button = QPushButton("Отменить загрузку", self)

            def cancel_pending() -> None:
                profile_transfer.cancel_pending_import()
                pending_label.setText("Загрузка профиля отменена.")
                cancel_button.setEnabled(False)

            cancel_button.clicked.connect(cancel_pending)
            layout.addWidget(pending_label)
            layout.addWidget(cancel_button, 0, Qt.AlignmentFlag.AlignLeft)
        group = QGroupBox("Перенос на другой компьютер и выгрузка переписки", self)
        group.setLayout(layout)
        return group

    def _run_transfer(self, title: str, fn, *args, on_success, cancellable: bool = False, **kwargs) -> None:
        progress = QProgressDialog(title, "Остановить" if cancellable else "", 0, 0, self)
        progress.setWindowTitle(title)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        if not cancellable:
            progress.setCancelButton(None)
        state = {"count": 0, "stop": False}
        if cancellable:
            progress.canceled.connect(lambda: state.__setitem__("stop", True))
            kwargs["stop"] = lambda: state["stop"]
            kwargs["progress"] = lambda count: state.__setitem__("count", count)
            timer = QTimer(progress)
            timer.timeout.connect(lambda: progress.setLabelText(f"{title}: писем {state['count']}"))
            timer.start(500)
        progress.show()
        worker = _CallableWorker(fn, *args, parent=self, **kwargs)

        def finish() -> None:
            progress.close()
            if worker in self._transfer_workers:
                self._transfer_workers.remove(worker)

        def succeeded(result: object) -> None:
            finish()
            on_success(result)

        def failed(message: str) -> None:
            finish()
            if state["stop"]:
                QMessageBox.information(self, title, "Выгрузка остановлена, выгруженная часть осталась в каталоге.")
            else:
                QMessageBox.warning(self, title, message)

        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        self._transfer_workers.append(worker)
        worker.start()

    def _on_export_mail(self) -> None:
        if not profile_transfer.allowed_here():
            return
        labels = list(mail_export.FORMATS.values())
        label, ok = QInputDialog.getItem(self, "Выгрузить переписку", "Формат:", labels, 0, False)
        if not ok:
            return
        fmt = next(key for key, value in mail_export.FORMATS.items() if value == label)
        base = QFileDialog.getExistingDirectory(self, "Куда выгрузить переписку")
        if not base:
            return
        target = Path(base) / f"Переписка {datetime.now():%Y-%m-%d %H-%M}"

        def done(result) -> None:
            profile_transfer.audit(
                "Выгрузка переписки", формат=fmt, каталог=result.target, писем=result.messages,
                без_тела=result.headers_only, не_выгружено=result.failed, архивов=result.archives,
            )
            text = f"Выгружено писем: {result.messages}, архивов: {result.archives}.\n{result.target}"
            if result.headers_only:
                text += (
                    f"\n\nПисем без тела: {result.headers_only} — оно ещё не было скачано с сервера, "
                    "выгружены только реквизиты."
                )
            if result.failed:
                text += f"\n\nНе выгружено из-за ошибок разбора: {result.failed} — подробности в журнале программы."
            QMessageBox.information(self, "Выгрузка переписки", text)

        self._run_transfer("Выгрузка переписки", mail_export.export_mail, target, fmt, on_success=done, cancellable=True)

    def _on_export_profile(self) -> None:
        if not profile_transfer.allowed_here():
            return
        default_name = f"профиль-{datetime.now():%Y-%m-%d}{profile_transfer.EXTENSION}"
        path, _ = QFileDialog.getSaveFileName(
            self, "Выгрузить профиль", default_name, f"Профиль redmail (*{profile_transfer.EXTENSION})"
        )
        if not path:
            return

        def done(manifest) -> None:
            QMessageBox.information(
                self, "Выгрузка профиля",
                f"Профиль выгружен: файлов {len(manifest['files'])}.\n\nНа новом компьютере: «Параметры» → "
                "«Хранилище» → «Загрузить профиль…». Пароли учётных записей нужно будет ввести заново.",
            )

        self._run_transfer("Выгрузка профиля", profile_transfer.export_profile, Path(path), on_success=done)

    def _on_import_profile(self) -> None:
        if not profile_transfer.allowed_here():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Загрузить профиль", "", f"Профиль redmail (*{profile_transfer.EXTENSION})"
        )
        if not path:
            return
        try:
            manifest = profile_transfer.read_manifest(Path(path))
        except profile_transfer.TransferError as exc:
            QMessageBox.warning(self, "Загрузка профиля", str(exc))
            return
        size_mb = manifest.get("total_size", 0) / (1024 * 1024)
        answer = QMessageBox.question(
            self, "Загрузка профиля",
            f"Профиль {manifest.get('user', '?')} с компьютера {manifest.get('host', '?')} от "
            f"{manifest.get('created', '?')}, {size_mb:.0f} МБ.\n\nОн заменит почту, календарь, контакты и настройки "
            "на этом компьютере; прежние данные сохранятся в резервную копию. Загрузка выполнится при следующем "
            "запуске программы. Пароли нужно будет ввести заново.\n\nПродолжить?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            profile_transfer.schedule_import(Path(path))
        except (profile_transfer.TransferError, OSError) as exc:
            QMessageBox.warning(self, "Загрузка профиля", str(exc))
            return
        if QMessageBox.question(
            self, "Загрузка профиля", "Закрыть программу сейчас? Профиль загрузится при следующем запуске."
        ) == QMessageBox.StandardButton.Yes:
            self.reject()
            QApplication.instance().quit()

    def _fill_theme_combo(self, selected: str) -> None:
        self.theme_combo.clear()
        self.theme_combo.addItem("Светлая", "light")
        self.theme_combo.addItem("Тёмная", "dark")
        for brand in branding.load_brands():
            self.theme_combo.addItem(f"Оформление: {brand.name}", brand.theme_value)
        index = self.theme_combo.findData(selected)
        self.theme_combo.setCurrentIndex(index if index >= 0 else 0)

    def _on_manage_brands(self) -> None:
        current = self.theme_combo.currentData()
        BrandsDialog(self).exec()
        self._fill_theme_combo(current)

    def theme(self) -> str:
        return self.theme_combo.currentData()


class _AccountOptionsMixin:
    """Настройки одной учётной записи помимо подключения: удаление писем с
    сервера после архивации и замена доменов получателей при отправке.
    Раньше были на общей вкладке «Параметров» (замечание: «туда же перенеси
    и настройки удаления и сопоставление доменов»)."""

    def _build_account_options(self, delete_on_server: bool, domain_rewrites: str) -> QGroupBox:
        self.delete_on_server_check = QCheckBox("Удалять письма с сервера после переноса в архив (необратимо)", self)
        self.delete_on_server_check.setChecked(bool(delete_on_server))
        self.domain_rewrites_edit = QPlainTextEdit(domain_rewrites or "", self)
        self.domain_rewrites_edit.setPlaceholderText("amurgpz.ru = vk.corp.amurgpz.ru")
        self.domain_rewrites_edit.setFixedHeight(70)
        hint = QLabel(
            "Переезд на другой сервер: по одному правилу в строке, старый домен = новый. Действует только "
            "для писем, отправленных с этой учётной записи; в адресной книге и письмах адреса остаются прежними.",
            self,
        )
        hint.setWordWrap(True)
        form = QFormLayout()
        form.addRow(self.delete_on_server_check)
        form.addRow("Замена доменов", self.domain_rewrites_edit)
        form.addRow(hint)
        self._options_form = form
        group = QGroupBox("Архив и переезд", self)
        group.setLayout(form)
        return group

    def _fit_account_window(self, layout: QVBoxLayout, forms: list) -> None:
        """Окно не меньше содержимого и подписи полей одной ширины во всех
        группах. Раньше окно открывалось низким, и поля в группах сжимались
        друг на друга (замечание: «криво, расширь окно вниз»)."""
        labels = []
        for form in forms + [self._options_form]:
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setVerticalSpacing(8)
            for row in range(form.rowCount()):
                item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                if item is not None and item.widget() is not None:
                    labels.append(item.widget())
        width = max((label.sizeHint().width() for label in labels), default=0)
        for label in labels:
            label.setMinimumWidth(width)
        # Минимальный размер окна берётся из разметки — сжать поля нельзя.
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.adjustSize()
        hint = self.sizeHint()
        screen = self.screen().availableGeometry() if self.screen() is not None else None
        height = hint.height() + 40
        if screen is not None:
            height = min(height, screen.height() - 40)
        self.resize(max(hint.width(), 600), height)

    def delete_on_server(self) -> bool:
        return self.delete_on_server_check.isChecked()

    def domain_rewrites(self) -> str:
        return self.domain_rewrites_edit.toPlainText().strip()


class ImapAccountDialog(_AccountOptionsMixin, QDialog):
    """Учётная запись IMAP/SMTP: подключение и её собственные настройки.
    Открывается кнопкой «Настроить…» или «Добавить учётную запись…» на
    вкладке «Учётные записи» вместо прежней общей вкладки «Почта», которая
    правила только ту запись, чья папка была открыта."""

    def __init__(
        self,
        parent=None,
        *,
        account: Account | None = None,
        smtp: SmtpAccount | None = None,
        delete_on_server: bool = False,
        domain_rewrites: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Учётная запись {account.username}" if account else "Новая учётная запись")
        self.host_edit = QLineEdit(getattr(account, "host", "") if account else "")
        self.port_edit = QSpinBox()
        self.port_edit.setRange(1, 65535)
        # account может быть учётной записью Exchange (у неё нет ни порта,
        # ни отдельного IMAP-хоста) — окно параметров из-за этого вообще не
        # открывалось (жалоба: "кнопка Параметры недоступна").
        self.port_edit.setValue(getattr(account, "port", 993) if account else 993)
        self.user_edit = QLineEdit(getattr(account, "username", "") if account else "")
        self.password_edit = QLineEdit(account.password if account else "")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ssl_check = QCheckBox("Использовать SSL")
        self.ssl_check.setChecked(getattr(account, "use_ssl", True) if account else True)

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
            self.auth_combo.findData(getattr(account, "auth_type", "password") if account else "password")
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

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(imap_group)
        layout.addWidget(smtp_group)
        layout.addWidget(self._build_account_options(delete_on_server, domain_rewrites))
        layout.addWidget(buttons)
        self._update_password_enabled()
        self._fit_account_window(layout, [imap_form, smtp_form])

    def _update_password_enabled(self) -> None:
        is_kerberos = self.auth_combo.currentData() == "kerberos"
        self.password_edit.setEnabled(not is_kerberos)

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


class EwsAccountDialog(_AccountOptionsMixin, QDialog):
    """Подключение к Exchange напрямую по EWS — отдельный диалог от
    обычного "Параметры" (IMAP/SMTP): модель входа принципиально другая —
    адрес сервера обычно не нужен вводить (автообнаружение по email), а
    для Kerberos/SSO вообще не нужен пароль в самом приложении (билет
    берётся из окружения ОС, см. ews_client.EwsSession)."""

    def __init__(
        self,
        parent=None,
        *,
        account: EwsAccount | None = None,
        delete_on_server: bool = False,
        domain_rewrites: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Exchange {account.email}" if account else "Подключить Exchange (EWS)")

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
        # Подписка на ящики коллег: открываются ВАШЕЙ учётной записью по
        # выданным ими правам, пароли владельцев не нужны.
        self.shared_edit = QLineEdit()
        self.shared_edit.setPlaceholderText("ivanov@corp.example, petrov@corp.example")
        self.shared_edit.setToolTip(
            "Почтовые ящики коллег, доступ к которым вам выдан. Открываются вашей учётной записью "
            "по правам владельца — его пароль не нужен. Появятся в дереве папок отдельными ветками."
        )
        self.shared_offline_check = QCheckBox("Хранить письма ящиков коллег локально (офлайн-копия и архив)")
        self.shared_offline_check.setToolTip(
            "Выключено: папки коллег открываются с сервера при обращении — база не растёт. "
            "Включено: письма коллег скачиваются, доступны без сети и попадают в автоархив вместе со своими."
        )

        form = QFormLayout()
        form.addRow("Email", self.email_edit)
        form.addRow("Способ входа", self.auth_combo)
        form.addRow("Логин", self.username_edit)
        form.addRow("Пароль", self.password_edit)
        form.addRow("Сервер EWS", self.server_edit)
        form.addRow("Ящики коллег", self.shared_edit)
        form.addRow("", self.shared_offline_check)

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

        if account is not None:
            self.email_edit.setText(account.email)
            self.auth_combo.setCurrentIndex(max(0, self.auth_combo.findData(account.auth_type)))
            self.username_edit.setText(account.username if account.username != account.email else "")
            self.password_edit.setText(account.password)
            self.server_edit.setText(account.server)
            self.shared_edit.setText(", ".join(account.shared_mailboxes))
            self.shared_offline_check.setChecked(account.shared_offline)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.test_button)
        layout.addWidget(self.status_label)
        layout.addWidget(self._build_account_options(delete_on_server, domain_rewrites))
        layout.addWidget(buttons)

        self._update_fields_enabled()
        self._fit_account_window(layout, [form])

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
            shared_mailboxes=tuple(
                part.strip() for part in self.shared_edit.text().replace(";", ",").split(",") if part.strip()
            ),
            shared_offline=self.shared_offline_check.isChecked(),
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


class _ComposeBodyEdit(QTextEdit):
    """QTextEdit тела письма с вставкой картинки из буфера обмена.

    Обычный QTextEdit.insertFromMimeData игнорирует image/* MIME-данные
    (скопированную в буфер картинку, а не путь к файлу) — вставлялся только
    текст/HTML. Жалоба: "вставка изображения из буфера обмена не работает".
    """

    def __init__(self, on_image_paste, parent=None) -> None:
        super().__init__(parent)
        self._on_image_paste = on_image_paste

    def canInsertFromMimeData(self, source) -> bool:  # noqa: N802 - Qt override
        return source.hasImage() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source) -> None:  # noqa: N802 - Qt override
        if source.hasImage():
            image = source.imageData()
            if not isinstance(image, QImage):
                image = QImage(image)
            if not image.isNull():
                self._on_image_paste(image)
                return
        super().insertFromMimeData(source)


class ComposeDialog(QDialog):
    def _on_switch_layout(self) -> None:
        widget = QApplication.focusWidget()
        if widget is None or not self.isAncestorOf(widget):
            widget = self.body_edit
        if not switch_layout_in_widget(widget):
            QApplication.beep()

    def _apply_letter_branding(self, brand) -> None:
        document = self.body_edit.document()
        font = QFont(document.defaultFont())
        if brand.letter_font_family:
            font.setFamily(brand.letter_font_family)
        if brand.letter_font_size:
            font.setPointSizeF(brand.letter_font_size)
        document.setDefaultFont(font)
        self.body_edit.setCurrentFont(font)
        if brand.letter_text_color:
            self.body_edit.setTextColor(QColor(brand.letter_text_color))

    def _insert_greeting(self, greeting: str) -> None:
        """Приветствие первой строкой и пустая строка после него; курсор —
        сразу под приветствием, чтобы писать текст. Если письмо уже
        начинается с приветствия (помощник продиктовал), второе не ставим."""
        existing = self.body_edit.toPlainText().lstrip().casefold()
        if existing.startswith(("здравствуй", "добр", "привет")):
            return False
        cursor = QTextCursor(self.body_edit.document())
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.insertText(greeting)
        cursor.insertBlock()
        cursor.insertBlock()
        self.body_edit.setTextCursor(cursor)
        return True

    def _set_greeting(self, text: str) -> None:
        """Сменить или убрать приветствие, уже поставленное первой строкой;
        если его не было — поставить."""
        document = self.body_edit.document()
        first = document.firstBlock()
        if self._greeting and first.text().strip() == self._greeting:
            cursor = QTextCursor(first)
            cursor.beginEditBlock()
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
            if text:
                cursor.insertText(text)
            else:
                # вместе с приветствием — пустая строка после него
                following = first.next()
                cursor.movePosition(QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor)
                if following.isValid() and not following.text().strip():
                    cursor.movePosition(QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
            cursor.endEditBlock()
            self._greeting = text
        elif text:
            cursor = QTextCursor(document)
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.insertText(text)
            cursor.insertBlock()
            cursor.insertBlock()
            self._greeting = text

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
        body_html: str | None = None,
        inline_images: dict[str, tuple[str, bytes]] | None = None,
        contacts: list[contact_store.Contact] | None = None,
        attachments: list[OutgoingAttachment] | None = None,
        signatures: list[Signature] | None = None,
        default_signature_id: str | None = None,
        greeting: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        # Обычное окно, а не диалог: оконный менеджер рисует у диалогов
        # уменьшенную рамку с мелким заголовком (жалоба: "название окна
        # очень мелко"), а окно письма живёт долго, ему нужны обычная
        # рамка, кнопки свернуть/развернуть и место в панели задач.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        # Размер — как пользователь оставил в прошлый раз; по умолчанию
        # просторное окно (панель форматирования на узком окне пряталась за
        # стрелку ">>", а письмо с картинками не влезало).
        restored = False
        try:
            saved = load_compose_geometry()
            if saved:
                restored = bool(self.restoreGeometry(QByteArray(saved)))
        except Exception:
            restored = False
        if not restored:
            self.resize(960, 720)
        self.attachments: list[OutgoingAttachment] = list(attachments) if attachments else []

        self._contacts = contacts or []

        self.to_edit = QLineEdit(to)
        self.to_edit.setClearButtonEnabled(True)  # крестик в самом поле (пожелание: "кнопка очищения поля Кому")
        self.to_edit.setPlaceholderText("Через запятую, если получателей несколько")
        # Поля «Кому»/«Тема» повыше (жалоба: "поле кому и тема расширь —
        # в тёмной теме всё сливается"; рамка/фон — в theme.py).
        self.to_edit.setMinimumHeight(30)
        if contacts:
            _install_recipient_completer(self.to_edit, contacts)
            _install_recipient_tooltip(self.to_edit, contacts)
        self.subject_edit = QLineEdit(subject)
        self.subject_edit.setMinimumHeight(30)

        # Раньше тело письма было простым QPlainTextEdit — жалоба: "нет
        # возможности вставить картинку... редактор не даёт установить
        # какие-либо шрифты или как-то иначе выделить письмо". QTextEdit
        # умеет всё это из коробки (rich text + insertImage), нужна только
        # небольшая панель инструментов сверху. setPlainText, а не передача
        # текста в конструктор/setHtml — цитата ответа/пересылки может
        # содержать "<"/">" (например, адрес в угловых скобках), который
        # иначе разобрался бы как HTML-тег, а не как текст.
        self.body_edit = _ComposeBodyEdit(lambda image: self._insert_image(image, "image/png"))
        self.body_edit.setObjectName("composeBody")
        self.body_edit.setAcceptRichText(True)
        self._inline_images: dict[str, tuple[str, bytes]] = dict(inline_images) if inline_images else {}
        if body_html:
            # Черновик, открытый повторно, мог быть сохранён с картинками —
            # регистрируем их как ресурсы ДО setHtml(), иначе <img
            # src="cid:..."> в разметке ссылается на данные, которых
            # документ ещё не знает, и картинка рисуется сломанной.
            for cid, (_content_type, payload) in self._inline_images.items():
                image = QImage.fromData(payload)
                if not image.isNull():
                    self.body_edit.document().addResource(
                        QTextDocument.ResourceType.ImageResource, QUrl(f"cid:{cid}"), image
                    )
            self.body_edit.setHtml(body_html)
        else:
            self.body_edit.setPlainText(body)
        # Оформление организации: шрифт, размер и цвет текста письма.
        # Черновик и пересылка приходят со своей разметкой — её не трогаем.
        brand = app_theme.current_brand()
        if brand is not None and body_html is None:
            self._apply_letter_branding(brand)
        self._greeting = greeting
        if greeting:
            if not self._insert_greeting(greeting):
                self._greeting = ""  # письмо уже начинается с приветствия
            # Приветствие — не правка человека: иначе докачанные позже
            # картинки пересылаемого письма не подставились бы (см.
            # apply_embedded_images), а закрытие пустого письма спрашивало бы
            # о сохранении.
            self.body_edit.document().setModified(False)

        # Подпись (жалоба: "нет возможности задать подпись или несколько
        # подписей и выбрать нужную") — подставляется автоматически только
        # для СВЕЖЕГО письма (body_html не задан, т.е. не черновик/не уже
        # готовое содержимое); при редактировании черновика повторная
        # автоподстановка задвоила бы уже сохранённую подпись. Смена в
        # комбобоксе всегда доступна — переключиться можно в любой момент.
        self._signatures = signatures or []
        self._signature_range: tuple[int, int] | None = None
        self.signature_combo: QComboBox | None = None
        if self._signatures:
            self.signature_combo = QComboBox(self)
            self.signature_combo.addItem("Без подписи", None)
            for sig in self._signatures:
                self.signature_combo.addItem(sig.name, sig.id)
            self.signature_combo.currentIndexChanged.connect(self._on_signature_changed)
            if body_html is None and default_signature_id:
                index = self.signature_combo.findData(default_signature_id)
                if index >= 0:
                    self.signature_combo.setCurrentIndex(index)

        self.bold_action = QAction("Ж", self)
        self.bold_action.setCheckable(True)
        self.bold_action.setToolTip("Полужирный")
        self.bold_action.toggled.connect(self._on_bold_toggled)
        self.italic_action = QAction("К", self)
        self.italic_action.setCheckable(True)
        self.italic_action.setToolTip("Курсив")
        self.italic_action.toggled.connect(self._on_italic_toggled)
        self.underline_action = QAction("Ч", self)
        self.underline_action.setCheckable(True)
        self.underline_action.setToolTip("Подчёркнутый")
        self.underline_action.toggled.connect(self._on_underline_toggled)

        self.font_family_combo = QFontComboBox(self)
        self.font_family_combo.setMaximumWidth(160)
        self.font_family_combo.currentFontChanged.connect(self._on_font_family_changed)

        self.font_size_combo = QComboBox(self)
        self.font_size_combo.setEditable(True)
        self.font_size_combo.setMaximumWidth(56)
        for size in (8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48):
            self.font_size_combo.addItem(str(size))
        self.font_size_combo.setCurrentText(str(int(self.body_edit.fontPointSize()) or 12))
        self.font_size_combo.currentTextChanged.connect(self._on_font_size_changed)

        insert_image_button = QPushButton("Вставить изображение…", self)
        insert_image_button.clicked.connect(self._on_insert_image)

        # Цвет текста письма (пожелание: "нет цвета текста у редактора").
        self.text_color_button = QToolButton(self)
        self.text_color_button.setText("A")
        self.text_color_button.setToolTip("Цвет текста")
        self.text_color_button.clicked.connect(self._on_text_color)
        self._text_color = self.body_edit.textColor()

        format_toolbar = QToolBar("Форматирование", self)
        format_toolbar.addAction(self.bold_action)
        format_toolbar.addAction(self.italic_action)
        format_toolbar.addAction(self.underline_action)
        # Буквы-подсказки Ж/К/Ч сами по себе рисуются полужирным/курсивом/
        # подчёркнутым шрифтом — понятно без иконок, что каждая делает.
        for action, tweak in (
            (self.bold_action, lambda f: f.setBold(True)),
            (self.italic_action, lambda f: f.setItalic(True)),
            (self.underline_action, lambda f: f.setUnderline(True)),
        ):
            button = format_toolbar.widgetForAction(action)
            if button is not None:
                font = button.font()
                tweak(font)
                button.setFont(font)
        format_toolbar.addWidget(self.font_family_combo)
        format_toolbar.addWidget(self.font_size_combo)
        format_toolbar.addWidget(self.text_color_button)
        self._update_color_button()
        format_toolbar.addSeparator()
        format_toolbar.addWidget(insert_image_button)
        format_toolbar.addSeparator()
        self.switch_layout_action = QAction("Раскладка", self)
        self.switch_layout_action.setToolTip(
            "Текст, набранный не в той раскладке («ghbdtn» → «привет»): выделенное или слово перед курсором "
            "(Pause или Ctrl+Shift+K)"
        )
        self.switch_layout_action.setShortcuts([QKeySequence(Qt.Key.Key_Pause), QKeySequence("Ctrl+Shift+K")])
        self.switch_layout_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.switch_layout_action.triggered.connect(self._on_switch_layout)
        format_toolbar.addAction(self.switch_layout_action)
        self.addAction(self.switch_layout_action)
        format_toolbar.addSeparator()
        self.greeting_combo = QComboBox(self)
        self.greeting_combo.setToolTip("Приветствие первой строкой письма; список — в «Параметры» → «Общие»")
        self.greeting_combo.addItem("Без приветствия", "")
        for text in greeting_choices():
            self.greeting_combo.addItem(text, text)
        if self._greeting and self.greeting_combo.findData(self._greeting) < 0:
            self.greeting_combo.addItem(self._greeting, self._greeting)
        self.greeting_combo.setCurrentIndex(max(0, self.greeting_combo.findData(self._greeting)))
        self.greeting_combo.currentIndexChanged.connect(
            lambda _index: self._set_greeting(self.greeting_combo.currentData() or "")
        )
        format_toolbar.addWidget(self.greeting_combo)
        self._format_toolbar = format_toolbar

        self.body_edit.currentCharFormatChanged.connect(self._sync_format_toolbar)

        address_book_button = QPushButton("Адресная книга…", self)
        address_book_button.clicked.connect(lambda: _open_contact_picker(self, self.to_edit, self._contacts))
        cc_bcc_button = QPushButton("Копия/Скрытая копия", self)
        cc_bcc_button.setFlat(True)
        cc_bcc_button.clicked.connect(self._show_cc_bcc)
        # Пожелание: "в карточку события и создание сообщения добавь
        # кнопку очистки получателей" — сбрасывает все три поля адресатов.
        clear_recipients_button = QPushButton("✕", self)
        clear_recipients_button.setToolTip("Очистить получателей (Кому, Копия, Скрытая копия)")
        clear_recipients_button.setFixedWidth(28)
        clear_recipients_button.clicked.connect(self._clear_recipients)
        to_row = QHBoxLayout()
        to_row.addWidget(self.to_edit)
        to_row.addWidget(clear_recipients_button)  # сразу за полем (пожелание: "крестик не там")
        to_row.addSpacing(12)  # зазор до «Адресной книги» (пожелание пользователя)
        to_row.addWidget(address_book_button)
        to_row.addWidget(cc_bcc_button)
        # contacts, а не self._contacts: тот присваивается ниже по __init__.
        self.to_list_view = RecipientListView(self.to_edit, contacts or [], self)

        self.cc_edit = QLineEdit(cc, self)
        self.cc_edit.setClearButtonEnabled(True)
        self.cc_edit.setPlaceholderText("Через запятую, если получателей несколько")
        if contacts:
            _install_recipient_completer(self.cc_edit, contacts)
            _install_recipient_tooltip(self.cc_edit, contacts)
        self.bcc_edit = QLineEdit(bcc, self)
        self.bcc_edit.setClearButtonEnabled(True)
        self.bcc_edit.setPlaceholderText("Через запятую, если получателей несколько")
        if contacts:
            _install_recipient_completer(self.bcc_edit, contacts)
            _install_recipient_tooltip(self.bcc_edit, contacts)

        form = QFormLayout()
        # Пожелание: "немного разнеси поля тема и кому".
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(12)
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
        _mark_primary(buttons.button(QDialogButtonBox.StandardButton.Ok))
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
        # Поля прижаты к верху, свободное место делят список адресатов и
        # текст письма (жалоба: "слишком большие расстояния между
        # элементами при растягивании").
        layout.addLayout(form)
        layout.addWidget(self.to_list_view, 2)
        layout.addLayout(attach_row)
        self.attachments_list.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.attachments_list)
        if self.signature_combo is not None:
            signature_row = QHBoxLayout()
            signature_row.addWidget(QLabel("Подпись:", self))
            signature_row.addWidget(self.signature_combo)
            signature_row.addStretch(1)
            layout.addLayout(signature_row)
        layout.addWidget(format_toolbar)
        layout.addWidget(self.body_edit, 5)
        layout.addWidget(buttons)

    def done(self, result: int) -> None:  # noqa: N802 - Qt override
        # Любое закрытие (отправить, черновик, крестик) — запомнить размер.
        try:
            save_compose_geometry(bytes(self.saveGeometry()))
        except Exception:
            pass  # размер окна не запомнится — не критично
        super().done(result)

    def _on_save_draft(self) -> None:
        self._save_as_draft = True
        self.accept()

    def accept(self) -> None:  # noqa: N802 - Qt override
        # Без получателя письмо не отправить — и окно не закрывать: раньше
        # окно закрывалось, выходило предупреждение, а письмо пропадало
        # (жалоба). Черновик сохраняется и без получателей.
        if not self._save_as_draft and not (self.recipients() or self.cc_recipients() or self.bcc_recipients()):
            QMessageBox.warning(
                self, "Нет получателя",
                "Укажите хотя бы одного получателя в «Кому», «Копия» или «Скрытая копия».\n"
                "Чтобы отложить письмо, нажмите «Сохранить черновик».",
            )
            self.to_edit.setFocus()
            return
        super().accept()

    def save_as_draft_requested(self) -> bool:
        return self._save_as_draft

    def apply_embedded_images(self, html_with_cids: str, images: dict[str, tuple[str, bytes]]) -> None:
        """Картинки пересылаемого письма докачались в фоне: регистрируем
        их как ресурсы документа и, если пользователь ещё ничего не менял,
        подменяем разметку на вариант с cid:. Если текст уже правился —
        разметку не трогаем (ушло бы с внешними ссылками, как раньше)."""
        if self.body_edit.document().isModified():
            return
        for cid, (_content_type, payload) in images.items():
            if cid in self._inline_images:
                continue
            self._inline_images[cid] = (_content_type, payload)
            image = QImage.fromData(payload)
            if not image.isNull():  # нерисуемый формат (svg и т.п.) всё равно уйдёт вложением
                self.body_edit.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl(f"cid:{cid}"), image)
        cursor_position = self.body_edit.textCursor().position()
        self.body_edit.setHtml(html_with_cids)
        if getattr(self, "_greeting", ""):
            self._insert_greeting(self._greeting)  # разметка заменена целиком — приветствие возвращаем
        cursor = self.body_edit.textCursor()
        cursor.setPosition(min(cursor_position, self.body_edit.document().characterCount() - 1))
        self.body_edit.setTextCursor(cursor)
        self.body_edit.document().setModified(False)

    def _clear_recipients(self) -> None:
        self.to_edit.clear()
        self.cc_edit.clear()
        self.bcc_edit.clear()
        self.to_edit.setFocus()

    def _show_cc_bcc(self) -> None:
        self._show_cc_bcc_fields(True)

    def _show_cc_bcc_fields(self, visible: bool) -> None:
        self._form.setRowVisible(self.cc_edit, visible)
        self._form.setRowVisible(self.bcc_edit, visible)

    def cc_recipients(self) -> list[str]:
        return _parse_recipient_list(self.cc_edit.text(), self._contacts)

    def bcc_recipients(self) -> list[str]:
        return _parse_recipient_list(self.bcc_edit.text(), self._contacts)

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
        return _parse_recipient_list(self.to_edit.text(), self._contacts)

    def subject(self) -> str:
        return self.subject_edit.text().strip()

    def body(self) -> str:
        return self.body_edit.toPlainText()

    def body_html(self) -> str:
        # Цвет текста из оформления организации — в разметку письма, чтобы
        # его увидел получатель.
        return branding.letter_html(app_theme.current_brand(), html_cleanup.force_utf8_charset(self.body_edit.toHtml()))

    def inline_images(self) -> dict[str, tuple[str, bytes]]:
        return dict(self._inline_images)

    def _on_bold_toggled(self, checked: bool) -> None:
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Bold if checked else QFont.Weight.Normal)
        self.body_edit.mergeCurrentCharFormat(fmt)
        self.body_edit.setFocus()

    def _on_italic_toggled(self, checked: bool) -> None:
        fmt = QTextCharFormat()
        fmt.setFontItalic(checked)
        self.body_edit.mergeCurrentCharFormat(fmt)
        self.body_edit.setFocus()

    def _on_underline_toggled(self, checked: bool) -> None:
        fmt = QTextCharFormat()
        fmt.setFontUnderline(checked)
        self.body_edit.mergeCurrentCharFormat(fmt)
        self.body_edit.setFocus()

    def _on_font_family_changed(self, font: QFont) -> None:
        self.body_edit.setFontFamily(font.family())
        self.body_edit.setFocus()

    def _on_font_size_changed(self, size_text: str) -> None:
        try:
            size = float(size_text)
        except ValueError:
            return
        if size > 0:
            self.body_edit.setFontPointSize(size)

    def _sync_format_toolbar(self, fmt: QTextCharFormat) -> None:
        # blockSignals — иначе programmatic setChecked() тут же снова
        # дёрнул бы _on_*_toggled и слил бы формат курсора с самим собой
        # (безвредно, но лишняя работа на каждое движение курсора).
        self.bold_action.blockSignals(True)
        self.bold_action.setChecked(fmt.fontWeight() >= QFont.Weight.Bold)
        self.bold_action.blockSignals(False)
        self.italic_action.blockSignals(True)
        self.italic_action.setChecked(fmt.fontItalic())
        self.italic_action.blockSignals(False)
        self.underline_action.blockSignals(True)
        self.underline_action.setChecked(fmt.fontUnderline())
        self.underline_action.blockSignals(False)
        color = fmt.foreground().color() if fmt.foreground().style() != Qt.BrushStyle.NoBrush else QColor()
        if color.isValid():
            self._text_color = color
            self._update_color_button()

    def _on_text_color(self) -> None:
        """Цвет текста письма (пожелание: "нет цвета текста у редактора")."""
        color = QColorDialog.getColor(self._text_color, self, "Цвет текста письма")
        if not color.isValid():
            return
        self._text_color = color
        self.body_edit.setTextColor(color)
        self._update_color_button()
        self.body_edit.setFocus()

    def _update_color_button(self) -> None:
        color = self._text_color if self._text_color.isValid() else self.palette().color(QPalette.ColorRole.Text)
        self.text_color_button.setStyleSheet(
            f"QToolButton {{ color: {color.name()}; font-weight: bold; text-decoration: underline; }}"
        )

    def _on_insert_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Вставить изображение", filter="Изображения (*.png *.jpg *.jpeg *.gif *.bmp)"
        )
        if not path:
            return
        data = Path(path).read_bytes()
        image = QImage.fromData(data)
        if image.isNull():
            QMessageBox.warning(self, "Не удалось вставить изображение", "Файл не распознан как изображение.")
            return
        content_type, _ = mimetypes.guess_type(path)
        self._insert_image(image, content_type or "image/png", data)

    def _insert_image(self, image: QImage, content_type: str, data: bytes | None = None) -> None:
        if data is None:
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            image.save(buffer, "PNG")
            data = bytes(buffer.data())
        cid = f"{uuid4().hex}@redmail"
        self._inline_images[cid] = (content_type, data)
        cursor = self.body_edit.textCursor()
        cursor.insertImage(image, f"cid:{cid}")

    def _on_signature_changed(self, _index: int) -> None:
        # Точный диапазон позиций, а не поиск по тексту/HTML — Qt не
        # сохраняет произвольную разметку (например, HTML-комментарии как
        # маркер) через цикл setHtml()/toHtml(), у него своё, более узкое
        # подмножество HTML. Запоминая (начало, конец) вставленной подписи
        # в документе, можно надёжно убрать именно её при переключении на
        # другую или на "Без подписи", не трогая остальной текст письма.
        cursor = self.body_edit.textCursor()
        if self._signature_range is not None:
            start, end = self._signature_range
            doc_end = self.body_edit.document().characterCount() - 1
            cursor.setPosition(min(start, doc_end))
            cursor.setPosition(min(end, doc_end), QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            self._signature_range = None
        sig_id = self.signature_combo.currentData()
        if sig_id:
            signature = next((s for s in self._signatures if s.id == sig_id), None)
            if signature is not None:
                # Картинки подписи регистрируем как ресурсы ДО insertHtml —
                # иначе <img src="cid:..."> внутри разметки подписи ссылался
                # бы на данные, которых документ ещё не знает, и рисовался
                # бы сломанным (тот же приём, что при открытии черновика).
                # Также добавляем их в _inline_images письма, чтобы они
                # реально ушли вложениями при отправке.
                for cid, (content_type, payload) in signature.inline_images.items():
                    image = QImage.fromData(payload)
                    if not image.isNull():
                        self.body_edit.document().addResource(
                            QTextDocument.ResourceType.ImageResource, QUrl(f"cid:{cid}"), image
                        )
                        self._inline_images[cid] = (content_type, payload)
                cursor.movePosition(QTextCursor.MoveOperation.End)
                self.body_edit.setTextCursor(cursor)
                start_pos = cursor.position()
                cursor.insertHtml("<br><br>" + signature.body_html)
                self._signature_range = (start_pos, cursor.position())
        self.body_edit.setTextCursor(cursor)


class SignatureEditDialog(QDialog):
    """Название + тело подписи — та же панель форматирования, что в
    ComposeDialog: Ж/К/Ч, гарнитура, размер и вставка изображения (жалоба:
    "редактор подписи не даёт вставлять картинку и изменять шрифт, сделай
    как при создании письма"). Само поле ввода — узкое и невысокое, на 4
    строки, а не растянутое на весь диалог (жалоба: "зачем такое широкое
    поле") — подпись обычно короткая, места под неё нужно немного."""

    def __init__(self, parent, signature: Signature | None = None):
        super().__init__(parent)
        self.setWindowTitle("Изменить подпись" if signature else "Новая подпись")
        # Ширина — не ради самого поля ввода (оно как раз узкое, см. ниже),
        # а чтобы панель форматирования не пряталась под скрытую стрелку
        # ">>" от нехватки места (тот же эффект, что уже был в ComposeDialog
        # и в основном тулбаре при недостаточной ширине).
        self.resize(560, 260)

        self.name_edit = QLineEdit(signature.name if signature else "")
        self.name_edit.setPlaceholderText("Например, «Рабочая»")

        self.body_edit = _ComposeBodyEdit(lambda image: self._insert_image(image, "image/png"))
        self.body_edit.setObjectName("composeBody")
        self.body_edit.setAcceptRichText(True)
        self._inline_images: dict[str, tuple[str, bytes]] = dict(signature.inline_images) if signature else {}
        if signature:
            for cid, (_content_type, payload) in self._inline_images.items():
                image = QImage.fromData(payload)
                if not image.isNull():
                    self.body_edit.document().addResource(
                        QTextDocument.ResourceType.ImageResource, QUrl(f"cid:{cid}"), image
                    )
            self.body_edit.setHtml(signature.body_html)
        # Минимум четыре строки, дальше поле растёт вместе с окном (было
        # setFixedHeight — окно растягивалось, а поле нет).
        line_height = self.body_edit.fontMetrics().lineSpacing()
        self.body_edit.setMinimumHeight(line_height * 4 + 24)
        self.body_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.bold_action = QAction("Ж", self)
        self.bold_action.setCheckable(True)
        self.bold_action.setToolTip("Полужирный")
        self.bold_action.toggled.connect(self._on_bold_toggled)
        self.italic_action = QAction("К", self)
        self.italic_action.setCheckable(True)
        self.italic_action.setToolTip("Курсив")
        self.italic_action.toggled.connect(self._on_italic_toggled)
        self.underline_action = QAction("Ч", self)
        self.underline_action.setCheckable(True)
        self.underline_action.setToolTip("Подчёркнутый")
        self.underline_action.toggled.connect(self._on_underline_toggled)

        self.font_family_combo = QFontComboBox(self)
        self.font_family_combo.setMaximumWidth(160)
        self.font_family_combo.currentFontChanged.connect(self._on_font_family_changed)

        self.font_size_combo = QComboBox(self)
        self.font_size_combo.setEditable(True)
        self.font_size_combo.setMaximumWidth(56)
        for size in (8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48):
            self.font_size_combo.addItem(str(size))
        self.font_size_combo.setCurrentText(str(int(self.body_edit.fontPointSize()) or 12))
        self.font_size_combo.currentTextChanged.connect(self._on_font_size_changed)

        insert_image_button = QPushButton("Вставить изображение…", self)
        insert_image_button.clicked.connect(self._on_insert_image)

        self.text_color_button = QToolButton(self)
        self.text_color_button.setText("A")
        self.text_color_button.setToolTip("Цвет текста")
        self.text_color_button.clicked.connect(self._on_text_color)
        self._text_color = self.body_edit.textColor()
        self._update_color_button()

        toolbar = QToolBar("Форматирование", self)
        toolbar.addAction(self.bold_action)
        toolbar.addAction(self.italic_action)
        toolbar.addAction(self.underline_action)
        for action, tweak in (
            (self.bold_action, lambda f: f.setBold(True)),
            (self.italic_action, lambda f: f.setItalic(True)),
            (self.underline_action, lambda f: f.setUnderline(True)),
        ):
            button = toolbar.widgetForAction(action)
            if button is not None:
                font = button.font()
                tweak(font)
                button.setFont(font)
        toolbar.addWidget(self.font_family_combo)
        toolbar.addWidget(self.font_size_combo)
        toolbar.addWidget(self.text_color_button)
        toolbar.addSeparator()
        toolbar.addWidget(insert_image_button)

        self.body_edit.currentCharFormatChanged.connect(self._sync_format_toolbar)

        form = QFormLayout()
        form.addRow("Название", self.name_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(toolbar)
        # Текст подписи занимает всё свободное место: раньше ниже стоял
        # addStretch, и при растягивании окна поле оставалось прежним, а
        # росла пустота (жалоба: "окно редактирования не меняется при
        # изменении окна").
        layout.addWidget(self.body_edit, 1)
        layout.addWidget(buttons)

    def accept(self) -> None:  # noqa: N802 - Qt override
        # Без названия подпись не сохранить: раньше окно молча закрывалось
        # и подпись пропадала (жалоба: "не сохраняется, если нет названия —
        # просто закрывается; надо предупреждать и не давать сохранить").
        if not self.name():
            QMessageBox.warning(self, "Подпись", "Укажите название подписи — без него её нельзя сохранить.")
            self.name_edit.setFocus()
            return
        super().accept()

    def _on_bold_toggled(self, checked: bool) -> None:
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Bold if checked else QFont.Weight.Normal)
        self.body_edit.mergeCurrentCharFormat(fmt)
        self.body_edit.setFocus()

    def _on_italic_toggled(self, checked: bool) -> None:
        fmt = QTextCharFormat()
        fmt.setFontItalic(checked)
        self.body_edit.mergeCurrentCharFormat(fmt)
        self.body_edit.setFocus()

    def _on_underline_toggled(self, checked: bool) -> None:
        fmt = QTextCharFormat()
        fmt.setFontUnderline(checked)
        self.body_edit.mergeCurrentCharFormat(fmt)
        self.body_edit.setFocus()

    def _on_font_family_changed(self, font: QFont) -> None:
        self.body_edit.setFontFamily(font.family())
        self.body_edit.setFocus()

    def _on_font_size_changed(self, size_text: str) -> None:
        try:
            size = float(size_text)
        except ValueError:
            return
        if size > 0:
            self.body_edit.setFontPointSize(size)

    def _on_text_color(self) -> None:
        """Цвет текста подписи (жалоба: "нет возможности задать цвет
        текста"). Действует на выделенный фрагмент, а без выделения — на
        то, что будет напечатано дальше."""
        color = QColorDialog.getColor(self._text_color, self, "Цвет текста подписи")
        if not color.isValid():
            return
        self._text_color = color
        self.body_edit.setTextColor(color)
        self._update_color_button()
        self.body_edit.setFocus()

    def _update_color_button(self) -> None:
        color = self._text_color if self._text_color.isValid() else self.palette().color(QPalette.ColorRole.Text)
        self.text_color_button.setStyleSheet(
            f"QToolButton {{ color: {color.name()}; font-weight: bold; text-decoration: underline; }}"
        )

    def _sync_format_toolbar(self, fmt: QTextCharFormat) -> None:
        self.bold_action.blockSignals(True)
        self.bold_action.setChecked(fmt.fontWeight() >= QFont.Weight.Bold)
        self.bold_action.blockSignals(False)
        self.italic_action.blockSignals(True)
        self.italic_action.setChecked(fmt.fontItalic())
        self.italic_action.blockSignals(False)
        self.underline_action.blockSignals(True)
        self.underline_action.setChecked(fmt.fontUnderline())
        self.underline_action.blockSignals(False)
        color = fmt.foreground().color() if fmt.foreground().style() != Qt.BrushStyle.NoBrush else QColor()
        if color.isValid():
            self._text_color = color
            self._update_color_button()

    def _on_insert_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Вставить изображение", filter="Изображения (*.png *.jpg *.jpeg *.gif *.bmp)"
        )
        if not path:
            return
        data = Path(path).read_bytes()
        image = QImage.fromData(data)
        if image.isNull():
            QMessageBox.warning(self, "Не удалось вставить изображение", "Файл не распознан как изображение.")
            return
        content_type, _ = mimetypes.guess_type(path)
        self._insert_image(image, content_type or "image/png", data)

    def _insert_image(self, image: QImage, content_type: str, data: bytes | None = None) -> None:
        if data is None:
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            image.save(buffer, "PNG")
            data = bytes(buffer.data())
        cid = f"{uuid4().hex}@redmail"
        self._inline_images[cid] = (content_type, data)
        cursor = self.body_edit.textCursor()
        cursor.insertImage(image, f"cid:{cid}")

    def name(self) -> str:
        return self.name_edit.text().strip()

    def body_html(self) -> str:
        return self.body_edit.toHtml()

    def inline_images(self) -> dict[str, tuple[str, bytes]]:
        return dict(self._inline_images)


class SignaturesDialog(QDialog):
    """Список подписей (жалоба: "нет возможности задать подпись или
    несколько подписей и выбрать нужную") — тот же принцип, что и
    MailRulesDialog: список + Добавить/Изменить/Удалить, плюс отметка
    "по умолчанию" (та, что автоматически подставляется в новое письмо)."""

    def __init__(self, parent, signatures: list[Signature], default_signature_id: str | None):
        super().__init__(parent)
        self.setWindowTitle("Подписи")
        self.resize(420, 320)
        self._signatures = list(signatures)
        self._default_id = default_signature_id

        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Название", "По умолчанию"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemDoubleClicked.connect(lambda _item: self._on_edit())
        self._refresh_table()

        add_button = QPushButton("Добавить…", self)
        add_button.clicked.connect(self._on_add)
        edit_button = QPushButton("Изменить…", self)
        edit_button.clicked.connect(self._on_edit)
        remove_button = QPushButton("Удалить", self)
        remove_button.clicked.connect(self._on_remove)
        default_button = QPushButton("Сделать по умолчанию", self)
        default_button.clicked.connect(self._on_make_default)
        button_row = QHBoxLayout()
        button_row.addWidget(add_button)
        button_row.addWidget(edit_button)
        button_row.addWidget(remove_button)
        button_row.addWidget(default_button)
        button_row.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addLayout(button_row)
        layout.addWidget(self.table)
        layout.addWidget(buttons)

    def _refresh_table(self) -> None:
        self.table.setRowCount(len(self._signatures))
        for row, sig in enumerate(self._signatures):
            self.table.setItem(row, 0, QTableWidgetItem(sig.name))
            self.table.setItem(row, 1, QTableWidgetItem("✓" if sig.id == self._default_id else ""))

    def _on_add(self) -> None:
        dialog = SignatureEditDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.name()
        if not name:
            return
        sig = Signature(id=str(uuid4()), name=name, body_html=dialog.body_html(), inline_images=dialog.inline_images())
        self._signatures.append(sig)
        if self._default_id is None:
            self._default_id = sig.id  # первая созданная подпись сразу становится подписью по умолчанию
        self._refresh_table()

    def _on_edit(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        dialog = SignatureEditDialog(self, self._signatures[row])
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.name()
        if not name:
            return
        existing = self._signatures[row]
        self._signatures[row] = Signature(
            id=existing.id, name=name, body_html=dialog.body_html(), inline_images=dialog.inline_images()
        )
        self._refresh_table()

    def _on_remove(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        sig = self._signatures[row]
        confirm = QMessageBox.question(
            self, "Удалить подпись", f"Удалить подпись «{sig.name}»?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        del self._signatures[row]
        if self._default_id == sig.id:
            self._default_id = self._signatures[0].id if self._signatures else None
        self._refresh_table()

    def _on_make_default(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        self._default_id = self._signatures[row].id
        self._refresh_table()

    def signatures(self) -> list[Signature]:
        return list(self._signatures)

    def default_signature_id(self) -> str | None:
        return self._default_id


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


# Напоминание о встрече: за сколько и каким способом. Выбор делается при
# создании встречи — общего умолчания нет намеренно (решение пользователя:
# «спрашивать при создании встречи»).
_REMIND_MODE_OPTIONS: list[tuple[str, str]] = [
    ("не напоминать", calendar_store.REMIND_NONE),
    ("окном", calendar_store.REMIND_WINDOW),
    ("голосом", calendar_store.REMIND_VOICE),
    ("голосом и окном", calendar_store.REMIND_BOTH),
]
_REMIND_WHEN_OPTIONS: list[tuple[str, int]] = [
    ("за 5 минут", 5),
    ("за 10 минут", 10),
    ("за 15 минут", 15),
    ("за 30 минут", 30),
    ("за час", 60),
    ("за сутки", 1440),
]


_RECURRENCE_OPTIONS: list[tuple[str, str | None]] = [
    ("Не повторяется", None),
    ("Каждый день", "FREQ=DAILY"),
    ("Каждую неделю", "FREQ=WEEKLY"),
    ("Каждый месяц", "FREQ=MONTHLY"),
    ("Каждый год", "FREQ=YEARLY"),
]


class _CalendarPickerDialog(QDialog):
    """Список календарей, обнаруженных на CalDAV-сервере (свои и
    расшаренные коллегами) — задача "получение расшаренных календарей для
    VK Mail". У VK (и вообще по CalDAV) нет делегирования всего аккаунта —
    только пошаренные по отдельности календари, и после того, как
    приглашение принято В ВЕБ-ИНТЕРФЕЙСЕ VK, сервер сам кладёт чужой
    календарь в наш calendar-home-set как обычную коллекцию; здесь он
    просто отображается в общем списке, отмеченный тем, чей он."""

    def __init__(self, parent, calendars: list["caldav_sync.CalDavCalendarInfo"]):
        super().__init__(parent)
        self.setWindowTitle("Календари на сервере")
        self.resize(440, 320)

        self.list_widget = QListWidget(self)
        # У VK оба своих календаря называются «Основной» — различить их в
        # списке можно только по адресу (жалоба: «поиск дал 2 календаря
        # моих»). Одинаковые имена дополняем хвостом адреса.
        names = [info.name for info in calendars]
        for info in calendars:
            if info.is_shared:
                suffix = f" — общий, от {info.owner or 'неизвестно'}"
                if info.read_only:
                    suffix += ", только чтение"
            else:
                suffix = " — только чтение" if info.read_only else ""
            if names.count(info.name) > 1:
                suffix += f" [{info.url.rstrip('/').rsplit('/', 1)[-1][:12]}]"
            item = QListWidgetItem(f"{info.name}{suffix}")
            item.setToolTip(info.url)
            item.setData(Qt.ItemDataRole.UserRole, info)
            self.list_widget.addItem(item)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self.accept())

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Выберите календарь для подключения:", self))
        layout.addWidget(self.list_widget)
        layout.addWidget(buttons)

    def selected(self) -> "caldav_sync.CalDavCalendarInfo | None":
        item = self.list_widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None


def _caldav_account_for(dialog, url: str):
    """Учётные данные для адреса календаря: подобранные по домену сервера,
    а если подобрать не из чего — переданные в окно при открытии."""
    credentials = dialog._credentials_for(url) if getattr(dialog, "_credentials_for", None) else None
    if credentials is None:
        credentials = (dialog._my_email, dialog._my_password, dialog._my_auth_type)
    username, password, auth_type = credentials
    return caldav_sync.CalDavAccount(url=url, username=username, password=password, auth_type=auth_type)


class _EditCalendarUrlDialog(QDialog):
    """Смена адреса CalDAV-календаря — жалоба: узкое QInputDialog.getText
    показывало длинный URL с UUID обрезанным (виден только хвост). Тот же
    поиск календарей на сервере, что в AddCalendarDialog — если нужно
    переподключить на другой (например, другой расшаренный) календарь, не
    придётся вручную набирать точный URL."""

    def __init__(
        self, parent, current_url: str, my_email: str, my_password: str, my_auth_type: str = "password",
        credentials_for=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Подключение CalDAV")
        self.resize(560, 160)
        self._my_email = my_email
        self._my_password = my_password
        self._my_auth_type = my_auth_type
        self._credentials_for = credentials_for
        self._test_workers: list[QThread] = []

        self.url_edit = QLineEdit(current_url, self)
        discover_button = QPushButton("Найти календари на сервере…", self)
        discover_button.clicked.connect(self._on_discover)
        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Адрес сервера", self.url_edit)
        form.addRow(discover_button)
        form.addRow(self.status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addWidget(buttons)

    def _on_discover(self) -> None:
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "Укажите адрес", "Адрес сервера CalDAV обязателен для поиска календарей.")
            return
        account = _caldav_account_for(self, url)
        self.status_label.setText("Ищу календари на сервере…")

        def discover() -> list[caldav_sync.CalDavCalendarInfo]:
            session = caldav_sync.CalDavSession(account)
            try:
                return session.list_calendars_detailed()
            finally:
                session.close()

        worker = _CallableWorker(discover, parent=self)

        def on_success(calendars: object) -> None:
            self._test_workers.remove(worker)
            if not calendars:
                self.status_label.setText("На сервере не найдено ни одного календаря.")
                return
            picker = _CalendarPickerDialog(self, calendars)
            if picker.exec() == QDialog.DialogCode.Accepted:
                chosen = picker.selected()
                if chosen is not None:
                    self.url_edit.setText(chosen.url)
                    self.status_label.setText(f"Выбран календарь: {chosen.name}")

        def on_failure(error_text: str) -> None:
            self.status_label.setText(f"Ошибка поиска: {error_text}")
            self._test_workers.remove(worker)

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._test_workers.append(worker)
        worker.start()

    def url(self) -> str:
        return self.url_edit.text().strip()


class AddCalendarDialog(QDialog):
    """Новый календарь — локальный или подключённый к внешнему CalDAV-серверу.

    Раньше адрес CalDAV-сервера был один на весь аккаунт и настраивался в
    Параметрах — нельзя было подключить несколько внешних календарей и
    смешивать их с локальными (пожелание: "лучше сделать настройку
    календаря не в параметрах, а у самого календаря"). Google-календарь
    показан в списке источников честно ОТКЛЮЧЁННЫМ пунктом, а не рабочей
    заглушкой — у Google CalDAV давно урезан для новых интеграций, нужна
    отдельная OAuth2-интеграция с Google Calendar API, которой здесь пока
    нет и которую нет смысла изображать частично работающей."""

    def __init__(
        self,
        parent=None,
        *,
        my_email: str = "",
        my_password: str = "",
        my_auth_type: str = "password",
        used_colors: set[str] | None = None,
        credentials_for=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Новый календарь")
        self._my_email = my_email
        self._my_password = my_password
        self._my_auth_type = my_auth_type
        self._credentials_for = credentials_for
        self._test_workers: list[QThread] = []

        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText("Например, «Работа»")

        used_colors = used_colors or set()
        self.color_combo = QComboBox(self)
        for label, hexval in _EVENT_COLOR_PALETTE:
            self.color_combo.addItem(_dot_icon(hexval), label, hexval)
        default_color = next(
            (hexval for _label, hexval in _EVENT_COLOR_PALETTE if hexval not in used_colors),
            _EVENT_COLOR_PALETTE[0][1],
        )
        default_index = self.color_combo.findData(default_color)
        self.color_combo.setCurrentIndex(default_index if default_index >= 0 else 0)

        self.source_combo = QComboBox(self)
        self.source_combo.addItem("Локальный", calendar_store.SOURCE_LOCAL)
        self.source_combo.addItem("CalDAV (VK Mail, Exchange и др.)", calendar_store.SOURCE_CALDAV)
        self.source_combo.addItem("Google Календарь / подписка по ссылке (.ics)", calendar_store.SOURCE_ICS)
        self.source_combo.addItem("Exchange (EWS) — из подключённой учётной записи", calendar_store.SOURCE_EWS)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)

        # Подписка на .ics: Google отдаёт весь календарь по «закрытому
        # адресу в формате iCal» без входа (CalDAV у Google — только через
        # OAuth-клиент Google Cloud, пароль приложения там не работает).
        self.ics_url_edit = QLineEdit(self)
        self.ics_url_edit.setPlaceholderText("https://calendar.google.com/calendar/ical/…/private-…/basic.ics")
        ics_hint = QLabel(
            "Google Календарь: Настройки календаря → «Интеграция календаря» → "
            "«Закрытый адрес в формате iCal». Подписка односторонняя: события "
            "читаются при синхронизации, изменения на сервер не отправляются.",
            self,
        )
        ics_hint.setWordWrap(True)
        self.ics_group = QGroupBox("Подписка по ссылке", self)
        ics_form = QFormLayout()
        ics_form.addRow("Адрес .ics", self.ics_url_edit)
        ics_form.addRow(ics_hint)
        self.ics_group.setLayout(ics_form)

        # Подписка на календарь коллеги в Exchange: пусто — свой календарь.
        self.ews_mailbox_edit = QLineEdit(self)
        self.ews_mailbox_edit.setPlaceholderText("ivanov@example.ru — пусто, если это ваш календарь")
        ews_hint = QLabel(
            "Календарь коллеги открывается ВАШЕЙ учётной записью Exchange по правам, которые он вам выдал "
            "(подписка) — его пароль не нужен. Если коллега прав не давал, сервер ответит отказом.", self,
        )
        ews_hint.setWordWrap(True)
        self.ews_group = QGroupBox("Календарь Exchange", self)
        ews_form = QFormLayout()
        ews_form.addRow("Ящик коллеги", self.ews_mailbox_edit)
        ews_form.addRow(ews_hint)
        self.ews_group.setLayout(ews_form)

        self.caldav_url_edit = QLineEdit(self)
        self.caldav_url_edit.setPlaceholderText("https://calendar.example.corp/caldav/")
        caldav_hint_label = QLabel(
            "Логин и пароль — те же, что для почты этого аккаунта. Сюда можно ввести любой "
            "адрес сервера (не обязательно точный адрес нужного календаря) и найти на нём все "
            "доступные календари, включая расшаренные коллегами, кнопкой ниже.",
            self,
        )
        caldav_hint_label.setWordWrap(True)
        self.caldav_test_button = QPushButton("Проверить подключение", self)
        self.caldav_test_button.clicked.connect(self._on_test_connection)
        # Жалоба/задача: "настроить получение расшаренных календарей для VK
        # Mail" — по CalDAV расшаренный коллегой календарь просто попадает
        # в наш calendar-home-set как обычная коллекция (принятие
        # приглашения происходит в веб-интерфейсе VK, не по CalDAV), нужно
        # только его ОБНАРУЖИТЬ, а не вручную подбирать точный URL.
        self.caldav_discover_button = QPushButton("Найти календари на сервере…", self)
        self.caldav_discover_button.clicked.connect(self._on_discover_calendars)
        self.caldav_test_status = QLabel("", self)
        self.caldav_test_status.setWordWrap(True)

        # У VK расшаренный коллегой календарь в наш дом календарей НЕ
        # попадает (видно в журнале поиска: там только свои) — он остаётся
        # под принципалом владельца. Поэтому отдельная кнопка: назвать
        # коллегу и посмотреть, что он нам открыл.
        self.caldav_colleague_edit = QLineEdit(self)
        self.caldav_colleague_edit.setPlaceholderText("логин или почта коллеги, например ivanov")
        self.caldav_colleague_button = QPushButton("Найти календари коллеги…", self)
        self.caldav_colleague_button.clicked.connect(self._on_discover_colleague_calendars)

        self.caldav_group = QGroupBox("Подключение CalDAV", self)
        caldav_form = QFormLayout()
        caldav_form.addRow("Адрес сервера", self.caldav_url_edit)
        caldav_form.addRow(caldav_hint_label)
        caldav_form.addRow(self.caldav_discover_button)
        caldav_form.addRow("Календарь коллеги", self.caldav_colleague_edit)
        caldav_form.addRow(self.caldav_colleague_button)
        caldav_form.addRow(self.caldav_test_button)
        caldav_form.addRow(self.caldav_test_status)
        self.caldav_group.setLayout(caldav_form)

        form = QFormLayout()
        form.addRow("Название", self.name_edit)
        form.addRow("Цвет", self.color_combo)
        form.addRow("Источник", self.source_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.caldav_group)
        layout.addWidget(self.ews_group)
        layout.addWidget(self.ics_group)
        layout.addStretch(1)
        layout.addWidget(buttons)

        self._on_source_changed()

    def _on_source_changed(self) -> None:
        self.caldav_group.setVisible(self.source_combo.currentData() == calendar_store.SOURCE_CALDAV)
        self.ics_group.setVisible(self.source_combo.currentData() == calendar_store.SOURCE_ICS)
        self.ews_group.setVisible(self.source_combo.currentData() == calendar_store.SOURCE_EWS)

    def _on_test_connection(self) -> None:
        url = self.caldav_url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "Укажите адрес", "Адрес сервера CalDAV обязателен для проверки.")
            return
        account = _caldav_account_for(self, url)
        self.caldav_test_button.setEnabled(False)
        self.caldav_test_status.setText("Проверка подключения…")

        def connect_and_check() -> tuple[int | None, str | None, str | None]:
            # Проверяем чтение и запись ОТДЕЛЬНО и обе по-настоящему (запись —
            # реальным одноразовым PUT+DELETE тестового события), а не только
            # чтение — иначе получается ровно та путаница, из-за которой всё
            # началось: "проверка подключения проходит, а синхронизация нет",
            # потому что раньше проверялось только чтение (PROPFIND).
            #
            # list_calendar_names() — это ДИСКАВЕРИ всего аккаунта
            # (current-user-principal/calendar-home-set), а не проверка
            # КОНКРЕТНОГО календаря по указанному URL (её теперь делает
            # test_write_access(), напрямую по account.url — см. правку
            # _primary_calendar). Некоторые серверы отвечают 500 на такой
            # дискавери-запрос, если он сделан НЕ от корня/принципала
            # аккаунта, а от URL глубоко вложенного конкретного календаря
            # (реальный случай на VK Mail/on-premise) — раньше эта ошибка
            # обрывала всю проверку ДО того, как успевал выполниться более
            # важный прямой тест чтения/записи именно нужного календаря.
            # Список календарей — необязательная диагностика, её сбой не
            # должен маскировать результат прямой проверки.
            session = caldav_sync.CalDavSession(account)
            try:
                try:
                    calendar_count = len(session.list_calendar_names())
                    discovery_error = None
                except caldav_sync.CalDavSyncError as exc:
                    calendar_count = None
                    discovery_error = str(exc)
                write_error: str | None = None
                try:
                    session.test_write_access()
                except caldav_sync.CalDavSyncError as exc:
                    write_error = str(exc)
                return calendar_count, discovery_error, write_error
            finally:
                session.close()

        worker = _CallableWorker(connect_and_check, parent=self)

        def on_success(result: object) -> None:
            calendar_count, discovery_error, write_error = result
            lines = []
            if discovery_error is None:
                lines.append(f"Список календарей на сервере: OK (найдено {calendar_count}).")
            else:
                lines.append(f"Список календарей на сервере получить не удалось: {discovery_error}")
            if write_error is None:
                lines.append("Чтение и запись именно этого календаря — OK.")
            else:
                lines.append(f"Чтение/запись этого календаря — ОШИБКА: {write_error}")
            self.caldav_test_status.setText(" ".join(lines))
            self.caldav_test_button.setEnabled(True)
            self._test_workers.remove(worker)

        def on_failure(error_text: str) -> None:
            self.caldav_test_status.setText(f"Ошибка: {error_text}")
            self.caldav_test_button.setEnabled(True)
            self._test_workers.remove(worker)

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._test_workers.append(worker)
        worker.start()

    def _on_discover_calendars(self) -> None:
        url = self.caldav_url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "Укажите адрес", "Адрес сервера CalDAV обязателен для поиска календарей.")
            return
        account = _caldav_account_for(self, url)
        self.caldav_discover_button.setEnabled(False)
        self.caldav_test_status.setText("Ищу календари на сервере…")

        def discover() -> list[caldav_sync.CalDavCalendarInfo]:
            session = caldav_sync.CalDavSession(account)
            try:
                return session.list_calendars_detailed()
            finally:
                session.close()

        worker = _CallableWorker(discover, parent=self)

        def on_success(calendars: object) -> None:
            self.caldav_discover_button.setEnabled(True)
            self._test_workers.remove(worker)
            if not calendars:
                self.caldav_test_status.setText("На сервере не найдено ни одного календаря.")
                return
            picker = _CalendarPickerDialog(self, calendars)
            if picker.exec() == QDialog.DialogCode.Accepted:
                chosen = picker.selected()
                if chosen is not None:
                    self.caldav_url_edit.setText(chosen.url)
                    if not self.name_edit.text().strip():
                        self.name_edit.setText(chosen.name)
                    self.caldav_test_status.setText(f"Выбран календарь: {chosen.name}")

        def on_failure(error_text: str) -> None:
            self.caldav_discover_button.setEnabled(True)
            self.caldav_test_status.setText(f"Ошибка поиска: {error_text}")
            self._test_workers.remove(worker)

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._test_workers.append(worker)
        worker.start()

    def _on_discover_colleague_calendars(self) -> None:
        """Календари коллеги: открываются НАШЕЙ учётной записью по правам,
        которые он выдал — его пароль не нужен."""
        url = self.caldav_url_edit.text().strip()
        who = self.caldav_colleague_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "Укажите адрес", "Сначала укажите адрес своего календаря CalDAV.")
            return
        if not who:
            QMessageBox.warning(self, "Укажите коллегу", "Введите логин или почтовый адрес коллеги.")
            return
        account = _caldav_account_for(self, url)
        self.caldav_colleague_button.setEnabled(False)
        self.caldav_test_status.setText(f"Ищу календари коллеги {who}…")

        def discover() -> list[caldav_sync.CalDavCalendarInfo]:
            session = caldav_sync.CalDavSession(account)
            try:
                return session.list_colleague_calendars(who)
            finally:
                session.close()

        worker = _CallableWorker(discover, parent=self)

        def on_success(calendars: object) -> None:
            self.caldav_colleague_button.setEnabled(True)
            self._test_workers.remove(worker)
            if not calendars:
                self.caldav_test_status.setText(
                    f"У коллеги {who} не нашлось календарей, открытых вам. Он должен выдать доступ в своём календаре."
                )
                return
            picker = _CalendarPickerDialog(self, calendars)
            if picker.exec() == QDialog.DialogCode.Accepted:
                chosen = picker.selected()
                if chosen is not None:
                    self.caldav_url_edit.setText(chosen.url)
                    if not self.name_edit.text().strip():
                        self.name_edit.setText(f"{chosen.name} ({who})")
                    self.caldav_test_status.setText(f"Выбран календарь коллеги: {chosen.name}")

        def on_failure(error_text: str) -> None:
            self.caldav_colleague_button.setEnabled(True)
            self.caldav_test_status.setText(f"Ошибка поиска: {error_text}")
            self._test_workers.remove(worker)

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._test_workers.append(worker)
        worker.start()

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Укажите название", "Название календаря обязательно.")
            return
        if self.source_combo.currentData() == calendar_store.SOURCE_EWS:
            mailbox = self.ews_mailbox_edit.text().strip()
            if mailbox and "@" not in mailbox:
                QMessageBox.warning(self, "Укажите ящик", "Ящик коллеги — это его почтовый адрес.")
                return
            self.accept()
            return
        if self.source_combo.currentData() == calendar_store.SOURCE_CALDAV and not self.caldav_url_edit.text().strip():
            QMessageBox.warning(self, "Укажите адрес", "Адрес CalDAV-сервера обязателен для этого источника.")
            return
        if self.source_combo.currentData() == calendar_store.SOURCE_ICS:
            url = ics_subscription.normalize_url(self.ics_url_edit.text())
            if not url.lower().startswith(("http://", "https://")):
                QMessageBox.warning(self, "Укажите адрес", "Адрес подписки должен начинаться с https:// или webcal://.")
                return
        self.accept()

    def name(self) -> str:
        return self.name_edit.text().strip()

    def color(self) -> str:
        return self.color_combo.currentData()

    def source_type(self) -> str:
        return self.source_combo.currentData()

    def caldav_url(self) -> str:
        """Адрес источника: URL CalDAV, ссылка .ics или — для Exchange —
        ящик коллеги, на календарь которого оформлена подписка."""
        source = self.source_combo.currentData()
        if source == calendar_store.SOURCE_ICS:
            return ics_subscription.normalize_url(self.ics_url_edit.text())
        if source == calendar_store.SOURCE_EWS:
            return self.ews_mailbox_edit.text().strip()
        return self.caldav_url_edit.text().strip()


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
        calendars: list[calendar_store.Calendar] | None = None,
        default_calendar_id: str | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Изменить встречу" if event else "Новая встреча")
        # Обычное окно, а не диалог: у диалогов оконный менеджер рисует
        # уменьшенную рамку с мелким заголовком (жалоба: "заголовок окон
        # очень маленький"), а окно встречи живёт долго.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.resize(520, 640)
        self.attachments: list[Attachment] = list(event.attachments) if event else []
        self._color = event.color if event else None
        self._temp_dirs: list[Path] = []

        # Список "моих календарей" (жалоба: "в календаре нельзя сделать
        # несколько календарей") — на какой календарь ляжет новое событие.
        # Всегда хотя бы один пункт: если список не передали (старые
        # вызовы диалога) или он пуст, показываем календарь по умолчанию,
        # чтобы диалог не падал и не оставался без выбора вовсе.
        calendars = calendars or [
            calendar_store.Calendar(
                id=calendar_store.DEFAULT_CALENDAR_ID, name="Мои встречи", color="#3B6FB6"
            )
        ]
        self.calendar_combo = QComboBox(self)
        for cal in calendars:
            self.calendar_combo.addItem(_dot_icon(cal.color), cal.name, cal.id)
        # Для выбора голосом: «календарь эксчейндж» ищется и по источнику.
        self._calendar_choices = [
            calendar_names.CalendarChoice(id=cal.id, name=cal.name, source=cal.source_type) for cal in calendars
        ]
        # Для НОВОГО события (event is None) — календарь, выбранный в списке
        # "Мои календари" слева, а не всегда default: иначе, создав новый
        # календарь и (например, скрыв старый чекбоксом) ожидая, что события
        # теперь пойдут в него, пользователь получал событие молча
        # сохранённым под default — который мог быть в этот момент скрыт, и
        # событие выглядело как будто не создалось (жалоба: "событие не
        # создаётся в новом календаре").
        target_calendar_id = event.calendar_id if event else (default_calendar_id or calendar_store.DEFAULT_CALENDAR_ID)
        index = self.calendar_combo.findData(target_calendar_id)
        self.calendar_combo.setCurrentIndex(index if index >= 0 else 0)

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
        self.attendees_edit.setClearButtonEnabled(True)
        self.attendees_edit.setPlaceholderText("Выберите участников")
        if contacts:
            _install_recipient_completer(self.attendees_edit, contacts)
            _install_recipient_tooltip(self.attendees_edit, contacts)
        self.description_edit = QPlainTextEdit(event.description if event else "")
        self.description_edit.setPlaceholderText("Добавьте описание")
        # Минимум как раньше (пожелание "поле текста увеличь в 2 раза"),
        # дальше описание растягивается вместе с окном.
        self.description_edit.setMinimumHeight(140)
        self.description_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
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

        # Напоминание выбирается у каждой встречи отдельно: у одной уместно
        # окно, у другой — голос, о третьей напоминать не надо вовсе.
        self.remind_when_combo = QComboBox(self)
        for label, minutes in _REMIND_WHEN_OPTIONS:
            self.remind_when_combo.addItem(label, minutes)
        self.remind_mode_combo = QComboBox(self)
        for label, mode in _REMIND_MODE_OPTIONS:
            self.remind_mode_combo.addItem(label, mode)
        self.remind_mode_combo.currentIndexChanged.connect(self._on_remind_mode_changed)
        if event:
            when_index = self.remind_when_combo.findData(event.remind_minutes)
            self.remind_when_combo.setCurrentIndex(when_index if when_index >= 0 else 0)
            mode_index = self.remind_mode_combo.findData(event.remind_mode)
            self.remind_mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        self._on_remind_mode_changed()

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
        _mark_primary(buttons.button(QDialogButtonBox.StandardButton.Ok))
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

        remind_row = QHBoxLayout()
        remind_row.addWidget(_icon_label("bell", self))
        remind_row.addWidget(QLabel("Напомнить", self))
        remind_row.addWidget(self.remind_mode_combo)
        remind_row.addWidget(self.remind_when_combo)
        remind_row.addStretch(1)

        clear_attendees_button = QPushButton("✕", self)
        clear_attendees_button.setToolTip("Убрать всех участников")
        clear_attendees_button.setFixedWidth(28)
        clear_attendees_button.clicked.connect(self.attendees_edit.clear)
        attendees_row = QHBoxLayout()
        attendees_row.addWidget(_icon_label("people", self))
        attendees_row.addWidget(self.attendees_edit)
        attendees_row.addWidget(clear_attendees_button)  # сразу за полем, как в письме
        attendees_row.addSpacing(12)
        attendees_row.addWidget(attendees_address_book_button)
        self.attendees_list_view = RecipientListView(self.attendees_edit, self._contacts, self)

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

        calendar_row = QHBoxLayout()
        calendar_row.addWidget(_icon_label("calendar", self))
        calendar_row.addWidget(self.calendar_combo)

        color_row = QHBoxLayout()
        color_row.addSpacing(22)
        color_row.addWidget(self.color_button)
        color_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary_edit)
        layout.addLayout(time_row)
        layout.addLayout(repeat_row)
        layout.addLayout(remind_row)
        layout.addLayout(calendar_row)
        layout.addLayout(attendees_row)
        attendees_list_row = QHBoxLayout()
        attendees_list_row.addSpacing(22)
        attendees_list_row.addWidget(self.attendees_list_view)
        # Свободное место делят список участников и описание, остальные
        # поля прижаты к верху (жалоба: "слишком большие расстояния между
        # элементами при растягивании; подними всё вверх").
        layout.addLayout(attendees_list_row, 2)
        layout.addLayout(location_row)
        layout.addLayout(description_row, 3)
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

    def calendar_id(self) -> str:
        return self.calendar_combo.currentData()

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
        # Письмо, приложенное к встрече, тоже открывается своим окном.
        open_attachment_payload(self, attachment)

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
        return _parse_recipient_list(self.attendees_edit.text(), self._contacts)

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

    def _on_remind_mode_changed(self) -> None:
        """«За сколько» имеет смысл только если напоминать вообще просили."""
        self.remind_when_combo.setEnabled(self.remind_mode_combo.currentData() != calendar_store.REMIND_NONE)

    def remind_mode(self) -> str:
        return self.remind_mode_combo.currentData() or calendar_store.REMIND_NONE

    def remind_minutes(self) -> int:
        if self.remind_mode() == calendar_store.REMIND_NONE:
            return -1
        return int(self.remind_when_combo.currentData() or 15)

    def _on_all_day_toggled(self, checked: bool) -> None:
        # "Весь день" — время суток не имеет значения, только даты; прячем
        # часы/минуты в отображении, чтобы это было видно, а не только
        # угадывалось по галочке (жалоба на референс: "нет возможности
        # выбрать весь день" — раньше такого переключателя не было вовсе).
        fmt = "dd.MM.yyyy" if checked else "dd.MM.yyyy HH:mm"
        self.start_edit.setDisplayFormat(fmt)
        self.end_edit.setDisplayFormat(fmt)


def _apply_event_form_changes(dialog: EventDialog, changes: dict) -> None:
    """Применить поля, пришедшие по каналу управления (ipc_server:
    event_form_open/event_form_set), к открытому EventDialog — прямо в
    виджеты, чтобы человек видел каждое изменение в окне. При смене даты или
    времени начала длительность сохраняется (конец переносится вслед)."""
    duration_seconds = dialog.start_edit.dateTime().secsTo(dialog.end_edit.dateTime())
    if "summary" in changes:
        dialog.summary_edit.setText(changes["summary"])

    start = dialog.start_edit.dateTime()
    moved = False
    if "start" in changes:
        local = changes["start"].astimezone()
        start = QDateTime(QDate(local.year, local.month, local.day), QTime(local.hour, local.minute))
        moved = True
    if "date" in changes:
        day = changes["date"]
        start = QDateTime(QDate(day.year, day.month, day.day), start.time())
        moved = True
    if "time" in changes:
        hour, minute = changes["time"]
        start = QDateTime(start.date(), QTime(hour, minute))
        moved = True
    if moved:
        dialog.start_edit.setDateTime(start)
        dialog.end_edit.setDateTime(start.addSecs(duration_seconds))
    if "duration_minutes" in changes:
        dialog.end_edit.setDateTime(dialog.start_edit.dateTime().addSecs(changes["duration_minutes"] * 60))

    if "recurrence" in changes:
        rule = changes["recurrence"]
        index = 0 if rule is None else dialog.recurrence_combo.findData(rule)
        if index < 0:
            raise ValueError(f"Неизвестное повторение: {rule!r}")
        dialog.recurrence_combo.setCurrentIndex(index)

    if "participants" in changes:
        dialog.attendees_edit.setText(", ".join(changes["participants"]))
    if "add_participants" in changes:
        # С именами из адресной книги («Шилкин Евгений Александрович <...>»)
        # — голый email в поле нечитаем; уже введённое не трогаем.
        contacts = getattr(dialog, "_contacts", None) or []
        current = {addr.casefold() for addr in dialog.attendee_emails()}
        text = dialog.attendees_edit.text().strip()
        entries = [text] if text else []
        for email in changes["add_participants"]:
            if email.casefold() not in current:
                entries.append(_recipient_entry(email, contacts))
                current.add(email.casefold())
        dialog.attendees_edit.setText(", ".join(entries))

    if "location" in changes:
        dialog.location_edit.setText(changes["location"])
    if "description" in changes:
        dialog.description_edit.setPlainText(changes["description"])
    if "all_day" in changes:
        dialog.all_day_check.setChecked(bool(changes["all_day"]))
    if "calendar" in changes:
        choice = calendar_names.match_calendar(changes["calendar"], getattr(dialog, "_calendar_choices", []))
        dialog.calendar_combo.setCurrentIndex(dialog.calendar_combo.findData(choice.id))


def _event_form_state(dialog: EventDialog, existing: calendar_store.Event | None) -> dict:
    """Текущее содержимое формы — ответ каждой команды event_form_*."""
    start = dialog.start_edit.dateTime()
    end = dialog.end_edit.dateTime()
    return {
        "uid": existing.uid if existing else None,
        "summary": dialog.summary(),
        "start": start.toString(Qt.DateFormat.ISODate),  # местное время, как в окне
        "end": end.toString(Qt.DateFormat.ISODate),
        "duration_minutes": max(0, start.secsTo(end) // 60),
        "recurrence": dialog.recurrence_rule(),
        "participants": dialog.attendee_emails(),
        "location": dialog.location(),
        "description": dialog.description(),
        "all_day": dialog.all_day(),
        "calendar": dialog.calendar_combo.currentText(),
        "calendar_id": dialog.calendar_id(),
    }


def _focus_event_form_field(dialog: EventDialog, field: str) -> None:
    widgets = {
        "subject": dialog.summary_edit,
        "date": dialog.start_edit,
        "time": dialog.start_edit,
        "duration": dialog.end_edit,
        "recurrence": dialog.recurrence_combo,
        "participants": dialog.attendees_edit,
        "calendar": dialog.calendar_combo,
        "location": dialog.location_edit,
        "description": dialog.description_edit,
    }
    widget = widgets[field]
    dialog.activateWindow()
    widget.setFocus(Qt.FocusReason.OtherFocusReason)


def _validate_event_form(dialog: EventDialog, existing: calendar_store.Event | None) -> None:
    """Те же проверки, что делает MainWindow._save_event_from_dialog, но
    ошибкой клиенту канала вместо QMessageBox: форма остаётся открытой, и
    человек исправляет поле голосом или мышью, а не заполняет всё заново."""
    start, end = dialog.start_utc(), dialog.end_utc()
    if not dialog.all_day() and end <= start:
        raise ValueError("Окончание должно быть позже начала.")
    start_unchanged = existing is not None and start.replace(second=0, microsecond=0) == existing.dtstart.replace(
        second=0, microsecond=0
    )
    if not start_unchanged:
        past_cutoff = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(
            timezone.utc
        )
        if start < past_cutoff:
            raise ValueError("Нельзя запланировать встречу на прошедшую дату/время.")


class EventDetailsDialog(QDialog):
    """Просмотр встречи, которую организовал не я — со ссылками кликабельными
    и вложениями открываемыми/сохраняемыми, как в письме, плюс участники
    (с аватарками и статусом ответа, как в референсе VK Mail) и кнопки
    "Иду/Не иду/Может быть" прямо здесь — раньше участие можно было
    поменять только через приглашение в почте, что неудобно, если письмо
    уже прочитано/не под рукой."""

    def __init__(self, parent, event: calendar_store.Event, contacts: list[contact_store.Contact] | None = None):
        super().__init__(parent)
        self.setWindowTitle(event.summary or "(без темы)")
        self.resize(440, 460)
        self.event = event
        # Участники приглашений часто приходят без имени — подставляем его
        # из адресной книги, чтобы в карточке были люди, а не адреса.
        contacts = contacts or []
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

        organizer_display = (
            event.organizer_name or _contact_name_for(event.organizer_email, contacts) or event.organizer_email
        )
        attendee_rows.append(_avatar_row(organizer_display, event.organizer_email, " — организатор"))
        for attendee in event.attendees:
            label = _PARTICIPATION_LABELS.get(attendee.participation, "")
            suffix = f" — {label}" if attendee.participation != "needs-action" else ""
            attendee_rows.append(
                _avatar_row(attendee.name or _contact_name_for(attendee.email, contacts), attendee.email, suffix)
            )
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
        # Кнопка, уже совпадающая с текущим ответом (при повторном открытии
        # уже отвеченного события) — сделана недоступной, чтобы было видно,
        # что ответ уже учтён, а не выглядело, будто клик ничего не делает
        # (жалоба: "если приняли приглашение на событие, должны пропасть
        # кнопки"). Отклонённые события теперь и вовсе не показываются в
        # календаре (см. refresh_calendar_view) — эта дверь остаётся именно
        # для "Иду"/"Может быть".
        for button, participation in ((going_button, "accepted"), (not_going_button, "declined"), (maybe_button, "tentative")):
            if event.my_participation == participation:
                button.setEnabled(False)
                button.setToolTip("Уже выбрано")
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
        # Письмо, приложенное к встрече, тоже открывается своим окном.
        open_attachment_payload(self, attachment)

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
    def __init__(
        self, parent=None, *, contact: contact_store.Contact | None = None,
        contacts: list[contact_store.Contact] | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Изменить контакт" if contact else "Новый контакт")
        self.resize(460, 420)
        self._contact = contact
        self._contacts = [c for c in (contacts or []) if contact is None or c.uid != contact.uid]

        self.name_edit = QLineEdit(contact.display_name if contact else "")
        self.title_edit = QLineEdit(contact.title if contact else "")
        self.department_edit = QLineEdit(contact.department if contact else "")
        self.emails_edit = QLineEdit(", ".join(contact.emails) if contact else "")
        self.group_check = QCheckBox("Группа — список адресов рассылки (у обычного контакта один адрес)", self)
        self.group_check.setChecked(bool(contact.is_group) if contact else False)
        self.group_check.toggled.connect(self._on_group_toggled)
        self._on_group_toggled(self.group_check.isChecked())
        self.phone_edit = QLineEdit(contact.phone if contact else "")
        self.org_edit = QLineEdit(contact.organization if contact else "")
        self.notes_edit = QPlainTextEdit(contact.notes if contact else "")

        # Участников группы — из книги, а не только руками (жалоба: "при
        # создании группы нет возможности выбрать адреса из имеющихся").
        self.pick_members_button = QPushButton("Добавить из книги…", self)
        self.pick_members_button.clicked.connect(self._on_pick_members)
        self.pick_members_button.setVisible(self.group_check.isChecked())
        self.group_check.toggled.connect(self.pick_members_button.setVisible)
        emails_row = QHBoxLayout()
        emails_row.addWidget(self.emails_edit, 1)
        emails_row.addWidget(self.pick_members_button)

        form = QFormLayout()
        form.addRow("Имя", self.name_edit)
        form.addRow("Должность", self.title_edit)
        form.addRow("Подразделение", self.department_edit)
        form.addRow("Email", emails_row)
        form.addRow(self.group_check)
        form.addRow("Телефон", self.phone_edit)
        form.addRow("Организация", self.org_edit)

        # Фотография сотрудника рядом с полями — как в корпоративной книге.
        self.photo_label = QLabel(self)
        self.photo_label.setFixedSize(96, 96)
        self.photo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if contact is not None:
            self.photo_label.setPixmap(_contact_avatar(contact, 96).pixmap(96, 96))
        header_row = QHBoxLayout()
        header_row.addLayout(form, 1)
        header_row.addWidget(self.photo_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(header_row)
        layout.addWidget(QLabel("Заметки", self))
        layout.addWidget(self.notes_edit)
        layout.addWidget(buttons)

    def _on_group_toggled(self, checked: bool) -> None:
        self.emails_edit.setPlaceholderText(
            "Адреса участников через запятую" if checked else "Один адрес (для списка адресов отметьте «Группа»)"
        )

    def _on_pick_members(self) -> None:
        if not self._contacts:
            QMessageBox.information(self, "Адресная книга", "В книге пока нет других контактов.")
            return
        dialog = ContactPickerDialog(self, self._contacts, preselected=self.emails_edit.text(), persons_only=True)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        existing = [e.strip().lower() for e in self.emails_edit.text().split(",") if e.strip()]
        for candidate in dialog.selected_candidates():
            pairs = getaddresses([candidate])
            addr = pairs[0][1].lower() if pairs else ""
            if addr and "@" in addr and addr not in existing:
                existing.append(addr)
        self.emails_edit.setText(", ".join(existing))

    def accept(self) -> None:  # noqa: N802 - Qt override
        emails = [e.strip() for e in self.emails_edit.text().split(",") if e.strip()]
        if not self.group_check.isChecked() and len(emails) > 1:
            QMessageBox.warning(
                self, "Контакт",
                "У обычного контакта один адрес — иначе письмо уйдёт на все сразу.\n"
                "Оставьте один адрес или отметьте «Группа», если это список рассылки.",
            )
            self.emails_edit.setFocus()
            return
        super().accept()

    def to_contact(self) -> contact_store.Contact:
        return contact_store.Contact(
            id=self._contact.id if self._contact else None,
            uid=self._contact.uid if self._contact else "",
            display_name=self.name_edit.text().strip(),
            emails=[e.strip() for e in self.emails_edit.text().split(",") if e.strip()],
            phone=self.phone_edit.text().strip(),
            organization=self.org_edit.text().strip(),
            notes=self.notes_edit.toPlainText(),
            is_group=self.group_check.isChecked(),
            title=self.title_edit.text().strip(),
            department=self.department_edit.text().strip(),
            photo=self._contact.photo if self._contact else b"",
            photo_type=self._contact.photo_type if self._contact else "",
        )


def _shared_domain_labels(first: str, second: str) -> int:
    """Сколько последних частей доменного имени совпадает:
    calendar.vkm.corp.amurgpz.ru и imap.vkm.corp.amurgpz.ru — четыре."""
    left = [part for part in (first or "").lower().split(".") if part]
    right = [part for part in (second or "").lower().split(".") if part]
    shared = 0
    while shared < min(len(left), len(right)) and left[-1 - shared] == right[-1 - shared]:
        shared += 1
    return shared


def _pick_calendar_account(url: str, accounts: list) -> object | None:
    """Учётная запись для сервера календаря: чей почтовый сервер ближе всего
    по домену к адресу календаря. Раньше бралась «текущая» запись — та, в
    папке которой последний раз щёлкнули. После просмотра папок Exchange
    календарь VK шёл на сервер с входом Exchange и получал отказ
    (журнал: "Unauthorized, сервер предлагает только Basic")."""
    from urllib.parse import urlparse

    host = urlparse(url if "://" in url else f"https://{url}").hostname or ""
    best = None
    best_score = (-1, False)
    for account in accounts:
        server = getattr(account, "host", "") or ""
        score = (_shared_domain_labels(host, server), getattr(account, "auth_type", "password") == "password")
        if score > best_score:
            best, best_score = account, score
    if best is None or best_score[0] < 2:
        return None
    return best


class _CalDavCalendarServer:
    """Календарь CalDAV для calendar_sync: встречи и удаления по UID."""

    def __init__(self, session, username: str) -> None:
        self._session = session
        self._username = username

    def push_event(self, event) -> None:
        self._session.push_event(event, self._username, self._username)

    def push_occurrence(self, event) -> None:
        self._session.push_occurrence(event, self._username, self._username)

    def cancel_occurrence(self, uid: str) -> None:
        self._session.cancel_occurrence(uid)

    def delete_event(self, uid: str) -> None:
        self._session.delete_event(uid)

    def fetch_events(self, start, end):
        return self._session.fetch_events(start, end, self._username)


class _ExchangeCalendarServer:
    """Календарь Exchange по тому же подключению, что и почта. mailbox —
    ящик коллеги (подписка): открывается нашей учётной записью по выданным
    им правам, пароль владельца не нужен."""

    def __init__(self, session, email: str, mailbox: str = "") -> None:
        self._session = session
        self._email = email
        self._mailbox = mailbox

    def push_event(self, event) -> None:
        ews_calendar.push_event(self._session, event, self._mailbox)

    def push_occurrence(self, event) -> None:
        ews_calendar.push_occurrence(self._session, event, self._mailbox)

    def cancel_occurrence(self, uid: str) -> None:
        ews_calendar.cancel_occurrence(self._session, uid, self._mailbox)

    def shift_series(self, uid: str, delta) -> None:
        ews_calendar.shift_series(self._session, uid, delta, self._mailbox)

    def respond(self, uid: str, participation: str) -> None:
        ews_calendar.respond(self._session, uid, participation, self._mailbox)

    def delete_event(self, uid: str) -> None:
        ews_calendar.delete_event(self._session, uid, self._mailbox)

    def fetch_events(self, start, end):
        return ews_calendar.fetch_events(self._session, start, end, self._email, self._mailbox)


class _IcsCalendarServer:
    """Подписка по ссылке: только чтение."""

    def __init__(self, url: str, username: str) -> None:
        self._url = url
        self._username = username

    def push_event(self, event) -> None:
        raise RuntimeError("подписка по ссылке только для чтения")

    def delete_event(self, uid: str) -> None:
        raise RuntimeError("подписка по ссылке только для чтения")

    def fetch_events(self, start, end):
        return [
            event for event in ics_subscription.fetch_events(self._url, self._username)
            if not (event.dtend < start or event.dtstart > end)
        ]


def account_key(account, protocol: str) -> str:
    """Ключ подключённой учётной записи в дереве и во внутренних словарях.
    Протокол и сервер — часть ключа: на время перехода с Exchange на VK
    один адрес живёт в обеих системах одновременно."""
    if protocol == "ews":
        host = getattr(account, "server", "") or getattr(account, "host", "") or "autodiscover"
        return f"ews:{host}:{account.email}"
    return f"imap:{account.host}:{account.username}"


def _process_memory_mb() -> float:
    """Память процесса и его дочерних процессов (просмотр письма живёт в
    отдельных процессах QtWebEngine) в мегабайтах. Без сторонних
    библиотек: /proc на Linux, иначе 0."""
    try:
        total = 0
        for status in Path("/proc").glob("[0-9]*/status"):
            try:
                text = status.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            pid_line = [line for line in text.splitlines() if line.startswith(("Pid:", "PPid:", "VmRSS:"))]
            values = {line.split(":", 1)[0]: line.split(":", 1)[1].strip() for line in pid_line}
            if "VmRSS" not in values:
                continue
            pid = int(values.get("Pid", "0"))
            ppid = int(values.get("PPid", "0"))
            if pid == os.getpid() or ppid == os.getpid():
                total += int(values["VmRSS"].split()[0])
        return total / 1024
    except Exception:
        return 0.0


def _exception_text(exc: BaseException) -> str:
    """Текст ошибки для показа пользователю. Некоторые исключения imaplib
    несут "сырой" ответ сервера как bytes прямо в args (жалоба: окно с
    ошибкой показывало буквально b'[AUTHENTICATIONFAILED] ...' — обычный
    str() на исключении с bytes-аргументом возвращает repr этих байт, а не
    читаемый текст) — декодируем перед показом."""
    parts = [arg.decode("utf-8", errors="replace") if isinstance(arg, bytes) else str(arg) for arg in exc.args]
    text = "; ".join(parts) if parts else str(exc)
    hint = _error_hint(text)
    return f"{text}\n\n{hint}" if hint else text


# Понятные подсказки к сырым ответам серверов, которые реально встречались.
_ERROR_HINTS: tuple[tuple[str, str], ...] = (
    (
        "parol prilozheniya",
        "Сервер требует пароль приложения: обычный пароль учётной записи для почтовых программ "
        "не подходит. Создайте пароль приложения в веб-интерфейсе почты и укажите его здесь.",
    ),
    (
        "CERTIFICATE_VERIFY_FAILED",
        "Сертификат сервера подписан центром сертификации, которого нет в списке доверенных. "
        "Добавьте корневой сертификат организации в системное хранилище "
        "(/etc/pki/ca-trust/source/anchors/ и update-ca-trust) и перезапустите программу.",
    ),
    (
        "SERVERBUG",
        "Внутренний сбой почтового сервера на этой команде. Программа переподключается и повторяет "
        "запрос сама; если повторяется часто — вопрос к администратору сервера.",
    ),
    (
        "autodiscover",
        "Автопоиск настроек Exchange не удался. Укажите адрес сервера EWS вручную "
        "(https://<сервер>/EWS/Exchange.asmx).",
    ),
)


def _error_hint(text: str) -> str | None:
    lowered = text.lower()
    for needle, hint in _ERROR_HINTS:
        if needle.lower() in lowered:
            return hint
    return None


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
            self.failed.emit(_exception_text(exc))
        else:
            self.succeeded.emit(result)


def _needs_another_bodies_round(stats, bodies_limit: int | None) -> bool:
    """Ставить ли ещё один раунд докачки тел по тому же ящику.

    Раунд имеет смысл, только если предыдущий что-то скачал. Иначе
    следующий не сделает ровно ничего: вне часов обслуживания тела не
    качаются, а сервер, попросивший подождать, ничего не отдаёт — при
    этом нескачанные тела остаются, и раунды шли один за другим без
    остановки (в журнале пользователя 1260 полных синхронизаций за 40
    секунд при полностью занятом процессоре и пустом списке писем)."""
    if bodies_limit is not None:
        return False  # периодический режим: один раунд за тик
    if getattr(stats, "server_busy", False):
        return False
    return stats.bodies_pending > 0 and stats.bodies_downloaded > 0


class _SyncWorker(QThread):
    """Полная синхронизация одного ящика в фоне (см. sync_engine):
    заголовки всех папок, затем тела писем от новых к старым. Прогресс —
    сигналом в GUI-поток; остановка — threading.Event (закрытие окна)."""

    progress = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)

    # Тела качаются раундами по BODIES_ROUND писем: между раундами окно
    # успевает проверить размер базы и запустить автоархив (жалоба: "почему
    # файл не разделился на архивы по 500 МБ? он уже 2,1 ГБ" — раньше
    # проверка шла только после ПОЛНОЙ докачки всех тел, а это часы).
    BODIES_ROUND = 200

    def __init__(self, mailbox: CachedMailbox, folders: list[str], stop: threading.Event, *, bodies_limit: int | None = None, headers: bool = True, parent=None):
        super().__init__(parent)
        self._mailbox = mailbox
        self._folders = folders
        self._stop = stop
        self._bodies_limit = bodies_limit
        self._headers = headers

    def run(self) -> None:  # noqa: N802 - Qt override
        try:
            def report(text: str, _done: int, _total: int) -> None:
                self.progress.emit(text)

            if self._headers:
                stats = self._mailbox.sync_all(self._folders, progress=report, stop=self._stop)
            else:
                stats = sync_engine.SyncStats()
            bodies = 0
            if not self._stop.is_set() and self._bodies_limit != 0:
                limit = self.BODIES_ROUND if self._bodies_limit is None else min(self._bodies_limit, self.BODIES_ROUND)
                bodies = self._mailbox.download_bodies(progress=report, stop=self._stop, limit=limit)
            stats.bodies_downloaded = bodies
            stats.bodies_pending = self._mailbox.pending_bodies()
            if bodies:
                memory_report.free_memory()
        except Exception as exc:
            self.failed.emit(_exception_text(exc))
        else:
            self.finished_ok.emit(stats)


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


_log = get_logger("ui")


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
        # Растёт при каждом restoreState() сохранённых ширин колонок (см.
        # _restore_window_state) — инвалидирует любой уже ЗАПЛАНИРОВАННЫЙ
        # (через QTimer.singleShot(0, ...)) пересчёт авто-стретча "Даты",
        # поставленный ДО восстановления (например, resizeEvent от самого
        # первого показа окна с ещё дефолтным размером, до explicit
        # restoreGeometry()) — иначе такой пересчёт срабатывал уже ПОСЛЕ
        # restoreState() и затирал только что честно восстановленную
        # пользователем ширину (жалоба: "опять не сохраняется настройка").
        self._column_stretch_generation = 0
        self._auto_stretching_date = False
        # Как только пользователь САМ (мышью) хоть раз потянул границу
        # "Даты" — колонка больше никогда не растягивается автоматически,
        # это уже её собственный явный выбор ширины (переживает перезапуск,
        # см. save_mail_date_column_pinned). Без этого ЛЮБОЙ последующий
        # resize (включая совершенно обычный, не при восстановлении) снова
        # пересчитывал бы "Дату" по формуле "остаток места" и заново стирал
        # то, что человек только что сам выставил (жалоба: "опять не
        # сохраняется настройка, я настраиваю ширину поля дата").
        self._date_column_pinned = load_mail_date_column_pinned()
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
        self.theme = load_theme()
        self.archive_storage_dir = load_archive_storage_dir()
        self.mail_rules: list[MailRule] = load_mail_rules()
        self.signatures: list[Signature] = load_signatures()
        self.default_signature_id: str | None = load_default_signature_id()
        # Держим ссылки на фоновые потоки (импорт архивов, отправка
        # приглашений) — без этого Python может собрать QThread раньше, чем
        # он реально завершится, даже если у него есть родитель-QObject.
        self._background_workers: list[QThread] = []
        self.filter_column = COL_SUBJECT
        # Сортировка и фильтры — свои у каждой учётной записи и архива
        # (пожелание: «фильтры и сортировки должны быть привязаны к учёткам,
        # сейчас они на таблицу»). Ключ — тот же, что у записи в дереве.
        self._view_states: dict[str, dict] = {}
        try:
            self._view_states = load_list_view_states()
        except Exception as exc:
            _log.warning("Настройки списка писем по учётным записям не прочитаны: %s", exc)
        self._view_key: str | None = None
        self._applying_view_state = False
        self.marker_filter: str | None = None
        # Фильтры списка писем (пожелание: "нужна фильтрация по важности,
        # наличию вложений, маркеру") — маркер выше, остальные два тут.
        self.filter_important = False
        self.filter_attachments = False
        self._temp_attachment_dirs: list[Path] = []
        self._base_font_point_size = QApplication.instance().font().pointSizeF() or 10.0

        # Один локальный календарь на пользователя (не на учётную запись —
        # как и почтовый кэш, это просто локальное состояние приложения).
        self.calendar_path = profile.calendar_db_path()
        # Разовая миграция: раньше был единственный общий адрес CalDAV на
        # весь аккаунт (настраивался в Параметрах) — источник CalDAV теперь
        # настраивается per-календарь (см. AddCalendarDialog), пожелание
        # "лучше сделать настройку календаря не в параметрах, а у самого
        # календаря". Чтобы уже настроенная синхронизация не пропала молча
        # при обновлении, превращаем старую настройку в обычный
        # CalDAV-календарь один раз и стираем её, чтобы не повторять
        # миграцию при каждом запуске.
        legacy_caldav_url = load_caldav_url()
        if legacy_caldav_url:
            try:
                has_caldav_calendar = any(
                    cal.source_type == calendar_store.SOURCE_CALDAV
                    for cal in calendar_store.list_calendars(self.calendar_path)
                )
                if not has_caldav_calendar:
                    calendar_store.create_user_calendar(
                        self.calendar_path, "CalDAV", "#00897B",
                        source_type=calendar_store.SOURCE_CALDAV, caldav_url=legacy_caldav_url,
                    )
                save_caldav_url("")
            except Exception:
                pass  # необязательная миграция — при сбое старая настройка просто останется нетронутой
        self.contacts_path = profile.contacts_db_path()
        # Крупные фото адресной книги — уменьшенными копиями, в фоне после запуска.
        QTimer.singleShot(20_000, self._shrink_contact_photos)
        # Модуль «Категории писем» (Параметры → «Модули»).
        self.category_store: mail_categories.CategoryStore | None = None
        self._categories_cache: dict[str, mail_categories.Category] = {}
        self._category_labels: dict[int, mail_categories.Label] = {}
        self.category_filter: str | None = None  # None — любая, "" — без категории
        self._category_token = 0
        self._contacts_view_signature: tuple[int, int] | None = None
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
        self.filter_edit.setObjectName("searchField")
        self.filter_edit.addAction(_toolbar_icon("search", 14), QLineEdit.ActionPosition.LeadingPosition)

        self.table = QTableWidget(0, MAIL_COLUMN_COUNT, self)
        self.table.setHorizontalHeaderLabels(["", _FLAG_MARK, "!", _ATTACHMENT_MARK, "От кого", "Категория", "Тема", "Дата"])
        self.table.setItemDelegateForColumn(COL_CHECK, _ThinCheckboxDelegate(self.table))
        self._update_marker_filter_indicator()
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        # И "Тема", и "Дата" — Interactive (пользователь может тянуть обе
        # мышью), а не Qt-шный stretchLastSection: тот растягивает СТРОГО
        # последнюю колонку, но заодно запрещает менять её ширину вручную —
        # что бы туда ни поставить ("Дату" или пустую служебную колонку под
        # неё), обязательно ловим жалобу либо "не меняется ширина колонки
        # дата", либо "пустота после даты, тема не расширяется". Вместо
        # этого — свой пересчёт: "Дата" (последняя колонка) сама дотягивается
        # до правого края при изменении размера окна И при ручном
        # перетаскивании границы ЛЮБОЙ другой колонки мышью (см.
        # _stretch_date_column/_on_mail_column_resized) — раньше тянулась
        # "Тема", но при ручном сужении именно её справа оставалась пустота
        # (пересчёт срабатывал только на resize окна, а не на перетаскивание
        # границы колонки мышью). "Дата" ближе к правому краю, поэтому
        # естественно забирает освободившееся место.
        header.setSectionResizeMode(COL_SUBJECT, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(COL_SUBJECT, 320)
        header.setSectionResizeMode(COL_CATEGORY, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(COL_CATEGORY, 150)
        header.setSectionResizeMode(COL_DATE, QHeaderView.ResizeMode.Interactive)
        for col in (COL_CHECK, COL_FLAG, COL_IMPORTANCE, COL_ATTACHMENT):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionsMovable(True)
        header.sectionClicked.connect(self._set_filter_column)
        header.sectionResized.connect(self._on_mail_column_resized)
        header.sortIndicatorChanged.connect(self._on_sort_indicator_changed)
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
        compose_action = QAction(_toolbar_icon("compose"), "Написать", self)
        compose_action.setToolTip("Написать письмо…")
        compose_action.triggered.connect(self.on_compose)

        self.reply_action = QAction(_toolbar_icon("reply"), "Ответить", self)
        self.reply_action.triggered.connect(self.on_reply)

        self.reply_all_action = QAction(_toolbar_icon("reply_all"), "Ответить всем", self)
        self.reply_all_action.setToolTip("Ответить всем — отправителю и всем получателям письма")
        self.reply_all_action.triggered.connect(self.on_reply_all)

        self.forward_action = QAction(_toolbar_icon("forward"), "Переслать", self)
        self.forward_action.triggered.connect(self.on_forward)

        self.delete_action = QAction(_toolbar_icon("delete"), "Удалить", self)
        self.delete_action.setToolTip("Удалить — в корзину (Del). Безвозвратно — Ctrl+Del или Shift+Удалить.")
        self.delete_action.triggered.connect(lambda: self.on_delete_selected())

        self.archive_selected_action = QAction(_toolbar_icon("archive"), "В архив…", self)
        self.archive_selected_action.setToolTip("В архив — выгрузить отмеченные письма в архив (копия или перемещение)")
        self.archive_selected_action.triggered.connect(self.on_archive_selected)

        mail_actions_toolbar = QToolBar("Письмо", self)
        mail_actions_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        # Ответить / Ответить всем / Переслать живут в шапке открытого
        # письма, рядом с тем, на что отвечают (пожелание пользователя), —
        # в верхней панели остались действия над списком писем.
        for action in (
            compose_action,
            self.delete_action,
            self.archive_selected_action,
        ):
            mail_actions_toolbar.addAction(action)
        # Написать/Ответить/Переслать/Удалить — с подписью рядом с иконкой
        # (по референсу пользователя), а не только иконка. Остальные
        # действия в этой панели (В архив и то, что добавляется ниже —
        # Обновить/В архив.../Импорт...) остаются только иконкой — тем же
        # способом экономии места, что и раньше.
        for labelled_action in (
            compose_action,
            self.delete_action,
        ):
            button = mail_actions_toolbar.widgetForAction(labelled_action)
            if button is not None:
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

        # Фильтр списка и переключатель режима (таблица / плитки) — в строке
        # поиска, а не в панели действий: там при узком окне они уезжали за
        # стрелку «>>» и пропадали (жалоба: "при растяжении кнопки
        # переключения режимов и фильтра скрываются").
        self.filter_button = QToolButton(self)
        self.filter_button.setText("Фильтр")
        self.filter_button.setIcon(_toolbar_icon("filter"))
        self.filter_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.filter_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.filter_button.setMenu(self._build_filter_menu())
        # Сортировка списка: в таблице она есть по клику на заголовок, а в
        # режиме плиток заголовка нет (жалоба: "в режиме плашек почту нельзя
        # сортировать по дате") — общая кнопка для обоих режимов.
        self.sort_button = QToolButton(self)
        self.sort_button.setText("Сортировка")
        self.sort_button.setIcon(_toolbar_icon("sort"))
        self.sort_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.sort_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.sort_button.setMenu(self._build_sort_menu())
        self.thread_grouping = load_thread_grouping()
        self.thread_grouping_action.setChecked(self.thread_grouping)
        view_group = QActionGroup(self)
        view_group.setExclusive(True)
        self.view_table_action = QAction(_toolbar_icon("view_table"), "Таблица", self)
        self.view_table_action.setCheckable(True)
        self.view_table_action.setToolTip("Список писем таблицей")
        self.view_cards_action = QAction(_toolbar_icon("view_cards"), "Плитки", self)
        self.view_cards_action.setCheckable(True)
        self.view_cards_action.setToolTip("Список писем плитками")
        self.view_buttons: list[QToolButton] = []
        for action in (self.view_table_action, self.view_cards_action):
            view_group.addAction(action)
            button = QToolButton(self)
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            self.view_buttons.append(button)
        self.view_table_action.triggered.connect(lambda: self._set_mail_view_mode("table"))
        self.view_cards_action.triggered.connect(lambda: self._set_mail_view_mode("cards"))

        # Плитки — второй режим списка (см. _populate_cards и далее).
        self.card_list = QListWidget(self)
        self.card_delegate = _MessageCardDelegate(
            lambda: self.summaries_by_uid, self.card_list,
            thread_info=lambda uid: self._thread_info.get(uid), on_thread_toggle=self._toggle_thread,
            category_for=self._category_chip,
        )
        self.card_list.setItemDelegate(self.card_delegate)
        # Выделение в плитках — то же, что в таблице (Ctrl/Shift-клик,
        # несколько строк): раньше плитки показывали одну, а таблица
        # хранила прежний набор — удаление действовало на невидимое
        # выделение (жалоба: "в режиме плиток записи не выделяются, но в
        # таблице письма выделены").
        self.card_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.card_list.setMouseTracking(True)
        # Del — в корзину, Ctrl+Del — безвозвратно (с подтверждением). Только
        # в списке писем: в строке фильтра и в окне письма Del по-прежнему
        # стирает текст.
        for widget in (self.table, self.card_list):
            to_trash = QShortcut(QKeySequence(Qt.Key.Key_Delete), widget)
            to_trash.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            to_trash.activated.connect(lambda: self.on_delete_selected())
            permanent = QShortcut(QKeySequence(Qt.KeyboardModifier.ControlModifier | Qt.Key.Key_Delete), widget)
            permanent.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            permanent.activated.connect(lambda: self.on_delete_selected(force_permanent=True))
        self.card_list.setUniformItemSizes(True)
        self.card_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.card_list.customContextMenuRequested.connect(self._on_card_context_menu)
        self.card_list.currentItemChanged.connect(self._on_card_current_changed)
        self.card_list.itemSelectionChanged.connect(self._on_card_selection_changed)
        self.card_list.itemChanged.connect(self._on_card_item_changed)
        self.card_list.itemDoubleClicked.connect(self._on_card_double_clicked)
        self._card_items_by_uid: dict[int, QListWidgetItem] = {}
        self._syncing_card_selection = False
        self._compose_windows: list[ComposeDialog] = []  # открытые немодальные окна писем
        # Цепочки по теме в списке: показывается последнее письмо, остальные
        # скрыты под значком раскрытия (и в таблице, и в плитках).
        self._thread_info: dict[int, _ThreadInfo] = {}
        self._expanded_threads: set[str] = set()
        self.table.viewport().installEventFilter(self)
        self.mail_view_mode = load_mail_view_mode()

        table_container = QWidget(self)
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(mail_actions_toolbar)
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(4)
        filter_row.addWidget(self.filter_edit, 1)
        filter_row.addWidget(self.filter_button)
        filter_row.addWidget(self.sort_button)
        for button in self.view_buttons:
            filter_row.addWidget(button)
        table_layout.addLayout(filter_row)
        table_layout.addWidget(self.table)
        table_layout.addWidget(self.card_list)
        self._set_mail_view_mode(self.mail_view_mode)

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
        header_layout = QVBoxLayout(self.message_header_widget)
        # Отступы сверху и снизу — пожелание "сделай отступ от верхнего края и снизу".
        header_layout.setContentsMargins(12, 12, 12, 12)
        header_layout.setSpacing(8)
        # Действия с письмом — строкой кнопок над реквизитами, такими же, как
        # «Написать» и «Удалить» над списком писем (пожелание: «сделай
        # кнопками и над заголовком»).
        self.open_message_window_action = QAction(_toolbar_icon("open_window"), "Открыть в окне", self)
        self.open_message_window_action.setToolTip("Открыть письмо в отдельном окне")
        self.open_message_window_action.triggered.connect(self.on_open_message_window)
        self.view_source_action = QAction(_toolbar_icon("source"), "Исходный текст", self)
        self.view_source_action.setToolTip("Оригинал письма: все заголовки и тело как есть (Ctrl+U)")
        self.view_source_action.setShortcut(QKeySequence("Ctrl+U"))
        self.view_source_action.triggered.connect(self.on_view_message_source)
        self.addAction(self.view_source_action)
        self.message_actions_bar = QWidget(self.message_header_widget)
        actions_row = QHBoxLayout(self.message_actions_bar)
        actions_row.setContentsMargins(0, 0, 0, 0)
        actions_row.setSpacing(6)
        for action in (
            self.reply_action, self.reply_all_action, self.forward_action, self.open_message_window_action,
            self.view_source_action,
        ):
            button = QToolButton(self.message_actions_bar)
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setIconSize(QSize(18, 18))
            actions_row.addWidget(button)
        actions_row.addStretch(1)
        header_layout.addWidget(self.message_actions_bar)

        self.message_header_label = QLabel(self.message_header_widget)
        self.message_header_label.setWordWrap(True)
        self.message_header_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.message_header_label.linkActivated.connect(self._on_header_link_activated)
        self.message_header_label.linkHovered.connect(self._on_header_link_hovered)
        self._header_to = ""
        self._header_cc = ""
        header_layout.addWidget(self.message_header_label)
        # Жалоба: "заголовок письма... занимает от 50% до 100%, должен
        # занимать 4 строки" — без явной политики размера QVBoxLayout ниже
        # (reading_layout) мог отдавать этому виджету всё "лишнее" место
        # вместо тела письма, если reading_pane почему-то не забирал его
        # первым (например, до первой загрузки контента). Fixed по вертикали
        # заставляет виджет всегда занимать РОВНО столько, сколько нужно
        # для его реального содержимого (sizeHint пересчитывается заново
        # при каждой смене текста/ширины), и ни пикселем больше.
        self.message_header_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
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
        # Тот же принцип токена, но для самого выбора письма (см.
        # on_message_selected) — растёт при каждом клике по письму.
        self._message_select_token = 0

        # QWebEngineView вместо QTextBrowser — у Qt-шного рич-текстового
        # движка не было ни position:absolute/флексбоксов, ни нормальной
        # CSS-вёрстки, которыми пользуются реальные маркетинговые письма
        # (жалоба: "HTML содержимое отражается криво" — текст поверх
        # картинки/карточки внахлёст, эти письма и на скриншотах). Фон у
        # веб-движка по умолчанию белый для страницы без явного background,
        # что и нужно — тело письма почти всегда HTML в расчёте на белый
        # фон, тёмная тема интерфейса не должна красить содержимое самих
        # писем (тот же принцип, что и в веб-почте — Gmail и т.п.).
        self.reading_pane = _create_mail_browser(self)
        _render_mail_html(self.reading_pane, _BODY_WRAP_TEMPLATE.format(content="Выберите письмо, чтобы увидеть текст"))

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
        # setStretchFactor управляет только распределением ДОПОЛНИТЕЛЬНОГО
        # места при последующих resize — без явного setSizes() сам QSplitter
        # при первой раскладке делит место строго пополам между списком
        # писем и панелью чтения (жалоба: "поле просмотра письма делится
        # пополам"), пока пользователь ни разу не потянул границу вручную —
        # ниже более уместные 2:1 умолчания на первый запуск.
        self.right_splitter.setSizes([500, 250])
        # Перетаскивание сплиттера меняет доступную ширину таблицы без
        # изменения размера самого окна (resizeEvent на него не сработает) —
        # тоже должно пересчитывать "Дату" (см. _stretch_date_column).
        self.right_splitter.splitterMoved.connect(lambda *_args: self._schedule_stretch_date_column())
        self._apply_pane_orientation()

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.main_splitter.addWidget(self.right_splitter)  # панель папок вставляется первой ниже (folder_panel)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([220, 980])
        self.main_splitter.splitterMoved.connect(lambda *_args: self._schedule_stretch_date_column())

        self.calendar_week_start = week_start_for(date.today())
        self.selected_calendar_event: calendar_store.Event | None = None
        self._calendar_scrolled_to_now = False

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
        caldav_sync_action = QAction(_toolbar_icon("sync"), "Синхронизировать календари", self)
        caldav_sync_action.setToolTip(
            "Синхронизировать календари — Exchange, CalDAV (VK и др.) и подписки по ссылке. "
            "Идёт и сама: после подключения почты и затем каждые три опроса."
        )
        caldav_sync_action.triggered.connect(lambda: self.on_caldav_sync())
        calendar_toolbar.addAction(caldav_sync_action)
        # Подпись рядом со значком: одной иконки было мало, кнопку не находили
        # (жалоба: "нет кнопки синхронизации календаря exchange").
        sync_button = calendar_toolbar.widgetForAction(caldav_sync_action)
        if isinstance(sync_button, QToolButton):
            sync_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            sync_button.setText("Синхронизировать")
        # Сжатый режим: ночные и ранние часы без встреч скрыты, рабочий день
        # растягивается на всю высоту окна (пожелание: «можно скрыть всё до
        # 7:00 — рамка станет шире; включил — убралось, выключил — появилось»).
        self.calendar_compact_action = QAction("Сжатый режим", self)
        self.calendar_compact_action.setCheckable(True)
        self.calendar_compact_action.setToolTip(
            "Показывать только часы от самой ранней до самой поздней встречи недели — "
            "они растягиваются на всю высоту окна. Неделя без встреч — 7:00–20:00."
        )
        self.calendar_compact_action.toggled.connect(self._on_calendar_compact_toggled)
        calendar_toolbar.addAction(self.calendar_compact_action)
        compact_button = calendar_toolbar.widgetForAction(self.calendar_compact_action)
        if isinstance(compact_button, QToolButton):
            compact_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        calendar_toolbar.addWidget(self.calendar_view_combo)

        # Левая панель: мини-календарь для быстрого перехода к неделе +
        # список "календарей" — пока фактически один локальный, но чекбокс
        # реально скрывает/показывает события, а не просто для вида.
        self.calendar_mini_picker = QCalendarWidget(self)
        self.calendar_mini_picker.setGridVisible(False)
        self.calendar_mini_picker.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.calendar_mini_picker.clicked.connect(self.on_calendar_mini_picker_clicked)

        # Раньше был единственный чекбокс "Мои встречи" — фактически один
        # локальный календарь на всех (жалоба: "в календаре нельзя сделать
        # несколько календарей"). Список ниже строится из хранилища
        # (calendar_store.list_calendars) — заполняется по-настоящему в
        # _refresh_calendars_list(), позже в __init__, когда self.calendar_path
        # уже готов.
        self._calendars_by_row: list[calendar_store.Calendar] = []
        self._visible_calendar_ids: set[str] = set()
        self.calendars_list = QListWidget(self)
        self.calendars_list.setFrameShape(QFrame.Shape.NoFrame)
        self.calendars_list.itemChanged.connect(self.on_calendar_item_changed)
        self.calendars_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.calendars_list.customContextMenuRequested.connect(self.on_calendar_list_context_menu)
        add_calendar_button = QPushButton("+ Добавить календарь", self)
        add_calendar_button.clicked.connect(self.on_add_calendar)
        calendars_group = QGroupBox("Мои календари", self)
        calendars_group_layout = QVBoxLayout(calendars_group)
        calendars_group_layout.addWidget(self.calendars_list)
        calendars_group_layout.addWidget(add_calendar_button)
        self._refresh_calendars_list()

        calendar_sidebar = QWidget(self)
        sidebar_layout = QVBoxLayout(calendar_sidebar)
        # Ширина боковой панели — по мини-календарю при текущем шрифте и
        # масштабе, а не жёсткие 240 px: при крупном масштабе левая колонка
        # календаря обрезалась (жалоба: "криво отрабатывает масштаб").
        # Фиксированная политика по горизонтали: ширина панели = её sizeHint
        # (мини-календарь + отступы) и пересчитывается вместе со шрифтом.
        calendar_sidebar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        calendar_sidebar.setMinimumWidth(240)
        sidebar_layout.addWidget(self.calendar_mini_picker)
        sidebar_layout.addWidget(calendars_group)
        sidebar_layout.addStretch(1)

        # По умолчанию — сегодня, а не None: иначе при первом открытии
        # календаря highlighted_day в refresh_calendar_view() откатывался на
        # начало недели/месяца (self.calendar_week_start/calendar_month_anchor),
        # и мини-календарь слева подсвечивал не сегодняшнее число, а, например,
        # понедельник текущей недели (жалоба: "при открытии календаря не
        # устанавливается текущие месяц и число").
        self.calendar_selected_day: date | None = date.today()
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
        self.calendar_week_grid.emptySlotDoubleClicked.connect(self.on_calendar_empty_slot_double_clicked)
        self.calendar_week_grid.emptySlotContextMenuRequested.connect(self.on_calendar_empty_slot_context_menu)

        calendar_scroll = QScrollArea(self)
        calendar_scroll.setWidget(self.calendar_week_grid)
        calendar_scroll.setWidgetResizable(True)
        calendar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._calendar_scroll = calendar_scroll
        # Сетка растягивается на высоту видимой области: раньше при
        # растягивании окна внизу оставалась пустота.
        calendar_scroll.viewport().installEventFilter(self)
        try:
            compact = load_calendar_compact()
        except Exception:
            compact = False
        self.calendar_week_grid.set_compact(compact)
        self.calendar_compact_action.blockSignals(True)
        self.calendar_compact_action.setChecked(compact)
        self.calendar_compact_action.blockSignals(False)

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
        self.contacts_table = QTableWidget(0, 6, self)
        self.contacts_table.setHorizontalHeaderLabels(
            ["Имя", "Должность", "Подразделение", "Email", "Телефон", "Организация"]
        )
        self.contacts_table.verticalHeader().setVisible(False)
        self.contacts_table.setIconSize(QSize(32, 32))
        self.contacts_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.contacts_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # Раньше растягивался только первый столбец (Stretch стоял только
        # на нём) — при изменении размера окна таблица "перекашивалась",
        # остальные столбцы оставались фиксированной ширины (жалоба: "в
        # контактах таблица расширяется не пропорционально, а не каждый
        # столбец"). Растягиваем все столбцы поровну.
        contacts_header = self.contacts_table.horizontalHeader()
        for col in range(self.contacts_table.columnCount()):
            contacts_header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.contacts_table.itemSelectionChanged.connect(self.on_contact_selection_changed)
        self.contacts_table.itemDoubleClicked.connect(self.on_contact_double_clicked)

        contacts_toolbar = QToolBar("Контакты", self)
        contacts_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)  # как в почте: значок и подпись
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

        # Поиск и фильтр по типу (жалобы: "по книге нет поиска", "не
        # отфильтровать группы, признак не виден").
        self.contacts_search_edit = QLineEdit(self)
        self.contacts_search_edit.setObjectName("searchField")
        self.contacts_search_edit.setPlaceholderText("Поиск по имени, адресу, организации")
        self.contacts_search_edit.addAction(_toolbar_icon("search", 14), QLineEdit.ActionPosition.LeadingPosition)
        self.contacts_search_edit.setClearButtonEnabled(True)
        self.contacts_search_edit.textChanged.connect(self._apply_contacts_filter)
        self.contacts_kind_combo = QComboBox(self)
        self.contacts_kind_combo.addItems(["Все", "Люди", "Группы"])
        self.contacts_kind_combo.currentIndexChanged.connect(self._apply_contacts_filter)
        contacts_filter_row = QHBoxLayout()
        contacts_filter_row.setContentsMargins(6, 4, 6, 4)
        contacts_filter_row.addWidget(self.contacts_search_edit, 1)
        contacts_filter_row.addWidget(self.contacts_kind_combo)
        self.contacts_count_label = QLabel("", self)
        contacts_filter_row.addWidget(self.contacts_count_label)

        # Два режима книги: таблица и карточки с фотографией (пожелание
        # «хочу карточки с аватаром»).
        self.contacts_view_table_action = QAction(_toolbar_icon("view_table"), "Таблицей", self)
        self.contacts_view_table_action.setCheckable(True)
        self.contacts_view_cards_action = QAction(_toolbar_icon("view_cards"), "Карточками", self)
        self.contacts_view_cards_action.setCheckable(True)
        contacts_view_group = QActionGroup(self)
        for action in (self.contacts_view_table_action, self.contacts_view_cards_action):
            contacts_view_group.addAction(action)
            button = QToolButton(self)
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            contacts_filter_row.addWidget(button)
        self.contacts_view_table_action.triggered.connect(lambda: self._set_contacts_view_mode("table"))
        self.contacts_view_cards_action.triggered.connect(lambda: self._set_contacts_view_mode("cards"))

        self.contacts_card_list = QListWidget(self)
        self.contacts_card_delegate = _ContactCardDelegate(lambda: self._contacts_by_row, self.contacts_card_list)
        self.contacts_card_list.setItemDelegate(self.contacts_card_delegate)
        self.contacts_card_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.contacts_card_list.setMouseTracking(True)
        self.contacts_card_list.setUniformItemSizes(True)
        self.contacts_card_list.itemSelectionChanged.connect(self._on_contact_card_selected)
        self.contacts_card_list.itemDoubleClicked.connect(self._on_contact_card_double_clicked)

        contacts_page = QWidget(self)
        contacts_layout = QVBoxLayout(contacts_page)
        contacts_layout.setContentsMargins(0, 0, 0, 0)
        contacts_layout.setSpacing(0)
        contacts_layout.addWidget(contacts_toolbar)
        contacts_layout.addLayout(contacts_filter_row)
        contacts_layout.addWidget(self.contacts_table)
        contacts_layout.addWidget(self.contacts_card_list)
        self.contacts_view_mode = load_contacts_view_mode()
        self._set_contacts_view_mode(self.contacts_view_mode)

        self.pages = QStackedWidget(self)
        self.pages.addWidget(self.main_splitter)  # 0: почта
        self.pages.addWidget(calendar_page)  # 1: календарь
        self.pages.addWidget(contacts_page)  # 2: контакты
        self.setCentralWidget(self.pages)

        toolbar = QToolBar("Основная", self)
        toolbar.setObjectName("modeBar")
        # Отступы сверху и снизу — через компоновку, а не QSS: padding у
        # QToolBar стиль Fusion игнорирует (пожелание: "добавь отступы
        # сверху и снизу меню").
        toolbar.layout().setContentsMargins(8, 8, 8, 8)
        toolbar.layout().setSpacing(6)
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
        log_action = QAction("Журнал подключений…", self)
        log_action.setToolTip("Журнал входов, переподключений, отправок и синхронизаций")
        log_action.triggered.connect(self.on_show_log)
        help_menu.addAction(log_action)
        diag_action = QAction("Расход памяти…", self)
        diag_action.setToolTip("Сколько памяти занимает программа и на что она уходит")
        diag_action.triggered.connect(self.on_show_memory)
        help_menu.addAction(diag_action)
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
        # Кнопки архивов — над деревом папок (пожелание: "кнопки, связанные
        # с подключением архива, перенеси в раздел дерева папок"), см.
        # folder_toolbar ниже: архив — это ещё один узел дерева, логично
        # управлять им рядом с деревом, а не среди действий над письмами.
        open_archive_action = QAction(_toolbar_icon("open_archive"), "Открыть архив…", self)
        open_archive_action.triggered.connect(self.on_open_archive)

        import_action = QAction(_toolbar_icon("import"), "Импортировать…", self)
        import_action.setToolTip("Импортировать — mbox/Maildir (Evolution) или .pst (Outlook) в архив")
        import_action.triggered.connect(self.on_import)

        self.archive_folder_action = QAction(_toolbar_icon("archive_folder"), "Архивировать папку…", self)
        self.archive_folder_action.setToolTip("Архивировать папку — выгрузить в архив всю папку целиком или всё старше выбранной даты")
        self.archive_folder_action.triggered.connect(self.on_archive_folder)

        self.folder_toolbar = QToolBar("Архивы", self)
        # Размер значков — как у остальных панелей (жалоба: "кнопки архива
        # над деревом папок меньше других").
        self.folder_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        for action in (open_archive_action, import_action, self.archive_folder_action):
            self.folder_toolbar.addAction(action)
        self.folder_panel = QWidget(self)
        folder_panel_layout = QVBoxLayout(self.folder_panel)
        folder_panel_layout.setContentsMargins(0, 0, 0, 0)
        folder_panel_layout.setSpacing(2)
        folder_panel_layout.addWidget(self.folder_toolbar)
        folder_panel_layout.addWidget(self.folder_tree, 1)
        self.main_splitter.insertWidget(0, self.folder_panel)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([220, 980])

        # Обновить — была в основном тулбаре наверху, перенесена вниз к
        # написать/ответить/переслать и поставлена первой (по просьбе
        # пользователя). insertAction ставит её перед "Написать письмо…",
        # а не в конец, куда её добавил бы addAction.
        refresh_action = QAction(_toolbar_icon("refresh"), "Обновить", self)
        refresh_action.triggered.connect(self.on_refresh)
        mail_actions_toolbar.insertAction(compose_action, refresh_action)

        self.setStatusBar(QStatusBar(self))
        # Индикатор фоновой синхронизации (жалоба: "нет информирования для
        # пользователя о прохождении синхронизации"): бегущая полоса и текст
        # справа в строке состояния, пока идёт подключение/обновление папки.
        self.busy_label = QLabel("", self)
        self.busy_bar = QProgressBar(self)
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setFixedWidth(90)
        self.busy_bar.setTextVisible(False)
        self.busy_label.hide()
        self.busy_bar.hide()
        self.statusBar().addPermanentWidget(self.busy_label)
        self.statusBar().addPermanentWidget(self.busy_bar)
        self._refresh_in_progress = False
        self._sync_worker: _SyncWorker | None = None
        self._autoarchive_active = False
        self._autoarchive_exhausted: set[str] = set()
        self._sync_queue: list[str] = []
        self._sync_stop = threading.Event()
        self._periodic_ticks = 0
        self.mailbox_folders: dict[str, list[str]] = {}

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

        self._calendar_sync_running = False
        self._calendar_changed_in_background = False
        self._apply_categories_module(plugin_enabled("categories", mail_plugins.CATEGORIES.enabled_by_default))
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._on_periodic_refresh)
        self._restart_poll_timer()

        # Локальный канал управления (см. ipc_server.py): через него внешняя
        # программа — прежде всего будущий голосовой ассистент — просит уже
        # запущенное окно открыть письмо/встречу с заполненными полями.
        # Поднимается последним и целиком необязателен: если имя сокета
        # занято живым соседним экземпляром или ОС не дала его создать,
        # приложение обязано работать дальше как обычно, поэтому здесь и
        # проверка результата, и защита от исключения.
        # Открытая через канал пошаговая форма встречи (диалог, исходное
        # событие) — см. ipc_event_form_open; None, пока формы нет.
        self._ipc_event_form: tuple[EventDialog, calendar_store.Event | None] | None = None
        self.ipc_server = IpcServer(self, parent=self)
        try:
            self.ipc_server.start()
        except Exception:
            pass

        QTimer.singleShot(0, self._restore_saved_account)
        QTimer.singleShot(0, self._restore_saved_archives)
        self._restore_window_state()

    def _restore_window_state(self) -> None:
        # restoreGeometry() ниже сама по себе синхронно доставляет
        # resizeEvent (смена размера окна — это и есть resize) — без этого
        # флага тот же самый auto-stretch пересчитывался бы через
        # resizeEvent() ПОКА мы ещё внутри restoreGeometry()/restoreState(),
        # затирая только что честно восстановленную ширину "Даты" ничуть не
        # хуже старого безусловного вызова (жалоба: "опять не сохраняется
        # настройка, я настраиваю ширину поля дата, а при повторном
        # открытии настройки возвращаются обратно").
        self._restoring_window_state = True
        # Инвалидирует любой auto-stretch "Даты", уже поставленный в очередь
        # ДО этого момента (например, от resizeEvent самого первого показа
        # окна с ещё дефолтным размером) — иначе он сработает уже ПОСЛЕ
        # restoreState() ниже и всё равно затрёт восстановленную ширину,
        # несмотря на флаг _restoring_window_state (тот успеет вернуться в
        # False к моменту срабатывания отложенного вызова).
        self._column_stretch_generation += 1
        try:
            geometry = load_window_geometry()
            if geometry:
                self.restoreGeometry(QByteArray(geometry))
            splitters_state = load_mail_splitters_state()
            if splitters_state:
                if "main" in splitters_state:
                    self.main_splitter.restoreState(QByteArray(splitters_state["main"]))
                if "right" in splitters_state:
                    self.right_splitter.restoreState(QByteArray(splitters_state["right"]))
            columns_state = load_mail_columns_state(MAIL_COLUMN_COUNT)
            if columns_state:
                self.table.horizontalHeader().restoreState(QByteArray(columns_state))
                # Ширины колонок восстановлены как есть — геометрия окна
                # восстановлена той же строкой выше, под тот же размер, под
                # который сохранялись и ширины колонок, пересчитывать
                # заново нечего.
                return
        except Exception:
            pass  # сохранённое расположение не подошло (например, число колонок изменилось) — не критично
        finally:
            self._restoring_window_state = False
        # Сохранённого состояния нет (первый запуск) или оно не подошло —
        # разложить "Дату" по ширине окна с нуля.
        self._schedule_stretch_date_column()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if getattr(self, "_restoring_window_state", False):
            return
        self._schedule_stretch_date_column()

    def _on_mail_column_resized(self, logical_index: int, _old_size: int, _new_size: int) -> None:
        if logical_index == COL_DATE:
            # Если это не мы сами (см. _auto_stretching_date в
            # _stretch_date_column) и не восстановление сохранённого
            # состояния — значит, пользователь только что САМ потянул
            # границу "Даты" мышью. С этого момента колонка больше никогда
            # не растягивается автоматически — это её явный, осознанный
            # выбор ширины, который должен пережить и обычный resize окна,
            # и перезапуск программы (жалоба: "опять не сохраняется
            # настройка, я настраиваю ширину поля дата, а при повторном
            # открытии настройки возвращаются обратно").
            if not self._auto_stretching_date and not getattr(self, "_restoring_window_state", False):
                self._date_column_pinned = True
                save_mail_date_column_pinned(True)
            return
        # Ручное перетаскивание границы любой ДРУГОЙ колонки (чаще всего —
        # "Тема") мышью не проходит через resizeEvent окна — без этого
        # хука пересчёт "Даты" срабатывал только при изменении размера
        # самого окна, и сужение "Темы" мышью оставляло пустоту справа от
        # "Даты" до следующего resize (жалоба: "при изменении ширины темы
        # появляется пустота справа").
        # header.restoreState() тоже шлёт sectionResized на каждую
        # восстанавливаемую колонку — без этой проверки тут же затирался бы
        # только что восстановленный размер "Даты" (см. _restore_window_state).
        if getattr(self, "_restoring_window_state", False):
            return
        self._schedule_stretch_date_column()

    def _schedule_stretch_date_column(self) -> None:
        # Отложено на следующий цикл событий: resizeEvent верхнего окна
        # доставляется ДО того, как Qt пересчитает геометрию вложенных
        # сплиттеров/таблицы — если читать self.table.viewport().width()
        # прямо здесь, там ещё старое (или вовсе не размеченное) значение,
        # и колонка застревала на минимальной ширине вместо реального
        # растягивания (жалоба вернулась: "тема не расширяется", хотя код
        # уже пытался это делать). К следующему тику раскладка уже готова.
        #
        # Поколение захватывается СЕЙЧАС, а не в момент срабатывания —
        # если между планированием и следующим тиком событий успеет
        # пройти restoreState() сохранённых ширин (см. _restore_window_state),
        # он увеличит _column_stretch_generation, и этот, уже устаревший,
        # вызов просто ничего не сделает вместо того, чтобы затереть только
        # что восстановленную пользователем ширину "Даты".
        generation = self._column_stretch_generation
        QTimer.singleShot(0, lambda: self._stretch_date_column_if_current(generation))

    def _stretch_date_column_if_current(self, generation: int) -> None:
        if generation != self._column_stretch_generation:
            return
        self._stretch_date_column()

    def _stretch_date_column(self) -> None:
        # "Дата" (последняя колонка) тянется до правого края (жалоба:
        # "таблица не растягивается на всё окно"), но обычным Qt-шным
        # stretchLastSection этого не добиться без потери возможности
        # потянуть её (или "Тему") мышью — см. комментарий у создания
        # self.table выше. Пересчитываем вручную: сколько места остаётся
        # после всех ОСТАЛЬНЫХ колонок — столько и отдаём "Дате", не трогая
        # их собственную, уже выставленную пользователем ширину.
        if self._date_column_pinned:
            # Пользователь уже задал ширину "Даты" сам — с этого момента
            # это НЕ авто-стретчащаяся колонка, трогать её нельзя (см.
            # _on_mail_column_resized).
            return
        header = self.table.horizontalHeader()
        other_width = sum(header.sectionSize(col) for col in range(header.count()) if col != COL_DATE)
        available = self.table.viewport().width()
        min_width = 90
        self._auto_stretching_date = True
        try:
            header.resizeSection(COL_DATE, max(min_width, available - other_width))
        finally:
            self._auto_stretching_date = False

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
            # Запись, потерянная прежней ошибкой сохранения, уцелела в
            # старом account.json — возвращаем её в список один раз.
            recovered = recover_legacy_account()
            if recovered:
                self.statusBar().showMessage(f"Учётная запись {recovered} восстановлена из прежних настроек", 10000)
        except Exception as exc:
            _log.warning("Восстановление записи из старых настроек не удалось: %s", exc)
        try:
            saved_accounts = load_accounts()
        except secret_store.SecretsUnavailable as exc:
            # Системное хранилище (gnome-keyring/KWallet по D-Bus) недоступно
            # и после повторов (жалоба: "периодически получаю: No recommended
            # backend was available"). Предлагаем запасной файл в профиле —
            # только по явному согласию, с честным описанием.
            answer = QMessageBox.question(
                self,
                "Хранилище паролей недоступно",
                f"Системное хранилище паролей не отвечает: {exc}\n\n"
                "Хранить пароли в файле профиля? Файл доступен только вашему пользователю ОС, "
                "но не зашифрован. Если отказаться, подключиться можно вручную через Параметры "
                "(пароль потребуется вводить при каждом запуске, пока хранилище недоступно).",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            secret_store.set_fallback_enabled(True)
            try:
                saved_accounts = load_accounts()
            except Exception as exc2:
                QMessageBox.warning(self, "Не удалось получить сохранённые данные входа", str(exc2))
                return
            if not saved_accounts:
                self.statusBar().showMessage("Паролей в файле профиля пока нет — подключитесь через Параметры", 8000)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Не удалось получить сохранённые данные входа",
                f"Хранилище паролей недоступно: {exc}\n\nПодключитесь заново вручную.",
            )
            return
        try:
            saved_ews_accounts = load_ews_accounts()
        except Exception:
            saved_ews_accounts = []  # то же хранилище секретов, что и выше — если оно уже пожаловалось, не дублируем

        # Жалоба: "при подключении ящика создаётся ощущение подвисания, нет
        # информирования о прохождении синхронизации" — вход на сервер и
        # список папок шли прямо в потоке интерфейса. Теперь по очереди в
        # фоне, с немодальным окном хода: видно, что именно происходит, а
        # окно программы живёт.
        try:
            disabled = set(load_disabled_accounts())
        except Exception:
            disabled = set()
        queue: list[tuple[str, object, object]] = [
            ("imap", acc, smtp) for acc, smtp in saved_accounts if account_key(acc, "imap") not in disabled
        ]
        queue += [("ews", acc, None) for acc in saved_ews_accounts if account_key(acc, "ews") not in disabled]
        skipped = (len(saved_accounts) + len(saved_ews_accounts)) - len(queue)
        if skipped:
            _log.info("Выключенных учётных записей пропущено при запуске: %d", skipped)
        if not queue:
            return
        self._connect_accounts_async(queue)

    def _connect_accounts_async(self, queue: list[tuple[str, object, object]]) -> None:
        """Подключает учётные записи из очереди по одной в фоне, показывая
        ход в отдельном окне. Тем же путём идёт и включение записи
        галочкой в параметрах: раньше включение ждало перезапуска
        программы, хотя выключение срабатывало сразу."""
        progress = QProgressDialog("Подключение к почте…", None, 0, len(queue), self)
        progress.setWindowTitle("Подключение")
        progress.setWindowModality(Qt.WindowModality.NonModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        progress.show()
        restored: list[str] = []
        done_count = 0

        def next_account() -> None:
            nonlocal done_count
            if not queue:
                progress.close()
                if restored:
                    self.statusBar().showMessage(f"Восстановлено подключений: {', '.join(restored)}", 5000)
                    self._refresh_folder_async(silent=True)
                    QTimer.singleShot(1500, self._start_full_sync)
                    # Календари — вскоре после подключения почты: Exchange
                    # берёт её соединение, CalDAV — её учётные данные.
                    self._schedule_calendar_sync(20_000)
                return
            protocol, account, smtp_account = queue.pop(0)
            name = account.email if protocol == "ews" else account.username
            progress.setLabelText(f"{name}: вход на сервер и список папок…")

            def connect():
                session = EwsSession(account) if protocol == "ews" else ImapSession(account)
                return session, session.list_folders()

            worker = _CallableWorker(connect, parent=self)

            def on_success(result: object) -> None:
                nonlocal done_count
                self._background_workers.remove(worker)
                session, folders = result
                if protocol == "ews":
                    self._add_or_replace_ews_account(account, session, folders)
                else:
                    self._add_or_replace_account(account, smtp_account, session, folders)
                restored.append(name)
                done_count += 1
                progress.setValue(done_count)
                next_account()

            def on_failure(error_text: str) -> None:
                nonlocal done_count
                self._background_workers.remove(worker)
                done_count += 1
                progress.setValue(done_count)
                title = "Не удалось войти в Exchange с сохранёнными данными" if protocol == "ews" else "Не удалось войти с сохранёнными данными"
                QMessageBox.warning(self, title, f"{name}: {error_text}\n\nПодключитесь заново вручную.")
                next_account()

            worker.succeeded.connect(on_success)
            worker.failed.connect(on_failure)
            self._background_workers.append(worker)
            worker.start()

        next_account()

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
        self._ensure_ews_calendar(account)

    def _ensure_ews_calendar(self, account: EwsAccount) -> None:
        """Календарь Exchange заводится сам при подключении почты Exchange.
        Отдельный адрес и пароль ему не нужны — встречи идут по тому же
        подключению EWS (вопрос: "календарь не надо подключать для
        exchange, как тогда он создастся в календаре?")."""
        try:
            calendars = calendar_store.list_calendars(self.calendar_path)
            if any(cal.source_type == calendar_store.SOURCE_EWS for cal in calendars):
                return  # уже заведён, второй такой же не нужен
            used_colors = {cal.color for cal in calendars}
            color = next(
                (hexval for _label, hexval in _EVENT_COLOR_PALETTE if hexval not in used_colors),
                _EVENT_COLOR_PALETTE[0][1],
            )
            created = calendar_store.create_user_calendar(
                self.calendar_path, f"Exchange: {account.email}", color,
                source_type=calendar_store.SOURCE_EWS,
            )
        except Exception as exc:
            _log.warning("Не удалось завести календарь Exchange: %s", exc)
            return
        _log.info("Заведён календарь Exchange «%s»", created.name)
        self._refresh_calendars_list(select_id=created.id)
        self.refresh_calendar_view()

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
        Ключ включает протокол и сервер: при переезде с Exchange на VK
        один и тот же адрес существует сразу в двух системах, и по одному
        только адресу вторая запись считалась «уже подключённой» и
        подменяла первую (жалоба: "добавление Exchange невозможно, первая
        запись совпадает с vk")."""
        key = account_key(account, protocol)
        old_mailbox = self.mailboxes.get(key)
        if old_mailbox is not None:
            old_mailbox.close()

        self.mailboxes[key] = CachedMailbox(
            session, account, body_max_bytes=load_body_max_size_mb() * 1024 * 1024
        )
        if protocol == "ews":
            # Прежние сборки сохраняли даты писем Exchange по Гринвичу —
            # один раз переводим их в местное время, до первой синхронизации.
            try:
                from redmail import cache_store

                shifted = cache_store.localize_utc_dates_once(self.mailboxes[key].account_key)
                if shifted:
                    _log.info("Exchange %s: даты %d писем переведены в местное время", key, shifted)
            except Exception as exc:
                _log.warning("Exchange %s: даты писем не переведены в местное время: %s", key, exc)
        # «Вся почта»/All Mail у Gmail — зеркало всех остальных папок: в полную
        # локальную копию не входит, иначе база удваивается (на .80: 3.3 ГБ).
        self.mailbox_folders[key] = [info.name for info in folders if _folder_role(info.name) != "all"]
        # Без офлайн-копии ящиков коллег их письма не качаются фоном: в папке
        # коллеги видны заголовки, тело берётся с сервера при открытии письма.
        skip_shared = excluded_shared_folders(
            (info.name for info in folders),
            bool(getattr(account, "shared_offline", False)),
            tuple(getattr(account, "shared_mailboxes", ()) or ()),
        )
        self.mailboxes[key].skip_body_folders = tuple(
            [info.name for info in folders if _folder_role(info.name) == "all"] + list(skip_shared)
        )
        self.mailbox_accounts[key] = account
        self.mailbox_smtp_accounts[key] = smtp_account
        self.mailbox_protocols[key] = protocol
        self.mailbox_trash_folders[key] = session.trash_folder()
        self.mailbox_sent_folders[key] = session.sent_folder()
        self.mailbox_drafts_folders[key] = session.drafts_folder()
        self.mailboxes[key].content_hook = self._calendar_hook_for(
            account, self.mailbox_drafts_folders[key], self.mailboxes[key].account_key,
        )

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

        # Раньше в дереве показывался внутренний ключ записи
        # («ews:svb-mail…:rsponomarev@…») — теперь та же подпись, что в
        # списке учётных записей; ключ остаётся во всплывающей подсказке.
        account = self.mailbox_accounts.get(key)
        title = self._account_title(account, self.mailbox_protocols.get(key, "imap")) if account is not None else key
        root = QTreeWidgetItem([title])
        root.setToolTip(0, title)
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
                    label = _DISPLAY_NAMES.get(part, part)
                    node = QTreeWidgetItem([label])
                    node.setData(0, _FOLDER_BASE_LABEL_ROLE, label)
                    node.setIcon(0, _folder_icon(None))
                    parent.addChild(node)
                    nodes[path] = node
                parent = node
            existing = parent.data(0, Qt.ItemDataRole.UserRole)
            if existing is not None and existing[1] != info.name:
                # Две разные папки с одним именем в дереве (у Gmail —
                # служебная «[Gmail]/Отправленные» и своя метка
                # «Отправленные»): раньше второй затирал первый, и узел
                # «Отправленные» открывал пустую папку (жалоба: "папка
                # отправленные пуста?"). Служебной — обычное имя, второй —
                # с пометкой.
                base_label = parent.data(0, _FOLDER_BASE_LABEL_ROLE) or parts[-1]
                container = parent.parent() if parent.parent() is not None else root
                duplicate = QTreeWidgetItem([f"{base_label} (своя папка)"])
                duplicate.setData(0, _FOLDER_BASE_LABEL_ROLE, f"{base_label} (своя папка)")
                container.addChild(duplicate)
                # Служебная — та, что лежит в скрытом контейнере ([Gmail]/…);
                # по одному имени не отличить: своя метка «Отправленные»
                # тоже похожа на служебную.
                new_is_special = any(seg in _HIDDEN_PATH_SEGMENTS for seg in info.name.split(delimiter))
                old_is_special = any(seg in _HIDDEN_PATH_SEGMENTS for seg in existing[1].split(delimiter))
                if new_is_special and not old_is_special:
                    # Узел без пометки достаётся служебной папке; прежнюю
                    # (свою) переносим в узел с пометкой, а ниже обычным
                    # порядком привязываем к узлу новую папку.
                    duplicate.setData(0, Qt.ItemDataRole.UserRole, existing)
                    duplicate.setIcon(0, _folder_icon(None))
                else:
                    duplicate.setData(0, Qt.ItemDataRole.UserRole, (key, info.name))
                    duplicate.setIcon(0, _folder_icon(_folder_role(info.name)))
                    if first_selectable is None:
                        first_selectable = duplicate
                    continue
            parent.setData(0, Qt.ItemDataRole.UserRole, (key, info.name))
            # Роль/иконка по полному "сырому" имени папки на сервере — сама
            # папка может быть значима (Отправленные, Спам...), даже если
            # промежуточные сегменты пути в дереве — просто контейнеры.
            parent.setIcon(0, _folder_icon(_folder_role(info.name)))
            if first_selectable is None:
                first_selectable = parent
            if info.name == "INBOX":
                inbox_item = parent

        self.folder_tree.expandAll()
        self._refresh_folder_unread_counts(key, folders)
        return inbox_item or first_selectable

    def _refresh_folder_unread_counts(self, key: str, folders: list[FolderInfo]) -> None:
        """Число непрочитанных в каждой папке — фоновый STATUS-опрос всех
        папок сразу после построения дерева (жалоба: "подписывать
        количество писем тоже"). Одна папка без ответа/с ошибкой не должна
        оставлять счётчики остальных пустыми — см. fetch_counts ниже."""
        mailbox = self.mailboxes.get(key)
        if mailbox is None:
            return

        def fetch_counts() -> dict[str, int]:
            counts: dict[str, int] = {}
            session = mailbox.interactive_session() if isinstance(mailbox, CachedMailbox) else mailbox.session
            for info in folders:
                try:
                    counts[info.name] = session.folder_unseen_count(info.name)
                except Exception:
                    continue
            return counts

        worker = _CallableWorker(fetch_counts, parent=self)

        def on_success(counts: object) -> None:
            self._apply_folder_unread_counts(key, counts)
            if worker in self._background_workers:
                self._background_workers.remove(worker)

        def on_failure(_error_text: str) -> None:
            if worker in self._background_workers:
                self._background_workers.remove(worker)

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._background_workers.append(worker)
        worker.start()

    def _apply_folder_unread_counts(self, key: str, counts: dict[str, int], *, partial: bool = False) -> None:
        """partial=True — обновить только папки из counts (остальные не
        трогать); иначе папки, которых нет в counts, показываются без числа."""
        root = self.mailbox_tree_roots.get(key)
        if root is None:
            return

        def walk(item: QTreeWidgetItem) -> None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, tuple) and len(data) == 2 and data[0] == key and (not partial or data[1] in counts):
                base = item.data(0, _FOLDER_BASE_LABEL_ROLE) or item.text(0)
                count = counts.get(data[1], 0)
                item.setText(0, f"{base} ({count})" if count else base)
            for i in range(item.childCount()):
                walk(item.child(i))

        walk(root)

    @contextmanager
    def _account_context(self, key: str | None):
        """На время блока делает запись key «текущей» (self.account,
        self.mailbox, SMTP, папки) и затем возвращает прежнюю — если за это
        время пользователь сам не переключился на другую запись."""
        if key is None or key not in self.mailboxes:
            yield
            return
        names = (
            "account", "mailbox", "smtp_account", "account_protocol", "account_root",
            "trash_folder_name", "sent_folder_name", "drafts_folder_name",
        )
        saved = {name: getattr(self, name, None) for name in names}
        chosen = self.mailboxes[key]
        self.account = self.mailbox_accounts[key]
        self.mailbox = chosen
        self.smtp_account = self.mailbox_smtp_accounts.get(key)
        self.account_protocol = self.mailbox_protocols.get(key, "imap")
        self.account_root = self.mailbox_tree_roots.get(key)
        self.trash_folder_name = self.mailbox_trash_folders.get(key)
        self.sent_folder_name = self.mailbox_sent_folders.get(key)
        self.drafts_folder_name = self.mailbox_drafts_folders.get(key)
        try:
            yield
        finally:
            if self.mailbox is chosen and saved["mailbox"] in self.mailboxes.values():
                for name, value in saved.items():
                    setattr(self, name, value)

    def _mailbox_key(self, mailbox=None) -> str | None:
        target = self.mailbox if mailbox is None else mailbox
        return next((k for k, m in self.mailboxes.items() if m is target), None)

    def _sync_folders_async(self, folders: list[str], *, mailbox=None) -> None:
        """Досинхронизировать локальные копии указанных папок в фоне.
        Нужно после переноса писем: письмо уже лежит в корзине на сервере,
        но в кэше «Корзины» его нет до следующего полного прохода — и
        папка выглядела пустой (жалоба: "удалились полностью, без
        помещения в корзину"). Счётчики в дереве обновляются здесь же.

        mailbox — ящик, в котором было действие. Без него берётся текущий,
        а текущий меняется, стоит щёлкнуть папку другой учётной записи,
        пока действие идёт в фоне (жалоба: "сбивается синхронизация и
        удаление, если переключаться между папками")."""
        mailbox = self.mailbox if mailbox is None else mailbox
        key = self._mailbox_key(mailbox)
        folders = [f for f in dict.fromkeys(folders) if f]
        if not isinstance(mailbox, CachedMailbox) or key is None or not folders:
            return

        def sync() -> dict[str, int]:
            counts: dict[str, int] = {}
            for folder in folders:
                try:
                    mailbox.refresh_folder(folder)
                except Exception as exc:
                    _log.warning("Досинхронизация папки %s не удалась: %s", folder, exc)
                    continue
                try:
                    counts[folder] = mailbox.interactive_session().folder_unseen_count(folder)
                except Exception:
                    continue
            return counts

        worker = _CallableWorker(sync, parent=self)

        def done(counts: object = None) -> None:
            if worker in self._background_workers:
                self._background_workers.remove(worker)
            if counts:
                self._apply_folder_unread_counts(key, counts, partial=True)
            if self.current_folder in folders and self.active_source is mailbox:
                # Пользователь уже открыл эту папку — показать обновлённый список.
                try:
                    self._render_folder(mailbox.folder_summaries(self.current_folder))
                except Exception:
                    pass

        worker.succeeded.connect(done)
        worker.failed.connect(lambda _e: done())
        self._background_workers.append(worker)
        worker.start()

    def _refresh_counts_async(self, folders: list[str], *, mailbox=None) -> None:
        """Счётчики непрочитанных в дереве для указанных папок — после
        удаления, переноса, отметки «прочитано» (жалоба: "после удаления
        число остаётся"). STATUS по интерактивному соединению, в фоне."""
        mailbox = self.mailbox if mailbox is None else mailbox
        key = self._mailbox_key(mailbox)
        folders = [f for f in dict.fromkeys(folders) if f]
        if not isinstance(mailbox, CachedMailbox) or key is None or not folders:
            return

        def fetch() -> dict[str, int]:
            counts: dict[str, int] = {}
            session = mailbox.interactive_session()
            for folder in folders:
                try:
                    counts[folder] = session.folder_unseen_count(folder)
                except Exception:
                    continue
            return counts

        worker = _CallableWorker(fetch, parent=self)

        def done(counts: object = None) -> None:
            if worker in self._background_workers:
                self._background_workers.remove(worker)
            if counts:
                self._apply_folder_unread_counts(key, counts, partial=True)

        worker.succeeded.connect(done)
        worker.failed.connect(lambda _e: done())
        self._background_workers.append(worker)
        worker.start()

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

    def _disconnect_account(self, key: str, *, forget: bool = False) -> None:
        # Раньше живую учётную запись (IMAP/EWS) нельзя было отключить
        # вообще, только закрыть всё приложение (жалоба: "нет возможности
        # отключить ящик") — по аналогии с "Закрыть архив" выше, но здесь
        # ещё нужно перевыбрать "текущую" запись, если отключаем именно её.
        # forget=True — пункт "Отключить ящик": запись убирается и из
        # сохранённых. Снятая в параметрах галочка вызывает этот же метод
        # без forget, и настройки записи остаются на месте.
        mailbox = self.mailboxes.pop(key, None)
        if mailbox is not None:
            try:
                mailbox.close()
            except Exception as exc:
                _log.warning("Отключение %s: соединение не закрылось: %s", key, exc)
        self.mailbox_accounts.pop(key, None)
        self.mailbox_smtp_accounts.pop(key, None)
        self.mailbox_protocols.pop(key, None)
        self.mailbox_folders.pop(key, None)
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

        if forget:
            self._save_all_accounts(forget=key)
        self.statusBar().showMessage("Ящик отключён" if forget else "Учётная запись отключена", 3000)

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
        moves = _mail_rule_moves(self.current_summaries, self.mail_rules)
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

        self._sync_folders_async(list(moves))
        try:
            summaries = self.mailbox.refresh_folder(source_folder)
        except Exception:
            summaries = None
        if summaries is not None:
            self._render_folder(summaries)
        self.statusBar().showMessage(f"По правилам перемещено писем: {moved_total}", 5000)

    def on_manage_signatures(self) -> None:
        dialog = SignaturesDialog(self, self.signatures, self.default_signature_id)
        dialog.exec()
        self.signatures = dialog.signatures()
        self.default_signature_id = dialog.default_signature_id()
        try:
            save_signatures(self.signatures)
            save_default_signature_id(self.default_signature_id)
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось сохранить подписи", str(exc))

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
        # Локальный импорт — та же причина, что у остальных ленивых
        # импортов в этом файле: main_window.py не должен тянуть за собой
        # __main__.py на верхнем уровне модуля (циклический импорт, ведь
        # именно __main__.py импортирует MainWindow при старте программы).
        from redmail.__main__ import app_version as get_app_version

        app_version = get_app_version()
        QMessageBox.about(
            self,
            "О программе",
            "<h3>RedMail</h3>"
            "<p>Почтовый клиент для RED OS — функциональный аналог Microsoft "
            "Outlook: почта по IMAP/SMTP, локальные архивы писем, календарь "
            "с приглашениями по электронной почте (iTIP), адресная книга.</p>"
            f"<p>Версия: {app_version}</p>"
            "<p>Автор: Пономарев Роман Сергеевич</p>",
        )

    def _ews_session_for_calendar(self):
        """Сессия Exchange для календаря: берём её у уже подключённой
        почтовой учётной записи Exchange (её же логин и способ входа)."""
        for key, protocol in self.mailbox_protocols.items():
            if protocol != "ews":
                continue
            mailbox = self.mailboxes.get(key)
            session = getattr(mailbox, "session", None)
            if session is not None:
                return session
        return None

    def _storage_stats(self) -> dict:
        try:
            from redmail import cache_store

            return cache_store.storage_stats()
        except Exception:
            return {}

    def _memory_report_text(self) -> str:
        """Расклад памяти: процесс, просмотр писем, куча, данные интерфейса,
        самые объёмные Python-объекты."""
        lines = memory_report.process_lines()
        lines.append("")
        lines.append(f"Писем в списке: {len(self.current_summaries)}, строк таблицы {self.table.rowCount()}, плиток {self.card_list.count()}")
        thread_bytes = sum(len(c.text or "") + len(c.html or "") for c in self._thread_content_cache.values())
        lines.append(f"Кэш цепочки: {len(self._thread_content_cache)} писем, {thread_bytes / (1024 * 1024):.1f} МБ текста")
        content = self.current_content
        if content is not None:
            attachments_bytes = sum(len(a.payload or b"") for a in (content.attachments or []))
            inline_bytes = sum(len(data) for _t, data in (content.inline_images or {}).values())
            lines.append(
                f"Открытое письмо: текст {(len(content.text or '') + len(content.html or '')) / (1024 * 1024):.1f} МБ, "
                f"вложения {attachments_bytes / (1024 * 1024):.1f} МБ, картинки {inline_bytes / (1024 * 1024):.1f} МБ"
            )
        cached_contacts = getattr(self, "_contacts_cache", None)
        if cached_contacts is not None:
            contacts = cached_contacts[1]
            photo_bytes = sum(len(c.photo or b"") for c in contacts)
            lines.append(f"Контакты в памяти: {len(contacts)}, фотографии {photo_bytes / (1024 * 1024):.1f} МБ")
        lines.append(f"Аватары в кэше: {len(_AVATAR_CACHE)}")
        for key, mailbox in self.mailboxes.items():
            session = getattr(mailbox, "session", None)
            id_map = getattr(session, "_id_map", None)
            extra = f", сопоставлений писем Exchange {len(id_map)}" if isinstance(id_map, dict) else ""
            lines.append(f"Учётная запись {key}{extra}")
        lines.append(
            f"Фоновых задач: {len(self._background_workers)}, окон писем открыто: {len(self._message_windows)}, "
            f"архивов открыто: {len(self.archives)}"
        )
        stats = self._storage_stats()
        if stats:
            lines.append(f"База почты на диске: {stats.get('db_bytes', 0) / (1024 * 1024):.0f} МБ")
        lines.append("")
        lines.append("Самые объёмные Python-объекты (тип, количество, МБ):")
        for name, count, megabytes in memory_report.python_objects():
            lines.append(f"  {name}: {count}, {megabytes:.1f} МБ")
        return "\n".join(lines)

    def on_show_memory(self) -> None:
        """Справка → «Расход памяти…»: расклад цифрами (жалоба: «наша
        программа жрёт много памяти, evolution в 3–5 раз меньше»). Текст
        можно скопировать, кнопка «Освободить» показывает, сколько памяти
        удерживалось без дела."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Расход памяти")
        dialog.resize(760, 640)
        text_edit = QPlainTextEdit(dialog)
        text_edit.setReadOnly(True)
        text_edit.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

        def refresh(title: str = "") -> None:
            report = self._memory_report_text()
            text_edit.setPlainText((title + "\n\n" if title else "") + report)
            _log.info("Расход памяти%s:\n%s", f" ({title})" if title else "", report)

        def free() -> None:
            before = memory_report.process_lines()[0]
            self._thread_content_cache.clear()
            _AVATAR_CACHE.clear()
            memory_report.free_memory()
            refresh(f"До освобождения: {before}")

        free_button = QPushButton("Освободить и пересчитать", dialog)
        free_button.clicked.connect(free)
        copy_button = QPushButton("Копировать", dialog)
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(text_edit.toPlainText()))
        close_button = QPushButton("Закрыть", dialog)
        close_button.clicked.connect(dialog.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(free_button)
        buttons.addWidget(copy_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout = QVBoxLayout(dialog)
        layout.addWidget(text_edit)
        layout.addLayout(buttons)
        refresh()
        dialog.exec()

    def on_show_log(self) -> None:
        """Справка → «Журнал подключений…» (пожелание: "писать лог
        подключений и синхронизаций, чтобы отладить подключения") —
        хвост файла журнала прямо в окне, чтобы можно было скопировать
        текст в обращение, плюс кнопка открыть каталог с файлами."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Журнал подключений")
        dialog.resize(900, 600)
        layout = QVBoxLayout(dialog)
        path_label = QLabel(f"Файл: {log_path()}")
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(path_label)
        text = QPlainTextEdit(dialog)
        text.setReadOnly(True)
        text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        mono = QFont("Monospace")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        text.setFont(mono)
        layout.addWidget(text, 1)

        def reload() -> None:
            content = tail_text()
            text.setPlainText(content or "Журнал пока пуст.")
            text.verticalScrollBar().setValue(text.verticalScrollBar().maximum())

        buttons = QHBoxLayout()
        refresh_button = QPushButton("Обновить")
        refresh_button.clicked.connect(reload)
        open_dir_button = QPushButton("Открыть папку")
        open_dir_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir()))))
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(dialog.accept)
        buttons.addWidget(refresh_button)
        buttons.addWidget(open_dir_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        reload()
        dialog.exec()

    def on_settings(self) -> None:
        known_accounts = self._known_accounts()
        dialog = SettingsDialog(
            self,
            poll_interval_minutes=self.poll_interval_minutes,
            pane_orientation=self.pane_orientation,
            archive_storage_dir=self.archive_storage_dir,
            theme=self.theme,
            profile_dir=load_profile_dir(),
            body_max_size_mb=load_body_max_size_mb(),
            auto_archive_size_mb=load_auto_archive_size_mb(),
            storage_stats=self._storage_stats(),
            auto_archive_enabled=load_auto_archive_enabled(),
            maintenance_window=load_maintenance_window(),
            others_reminder=load_others_reminder(),
            tls_ca_file=load_tls_ca_file(),
            accounts=known_accounts,
            disabled_accounts=tuple(load_disabled_accounts()),
            font_scale=load_font_scale(),
            greeting_mode=load_greeting_mode(),
            plugins_enabled={p.id: plugin_enabled(p.id, p.enabled_by_default) for p in mail_plugins.available_plugins()},
            category_store=self.category_store,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        old_profile_dir = load_profile_dir()

        self.poll_interval_minutes = dialog.poll_interval_minutes()
        self.pane_orientation = dialog.pane_orientation()
        self.archive_storage_dir = dialog.archive_storage_dir()
        new_theme = dialog.theme()
        theme_changed = new_theme != self.theme
        self.theme = new_theme
        try:
            save_poll_interval_minutes(self.poll_interval_minutes)
            save_pane_orientation(self.pane_orientation)
            save_archive_storage_dir(self.archive_storage_dir)
            save_theme(self.theme)
            save_body_max_size_mb(dialog.body_max_size_mb())
            save_auto_archive_size_mb(dialog.auto_archive_size_mb())
            save_auto_archive_enabled(dialog.auto_archive_enabled())
            save_maintenance_window(*dialog.maintenance_window())
            save_others_reminder(*dialog.others_reminder())
            save_tls_ca_file(dialog.tls_ca_file())
            tls_trust.apply_trust(dialog.tls_ca_file())
            self._apply_disabled_accounts(dialog.disabled_accounts())
            save_greeting_mode(dialog.greeting_mode())
            for plugin_id, enabled in dialog.plugins_enabled().items():
                save_plugin_enabled(plugin_id, enabled)
            self._apply_categories_module(dialog.plugins_enabled().get(mail_plugins.CATEGORIES.id, False))
            if abs(dialog.font_scale() - load_font_scale()) > 0.001:
                # Тот же путь, что у ползунка в строке состояния.
                self.font_scale_slider.setValue(int(round(dialog.font_scale() * 100)))
                self.on_font_scale_committed()
            for mailbox in self.mailboxes.values():
                if isinstance(mailbox, CachedMailbox):
                    mailbox.body_max_bytes = dialog.body_max_size_mb() * 1024 * 1024
            new_profile_dir = dialog.profile_dir()
            if new_profile_dir != old_profile_dir:
                save_profile_dir(None if new_profile_dir == profile.default_profile_dir() else new_profile_dir)
                QMessageBox.information(
                    self, "Каталог профиля",
                    f"Новый каталог профиля: {new_profile_dir}\n\nБазы почты, календаря и контактов будут "
                    "созданы там при следующем запуске; чтобы перенести данные, скопируйте файлы "
                    "mail.sqlite3, calendar.rmcal и contacts.rmcontacts из старого каталога.",
                )
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось сохранить параметры", str(exc))
        self._restart_poll_timer()
        self._apply_pane_orientation()
        if theme_changed:
            app = QApplication.instance()
            previous_family = app.font().family()
            app_theme.apply_theme(app, self.theme)
            # Гарнитура оформления — и уже открытым виджетам с шрифтом
            # приложения; размер пересчитывается от нового базового.
            new_family = app.font().family()
            if new_family != previous_family:
                for widget in app.allWidgets():
                    widget_font = widget.font()
                    if widget_font.family() == previous_family:
                        widget_font.setFamily(new_family)
                        widget.setFont(widget_font)
            self._base_font_point_size = app.font().pointSizeF() or self._base_font_point_size
            self._apply_font_scale(load_font_scale())
            # Ячейки месячного вида красят себя сами через inline
            # setStyleSheet() (см. MonthCellWidget._apply_style) — общий
            # QSS приложения их не перекрашивает, нужно попросить явно.
            self.calendar_month_grid.refresh_theme()

    def on_add_account(self) -> None:
        """Добавить ЕЩЁ одну учётную запись, не закрывая уже открытые
        (жалоба: "несколько учётных записей одновременно — сейчас клиент
        держит только одно подключение") — в отличие от "Параметры…",
        который правит текущую."""
        dialog = ImapAccountDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        new_account = dialog.account()
        if not new_account.host or not new_account.username:
            return None
        new_smtp = dialog.smtp_account()
        new_smtp = new_smtp if new_smtp.host else None
        key = account_key(new_account, "imap")
        if key in self.mailboxes:
            QMessageBox.information(
                self, "Уже подключено", f"Учётная запись {new_account.username} на {new_account.host} уже открыта."
            )
            return None

        try:
            session = ImapSession(new_account)
            folders = session.list_folders()
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка подключения", str(exc))
            return None
        self._add_or_replace_account(new_account, new_smtp, session, folders)
        self._save_all_accounts()
        self._save_account_options(key, dialog)
        self.statusBar().showMessage(f"Добавлена учётная запись: {new_account.username}", 5000)
        return key, self._account_title(new_account, "imap")

    def on_add_ews_account(self) -> None:  # noqa: D102 - см. тело
        """Подключение к Exchange напрямую по EWS — отдельный путь от
        обычного IMAP (кнопка выше): нужен, когда IMAP отключён политикой
        безопасности организации, или когда вход должен идти по Kerberos
        (SSO) без ввода пароля в самом приложении."""
        dialog = EwsAccountDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        new_account = dialog.account()
        if not new_account.email:
            return None
        key = account_key(new_account, "ews")
        if key in self.mailboxes:
            QMessageBox.information(
                self, "Уже подключено", f"Учётная запись Exchange {new_account.email} уже открыта."
            )
            return None
        try:
            session = EwsSession(new_account)
            folders = session.list_folders()
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось подключиться к Exchange", str(exc))
            return None
        self._add_or_replace_ews_account(new_account, session, folders)
        self._save_all_accounts()
        self._save_account_options(key, dialog)
        self.statusBar().showMessage(f"Exchange подключён: {new_account.email}", 5000)
        return key, self._account_title(new_account, "ews")

    def _saved_account(self, key: str):
        """(протокол, учётная запись, SMTP) по ключу — из открытых или
        сохранённых (выключенная галочкой запись не открыта)."""
        if key in self.mailbox_accounts:
            return self.mailbox_protocols.get(key, "imap"), self.mailbox_accounts[key], self.mailbox_smtp_accounts.get(key)
        try:
            for account, smtp in load_accounts():
                if account_key(account, "imap") == key:
                    return "imap", account, smtp
            for account in load_ews_accounts():
                if account_key(account, "ews") == key:
                    return "ews", account, None
        except Exception as exc:
            _log.warning("Сохранённые учётные записи не прочитаны: %s", exc)
        return None

    def _save_account_options(self, key: str, dialog, *, previous_key: str | None = None) -> None:
        known = [known_key for known_key, _title in self._known_accounts()]
        try:
            set_delete_on_server(key, dialog.delete_on_server(), known)
            set_domain_rewrites(key, dialog.domain_rewrites(), known, forget=previous_key if previous_key != key else None)
        except Exception as exc:
            QMessageBox.warning(self, "Настройки учётной записи", f"Не удалось сохранить: {exc}")

    def on_edit_account(self, key: str):
        """Окно одной учётной записи: подключение, удаление с сервера, замена
        доменов. Возвращает (ключ, подпись) — ключ меняется, если сменили
        сервер или логин — или None, если ничего не сохранено."""
        found = self._saved_account(key)
        if found is None:
            QMessageBox.information(self, "Учётная запись", "Настройки этой записи не найдены — добавьте её заново.")
            return None
        protocol, account, smtp = found
        options = {"delete_on_server": delete_on_server_for(key), "domain_rewrites": load_domain_rewrites(key)}
        if protocol == "ews":
            dialog = EwsAccountDialog(self, account=account, **options)
        else:
            dialog = ImapAccountDialog(self, account=account, smtp=smtp if smtp is not account else None, **options)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        new_account = dialog.account()
        new_smtp = None
        if protocol == "ews":
            if not new_account.email:
                return None
            changed = new_account != account
        else:
            if not new_account.host or not new_account.username:
                return None
            new_smtp = dialog.smtp_account()
            new_smtp = new_smtp if new_smtp.host else None
            changed = new_account != account or new_smtp != smtp
        new_key = account_key(new_account, protocol)
        if changed:
            if key in self.mailboxes:
                try:
                    session = EwsSession(new_account) if protocol == "ews" else ImapSession(new_account)
                    folders = session.list_folders()
                except Exception as exc:
                    QMessageBox.critical(self, "Ошибка подключения", str(exc))
                    return None
                if new_key != key:
                    self._disconnect_account(key)
                if protocol == "ews":
                    self._add_or_replace_ews_account(new_account, session, folders)
                else:
                    self._add_or_replace_account(new_account, new_smtp, session, folders)
                self._save_all_accounts(forget=key if new_key != key else None)
            else:
                # Запись выключена — только сохраняем, подключится при включении.
                try:
                    if protocol == "ews":
                        merge_ews_accounts([new_account], forget=key if new_key != key else None)
                    else:
                        merge_accounts([(new_account, new_smtp)], forget=key if new_key != key else None)
                except Exception as exc:
                    QMessageBox.warning(self, "Учётная запись", f"Не удалось сохранить: {exc}")
                    return None
        self._save_account_options(new_key, dialog, previous_key=key)
        self.statusBar().showMessage("Настройки учётной записи сохранены", 5000)
        return new_key, self._account_title(new_account, protocol)

    def _known_accounts(self) -> list[tuple[str, str]]:
        """(ключ, подпись) для списка в настройках: и подключённые сейчас,
        и выключенные (они в программе не открыты, но настройки есть)."""
        known: dict[str, str] = {}
        for key in self.mailboxes:
            account = self.mailbox_accounts.get(key)
            protocol = self.mailbox_protocols.get(key, "imap")
            if account is None:
                continue
            known[key] = self._account_title(account, protocol)
        try:
            for account, _smtp in load_accounts():
                known.setdefault(account_key(account, "imap"), self._account_title(account, "imap"))
            for account in load_ews_accounts():
                known.setdefault(account_key(account, "ews"), self._account_title(account, "ews"))
        except Exception as exc:
            _log.warning("Список учётных записей: %s", exc)
        return sorted(known.items(), key=lambda item: item[1].lower())

    @staticmethod
    def _account_title(account, protocol: str) -> str:
        if protocol == "ews":
            server = getattr(account, "server", "") or "автопоиск"
            return f"Exchange: {account.email} ({server})"
        return f"IMAP: {account.username} ({account.host})"

    def _apply_disabled_accounts(self, disabled: list[str]) -> None:
        """Сохранить выключатели и сразу отключить те записи, которые
        выключили (включение требует перезапуска — подключение к серверу
        идёт через ту же очередь, что и при старте)."""
        previous = set(load_disabled_accounts())
        disabled_set = set(disabled)
        if previous == disabled_set:
            return
        try:
            save_disabled_accounts(disabled)
        except Exception as exc:
            QMessageBox.warning(self, "Учётные записи", f"Не удалось сохранить список: {exc}")
            return
        for key in disabled_set - previous:
            if key in self.mailboxes:
                self._disconnect_account(key)
        enabled_again = previous - disabled_set
        if enabled_again:
            self._connect_enabled_accounts(enabled_again)

    def _connect_enabled_accounts(self, keys: set[str]) -> None:
        """Подключает записи, которым вернули галочку, не дожидаясь
        перезапуска (во время перехода с одной почтовой системы на другую
        записи переключают туда-обратно, и перезапуск ради этого неудобен)."""
        try:
            saved_imap = load_accounts()
            saved_ews = load_ews_accounts()
        except Exception as exc:
            QMessageBox.warning(self, "Учётные записи", f"Не удалось прочитать сохранённые записи: {exc}")
            return
        queue: list[tuple[str, object, object]] = [
            ("imap", account, smtp)
            for account, smtp in saved_imap
            if account_key(account, "imap") in keys and account_key(account, "imap") not in self.mailboxes
        ]
        queue += [
            ("ews", account, None)
            for account in saved_ews
            if account_key(account, "ews") in keys and account_key(account, "ews") not in self.mailboxes
        ]
        if not queue:
            QMessageBox.information(
                self, "Учётные записи",
                "Настроек включённой записи не нашлось — добавьте её заново через «Добавить учётную запись».",
            )
            return
        self._connect_accounts_async(queue)

    def _save_all_accounts(self, *, forget: str | None = None) -> None:
        """Записи, выключенные галочкой, в программе не открыты, а раньше
        сохранялись только открытые — следующее сохранение стирало
        выключенную запись из файла, и в списке параметров её уже не было
        (жалоба: "чтобы поставить галочку, она должна быть в списке — её
        нет"). Теперь открытые записи дописываются к сохранённым, а
        убирается только та, которую отключили явно."""
        try:
            merge_accounts(
                [
                    (self.mailbox_accounts[key], self.mailbox_smtp_accounts[key])
                    for key in self.mailboxes
                    if self.mailbox_protocols[key] == "imap"
                ],
                forget=forget,
            )
            merge_ews_accounts(
                [self.mailbox_accounts[key] for key in self.mailboxes if self.mailbox_protocols[key] == "ews"],
                forget=forget,
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
        # Смена ориентации панели чтения меняет доступную под таблицу
        # ширину (горизонтально — делит её с панелью письма, вертикально —
        # нет), но не размер самого окна — resizeEvent на это не сработает.
        self._schedule_stretch_date_column()

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
        new_size = self._base_font_point_size * scale
        font = app.font()
        font.setPointSizeF(new_size)
        app.setFont(font)
        # Жалоба: "надо менять весь шрифт в окне, раньше работало" —
        # QApplication.setFont() каскадируется автоматически только на
        # виджеты БЕЗ собственного явно установленного шрифта; часть
        # виджетов в этом приложении такой явный шрифт получила (кнопки
        # Ж/К/Ч и т.п.) либо уже отрисована и не переполисовывается сама
        # по себе — они просто не подхватывали смену размера. Проходим по
        # всем СУЩЕСТВУЮЩИМ виджетам явно (меняя только размер, а не
        # гарнитуру/жирность/курсив — они у каждого виджета свои) — так
        # масштаб гарантированно применяется везде в уже открытом окне, а
        # не только к тому, что будет создано позже.
        for widget in app.allWidgets():
            widget_font = widget.font()
            if widget_font.pointSizeF() > 0:
                widget_font.setPointSizeF(new_size)
                widget.setFont(widget_font)
        # Тело письма рендерится в QWebEngineView (Chromium), который не
        # видит QApplication.font() вообще — без этого ползунок в статус-баре
        # менял размер шрифта везде, КРОМЕ самого письма (см. _create_mail_browser).
        reading_pane = getattr(self, "reading_pane", None)
        if reading_pane is not None:
            reading_pane.setZoomFactor(scale)

    def _set_filter_column(self, column: int) -> None:
        if column == COL_CHECK:
            self._toggle_check_all()
            return
        if column == COL_FLAG:
            self._open_marker_filter_menu()
            return
        if column not in _FILTER_COLUMNS:
            return
        self.filter_column = column
        self.filter_edit.setPlaceholderText(f"Фильтр: {_FILTER_COLUMNS[column]}")
        self.on_filter_changed(self.filter_edit.text())
        self._remember_view_state()

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
        self._remember_view_state()

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
        self._switch_view_state(source_key)
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
        if getattr(source, "is_folder_synced", None) is not None:
            # Открыли папку — тихо сверяемся с сервером в фоне (окно не
            # ждёт): раньше уже синхронизированная папка показывалась
            # только из кэша, и письма, перенесённые другим действием или
            # другим клиентом, появлялись лишь при следующем полном
            # проходе (раз в полчаса).
            self._refresh_folder_async(silent=True)

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
            self._disconnect_account(account_key, forget=True)
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

    def _set_busy(self, text: str | None) -> None:
        if text:
            self.busy_label.setText(text)
            self.busy_label.show()
            self.busy_bar.show()
        else:
            self.busy_label.hide()
            self.busy_bar.hide()

    def on_refresh(self) -> None:
        self._refresh_folder_async(silent=False)

    def _refresh_folder_async(self, *, silent: bool) -> None:
        """Обновление текущей папки с сервера в фоне (жалоба: "ощущение
        подвисания… нет информирования о синхронизации") — раньше
        refresh_folder шёл в потоке интерфейса и на медленной сети
        замораживал окно. Пока идёт — индикатор в строке состояния; второй
        запуск поверх первого не стартует."""
        source = self.active_source
        folder = self.current_folder
        if not source or not folder or self._refresh_in_progress:
            return
        self._refresh_in_progress = True
        self._set_busy(f"Синхронизация: {folder}…")
        worker = _CallableWorker(source.refresh_folder, folder, parent=self)

        def finish() -> None:
            self._refresh_in_progress = False
            self._set_busy(None)
            if worker in self._background_workers:
                self._background_workers.remove(worker)

        def on_success(summaries: object) -> None:
            finish()
            if source is self.mailbox:
                self._refresh_counts_async([folder])
            if source is not self.active_source or folder != self.current_folder:
                return  # пользователь уже переключил папку — не подменяем список
            self._render_folder(summaries)
            self.statusBar().showMessage(f"Обновлено: {folder}", 3000)

        def on_failure(error_text: str) -> None:
            finish()
            if not silent:
                QMessageBox.critical(self, "Ошибка обновления", error_text)

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._background_workers.append(worker)
        worker.start()

    def _start_full_sync(self, bodies_limit: int | None = None) -> None:
        """Полная синхронизация всех подключённых ящиков по очереди (аналог
        офлайн-копии OST): заголовки всех папок, затем тела от новых к
        старым. Идёт в фоне порциями, индикатор — в строке состояния."""
        if self._sync_worker is not None or self._autoarchive_active:
            return
        self._sync_queue = [(key, True) for key in self.mailboxes if self.mailbox_protocols.get(key) in ("imap", "ews")]
        self._sync_stop.clear()
        self._sync_next(bodies_limit)

    def _sync_next(self, bodies_limit: int | None = None) -> None:
        if not self._sync_queue or self._sync_stop.is_set():
            self._sync_worker = None
            self._set_busy(None)
            return
        key, headers = self._sync_queue.pop(0)
        mailbox = self.mailboxes.get(key)
        # Папки подписанных ящиков коллег входят в офлайн-копию (и, значит,
        # в автоархив) только если это включено в учётной записи: чужой ящик
        # может быть огромным, а нужен обычно на просмотр — тогда его
        # содержимое запрашивается с сервера при открытии папки.
        account = self.mailbox_accounts.get(key)
        excluded = set(excluded_shared_folders(
            self.mailbox_folders.get(key, []),
            bool(getattr(account, "shared_offline", False)),
            tuple(getattr(account, "shared_mailboxes", ()) or ()),
        ))
        folders = [name for name in self.mailbox_folders.get(key, []) if name not in excluded]
        if mailbox is None or not folders:
            self._sync_next(bodies_limit)
            return
        # Пока база больше порога автоархива, тела фоном не качаем: иначе
        # архиватор и докачка соревнуются за одно IMAP-соединение, а база
        # растёт быстрее, чем освобождается (жалоба: "база не уменьшается").
        effective_limit = bodies_limit
        if not in_maintenance_window():
            effective_limit = 0  # вне часов обслуживания — только заголовки и флаги
        if load_auto_archive_enabled() and isinstance(mailbox, CachedMailbox):
            try:
                db_bytes = self._storage_stats().get("db_bytes", 0)
            except Exception:
                db_bytes = 0
            if db_bytes > load_auto_archive_size_mb() * 1024 * 1024:
                effective_limit = 0
        worker = _SyncWorker(mailbox, folders, self._sync_stop, bodies_limit=effective_limit, headers=headers, parent=self)
        self._sync_worker = worker

        def on_progress(text: str) -> None:
            self._set_busy(f"{key}: {text}")

        def finish() -> None:
            if worker in self._background_workers:
                self._background_workers.remove(worker)
            self._sync_worker = None

        def on_done(stats: object) -> None:
            finish()
            _log.info("Полная синхронизация %s: новых %d, удалено %d, тел скачано %d", key, stats.added, stats.deleted, stats.bodies_downloaded)
            if self._calendar_changed_in_background:
                self._calendar_changed_in_background = False
                self.refresh_calendar_view()
            if self.active_source is mailbox and self.current_folder:
                try:
                    self._render_folder(mailbox.folder_summaries(self.current_folder))
                except Exception:
                    pass
            def continue_sync() -> None:
                # Полный режим: пока есть нескачанные тела — ещё раунд по
                # этому же ящику (без повторного обхода заголовков), после
                # каждого раунда — проверка автоархива. В периодическом
                # режиме (bodies_limit задан) — один раунд за тик.
                #
                # Следующий раунд ставится, ТОЛЬКО если предыдущий что-то
                # скачал. Иначе раунд ничего не делает (вне часов
                # обслуживания тела не качаются, и сервер, попросивший
                # подождать, тоже ничего не отдаёт), а нескачанные тела
                # остаются — и раунды шли один за другим без остановки: в
                # журнале пользователя 1260 полных синхронизаций за 40
                # секунд, процессор занят на 100%, почта при этом не
                # появлялась (жалоба: "потом всё зависало с загрузкой
                # процессора до 120%").
                if _needs_another_bodies_round(stats, bodies_limit) and not self._sync_stop.is_set():
                    self._sync_queue.append((key, False))
                elif self._autoarchive_pending(key) and not self._sync_stop.is_set():
                    # база всё ещё больше порога — следующий раунд автоархива
                    # (ставится через тот же цикл, тела при этом не качаются)
                    self._sync_queue.append((key, False))
                self._sync_next(bodies_limit)

            self._maybe_autoarchive(key, continue_sync)

        def on_failed(error_text: str) -> None:
            finish()
            _log.error("Полная синхронизация %s не удалась: %s", key, error_text)
            self._sync_next(bodies_limit)

        worker.progress.connect(on_progress)
        worker.finished_ok.connect(on_done)
        worker.failed.connect(on_failed)
        self._background_workers.append(worker)
        self._set_busy(f"{key}: синхронизация…")
        worker.start()

    def _autoarchive_pending(self, key: str) -> bool:
        mailbox = self.mailboxes.get(key)
        if mailbox is None or not isinstance(mailbox, CachedMailbox) or not load_auto_archive_enabled():
            return False
        if key in self._autoarchive_exhausted:
            return False  # прошлый план был пуст (нечего архивировать) — не крутиться вхолостую
        try:
            return self._storage_stats().get("db_bytes", 0) > load_auto_archive_size_mb() * 1024 * 1024
        except Exception:
            return False

    def _maybe_autoarchive(self, key: str, then: Callable[[], None]) -> None:
        """Автоархив по размеру базы (договорённость: порог 500 МБ, самые
        старые письма — в файл архива, затем удаление с сервера). Первый
        запуск для учётной записи — с подтверждением: операция необратима
        на сервере."""
        mailbox = self.mailboxes.get(key)
        if mailbox is None or not isinstance(mailbox, CachedMailbox) or not load_auto_archive_enabled():
            then()
            return
        if self._autoarchive_active:
            then()  # один архиватор за раз — иначе два потока переносят одни и те же письма
            return
        if not in_maintenance_window():
            then()  # часы обслуживания не наступили
            return
        try:
            # Файлы первых сборок лежали в «Каталоге для новых архивов» — забрать в профиль.
            autoarchive.relocate_archives(Path(self.archive_storage_dir), profile.archives_dir())
        except Exception as exc:
            _log.warning("Автоархив: перенос файлов в профиль не удался: %s", exc)
        threshold = load_auto_archive_size_mb() * 1024 * 1024
        skip = {name for name in (self.mailbox_trash_folders.get(key), self.mailbox_drafts_folders.get(key)) if name}
        # Ящики коллег без офлайн-копии в архив не попадают: архив — это
        # своя почта, за чужую он расти не должен.
        archive_account = self.mailbox_accounts.get(key)
        skip |= set(excluded_shared_folders(
            self.mailbox_folders.get(key, []),
            bool(getattr(archive_account, "shared_offline", False)),
            tuple(getattr(archive_account, "shared_mailboxes", ()) or ()),
        ))
        try:
            plan = autoarchive.make_plan(mailbox.account_key, threshold, skip_folders=skip)
        except Exception as exc:
            _log.error("Автоархив %s: не удалось составить план: %s", key, exc)
            then()
            return
        if not plan.candidates:
            if plan.to_free_bytes > 0:
                self._autoarchive_exhausted.add(key)
            then()
            return
        self._autoarchive_exhausted.discard(key)
        confirmed = load_auto_archive_confirmed()
        delete_on_server = delete_on_server_for(key)
        if delete_on_server and key not in confirmed:
            # Вопрос только когда включено удаление с сервера: это
            # необратимо. В режиме по умолчанию (сервер не трогаем)
            # спрашивать не о чем (пользователь: "зачем спрашивать?").
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("Автоархив")
            box.setText(
                f"База почты {plan.db_bytes / (1024 * 1024):.0f} МБ превысила порог "
                f"{plan.threshold_bytes / (1024 * 1024):.0f} МБ.\n\n"
                f"Перенести {plan.count} самых старых писем ({plan.total_bytes / (1024 * 1024):.0f} МБ, "
                f"{plan.oldest_date[:10]} — {plan.newest_date[:10]}) в файл архива в каталоге\n{profile.archives_dir()}\n"
                "и удалить их с сервера? Письма останутся в списке и будут читаться из архива.\n\n"
                "Больше этот вопрос для этой учётной записи задаваться не будет; отключить автоархив можно в Параметрах."
            )
            yes = box.addButton("Да, перенести и удалить", QMessageBox.ButtonRole.YesRole)
            box.addButton("Нет", QMessageBox.ButtonRole.NoRole)
            box.exec()
            if box.clickedButton() is not yes:
                _log.info("Автоархив %s: пользователь отказался (%d писем)", key, plan.count)
                then()
                return
            save_auto_archive_confirmed([*confirmed, key])
        worker = _CallableWorker(
            autoarchive.run, mailbox, plan, profile.archives_dir(),
            stop=self._sync_stop, delete_on_server=delete_on_server,
            full_vacuum=load_maintenance_window()[0], parent=self,
        )

        def done(_result: object = None) -> None:
            self._autoarchive_active = False
            memory_report.free_memory()  # через автоархив проходят сотни мегабайт писем
            if worker in self._background_workers:
                self._background_workers.remove(worker)
            self._set_busy(None)
            if self.active_source is mailbox and self.current_folder:
                try:
                    self._render_folder(mailbox.folder_summaries(self.current_folder))
                except Exception:
                    pass
            then()

        def on_success(result: object) -> None:
            self.statusBar().showMessage(
                f"Автоархив: перенесено {result.archived} писем, освобождено {result.bytes_freed / (1024 * 1024):.0f} МБ", 8000
            )
            done()

        def on_failure(error_text: str) -> None:
            _log.error("Автоархив %s: %s", key, error_text)
            done()

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._background_workers.append(worker)
        self._autoarchive_active = True
        self._set_busy(f"{key}: автоархив, раунд {plan.count} писем ({plan.db_bytes / (1024 * 1024):.0f} МБ → цель {plan.threshold_bytes * 0.8 / (1024 * 1024):.0f} МБ)…")
        worker.start()

    def _refresh_inbox_async(self) -> None:
        """Тихая проверка «Входящих» по таймеру, когда открыта другая папка:
        заголовки и счётчик непрочитанных в дереве."""
        mailbox = self.mailbox
        if not isinstance(mailbox, CachedMailbox) or getattr(self, "_inbox_refresh_in_progress", False):
            return
        key = next((k for k, m in self.mailboxes.items() if m is mailbox), None)
        if key is None:
            return
        self._inbox_refresh_in_progress = True

        def refresh() -> dict[str, int]:
            mailbox.refresh_folder("INBOX")
            try:
                return {"INBOX": mailbox.interactive_session().folder_unseen_count("INBOX")}
            except Exception:
                return {}

        worker = _CallableWorker(refresh, parent=self)

        def finish() -> None:
            self._inbox_refresh_in_progress = False
            if worker in self._background_workers:
                self._background_workers.remove(worker)

        def on_success(counts: object) -> None:
            finish()
            if counts:
                self._apply_folder_unread_counts(key, counts, partial=True)

        def on_failure(_error_text: str) -> None:
            finish()

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._background_workers.append(worker)
        worker.start()

    def _on_periodic_refresh(self) -> None:
        # Тихая фоновая проверка по таймеру — без модальных окон об ошибках,
        # чтобы не перебивать пользователя, если тот занят (например, пишет письмо).
        # Архивы локальны и статичны — опрашивать их по таймеру незачем.
        self._calendar_ticks = getattr(self, "_calendar_ticks", 0) + 1
        if self._calendar_ticks % 3 == 0:
            # Календари — раз в три опроса и независимо от того, какая папка
            # открыта (жалоба: "нет синхронизации с календарём" — раньше
            # только по кнопке).
            self.on_caldav_sync(silent=True)
        if self.active_source is not self.mailbox or not self.mailbox or not self.current_folder:
            return
        self._refresh_folder_async(silent=True)
        # Каждый опрос — «Входящие» (ради новых писем) и текущая папка.
        # Полный проход по всем папкам и докачка тел — раз в шесть опросов
        # (полчаса при 5 минутах), и только если предыдущий уже закончился
        # (пользователь: "опрос раз в 5 минут нужен для входящих; не нужно
        # делать постоянную синхронизацию").
        if self.current_folder != "INBOX":
            self._refresh_inbox_async()
        self._periodic_ticks += 1
        if self._periodic_ticks % 6 == 0 and self._sync_worker is None:
            QTimer.singleShot(2000, lambda: self._start_full_sync(bodies_limit=None))

    def _clear_reading_pane(self) -> None:
        _render_mail_html(self.reading_pane, _BODY_WRAP_TEMPLATE.format(content=""))
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

    def _row_search_text(self, row: int) -> str:
        """По чему ищем в строке. В плитке столбцов не видно и выбрать
        столбец фильтра негде (жалоба: «в режиме плитки поиск только по
        теме») — там ищем по тому, что на самой плитке и написано: от кого
        и тема. В таблице — по выбранному столбцу, как раньше."""
        if self.mail_view_mode == "cards":
            parts = [self.table.item(row, column) for column in (COL_SENDER, COL_SUBJECT)]
            return " ".join(item.text() for item in parts if item is not None).lower()
        item = self.table.item(row, self.filter_column)
        return item.text().lower() if item is not None else ""

    def _rows_match(self, needle: str) -> bool:
        needle = needle.strip().lower()
        if not needle:
            return True
        return any(needle in self._row_search_text(row) for row in range(self.table.rowCount()))

    def on_filter_changed(self, text: str) -> None:
        # Набрали не в той раскладке — исправляем строку поиска, если в
        # другой раскладке письма находятся.
        text = fix_search_layout(self.filter_edit, self._rows_match)
        needle = text.strip().lower()
        for row in range(self.table.rowCount()):
            visible = True
            if needle:
                visible = needle in self._row_search_text(row)
            summary = self._summary_for_row(row)
            if visible and summary is not None:
                visible = self._summary_passes_filters(summary) and not self._thread_hidden(summary, needle)
            self.table.setRowHidden(row, not visible)
            if summary is not None:
                card = self._card_items_by_uid.get(summary.uid)
                if card is not None:
                    card.setHidden(not visible)

    def _summary_passes_filters(self, summary: MessageSummary) -> bool:
        """Фильтры "Важные" / "С вложениями" / маркер (пожелание: "нужна
        фильтрация по важности, наличию вложений, маркеру"). Маркеров на
        письме может быть несколько — фильтр по цвету означает "среди них
        есть этот цвет"."""
        if self.filter_important and summary.importance != "high":
            return False
        if self.filter_attachments and not summary.has_attachments:
            return False
        if self.category_filter is not None:
            label = self._category_labels.get(summary.uid)
            category_id = label.category_id if label is not None else None
            if (category_id or "") != self.category_filter:
                return False
        if self.marker_filter is not None:
            markers = split_markers(summary.marker_color)
            if self.marker_filter == _ANY_MARKER_FILTER:
                return bool(markers)
            return self.marker_filter in markers
        return True

    def _render_folder(self, summaries: list[MessageSummary]) -> None:
        previously_selected_uid = self.selected_summary.uid if self.selected_summary else None

        if len(summaries) > MAX_LIST_ROWS:
            # Полная локальная копия может содержать десятки тысяч писем —
            # таблица показывает последние MAX_LIST_ROWS (список отсортирован
            # новые сверху), остальные доступны через поиск/архив.
            self.statusBar().showMessage(f"Показаны последние {MAX_LIST_ROWS} из {len(summaries)} писем", 6000)
            summaries = summaries[:MAX_LIST_ROWS]
        self.current_summaries = summaries
        self.summaries_by_uid = {s.uid: s for s in summaries}

        # Сортировку на время заполнения отключаем: иначе Qt переставляет
        # строки после каждого setItem(), и индекс row перестаёт совпадать
        # с тем, что мы только что туда положили.
        self.table.setSortingEnabled(False)
        # Выделение в Qt индексное, а не по содержимому — просто заменить
        # содержимое строк через setItem() недостаточно: если, скажем,
        # строка 0 была выделена в предыдущей папке, она остаётся "выделена"
        # и после того, как в неё легли данные СОВСЕМ другого письма из
        # новой папки, потому что сам индекс (0) не поменялся. При этом
        # itemSelectionChanged не срабатывает (набор выделенных индексов не
        # изменился), и панель чтения так и показывает старое/пустое
        # содержимое, хотя строка визуально выглядит выбранной (жалоба:
        # "при переключении между папками выделенное письмо не
        # открывается"). Ниже уже есть код, который заново выделяет нужную
        # строку по uid, если он есть в новой папке — например, при простом
        # обновлении текущей папки.
        self.table.clearSelection()
        self.table.setRowCount(len(summaries))
        # В "Отправленных" "От кого" — всегда сам пользователь, бесполезная
        # колонка; вместо неё показываем "Кому" (жалоба: "в отправленных
        # нет поля адресат, невозможно понять кому писали, а кому нет").
        is_sent_folder = self.current_folder is not None and self.current_folder == self.sent_folder_name
        sender_header_item = self.table.horizontalHeaderItem(COL_SENDER)
        if sender_header_item is not None:
            sender_header_item.setText("Кому" if is_sent_folder else "От кого")
        # Зависание на полной локальной копии (py-spy на .80: главный поток
        # минутами сидел в setItem): у колонок галочки/маркера/важности/
        # скрепки стоит ResizeToContents, и Qt пересчитывал ширину колонки
        # по ВСЕМ строкам на каждую вставленную ячейку — квадратично от
        # числа писем, а строк теперь до 3000. На время заполнения эти
        # колонки делаются фиксированными, перерисовка и сигналы заголовка
        # выключаются; пересчёт — один раз в конце.
        header = self.table.horizontalHeader()
        auto_columns = [
            col for col in range(self.table.columnCount())
            if header.sectionResizeMode(col) == QHeaderView.ResizeMode.ResizeToContents
        ]
        for col in auto_columns:
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        header.blockSignals(True)
        self.table.setUpdatesEnabled(False)
        self._thread_info = _thread_infos(summaries) if self.thread_grouping else {}
        self.card_delegate.expanded_keys = self._expanded_threads
        try:
            self._fill_message_rows(summaries, is_sent_folder)
        finally:
            self.table.setUpdatesEnabled(True)
            header.blockSignals(False)
            for col in auto_columns:
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        if header.sortIndicatorSection() not in _SORTABLE_COLUMNS:
            # Без явной сортировки строки остаются в порядке базы и письма
            # цепочки разбросаны по списку — по умолчанию дата, новые сверху.
            header.setSortIndicator(COL_DATE, Qt.SortOrder.DescendingOrder)
        self.table.setSortingEnabled(True)
        self._populate_cards(summaries, is_sent_folder)

        self.statusBar().showMessage(f"{self.current_folder}: писем {len(summaries)}", 5000)
        self.on_filter_changed(self.filter_edit.text())

        if previously_selected_uid is not None:
            row = self._row_for_uid(previously_selected_uid)
            if row is not None:
                self.table.selectRow(row)
        self._categorize_folder_async(summaries)

    def _fill_message_rows(self, summaries: list[MessageSummary], is_sent_folder: bool) -> None:
        for row, summary in enumerate(summaries):
            check_item = QTableWidgetItem()
            # ItemIsSelectable — без него Qt при selectRow()/выборе строки
            # мышью не включает эту ячейку в выделение (QItemSelectionModel
            # пропускает несовместимые с выделением индексы), и в столбце
            # чекбокса оставался видимый "провал" на фоне подсвеченной
            # остальной строки. Жалоба: "выбор письма в тёмной теме опять же
            # невиден... но зато выделяется часть поля" — именно этот провал
            # и был той "невыделенной частью".
            check_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            check_item.setCheckState(Qt.CheckState.Unchecked)
            check_item.setData(Qt.ItemDataRole.UserRole, summary.uid)
            self.table.setItem(row, COL_CHECK, check_item)

            info = self._thread_info.get(summary.uid)
            head = self.summaries_by_uid.get(info.head_uid, summary) if info is not None else summary
            group_key = info.key if info is not None else ""
            rank = 0 if info is None or info.is_head else 1

            def thread_item(text: str, head_value: str, own: str | None = None) -> _ThreadSortItem:
                return _ThreadSortItem(
                    text, group_value=head_value, group_key=group_key, rank=rank,
                    own=text if own is None else own, date=summary.date,
                )

            flag_item = self._readonly_item(thread_item("", head.marker_color or "", summary.marker_color or ""))
            if summary.marker_color:
                flag_item.setIcon(_markers_icon(summary.marker_color))
            self.table.setItem(row, COL_FLAG, flag_item)

            self.table.setItem(
                row, COL_IMPORTANCE,
                self._readonly_item(thread_item(_importance_mark(summary.importance), _importance_mark(head.importance))),
            )
            self.table.setItem(
                row, COL_ATTACHMENT,
                self._readonly_item(thread_item(
                    _ATTACHMENT_MARK if summary.has_attachments else "", _ATTACHMENT_MARK if head.has_attachments else "",
                )),
            )
            sender_text = summary.to if is_sent_folder else summary.sender
            head_sender = head.to if is_sent_folder else head.sender
            sender_item = thread_item(sender_text, (head_sender or "").casefold(), (sender_text or "").casefold())
            subject_item = thread_item(
                _thread_subject_text(summary, info, group_key in self._expanded_threads),
                group_key or _normalize_subject(summary.subject or "").casefold(), (summary.subject or "").casefold(),
            )
            if not summary.is_read:
                # Непрочитанное — жирным, как в любом другом почтовом клиенте.
                bold_font = sender_item.font()
                bold_font.setBold(True)
                sender_item.setFont(bold_font)
                subject_item.setFont(bold_font)
            self.table.setItem(row, COL_SENDER, sender_item)
            self.table.setItem(row, COL_SUBJECT, subject_item)
            self.table.setItem(row, COL_DATE, thread_item(summary.date, head.date))
            category_name, head_category = self._category_name(summary.uid), self._category_name(head.uid)
            category_item = thread_item(category_name, _category_sort_key(head_category), _category_sort_key(category_name))
            self._paint_category_item(category_item, summary.uid)
            self.table.setItem(row, COL_CATEGORY, category_item)

    @staticmethod
    def _readonly_item(text: str | QTableWidgetItem) -> QTableWidgetItem:
        item = QTableWidgetItem(text) if isinstance(text, str) else text
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return item

    def _summary_for_row(self, row: int) -> MessageSummary | None:
        uid = self.table.item(row, COL_CHECK).data(Qt.ItemDataRole.UserRole)
        return self.summaries_by_uid.get(uid)

    def _row_for_uid(self, uid: int) -> int | None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_CHECK)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == uid:
                return row
        return None

    # ---- Плитки (второй режим списка писем) -------------------------------
    # Договорённость: два режима отображения — таблица (как раньше) и
    # плитки по дизайн-референсу (аватар с инициалами, две строки, дата и
    # признаки справа). Таблица остаётся источником правды (чекбоксы для
    # массовых действий, сортировка, восстановление выделения по uid) — и в
    # режиме плиток она просто скрыта; плитки показывают те же summaries и
    # транслируют выбор/галочки/двойной клик/контекстное меню в таблицу по
    # uid, так что вся остальная логика (чтение письма, удаление, маркеры)
    # не дублируется.

    def _populate_cards(self, _summaries: list[MessageSummary], is_sent_folder: bool) -> None:
        self.card_delegate.sent_mode = is_sent_folder
        self._reorder_cards_from_table()

    def _reorder_cards_from_table(self) -> None:
        """Перестроить плитки в порядке строк таблицы (после сортировки по
        заголовку или из меню «Сортировка»); галочки и скрытие фильтром
        берутся из таблицы, текущее письмо сохраняется."""
        current_uid = self.selected_summary.uid if self.selected_summary is not None else None
        self.card_list.blockSignals(True)
        self._syncing_card_selection = True
        try:
            self.card_list.clear()
            self._card_items_by_uid = {}
            for row in range(self.table.rowCount()):
                check_item = self.table.item(row, COL_CHECK)
                if check_item is None:
                    continue
                uid = check_item.data(Qt.ItemDataRole.UserRole)
                item = QListWidgetItem()
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(check_item.checkState())
                item.setData(Qt.ItemDataRole.UserRole, uid)
                self.card_list.addItem(item)
                item.setHidden(self.table.isRowHidden(row))
                self._card_items_by_uid[uid] = item
        finally:
            self.card_list.blockSignals(False)
            self._syncing_card_selection = False
        if current_uid is not None:
            self._sync_card_selection(current_uid)

    def _on_sort_indicator_changed(self, _column: int, _order: Qt.SortOrder) -> None:
        if self._card_items_by_uid:
            self._reorder_cards_from_table()
        self._remember_view_state()

    # ---- Сортировка и фильтры по учётным записям -----------------------------

    _VIEW_COLUMNS = {"sender": COL_SENDER, "subject": COL_SUBJECT, "date": COL_DATE, "category": COL_CATEGORY}

    def _capture_view_state(self) -> dict:
        header = self.table.horizontalHeader()
        names = {column: name for name, column in self._VIEW_COLUMNS.items()}
        return {
            "sort": names.get(header.sortIndicatorSection(), "date"),
            "order": "asc" if header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder else "desc",
            "filter_column": names.get(self.filter_column, "subject"),
            "filter_text": self.filter_edit.text(),
            "important": bool(self.filter_important),
            "attachments": bool(self.filter_attachments),
            "marker": self.marker_filter,
            "category": self.category_filter,
            "grouping": bool(self.thread_grouping),
        }

    def _apply_view_state(self, state: dict) -> None:
        self._applying_view_state = True
        try:
            self.filter_important = bool(state.get("important", False))
            self.filter_attachments = bool(state.get("attachments", False))
            self.marker_filter = state.get("marker")
            category = state.get("category")
            self.category_filter = category if category is None or category == "" or category in self._categories_cache else None
            self.filter_column = self._VIEW_COLUMNS.get(state.get("filter_column", "subject"), COL_SUBJECT)
            if self.filter_column not in _FILTER_COLUMNS:
                self.filter_column = COL_SUBJECT
            self.filter_edit.setPlaceholderText(f"Фильтр: {_FILTER_COLUMNS[self.filter_column]}")
            self.filter_edit.blockSignals(True)
            self.filter_edit.setText(str(state.get("filter_text", "") or ""))
            self.filter_edit.blockSignals(False)
            grouping = bool(state.get("grouping", self.thread_grouping))
            if grouping != self.thread_grouping:
                self.thread_grouping = grouping
                self.thread_grouping_action.blockSignals(True)
                self.thread_grouping_action.setChecked(grouping)
                self.thread_grouping_action.blockSignals(False)
            column = self._VIEW_COLUMNS.get(state.get("sort", "date"), COL_DATE)
            order = Qt.SortOrder.AscendingOrder if state.get("order") == "asc" else Qt.SortOrder.DescendingOrder
            self.table.horizontalHeader().setSortIndicator(column, order)
            self._update_marker_filter_indicator()
            self._update_filter_button_indicator()
        finally:
            self._applying_view_state = False

    def _switch_view_state(self, key: str) -> None:
        """Выбрана папка другой учётной записи или архива — вернуть её
        сортировку и фильтры, а свои у прежней запомнить."""
        if key == self._view_key:
            return
        if self._view_key is not None:
            self._view_states[self._view_key] = self._capture_view_state()
        self._view_key = key
        state = self._view_states.get(key)
        if state is None:
            # Впервые: сортировка по дате, новые сверху, фильтров нет;
            # группировка — как была задана до разделения по учётным записям.
            state = {"sort": "date", "order": "desc", "grouping": load_thread_grouping()}
        self._apply_view_state(state)
        self._save_view_states()

    def _remember_view_state(self) -> None:
        if self._applying_view_state or self._view_key is None:
            return
        self._view_states[self._view_key] = self._capture_view_state()
        self._save_view_states()

    def _save_view_states(self) -> None:
        # Текст в строке фильтра между запусками не храним — только в сеансе.
        stored = {key: {k: v for k, v in state.items() if k != "filter_text"} for key, state in self._view_states.items()}
        try:
            save_list_view_states(stored)
        except Exception as exc:
            _log.warning("Настройки списка писем не сохранены: %s", exc)

    _SORT_CHOICES: tuple[tuple[str, int, Qt.SortOrder], ...] = (
        ("Дата: новые сверху", COL_DATE, Qt.SortOrder.DescendingOrder),
        ("Дата: старые сверху", COL_DATE, Qt.SortOrder.AscendingOrder),
        ("От кого", COL_SENDER, Qt.SortOrder.AscendingOrder),
        ("Тема", COL_SUBJECT, Qt.SortOrder.AscendingOrder),
        ("Категория", COL_CATEGORY, Qt.SortOrder.AscendingOrder),
    )

    def _build_sort_menu(self) -> QMenu:
        menu = QMenu(self)
        group = QActionGroup(self)
        group.setExclusive(True)
        self._sort_actions: dict[QAction, tuple[int, Qt.SortOrder]] = {}
        for label, column, order in self._SORT_CHOICES:
            action = menu.addAction(label)
            action.setCheckable(True)
            group.addAction(action)
            self._sort_actions[action] = (column, order)
        group.triggered.connect(self._on_sort_action)
        menu.addSeparator()
        self.thread_grouping_action = menu.addAction("Группировать письма по теме")
        self.thread_grouping_action.setCheckable(True)
        self.thread_grouping_action.toggled.connect(self._on_thread_grouping_toggled)
        menu.aboutToShow.connect(self._sync_sort_menu)
        return menu

    # ---- Цепочки по теме в списке ------------------------------------------

    def _on_thread_grouping_toggled(self, enabled: bool) -> None:
        if enabled == self.thread_grouping:
            return
        self.thread_grouping = enabled
        try:
            save_thread_grouping(enabled)  # умолчание для записей, у которых своего выбора ещё нет
        except Exception:
            pass
        self._remember_view_state()
        if self.current_folder is not None:
            self._render_folder(list(self.current_summaries))

    def _toggle_thread(self, key: str) -> None:
        if key in self._expanded_threads:
            self._expanded_threads.discard(key)
        else:
            self._expanded_threads.add(key)
        self.card_delegate.expanded_keys = self._expanded_threads
        for uid, info in self._thread_info.items():
            if info.key == key and info.is_head:
                row = self._row_for_uid(uid)
                summary = self.summaries_by_uid.get(uid)
                if row is not None and summary is not None:
                    item = self.table.item(row, COL_SUBJECT)
                    if item is not None:
                        item.setText(_thread_subject_text(summary, info, key in self._expanded_threads))
                break
        self.on_filter_changed(self.filter_edit.text())
        self._refresh_cards()

    def _thread_hidden(self, summary: MessageSummary, needle: str) -> bool:
        """Дочернее письмо свёрнутой цепочки скрыто — кроме случая, когда
        идёт поиск по строке: найденное показываем всегда."""
        if needle:
            return False
        info = self._thread_info.get(summary.uid)
        return info is not None and info.count > 1 and not info.is_head and info.key not in self._expanded_threads

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        calendar_scroll = getattr(self, "_calendar_scroll", None)
        if calendar_scroll is not None and watched is calendar_scroll.viewport() and event.type() == QEvent.Type.Resize:
            self.calendar_week_grid.set_viewport_height(calendar_scroll.viewport().height())
        if watched is self.table.viewport() and event.type() == QEvent.Type.MouseButtonPress \
                and event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            index = self.table.indexAt(pos)
            if index.isValid() and index.column() == COL_SUBJECT:
                summary = self._summary_for_row(index.row())
                info = self._thread_info.get(summary.uid) if summary is not None else None
                if info is not None and info.count > 1 and info.is_head:
                    cell_left = self.table.visualRect(index).left()
                    if pos.x() - cell_left <= _THREAD_TOGGLE_WIDTH:
                        self._toggle_thread(info.key)
                        return True
        return super().eventFilter(watched, event)

    def _on_calendar_compact_toggled(self, compact: bool) -> None:
        self.calendar_week_grid.set_compact(compact)
        try:
            save_calendar_compact(compact)
        except Exception as exc:
            _log.info("Сжатый режим календаря не сохранён: %s", exc)
        # После смены видимых часов — к текущему времени, как при открытии.
        self._calendar_scroll.verticalScrollBar().setValue(self.calendar_week_grid.scroll_position_for_now())

    def _sync_sort_menu(self) -> None:
        header = self.table.horizontalHeader()
        current = (header.sortIndicatorSection(), header.sortIndicatorOrder())
        for action, value in self._sort_actions.items():
            action.setChecked(value == current)

    def _on_sort_action(self, action: QAction) -> None:
        column, order = self._sort_actions[action]
        # sortByColumn ставит индикатор в заголовке и сортирует; плитки
        # перестраиваются по sortIndicatorChanged — а если индикатор не
        # изменился (тот же выбор), перестраиваем явно.
        self.table.sortByColumn(column, order)
        if self._card_items_by_uid:
            self._reorder_cards_from_table()

    def _refresh_cards(self) -> None:
        self.card_list.viewport().update()

    def _on_card_current_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None or self._syncing_card_selection:
            return
        row = self._row_for_uid(current.data(Qt.ItemDataRole.UserRole))
        if row is None:
            return
        # Только текущая ячейка таблицы, без изменения выделения: Qt при
        # Ctrl-клике сначала меняет текущий элемент, потом переключает
        # выделение (toggle) — если здесь заранее выделить строку, toggle
        # её тут же снимет, и набор «сбрасывается» (жалоба). Выделение
        # зеркалится по itemSelectionChanged (см. _on_card_selection_changed),
        # а таблица по своему itemSelectionChanged открывает письмо.
        self._syncing_card_selection = True
        try:
            self.table.selectionModel().setCurrentIndex(
                self.table.model().index(row, COL_SUBJECT), QItemSelectionModel.SelectionFlag.NoUpdate
            )
        finally:
            self._syncing_card_selection = False

    def _on_card_selection_changed(self) -> None:
        if self._syncing_card_selection:
            return
        self._syncing_card_selection = True
        try:
            self._mirror_cards_selection_to_table()
        finally:
            self._syncing_card_selection = False

    def _mirror_cards_selection_to_table(self) -> None:
        """Набор выделенных плиток → выделенные строки таблицы (источник
        правды для массовых действий)."""
        selected_uids = {item.data(Qt.ItemDataRole.UserRole) for item in self.card_list.selectedItems()}
        model = self.table.selectionModel()
        model.clearSelection()
        for row in range(self.table.rowCount()):
            check_item = self.table.item(row, COL_CHECK)
            if check_item is not None and check_item.data(Qt.ItemDataRole.UserRole) in selected_uids:
                model.select(self.table.model().index(row, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)

    def _sync_card_selection(self, uid: int) -> None:
        """Выделение таблицы → плитки: все выделенные строки и текущая."""
        item = self._card_items_by_uid.get(uid)
        if item is None or self._syncing_card_selection:
            return
        self._syncing_card_selection = True
        try:
            selected_rows = {index.row() for index in self.table.selectionModel().selectedRows()}
            selected_uids = {
                self.table.item(row, COL_CHECK).data(Qt.ItemDataRole.UserRole) for row in selected_rows
                if self.table.item(row, COL_CHECK) is not None
            }
            self.card_list.clearSelection()
            for card_uid, card_item in self._card_items_by_uid.items():
                card_item.setSelected(card_uid in selected_uids or card_uid == uid)
            self.card_list.setCurrentItem(item, QItemSelectionModel.SelectionFlag.NoUpdate)
        finally:
            self._syncing_card_selection = False

    def _on_card_item_changed(self, item: QListWidgetItem) -> None:
        # Галочка на плитке = галочка в таблице (источник правды для
        # _checked_uids — удаление/архив отмеченных).
        row = self._row_for_uid(item.data(Qt.ItemDataRole.UserRole))
        if row is None:
            return
        check_item = self.table.item(row, COL_CHECK)
        if check_item is not None and check_item.checkState() != item.checkState():
            check_item.setCheckState(item.checkState())

    def _on_card_double_clicked(self, item: QListWidgetItem) -> None:
        row = self._row_for_uid(item.data(Qt.ItemDataRole.UserRole))
        if row is None:
            return
        table_item = self.table.item(row, COL_SUBJECT)
        if table_item is not None:
            self.on_table_item_double_clicked(table_item)

    def _on_card_context_menu(self, pos) -> None:
        item = self.card_list.itemAt(pos)
        if item is None:
            return
        uid = item.data(Qt.ItemDataRole.UserRole)
        row = self._row_for_uid(uid)
        summary = self.summaries_by_uid.get(uid)
        if row is None or summary is None:
            return
        self._show_message_context_menu(summary, row, self.card_list.mapToGlobal(pos), with_marker=True)

    def _set_mail_view_mode(self, mode: str) -> None:
        self.mail_view_mode = mode
        is_cards = mode == "cards"
        self.table.setVisible(not is_cards)
        self.card_list.setVisible(is_cards)
        self.view_table_action.setChecked(not is_cards)
        self.view_cards_action.setChecked(is_cards)
        try:
            save_mail_view_mode(mode)
        except Exception:
            pass  # режим не запомнится между запусками — не критично
        if is_cards and self.selected_summary is not None:
            self._sync_card_selection(self.selected_summary.uid)

    # ---- Фильтр списка (важные / вложения / маркер) ------------------------

    def _build_filter_menu(self) -> QMenu:
        menu = QMenu(self)
        self.filter_important_action = menu.addAction("Только важные")
        self.filter_important_action.setCheckable(True)
        self.filter_important_action.toggled.connect(self._on_simple_filter_toggled)
        self.filter_attachments_action = menu.addAction("Только с вложениями")
        self.filter_attachments_action.setCheckable(True)
        self.filter_attachments_action.toggled.connect(self._on_simple_filter_toggled)
        menu.addSeparator()
        marker_group = QActionGroup(self)
        marker_group.setExclusive(True)
        self._marker_filter_actions: dict[QAction, str | None] = {}
        for value, label in ((None, "Маркер: любые письма"), (_ANY_MARKER_FILTER, "С любым маркером")):
            action = menu.addAction(label)
            action.setCheckable(True)
            marker_group.addAction(action)
            self._marker_filter_actions[action] = value
        for color, label in _MARKER_LABELS.items():
            action = menu.addAction(_marker_icon(color), label)
            action.setCheckable(True)
            marker_group.addAction(action)
            self._marker_filter_actions[action] = color
        marker_group.triggered.connect(self._on_marker_filter_action)
        menu.addSeparator()
        # Категории — отдельным подменю, пересобирается при каждом открытии:
        # список категорий правится в «Параметрах».
        self.category_filter_menu = menu.addMenu("Категория")
        self.category_filter_menu.aboutToShow.connect(self._rebuild_category_filter_menu)
        menu.aboutToShow.connect(self._sync_filter_menu)
        return menu

    def _rebuild_category_filter_menu(self) -> None:
        menu = self.category_filter_menu
        menu.clear()
        group = QActionGroup(menu)
        group.setExclusive(True)
        options = [(None, "Любая", None)]
        if self.category_store is not None:
            options += [(c.id, c.name, c.color) for c in self._categories_cache.values()]
            options.append(("", "Без категории", None))
        for value, label, color in options:
            action = menu.addAction(_dot_icon(color) if color else QIcon(), label)
            action.setCheckable(True)
            action.setChecked(value == self.category_filter)
            action.setData(value)
            group.addAction(action)
        group.triggered.connect(self._on_category_filter_action)

    def _on_category_filter_action(self, action: QAction) -> None:
        self.category_filter = action.data()
        self._update_filter_button_indicator()
        self.on_filter_changed(self.filter_edit.text())
        self._remember_view_state()

    def _sync_filter_menu(self) -> None:
        self.filter_important_action.setChecked(self.filter_important)
        self.filter_attachments_action.setChecked(self.filter_attachments)
        for action, value in self._marker_filter_actions.items():
            action.setChecked(value == self.marker_filter)

    def _on_simple_filter_toggled(self, _checked: bool) -> None:
        self.filter_important = self.filter_important_action.isChecked()
        self.filter_attachments = self.filter_attachments_action.isChecked()
        self._update_filter_button_indicator()
        self.on_filter_changed(self.filter_edit.text())
        self._remember_view_state()

    def _on_marker_filter_action(self, action: QAction) -> None:
        self.marker_filter = self._marker_filter_actions.get(action)
        self._update_marker_filter_indicator()
        self._update_filter_button_indicator()
        self.on_filter_changed(self.filter_edit.text())
        self._remember_view_state()

    def _update_filter_button_indicator(self) -> None:
        active = (
            self.filter_important or self.filter_attachments or self.marker_filter is not None
            or self.category_filter is not None
        )
        self.filter_button.setText("Фильтр •" if active else "Фильтр")

    def on_mail_table_context_menu(self, pos) -> None:
        item = self.table.itemAt(pos)
        if item is None:
            return
        summary = self._summary_for_row(item.row())
        if summary is None:
            return
        self._show_message_context_menu(summary, item.row(), self.table.mapToGlobal(pos), with_marker=False)

    def _show_message_context_menu(self, summary: MessageSummary, row: int, global_pos, *, with_marker: bool) -> None:
        # Общее меню для таблицы и плиток; в плитках нет колонки маркера,
        # поэтому там маркер — пунктом меню.
        menu = QMenu(self)
        toggle_read_action = menu.addAction(
            "Отметить как непрочитанное" if summary.is_read else "Отметить как прочитанное"
        )
        marker_action = menu.addAction("Маркер…") if with_marker else None
        restore_action = None
        in_trash = (
            self.active_source is self.mailbox
            and self.trash_folder_name is not None
            and self.current_folder == self.trash_folder_name
        )
        if in_trash:
            restore_action = menu.addAction("Восстановить из корзины")
        category_actions: dict[QAction, str | None] = {}
        if self.category_store is not None and getattr(self.active_source, "account_key", None):
            category_menu = menu.addMenu("Категория")
            current = self._category_labels.get(summary.uid)
            for category in self._categories_cache.values():
                action = category_menu.addAction(_dot_icon(category.color), category.name)
                action.setCheckable(True)
                action.setChecked(current is not None and current.category_id == category.id)
                category_actions[action] = category.id
            category_menu.addSeparator()
            category_actions[category_menu.addAction("Без категории")] = None
        menu.addSeparator()
        source_action = menu.addAction(_toolbar_icon("source"), "Исходный текст письма…")
        add_contact_action = menu.addAction("Добавить отправителя в контакты…")
        chosen = menu.exec(global_pos)

        if chosen is source_action:
            self._show_message_source(summary)
            return

        if chosen in category_actions:
            self._set_category_manually(summary, category_actions[chosen])
            return

        if chosen is toggle_read_action:
            self._set_message_read(row, summary, not summary.is_read)
            return
        if marker_action is not None and chosen is marker_action:
            self._open_marker_menu(summary, row)
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
            dialog = ContactDialog(self, contact=existing, contacts=self._load_contacts())
        else:
            prefilled = contact_store.Contact(
                display_name=summary.sender, emails=[summary.sender_email] if summary.sender_email else []
            )
            dialog = ContactDialog(self, contact=prefilled, contacts=self._load_contacts())
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
        self._open_marker_menu(summary, item.row())

    def on_table_item_double_clicked(self, item: QTableWidgetItem) -> None:
        if not self.active_source or not self.current_folder:
            return
        summary = self._summary_for_row(item.row())
        if summary is None:
            return
        self._with_message_content(summary, lambda content: self._open_loaded_message(summary, content))

    def _open_loaded_message(self, summary: MessageSummary, content: MessageContent) -> None:
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
                body_html=html_cleanup.simplify_html_for_editor(content.html) or None,
                inline_images=content.inline_images,
                contacts=self._load_contacts(),
                attachments=[
                    OutgoingAttachment(filename=a.filename, content_type=a.content_type, payload=a.payload)
                    for a in content.attachments
                ],
                signatures=self._compose_signatures(),
                default_signature_id=self.default_signature_id,
            )
            self._exec_compose(dialog, source_draft=(self.current_folder, summary.uid))
            return

        self._open_message_window(summary, content)

    def _open_marker_menu(self, summary: MessageSummary, row: int) -> None:
        # Пожелание: "на письмо можно поставить несколько маркеров" — пункты
        # меню с галочками, клик переключает один цвет, остальные остаются.
        menu = QMenu(self)
        none_action = menu.addAction("Без маркера")
        menu.addSeparator()
        current = split_markers(summary.marker_color)
        action_colors: dict[QAction, str] = {}
        for color, label in _MARKER_LABELS.items():
            action = menu.addAction(_marker_icon(color), label)
            action.setCheckable(True)
            action.setChecked(color in current)
            action_colors[action] = color

        chosen = menu.exec(QCursor.pos())
        if chosen is None:
            return
        if chosen is none_action:
            new_value = None
        else:
            toggled = action_colors[chosen]
            colors = [c for c in current if c != toggled] if toggled in current else [*current, toggled]
            new_value = join_markers(colors)

        try:
            self.active_source.set_marker(
                self.current_folder, summary.uid, new_value, previous_color=summary.marker_color
            )
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось изменить маркер", str(exc))
            return

        summary.marker_color = new_value
        flag_item = self.table.item(row, COL_FLAG)
        if flag_item is not None:
            flag_item.setIcon(_markers_icon(new_value))
        self._refresh_cards()
        self.on_filter_changed(self.filter_edit.text())

    def _toggle_check_all(self) -> None:
        """Клик по заголовку колонки галочек: отметить все видимые письма
        (или снять отметки, если все уже отмечены) — для удаления/переноса
        всей папки разом (пожелание: "выбор всех писем в папке для
        удаления, сейчас только вручную")."""
        visible_rows = [row for row in range(self.table.rowCount()) if not self.table.isRowHidden(row)]
        if not visible_rows:
            return
        all_checked = all(
            self.table.item(row, COL_CHECK).checkState() == Qt.CheckState.Checked for row in visible_rows
        )
        state = Qt.CheckState.Unchecked if all_checked else Qt.CheckState.Checked
        self.table.setUpdatesEnabled(False)
        try:
            for row in visible_rows:
                self.table.item(row, COL_CHECK).setCheckState(state)
        finally:
            self.table.setUpdatesEnabled(True)
        self.statusBar().showMessage(
            f"Отмечено писем: {len(visible_rows)}" if state == Qt.CheckState.Checked else "Отметки сняты", 4000
        )

    def _expand_collapsed_threads(self, uids: list[int]) -> list[int]:
        """Отмеченное головное письмо свёрнутой цепочки означает всю цепочку:
        её остальные письма скрыты, отметить их нельзя (жалоба: "если
        удаляю группу — удаляется 1 письмо")."""
        result: list[int] = []
        seen: set[int] = set()
        for uid in uids:
            if uid in seen:
                continue
            info = self._thread_info.get(uid)
            if info is not None and info.is_head and info.count > 1 and info.key not in self._expanded_threads:
                for member_uid, member in self._thread_info.items():
                    if member.key == info.key and member_uid not in seen:
                        seen.add(member_uid)
                        result.append(member_uid)
            elif uid not in seen:
                seen.add(uid)
                result.append(uid)
        return result

    def _checked_uids(self) -> list[int]:
        checked = [
            self.table.item(row, COL_CHECK).data(Qt.ItemDataRole.UserRole)
            for row in range(self.table.rowCount())
            if self.table.item(row, COL_CHECK).checkState() == Qt.CheckState.Checked
        ]
        if checked:
            return self._expand_collapsed_threads(checked)
        # Ничего не отмечено галочками — действуем на выделенные строки
        # (Ctrl/Shift-клик или одно открытое письмо): пожелание "при
        # удалении выделенного письма не просить поставить галочку".
        # Удаление в корзину обратимо, безвозвратное — с подтверждением.
        selected_rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        if not selected_rows:
            return []
        return self._expand_collapsed_threads(
            [self.table.item(row, COL_CHECK).data(Qt.ItemDataRole.UserRole) for row in selected_rows]
        )

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
            self._sync_folders_async(["INBOX"])
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось восстановить", str(exc))
            return
        try:
            summaries = self.mailbox.refresh_folder(self.current_folder)
        except Exception:
            summaries = [s for s in self.current_summaries if s.uid not in checked_uids]
        self._render_folder(summaries)
        self.statusBar().showMessage(f"Восстановлено во «Входящие»: {len(checked_uids)}", 5000)

    def on_delete_selected(self, *, force_permanent: bool = False) -> None:
        if not self.active_source or not self.current_folder:
            return
        checked_uids = self._checked_uids()
        if not checked_uids:
            QMessageBox.information(self, "Нечего удалять", "Отметьте галочками письма, которые нужно удалить.")
            return

        source = self.active_source
        folder = self.current_folder
        # Ящик и его корзина запоминаются в момент нажатия: фоновая часть
        # раньше брала «текущий» ящик в момент выполнения, и щелчок по папке
        # другой учётной записи в это время уводил удаление не туда.
        live_mailbox = self.mailbox if source is self.mailbox else None
        trash_name = self.trash_folder_name
        moved_to_folder: str | None = None  # куда переехали письма (для досинхронизации папки)
        if live_mailbox is not None:
            shift_held = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
            already_in_trash = trash_name is not None and folder == trash_name
            permanent = force_permanent or shift_held or already_in_trash or not trash_name

            if permanent:
                confirm = QMessageBox.question(
                    self,
                    "Удалить безвозвратно",
                    f"Удалить выбранные письма насовсем ({len(checked_uids)})? Это действие нельзя отменить.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if confirm != QMessageBox.StandardButton.Yes:
                    return
                operation = lambda: live_mailbox.delete_messages(folder, checked_uids)  # noqa: E731
                status_text = f"Удалено безвозвратно: {len(checked_uids)}"
            else:
                trash = trash_name
                operation = lambda: live_mailbox.move_to_trash(folder, checked_uids, trash)  # noqa: E731
                status_text = f"Перемещено в корзину: {len(checked_uids)}"
                moved_to_folder = trash
        else:
            confirm = QMessageBox.question(
                self,
                "Удалить из архива",
                f"Удалить выбранные письма из архива насовсем ({len(checked_uids)})? Это действие нельзя отменить.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
            operation = lambda: source.delete_messages(folder, checked_uids)  # noqa: E731
            status_text = f"Удалено из архива: {len(checked_uids)}"

        if self.selected_summary and self.selected_summary.uid in checked_uids:
            self._clear_reading_pane()
        # Строки исчезают из списка сразу (жалоба: "удаление идёт как-то
        # медленно"); сервер и обновление списка — в фоне: раньше удаление
        # шло в потоке интерфейса и ждало, пока фоновая синхронизация
        # освободит соединение (жалоба: "попробовал удалить — опять висит").
        removed = set(checked_uids)
        self._render_folder([s for s in self.current_summaries if s.uid not in removed])
        self._set_busy("Удаление…")

        def do_delete() -> list[MessageSummary]:
            operation()
            return source.refresh_folder(folder)

        worker = _CallableWorker(do_delete, parent=self)

        def finish() -> None:
            self._set_busy(None)
            if worker in self._background_workers:
                self._background_workers.remove(worker)

        def on_success(summaries: object) -> None:
            finish()
            if source is self.active_source and folder == self.current_folder:
                self._render_folder(summaries)
            self.statusBar().showMessage(status_text, 5000)
            if live_mailbox is not None:
                if moved_to_folder:
                    self._sync_folders_async([moved_to_folder], mailbox=live_mailbox)
                self._refresh_counts_async([folder, trash_name or ""], mailbox=live_mailbox)

        def on_failure(error_text: str) -> None:
            finish()
            QMessageBox.critical(self, "Ошибка удаления", error_text)

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._background_workers.append(worker)
        worker.start()

    def on_message_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or not self.active_source or not self.current_folder:
            return
        summary = self._summary_for_row(rows[0].row())
        if summary is None:
            return
        self.selected_summary = summary
        self._sync_card_selection(summary.uid)
        self.current_invite = None
        self.invite_bar.hide()

        # Загрузка тела письма — сетевой запрос при первом (ещё не
        # закэшированном) открытии письма, раньше выполнялся синхронно
        # прямо тут. На перегруженной/медленной сети это подвешивало ВСЁ
        # окно целиком, не только чтение письма (жалоба: "программа
        # подвисает... не реагирует на клики мыши"). Токен — та же защита
        # от гонки, что и в _render_thread: если пользователь успел
        # выбрать другое письмо, пока это грузилось, устаревший ответ
        # применять не нужно.
        self._message_select_token += 1
        token = self._message_select_token
        folder = self.current_folder
        source = self.active_source
        row = rows[0].row()

        _render_mail_html(self.reading_pane, _BODY_WRAP_TEMPLATE.format(content="Загрузка…"))
        self.thread_list.hide()
        self.attachments_list.clear()
        self.attachments_list.hide()
        self.message_header_widget.hide()

        worker = _CallableWorker(source.message_content, folder, summary.uid, parent=self)

        def on_success(content: object) -> None:
            self._background_workers.remove(worker)
            if token != self._message_select_token:
                return  # пользователь уже открыл другое письмо — этот ответ больше не актуален
            self.current_body = content.text
            self.current_attachments = content.attachments
            self.current_content = content
            self._render_message_header(summary, content)
            self._render_thread(summary, content)
            self._update_invite_bar(content)
            self._refresh_attachments_list()

            if not summary.is_read:
                # Отложено на следующий цикл событий: сама отметка
                # "прочитано" на сервере — это тоже сетевой запрос (STORE);
                # письмо сначала показывается, а запрос уходит следом.
                QTimer.singleShot(0, lambda: self._set_message_read(row, summary, True))

        def on_failure(error_text: str) -> None:
            self._background_workers.remove(worker)
            if token != self._message_select_token:
                return
            self.current_body = ""
            self.current_attachments = []
            self.current_content = None
            _render_mail_html(
                self.reading_pane,
                _BODY_WRAP_TEMPLATE.format(content=html.escape(f"Не удалось загрузить письмо: {error_text}")),
            )

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._background_workers.append(worker)
        worker.start()

    def _render_message_header(self, summary: MessageSummary, content: MessageContent) -> None:
        subject = content.subject or summary.subject or "(без темы)"
        sender = content.from_ or (f"{summary.sender} <{summary.sender_email}>" if summary.sender_email else summary.sender)
        self._header_to = content.to
        self._header_cc = content.cc
        self.message_header_label.setText(
            _build_message_header_html(subject, sender, content.to, content.cc, summary.date)
        )
        self.message_header_widget.show()
        self._render_thread_list(summary)

    def _on_header_link_activated(self, href: str) -> None:
        if href == "recipients:to":
            _show_full_recipient_list(self, "Кому", self._header_to)
        elif href == "recipients:cc":
            _show_full_recipient_list(self, "Копия", self._header_cc)

    def _on_header_link_hovered(self, href: str) -> None:
        # Жалоба: "если много получателей — скрывать за многоточием,
        # показывать при наведении в сплывающем окне" — клик по ссылке "и
        # ещё N…" уже открывал полный список (см. _on_header_link_activated),
        # но наведение мышью само по себе никак не реагировало.
        if href == "recipients:to":
            QToolTip.showText(QCursor.pos(), _full_recipient_list_text(self._header_to), self.message_header_label)
        elif href == "recipients:cc":
            QToolTip.showText(QCursor.pos(), _full_recipient_list_text(self._header_cc), self.message_header_label)
        else:
            QToolTip.hideText()

    def _thread_summaries_for(self, summary: MessageSummary) -> list[MessageSummary]:
        """Остальные письма текущей папки с той же темой (без Re:/Fwd:/
        Ответ:/Пересыл:), от новых к старым, как в списке писем (пожелание:
        «внутри группы — от нового к старому») — не включает само summary."""
        normalized = _normalize_subject(summary.subject)
        return sorted(
            (s for s in self.current_summaries if s.uid != summary.uid and _normalize_subject(s.subject) == normalized),
            key=lambda s: (s.date, s.uid),
            reverse=True,
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
        # якорю, а не смена выбранной строки в таблице. Перезагружаем ТОТ
        # ЖЕ временный файл (путь запомнен как свойство view в
        # _render_mail_html) с другим фрагментом — обычная навигация по
        # якорю, без JavaScript.
        uid = item.data(Qt.ItemDataRole.UserRole)
        path_str = self.reading_pane.property("_redmail_temp_html_path")
        if not path_str:
            return
        url = QUrl.fromLocalFile(path_str)
        url.setFragment(f"msg-{uid}")
        self.reading_pane.load(url)

    def _render_thread(self, summary: MessageSummary, content: MessageContent) -> None:
        """Показывает письмо вместе со всей его цепочкой подряд, одной
        прокручиваемой лентой (жалоба: "есть только ссылки на другие
        письма, а надо просмотр цепочки последовательно"). Если у письма
        нет цепочки — обычный показ одного письма, без изменений."""
        thread = self._thread_summaries_for(summary)
        if not thread:
            self._render_body(content)
            return

        # Самые свежие письма цепочки, сверху новые — тот же порядок, что в
        # списке писем (раньше лента шла от старых к новым).
        all_in_thread = sorted([summary, *thread], key=lambda s: (s.date, s.uid), reverse=True)[:_THREAD_DEPTH_LIMIT]
        self._remember_thread_content(summary.uid, content)
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
                    self._remember_thread_content(uid, other_content)
            self._paint_thread(summary, all_in_thread, content)

        def on_failure(_message: str) -> None:
            self._background_workers.remove(worker)

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._background_workers.append(worker)
        worker.start()

    def _remember_thread_content(self, uid: int, content: MessageContent) -> None:
        """Положить письмо в кэш цепочки: без вложений и встроенных картинок
        и не больше _THREAD_CACHE_LIMIT писем (самые старые вытесняются)."""
        self._thread_content_cache[uid] = _lightweight_content(content)
        while len(self._thread_content_cache) > _THREAD_CACHE_LIMIT:
            self._thread_content_cache.pop(next(iter(self._thread_content_cache)))

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
                body_html = content.html or _linkify(content.text)
                if content.html:
                    body_html = _inline_images_to_data_uris(body_html, content.inline_images, content.attachments)
                entries.append((other, body_html))
                continue
            other_content = self._thread_content_cache.get(other.uid)
            if other_content is None:
                entries.append((other, "<i>Загрузка…</i>"))
                continue
            # Тело остальных писем цепочки — только текстом, не их родным
            # HTML: см. подробное объяснение в _build_thread_html.
            preview = _content_preview_text(other_content)
            # Текстовая часть, а без неё — текст из HTML (жалоба: в цепочке
            # одни заглушки «предпросмотр текста недоступен»).
            body = _linkify(preview) if preview else "<i>(письмо без текста)</i>"
            entries.append((other, body))

        thread_html = _build_thread_html(entries, summary.uid)
        wrapped_html = _BODY_WRAP_TEMPLATE.format(content=thread_html)
        # Прокрутка к якорю передаётся прямо в URL (#msg-N) — обычная
        # навигация браузера, срабатывает уже при самой загрузке страницы,
        # без отдельного отложенного вызова после setHtml(), как было
        # нужно для scrollToAnchor() у QTextDocument.
        _render_mail_html(self.reading_pane, wrapped_html, anchor=f"msg-{summary.uid}")

    def on_view_message_source(self) -> None:
        if self.selected_summary is not None:
            self._show_message_source(self.selected_summary)

    def _show_message_source(self, summary: MessageSummary) -> None:
        """Оригинал письма в отдельном окне. Берётся с сервера (или из
        архива) в фоне; нет сети или письма на сервере — собираем из
        локальной копии и честно помечаем, что это не оригинал."""
        source, folder = self.active_source, self.current_folder
        if source is None or folder is None:
            return
        content = self.current_content if self.selected_summary is summary else None
        title = summary.subject or "(без темы)"

        def load() -> tuple[bytes, bool]:
            try:
                return source.original_message(folder, summary.uid), True
            except Exception as exc:
                _log.warning("Исходный текст письма %s/%s с сервера не получен: %s", folder, summary.uid, exc)
                local = content if content is not None else source.message_content(folder, summary.uid)
                raw = mail_export.build_message(
                    subject=local.subject or summary.subject, sender=summary.sender, sender_email=summary.sender_email,
                    date=summary.date, message_id=summary.message_id, importance=summary.importance, to=summary.to,
                    body=local.text, html=local.html, content_from=local.from_, content_to=local.to,
                    content_cc=local.cc,
                    attachments=[(item.filename, item.content_type, item.payload) for item in local.attachments],
                    inline_images=[(cid, ctype, data) for cid, (ctype, data) in local.inline_images.items()],
                )
                return raw, False

        self.statusBar().showMessage("Загружаю исходный текст письма…")
        worker = _CallableWorker(load, parent=self)

        def done(result: object) -> None:
            self.statusBar().clearMessage()
            if worker in self._background_workers:
                self._background_workers.remove(worker)
            raw, original = result
            window = MessageSourceWindow(title, raw, original=original, parent=self)
            window.destroyed.connect(
                lambda: self._message_windows.remove(window) if window in self._message_windows else None
            )
            self._message_windows.append(window)
            window.show()

        def failed(message: str) -> None:
            self.statusBar().clearMessage()
            if worker in self._background_workers:
                self._background_workers.remove(worker)
            QMessageBox.warning(self, "Исходный текст письма", message)

        worker.succeeded.connect(done)
        worker.failed.connect(failed)
        self._background_workers.append(worker)
        worker.start()

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

    def _run_in_background(self, fn, *args) -> None:
        """Запускает необязательную сетевую операцию (STORE флага и т.п.)
        в фоне и молча забывает о результате: она не должна ни блокировать
        интерфейс, ни мешать загрузке следующего письма — IMAP-сессия
        выполняет команды строго по очереди (см. ImapSession), и вызов из
        основного потока ждал бы, пока фоновая загрузка тела не закончится
        (жалоба: "подвисает при переходе от письма к письму")."""
        worker = _CallableWorker(fn, *args, parent=self)

        def _done(*_args) -> None:
            if worker in self._background_workers:
                self._background_workers.remove(worker)

        worker.succeeded.connect(_done)
        worker.failed.connect(_done)
        self._background_workers.append(worker)
        worker.start()

    def _set_message_read(self, row: int, summary: MessageSummary, read: bool) -> None:
        # Отметка на сервере — в фоне; локально письмо уже показано/отмечено.
        self._run_in_background(self.active_source.set_read, self.current_folder, summary.uid, read)
        summary.is_read = read
        for col in (COL_SENDER, COL_SUBJECT):
            item = self.table.item(row, col)
            font = item.font()
            font.setBold(not read)
            item.setFont(font)
        self._refresh_cards()
        if self.active_source is self.mailbox and self.current_folder:
            self._refresh_counts_async([self.current_folder])

    def _mark_summary_answered(self, source: object, folder: str, uid: int) -> None:
        """Ставит \\Answered на письмо, на которое только что отправлен
        ответ, и сразу же обновляет строку в таблице, если это письмо всё
        ещё в текущей открытой папке (жалоба: "если мы ответили на письмо,
        это никак не отражается, нужен какой-то признак")."""
        self._run_in_background(source.set_answered, folder, uid)  # необязательная отметка — письмо уже реально отправлено
        if self.current_folder != folder or self.active_source is not source:
            return
        summary = self.summaries_by_uid.get(uid)
        if summary is not None:
            summary.is_answered = True
            row = self._row_for_uid(uid)
            item = self.table.item(row, COL_SUBJECT) if row is not None else None
            if item is not None:
                info = self._thread_info.get(uid)
                item.setText(_thread_subject_text(summary, info, info is not None and info.key in self._expanded_threads))
            self._refresh_cards()

    def _calendar_hook_for(self, account, drafts_folder: str | None, cache_key: str | None = None):
        """Разбор писем, скачанных фоном: приглашения и .ics попадают в
        календарь, а письмо получает категорию — даже если его не открывали.
        Работает в потоке докачки — окна не трогает."""
        my_email = getattr(account, "email", "") or getattr(account, "username", "")
        calendar_path = self.calendar_path

        def hook(folder: str, uid: int, content) -> None:
            if drafts_folder and folder == drafts_folder:
                return
            store = self.category_store
            if store is not None and cache_key:
                try:
                    store.classify_and_store(cache_key, folder, uid, _facts_from_content(content))
                except Exception as exc:
                    _log.debug("Категория письма %s/%d не определена: %s", folder, uid, exc)
            if not calendar_mail.has_calendar_data(content):
                return
            if calendar_mail.apply_calendar_parts(
                calendar_path, content, my_email,
                target_calendar_id=calendar_store.default_calendar_id(calendar_path),
                server_keeps_invites=isinstance(account, EwsAccount),
            ):
                self._calendar_changed_in_background = True

        return hook

    # ---- Категории писем (подключаемый модуль) ------------------------------

    def _apply_categories_module(self, enabled: bool) -> None:
        """Включить или выключить модуль категорий без перезапуска."""
        if enabled and self.category_store is None:
            try:
                self.category_store = mail_categories.CategoryStore(profile.profile_dir() / "categories.sqlite3")
            except Exception as exc:
                _log.error("Модуль категорий не запущен: %s", exc)
                self.category_store = None
        elif not enabled:
            self.category_store = None
            self._category_labels = {}
            self.category_filter = None
        self._reload_categories()
        self.table.setColumnHidden(COL_CATEGORY, self.category_store is None)
        if self.current_summaries:
            self._render_folder(self.current_summaries)

    def _reload_categories(self) -> None:
        store = self.category_store
        try:
            self._categories_cache = {c.id: c for c in store.categories()} if store is not None else {}
        except Exception as exc:
            _log.warning("Категории не прочитаны: %s", exc)
            self._categories_cache = {}

    def _category_chip(self, uid: int) -> tuple[str, str] | None:
        label = self._category_labels.get(uid)
        category = self._categories_cache.get(label.category_id) if label is not None and label.category_id else None
        return (category.name, category.color) if category is not None else None

    def _category_name(self, uid: int) -> str:
        label = self._category_labels.get(uid)
        category = self._categories_cache.get(label.category_id) if label is not None and label.category_id else None
        return category.name if category is not None else ""

    def _paint_category_item(self, item: QTableWidgetItem, uid: int) -> None:
        label = self._category_labels.get(uid)
        category = self._categories_cache.get(label.category_id) if label is not None and label.category_id else None
        item.setText(category.name if category is not None else "")
        if category is not None:
            item.setForeground(QColor(category.color))
            how = {
                mail_categories.SOURCE_USER: "выбрана вручную",
                mail_categories.SOURCE_RULE: "по описанию категории",
                mail_categories.SOURCE_HEADERS: "по признакам рассылки",
                mail_categories.SOURCE_MODEL: f"по обучению, уверенность {label.score:.0%}",
            }.get(label.source, "")
            item.setToolTip(f"{category.name} — {how}" if how else category.name)
        else:
            item.setToolTip("")

    def _apply_category_labels(self, labels: dict) -> None:
        self._category_labels = labels
        self.table.setSortingEnabled(False)
        try:
            for row in range(self.table.rowCount()):
                check = self.table.item(row, COL_CHECK)
                item = self.table.item(row, COL_CATEGORY)
                if check is None or item is None:
                    continue
                uid = check.data(Qt.ItemDataRole.UserRole)
                self._paint_category_item(item, uid)
                if isinstance(item, _ThreadSortItem):
                    # Категории приходят в фоне уже после построения списка —
                    # обновляем и значения для сортировки.
                    info = self._thread_info.get(uid)
                    head_uid = info.head_uid if info is not None else uid
                    item.own = _category_sort_key(self._category_name(uid))
                    item.group_value = _category_sort_key(self._category_name(head_uid))
        finally:
            self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        if header.sortIndicatorSection() == COL_CATEGORY:
            self.table.sortItems(COL_CATEGORY, header.sortIndicatorOrder())
            if self._card_items_by_uid:
                self._reorder_cards_from_table()
        self._refresh_cards()
        self.on_filter_changed(self.filter_edit.text())

    def _categorize_folder_async(self, summaries: list[MessageSummary]) -> None:
        """Категории писем открытой папки: известные — сразу из базы модуля,
        недостающие — в фоне, по теме, отправителю и сохранённому тексту."""
        store = self.category_store
        source = self.active_source
        folder = self.current_folder
        account = getattr(source, "account_key", None)
        if store is None or not account or not folder:
            self._category_labels = {}
            return
        self._category_token += 1
        token = self._category_token
        try:
            known = store.labels_for(account, folder)
        except Exception as exc:
            _log.warning("Категории папки %s не прочитаны: %s", folder, exc)
            return
        self._apply_category_labels(known)
        missing = [s for s in summaries if s.uid not in known][:1000]
        if not missing:
            return

        def work() -> dict:
            model = store.model()
            for summary in missing:
                text = ""
                try:
                    from redmail import cache_store

                    text = cache_store.get_message_text(account, folder, summary.uid) or ""
                except Exception:
                    text = ""
                store.classify_and_store(account, folder, summary.uid, _facts_from_summary(summary, text), model=model)
            return store.labels_for(account, folder)

        worker = _CallableWorker(work, parent=self)

        def done(result: object = None) -> None:
            if worker in self._background_workers:
                self._background_workers.remove(worker)
            if result is not None and token == self._category_token and source is self.active_source and folder == self.current_folder:
                self._apply_category_labels(result)

        worker.succeeded.connect(done)
        worker.failed.connect(lambda error: (_log.warning("Разметка категорий не удалась: %s", error), done()))
        self._background_workers.append(worker)
        worker.start()

    def _set_category_manually(self, summary: MessageSummary, category_id: str | None) -> None:
        """Человек выбрал категорию: для этого письма и отмеченных галочкой.
        Выбор сохраняется и учит модуль на будущее."""
        store = self.category_store
        account = getattr(self.active_source, "account_key", None)
        folder = self.current_folder
        if store is None or not account or not folder:
            return
        uids = [uid for uid in self._checked_uids() if uid in self.summaries_by_uid] or [summary.uid]
        if summary.uid not in uids:
            uids = [summary.uid]
        from redmail import cache_store

        for uid in uids:
            target = self.summaries_by_uid[uid]
            try:
                text = cache_store.get_message_text(account, folder, uid) or ""
            except Exception:
                text = ""
            store.set_user_label(account, folder, uid, category_id, _facts_from_summary(target, text))
        self._apply_category_labels(store.labels_for(account, folder))
        name = self._categories_cache[category_id].name if category_id in self._categories_cache else "без категории"
        self.statusBar().showMessage(f"Категория «{name}»: писем {len(uids)}", 5000)

    def _update_invite_bar(self, content: MessageContent) -> None:
        if not self.account or not calendar_mail.has_calendar_data(content):
            return
        my_email = getattr(self.account, "email", "") or self.account.username
        try:
            # Новое приглашение — в основной календарь; из ящика Exchange —
            # без своей копии: встречу в календарь Exchange кладёт сервер.
            results = calendar_mail.apply_calendar_parts(
                self.calendar_path, content, my_email,
                target_calendar_id=calendar_store.default_calendar_id(self.calendar_path),
                server_keeps_invites=isinstance(self.account, EwsAccount),
            )
        except Exception as exc:
            # Не проглатывать молча — иначе панель приглашения просто не
            # появляется без единого следа, почему.
            QMessageBox.critical(self, "Не удалось обработать приглашение", str(exc))
            return
        if not results:
            return
        result = results[0]
        if result.method == "PUBLISH":
            names = ", ".join(f"«{event.summary}»" for event in result.imported[:3])
            more = f" и ещё {len(result.imported) - 3}" if len(result.imported) > 3 else ""
            self.current_invite = None
            self.invite_label.setText(f"Добавлено в календарь из вложения: {names}{more} — {_format_event_time(result.event)}")
            for button in (self.invite_accept_button, self.invite_tentative_button, self.invite_decline_button):
                button.setEnabled(False)
            self.invite_bar.show()
        else:
            self._show_invite_result(result)
        self.refresh_calendar_view()

    def _show_invite_result(self, result) -> None:
        invite = result.invite
        event = result.event
        if result.rejected_sender:
            # Изменение встречи пришло не от организатора (или ответ — не от
            # самого участника): в календарь не внесено, объясняем почему.
            self.current_invite = None
            kinds = {"REQUEST": "Изменение встречи", "CANCEL": "Отмена встречи", "REPLY": "Ответ участника"}
            self.invite_label.setText(
                f"{kinds.get(result.method, 'Изменение')} «{invite.event.summary}» прислал {result.rejected_sender}, "
                "а не организатор — в календарь не внесено."
            )
            for button in (self.invite_accept_button, self.invite_tentative_button, self.invite_decline_button):
                button.setEnabled(False)
            self.invite_bar.show()
            return
        if result.method == "REQUEST":
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
        elif result.method == "CANCEL":
            self.current_invite = None
            self.invite_label.setText(f"Встреча отменена: «{invite.event.summary}»")
            for button in (self.invite_accept_button, self.invite_tentative_button, self.invite_decline_button):
                button.setEnabled(False)
        else:
            self.current_invite = None
            label = _PARTICIPATION_LABELS.get(result.participation, result.participation)
            self.invite_label.setText(f"{result.replying_attendee}: {label} — «{invite.event.summary}»")
            for button in (self.invite_accept_button, self.invite_tentative_button, self.invite_decline_button):
                button.setEnabled(False)
        self.invite_bar.show()

    def on_invite_response(self, participation: str) -> None:
        if self.current_invite is None or not self.account:
            return
        if isinstance(self.account, EwsAccount) and self.selected_summary is not None and self.current_folder:
            # Ящик Exchange: ответ — самим сервером по письму-приглашению (как
            # Outlook); своей почты SMTP у такой учётной записи нет.
            invite_event = self.current_invite.event
            session = self.mailbox.interactive_session() if self.mailbox is not None else None
            folder, uid = self.current_folder, self.selected_summary.uid
            self._respond_via_exchange(
                lambda: session.respond_to_meeting(folder, uid, participation), invite_event, participation
            )
            self.current_invite = None
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
        if self._is_exchange_calendar(event.calendar_id):
            session = self._ews_session_for_calendar()
            calendar = self._calendar_by_id(event.calendar_id)
            if session is not None and calendar is not None:
                email = getattr(getattr(session, "account", None), "email", "") or ""
                server = _ExchangeCalendarServer(session, email, calendar.caldav_url.strip())
                self._respond_via_exchange(lambda: server.respond(event.uid, participation), event, participation)
                return
        updated = self._respond_to_invite(event.uid, participation)
        if updated is None:
            return
        self.selected_calendar_event = updated
        self.refresh_calendar_view()
        self.statusBar().showMessage(f"Ответ сохранён, отправляется: {_REPLY_VERBS[participation].lower()}", 3000)

    def _respond_via_exchange(self, send, event: calendar_store.Event, participation: str) -> None:
        """Ответ на приглашение через Exchange — в фоне; наша копия встречи
        (если есть) получает тот же ответ, календарь Exchange обновит
        синхронизация."""
        verb = _REPLY_VERBS[participation]
        for button in (self.invite_accept_button, self.invite_tentative_button, self.invite_decline_button):
            button.setEnabled(False)
        self.invite_label.setText(f"«{event.summary}» — {_PARTICIPATION_LABELS[participation]}")
        self.statusBar().showMessage(f"Ответ отправляется через Exchange: {verb.lower()}")
        worker = _CallableWorker(send, parent=self)

        def done(_result: object = None) -> None:
            if worker in self._background_workers:
                self._background_workers.remove(worker)
            try:
                calendar_store.set_my_participation(self.calendar_path, event.uid, participation)
            except Exception:
                pass
            self.statusBar().showMessage(f"Ответ на приглашение отправлен: {verb.lower()}", 5000)
            self._schedule_calendar_sync(1000)

        def failed(error_text: str) -> None:
            if worker in self._background_workers:
                self._background_workers.remove(worker)
            QMessageBox.warning(self, "Не удалось ответить на приглашение", error_text)

        worker.succeeded.connect(done)
        worker.failed.connect(failed)
        self._background_workers.append(worker)
        worker.start()

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
            self.reply_all_action,
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
        self.calendar_selected_day = date.today()
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
        # Раньше импорт всегда сваливал события в календарь по умолчанию,
        # смешивая их с личными встречами — не было способа получить именно
        # "календарь из другого источника" отдельным списком со своим
        # именем/цветом/видимостью (жалоба: "не создаётся календарь из
        # другого источника"). Теперь импорт создаёт для него отдельный
        # календарь, по умолчанию названный по имени файла.
        suggested_name = Path(path_str).stem
        name, ok = QInputDialog.getText(
            self, "Импорт календаря", "Название для импортированного календаря:", text=suggested_name
        )
        if not ok:
            return
        name = name.strip() or suggested_name
        try:
            used_colors = {cal.color for cal in self._load_calendars()}
            color = next(
                (hexval for _label, hexval in _EVENT_COLOR_PALETTE if hexval not in used_colors),
                _EVENT_COLOR_PALETTE[0][1],
            )
            calendar = calendar_store.create_user_calendar(self.calendar_path, name, color)
            data = Path(path_str).read_bytes()
            count = itip.import_ics(
                self.calendar_path, data, self.account.username if self.account else "", calendar_id=calendar.id
            )
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка импорта", str(exc))
            return
        self._refresh_calendars_list(select_id=calendar.id)
        self.refresh_calendar_view()
        self.statusBar().showMessage(f"Импортировано событий: {count} (календарь «{name}»)", 5000)

    def _calendar_by_id(self, calendar_id: str):
        try:
            return next((cal for cal in calendar_store.list_calendars(self.calendar_path) if cal.id == calendar_id), None)
        except Exception:
            return None

    def _is_server_calendar(self, calendar_id: str) -> bool:
        calendar = self._calendar_by_id(calendar_id)
        return calendar is not None and calendar.source_type in (calendar_store.SOURCE_CALDAV, calendar_store.SOURCE_EWS)

    def _is_exchange_calendar(self, calendar_id: str) -> bool:
        calendar = self._calendar_by_id(calendar_id)
        return calendar is not None and calendar.source_type == calendar_store.SOURCE_EWS

    def _calendar_credentials(self, url: str) -> tuple[str, str, str] | None:
        """(логин, пароль, способ входа) для сервера календаря — от учётной
        записи, чей почтовый сервер ближе всего по домену (см.
        _pick_calendar_account). Годятся и выключенные галочкой записи:
        календарь VK нужен, даже когда почта VK сейчас не открыта."""
        accounts = [
            account for key, account in self.mailbox_accounts.items() if self.mailbox_protocols.get(key) == "imap"
        ]
        try:
            accounts += [account for account, _smtp in load_accounts()]
        except Exception as exc:
            _log.debug("Календарь: сохранённые учётные записи не прочитаны: %s", exc)
        chosen = _pick_calendar_account(url, accounts)
        if chosen is None:
            return None
        return chosen.username, getattr(chosen, "password", ""), getattr(chosen, "auth_type", "password")

    def _schedule_calendar_sync(self, delay_ms: int = 1500) -> None:
        """Тихая синхронизация календарей чуть позже — после сохранения,
        переноса или удаления встречи, чтобы изменение сразу ушло на сервер."""
        QTimer.singleShot(delay_ms, lambda: self.on_caldav_sync(silent=True))

    def on_caldav_sync(self, *, silent: bool = False) -> None:
        """Синхронизация всех календарей с серверами — CalDAV (VK и др.),
        Exchange и подписок по ссылке — в фоне (сетевые запросы раньше
        шли в потоке окна, и оно замерзало целиком).

        Каждый календарь идёт со своей учётной записью: для CalDAV — той, чей
        почтовый сервер ближе по домену, для Exchange — подключённой почтой
        Exchange. Ошибка одного календаря не останавливает остальные.
        silent — запуск по таймеру или после правки встречи: без окон,
        итог только в строке состояния и журнале."""
        if self._calendar_sync_running:
            return
        try:
            calendars = [
                cal for cal in calendar_store.list_calendars(self.calendar_path)
                if cal.source_type in (calendar_store.SOURCE_CALDAV, calendar_store.SOURCE_ICS, calendar_store.SOURCE_EWS)
            ]
        except Exception as exc:
            _log.error("Календари: список не прочитан: %s", exc)
            return
        if not calendars:
            if not silent:
                QMessageBox.information(
                    self, "Синхронизация не настроена",
                    "Добавьте календарь с источником Exchange, CalDAV или подписку по ссылке (.ics) "
                    "через «+ Добавить календарь».",
                )
            return

        plans = []
        problems: list[str] = []
        for cal in calendars:
            if cal.source_type == calendar_store.SOURCE_EWS:
                session = self._ews_session_for_calendar()
                if session is None:
                    problems.append(f"«{cal.name}»: не подключена учётная запись Exchange")
                    continue
                email = getattr(getattr(session, "account", None), "email", "") or ""
                # Для подписки на чужой календарь в caldav_url лежит адрес ящика коллеги.
                mailbox = cal.caldav_url.strip()
                plans.append((
                    cal,
                    lambda session=session, email=email, mailbox=mailbox: (
                        _ExchangeCalendarServer(session, email, mailbox), None
                    ),
                    bool(mailbox) and False,
                ))
            elif cal.source_type == calendar_store.SOURCE_ICS:
                username = getattr(self.account, "username", "") if self.account else ""
                plans.append((cal, lambda cal=cal, username=username: (_IcsCalendarServer(cal.caldav_url, username), None), True))
            else:
                credentials = self._calendar_credentials(cal.caldav_url)
                if credentials is None and self.account is not None and not isinstance(self.account, EwsAccount):
                    credentials = (
                        self.account.username, getattr(self.account, "password", ""),
                        getattr(self.account, "auth_type", "password"),
                    )
                if credentials is None:
                    problems.append(f"«{cal.name}»: нет учётной записи почты для сервера календаря")
                    continue
                username, password, auth_type = credentials

                def open_caldav(cal=cal, username=username, password=password, auth_type=auth_type):
                    session = caldav_sync.CalDavSession(
                        caldav_sync.CalDavAccount(url=cal.caldav_url, username=username, password=password, auth_type=auth_type)
                    )
                    return _CalDavCalendarServer(session, username), session

                plans.append((cal, open_caldav, False))

        calendar_path = self.calendar_path
        window_start = datetime.now(timezone.utc) - timedelta(days=30)
        window_end = datetime.now(timezone.utc) + timedelta(days=180)

        def do_sync() -> list:
            reports = []
            for cal, open_server, read_only in plans:
                try:
                    server, closable = open_server()
                except Exception as exc:
                    report = calendar_sync.CalendarSyncReport(name=cal.name, errors=[f"подключение: {exc}"])
                    _log.warning("Календарь «%s»: подключение не удалось: %s", cal.name, exc)
                    reports.append(report)
                    continue
                try:
                    reports.append(calendar_sync.sync_calendar(
                        calendar_path, cal, server, window_start, window_end, read_only=read_only,
                    ))
                finally:
                    if closable is not None:
                        closable.close()
            return reports

        self._calendar_sync_running = True
        if not silent:
            self.statusBar().showMessage("Синхронизация календарей…")
        worker = _CallableWorker(do_sync, parent=self)

        def finish() -> None:
            self._calendar_sync_running = False
            if worker in self._background_workers:
                self._background_workers.remove(worker)

        def on_success(result: object) -> None:
            finish()
            reports = list(result or [])
            self.refresh_calendar_view()
            pushed = sum(r.pushed for r in reports)
            pulled = sum(r.pulled for r in reports)
            errors = problems + [f"«{r.name}»: {error}" for r in reports for error in r.errors]
            summary = f"Календари: отправлено {pushed}, получено {pulled}"
            if errors:
                summary += f", ошибок {len(errors)}"
            self.statusBar().showMessage(summary, 7000)
            if errors and not silent:
                QMessageBox.warning(self, "Синхронизация календарей", "\n".join(errors[:10]))

        def on_failure(error_text: str) -> None:
            finish()
            _log.error("Календари: синхронизация не удалась: %s", error_text)
            if silent:
                self.statusBar().showMessage(f"Календари: {error_text}", 7000)
            else:
                QMessageBox.critical(self, "Синхронизация календарей", error_text)

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
        events = [e for e in events if e.calendar_id in self._visible_calendar_ids]
        # Отклонённое приглашение (я не организатор и явно отказался) больше
        # не показывается в сетке вовсе — жалоба: "если приняли приглашение
        # на событие, то либо должны пропасть кнопки, либо при отказе —
        # пропадать событие из календаря". Свои же встречи (is_organizer)
        # participation ко мне не относится — их decline не бывает.
        events = [e for e in events if e.is_organizer or e.my_participation != "declined"]
        # calendar_id -> цвет календаря — раньше карточка события всегда
        # красилась по роли (я организатор/меня пригласили), без единой
        # привязки к тому, в каком именно календаре событие лежит, и
        # событие в НОВОМ календаре выглядело неотличимо от события в
        # календаре по умолчанию (жалоба: "не видно связи события с
        # календарём"). См. _event_color в week_calendar.py.
        calendar_colors = {cal.id: cal.color for cal in self._calendars_by_row}

        if self.calendar_view_mode == "month":
            self.calendar_month_label.setText(
                f"{_MONTH_NAMES[self.calendar_month_anchor.month - 1]} {self.calendar_month_anchor.year}"
            )
            self.calendar_month_grid.set_month(self.calendar_month_anchor, events, calendar_colors)
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
            self.calendar_week_header.set_selected_day(self.calendar_selected_day)
            self.calendar_all_day_row.set_week(self.calendar_week_start, all_day_events, calendar_colors)
            self.calendar_week_grid.set_week(self.calendar_week_start, timed_events, calendar_colors)
            self.calendar_week_grid.set_selected_day(self.calendar_selected_day)
            # Раньше здесь всегда подставлялся понедельник недели — если
            # пользователь кликал в мини-календаре не по понедельнику
            # (например, 21.08 — пятница), тот же refresh_calendar_view()
            # тут же откатывал выделение обратно на 17.08 и день визуально
            # "не выбирался". Позже забыли добавить сюда же
            # calendar_selected_day (он уже используется в ветке "месяц" и
            # проставлен по умолчанию на сегодня) — из-за этого при первом
            # открытии календаря (режим "неделя" по умолчанию) мини-календарь
            # слева подсвечивал понедельник ТЕКУЩЕЙ недели, а не сегодняшнее
            # число (жалоба: "не исправил отображение месяца, показывает 31
            # августа" — 31.08.2026 и есть понедельник недели, в которую
            # попадает сегодняшнее 06.09.2026).
            highlighted_day = (
                self._mini_picker_target_day or self.calendar_selected_day or self.calendar_week_start
            )

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

    def _refresh_calendars_list(self, *, select_id: str | None = None) -> None:
        """Перестраивает список "Мои календари" из хранилища — вызывается
        при старте и после любого добавления/переименования/удаления
        календаря. select_id — какой календарь оставить/сделать текущим
        выбором в списке (например, только что созданный)."""
        try:
            calendars = calendar_store.list_calendars(self.calendar_path)
        except Exception:
            calendars = []
        self._calendars_by_row = calendars
        self.calendars_list.blockSignals(True)
        self.calendars_list.clear()
        default_id = calendar_store.default_calendar_id(self.calendar_path) if calendars else ""
        for cal in calendars:
            # Основной календарь виден сразу: в него уходят новые встречи и
            # то, что диктуется голосовому помощнику.
            title = f"{cal.name} — основной" if cal.id == default_id else cal.name
            item = QListWidgetItem(_dot_icon(cal.color), title)
            if cal.id == default_id:
                item_font = item.font()
                item_font.setBold(True)
                item.setFont(item_font)
            item.setData(Qt.ItemDataRole.UserRole, cal.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if cal.visible else Qt.CheckState.Unchecked)
            self.calendars_list.addItem(item)
            if select_id is not None and cal.id == select_id:
                self.calendars_list.setCurrentItem(item)
        self.calendars_list.blockSignals(False)
        self._visible_calendar_ids = {cal.id for cal in calendars if cal.visible}

    def on_calendar_item_changed(self, item: QListWidgetItem) -> None:
        calendar_id = item.data(Qt.ItemDataRole.UserRole)
        visible = item.checkState() == Qt.CheckState.Checked
        try:
            calendar_store.set_calendar_visible(self.calendar_path, calendar_id, visible)
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось сохранить", str(exc))
            return
        if visible:
            self._visible_calendar_ids.add(calendar_id)
        else:
            self._visible_calendar_ids.discard(calendar_id)
        self.refresh_calendar_view()

    def on_add_calendar(self) -> None:
        used_colors = {cal.color for cal in self._calendars_by_row}
        dialog = AddCalendarDialog(
            self,
            my_email=self.account.username if self.account else "",
            my_password=getattr(self.account, "password", "") if self.account else "",
            my_auth_type=getattr(self.account, "auth_type", "password") if self.account else "password",
            used_colors=used_colors,
            credentials_for=self._calendar_credentials,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            created = calendar_store.create_user_calendar(
                self.calendar_path, dialog.name(), dialog.color(),
                source_type=dialog.source_type(), caldav_url=dialog.caldav_url(),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось создать календарь", str(exc))
            return
        self._refresh_calendars_list(select_id=created.id)
        self.refresh_calendar_view()

    def on_calendar_list_context_menu(self, pos) -> None:
        item = self.calendars_list.itemAt(pos)
        if item is None:
            return
        calendar_id = item.data(Qt.ItemDataRole.UserRole)
        calendar = next((cal for cal in self._calendars_by_row if cal.id == calendar_id), None)
        menu = QMenu(self)
        default_action = menu.addAction("Сделать основным")
        default_action.setToolTip(
            "В основной календарь попадают новые встречи, в том числе созданные голосом"
        )
        try:
            default_action.setEnabled(calendar_id != calendar_store.default_calendar_id(self.calendar_path))
        except Exception:
            pass
        menu.addSeparator()
        rename_action = menu.addAction("Переименовать…")
        color_action = menu.addAction("Цвет…")
        connection_action = None
        if calendar is not None and calendar.source_type == calendar_store.SOURCE_CALDAV:
            connection_action = menu.addAction("Подключение…")
        menu.addSeparator()
        delete_action = menu.addAction("Удалить")
        if len(self._calendars_by_row) <= 1:
            delete_action.setEnabled(False)
            delete_action.setToolTip("Нельзя удалить единственный оставшийся календарь")
        chosen = menu.exec(self.calendars_list.mapToGlobal(pos))
        if chosen is default_action:
            try:
                calendar_store.set_default_calendar(self.calendar_path, calendar_id)
            except Exception as exc:
                QMessageBox.warning(self, "Не удалось сохранить", str(exc))
                return
            self._refresh_calendars_list(select_id=calendar_id)
            self.statusBar().showMessage("Основной календарь изменён", 5000)
        elif chosen is rename_action:
            self._rename_calendar(calendar_id, item.text())
        elif chosen is color_action:
            self._recolor_calendar(calendar_id)
        elif connection_action is not None and chosen is connection_action:
            self._edit_calendar_connection(calendar_id, calendar.caldav_url)
        elif chosen is delete_action:
            self._delete_calendar(calendar_id, item.text())

    def _edit_calendar_connection(self, calendar_id: str, current_url: str) -> None:
        # Жалоба: узкое поле QInputDialog.getText показывало длинный URL с
        # UUID обрезанным (виден только хвост) — не поместить длинный адрес
        # для чтения/правки. Заодно та же кнопка "Найти календари на
        # сервере...", что и в "Новый календарь" — если нужно ПЕРЕподключить
        # календарь на другой (например, другой расшаренный), не придётся
        # вручную набирать точный URL.
        dialog = _EditCalendarUrlDialog(
            self,
            current_url,
            self.account.username,
            getattr(self.account, "password", ""),
            getattr(self.account, "auth_type", "password"),
            credentials_for=self._calendar_credentials,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        url = dialog.url()
        if not url or url == current_url:
            return
        try:
            calendar_store.set_calendar_caldav_url(self.calendar_path, calendar_id, url)
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось сохранить", str(exc))
            return
        self._refresh_calendars_list(select_id=calendar_id)

    def _rename_calendar(self, calendar_id: str, current_name: str) -> None:
        name, ok = QInputDialog.getText(self, "Переименовать календарь", "Название календаря:", text=current_name)
        name = name.strip()
        if not ok or not name or name == current_name:
            return
        try:
            calendar_store.rename_calendar(self.calendar_path, calendar_id, name)
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось переименовать", str(exc))
            return
        self._refresh_calendars_list(select_id=calendar_id)

    def _recolor_calendar(self, calendar_id: str) -> None:
        current = next((cal.color for cal in self._calendars_by_row if cal.id == calendar_id), "#3B6FB6")
        color = QColorDialog.getColor(QColor(current), self, "Цвет календаря")
        if not color.isValid():
            return
        try:
            calendar_store.set_calendar_color(self.calendar_path, calendar_id, color.name())
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось сохранить цвет", str(exc))
            return
        self._refresh_calendars_list(select_id=calendar_id)
        self.refresh_calendar_view()

    def _delete_calendar(self, calendar_id: str, name: str) -> None:
        if len(self._calendars_by_row) <= 1:
            return
        confirm = QMessageBox.question(
            self,
            "Удалить календарь",
            f"Удалить календарь «{name}» вместе со всеми его событиями? Это действие нельзя отменить.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            calendar_store.delete_calendar(self.calendar_path, calendar_id)
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось удалить", str(exc))
            return
        self._refresh_calendars_list()
        self.refresh_calendar_view()

    def _on_calendar_event_clicked(self, event: calendar_store.Event) -> None:
        self.selected_calendar_event = event
        self._apply_calendar_selection_highlight()

    def _on_calendar_event_double_clicked(self, event: calendar_store.Event) -> None:
        self.selected_calendar_event = event
        self._apply_calendar_selection_highlight()
        if not event.is_organizer:
            dialog = EventDetailsDialog(self, event, contacts=self._load_contacts())
            if dialog.exec() == QDialog.DialogCode.Accepted:
                if dialog.chosen_participation is not None:
                    self.on_calendar_rsvp(event, dialog.chosen_participation)
                elif dialog.copy_requested:
                    self.on_copy_event(event)
            return
        dialog = EventDialog(
            self,
            event=event,
            my_email=self.account.username if self.account else "",
            contacts=self._load_contacts(),
            calendars=self._load_calendars(),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_event_from_dialog(dialog, existing=event)

    def _load_calendars(self) -> list[calendar_store.Calendar]:
        try:
            return calendar_store.list_calendars(self.calendar_path)
        except Exception:
            return []

    def on_new_event(self, *, default_start: datetime | None = None) -> None:
        if not self.account:
            QMessageBox.warning(self, "Нет учётной записи", "Сначала подключитесь к почте в настройках.")
            return
        if default_start is None and self.calendar_selected_day is not None:
            # Кнопка "Новое событие" на панели — если пользователь кликом
            # выбрал день в шапке календаря, событие по умолчанию ставим
            # туда, а не всегда на "сейчас+час". Но calendar_selected_day
            # теперь ВСЕГДА заполнен (по умолчанию — сегодня, см. фикс
            # "при открытии календаря не устанавливается текущее число"),
            # а не только после явного клика по дню — из-за этого "сегодня
            # в 9:00" почти всегда оказывается уже в прошлом (сейчас позже
            # 9 утра), и _save_event_from_dialog ниже молча отклонял
            # сохранение проверкой на прошедшее время (жалоба: "событие не
            # создаётся"). Берём такой дефолт только если он ещё не прошёл;
            # иначе просто оставляем None — EventDialog сам подставит
            # "сейчас + 1 час".
            candidate_start = self._slot_to_datetime(self.calendar_selected_day, 9 * 60)
            if candidate_start > datetime.now().astimezone():
                default_start = candidate_start
        # Новая встреча ложится в основной календарь. Выбор в списке слева
        # его перебивает: пользователь явно указал, куда писать.
        current_item = self.calendars_list.currentItem()
        default_calendar_id = current_item.data(Qt.ItemDataRole.UserRole) if current_item else None
        if default_calendar_id is None:
            try:
                default_calendar_id = calendar_store.default_calendar_id(self.calendar_path)
            except Exception:
                default_calendar_id = None
        dialog = EventDialog(
            self,
            my_email=self.account.username,
            contacts=self._load_contacts(),
            default_start=default_start,
            calendars=self._load_calendars(),
            default_calendar_id=default_calendar_id,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_event_from_dialog(dialog, existing=None)

    def on_calendar_day_clicked(self, day: date) -> None:
        self.calendar_selected_day = day
        self.calendar_week_header.set_selected_day(day)
        self.calendar_week_grid.set_selected_day(day)

    def on_calendar_empty_slot_clicked(self, day: date, minutes: int) -> None:
        """Одиночный клик — только выделить день: окно создания события по
        одному клику открывалось случайно при любом движении мышью."""
        self.calendar_selected_day = day
        self.calendar_week_header.set_selected_day(day)
        self.calendar_week_grid.set_selected_day(day)

    def on_calendar_empty_slot_double_clicked(self, day: date, minutes: int) -> None:
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
        dialog = EventDialog(
            self,
            event=event,
            my_email=self.account.username,
            contacts=self._load_contacts(),
            calendars=self._load_calendars(),
        )
        dialog.setWindowTitle("Копия встречи")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_event_from_dialog(dialog, existing=None)

    def _shrink_contact_photos(self) -> None:
        worker = _CallableWorker(contact_store.shrink_large_photos, Path(self.contacts_path), _shrink_photo_bytes, parent=self)

        def finished(result: object = None) -> None:
            if worker in self._background_workers:
                self._background_workers.remove(worker)
            if result:
                _log.info("Адресная книга: уменьшено фотографий %s", result)
                memory_report.free_memory()

        worker.succeeded.connect(finished)
        worker.failed.connect(lambda text: (_log.warning("Уменьшение фото контактов не удалось: %s", text), finished()))
        self._background_workers.append(worker)
        worker.start()

    def _contacts_signature(self) -> tuple[int, int] | None:
        """Отпечаток файла адресной книги: меняется при любой записи в неё,
        откуда бы та ни пришла (окно контакта, импорт, письмо, помощник)."""
        try:
            stat = Path(self.contacts_path).stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def _load_contacts(self) -> list[contact_store.Contact]:
        signature = self._contacts_signature()
        cached = getattr(self, "_contacts_cache", None)
        if cached is not None and signature is not None and cached[0] == signature:
            return cached[1]
        try:
            contacts = contact_store.list_contacts(self.contacts_path)
        except Exception:
            return []
        self._contacts_cache = (signature, contacts)
        return contacts

    def _show_contacts_page(self) -> None:
        self.pages.setCurrentIndex(2)
        self._update_mail_actions_enabled()
        # Раздел перестраивался заново при каждом переходе — вся книга из
        # базы и все фотографии (жалоба: "медленно переключается в раздел
        # контакты"). Теперь — только если книга менялась с прошлого раза.
        if self._contacts_view_signature is not None and self._contacts_view_signature == self._contacts_signature():
            return
        self.refresh_contacts_view()

    def refresh_contacts_view(self) -> None:
        self._contacts_view_signature = None
        try:
            contacts = self._load_contacts()
            if not contacts and self._contacts_signature() is not None:
                contacts = contact_store.list_contacts(self.contacts_path)  # пустая книга или сбой — перепроверяем
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось загрузить контакты", str(exc))
            return
        self._contacts_by_row = contacts
        self.contacts_table.setRowCount(len(contacts))
        for row, contact in enumerate(contacts):
            name_item = QTableWidgetItem(f"{contact.display_name} (группа)" if contact.is_group else contact.display_name)
            if contact.is_group:
                name_item.setIcon(_toolbar_icon("group"))
                name_item.setToolTip("Группа — список адресов рассылки")
            else:
                name_item.setIcon(_contact_avatar(contact))
                tooltip = "\n".join(
                    part for part in (contact.display_name, contact.title, contact.department, contact.organization) if part
                )
                name_item.setToolTip(tooltip)
            self.contacts_table.setItem(row, 0, name_item)
            self.contacts_table.setItem(row, 1, QTableWidgetItem(contact.title))
            self.contacts_table.setItem(row, 2, QTableWidgetItem(contact.department))
            emails_text = (
                f"{len(contact.emails)} адресов: " + ", ".join(contact.emails[:3]) + ("…" if len(contact.emails) > 3 else "")
                if contact.is_group else ", ".join(contact.emails)
            )
            self.contacts_table.setItem(row, 3, QTableWidgetItem(emails_text))
            self.contacts_table.setItem(row, 4, QTableWidgetItem(contact.phone))
            self.contacts_table.setItem(row, 5, QTableWidgetItem(contact.organization))
        self.contacts_card_list.blockSignals(True)
        try:
            self.contacts_card_list.clear()
            for row in range(len(contacts)):
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, row)
                self.contacts_card_list.addItem(item)
        finally:
            self.contacts_card_list.blockSignals(False)
        self._apply_contacts_filter()
        self._contacts_view_signature = self._contacts_signature()

    def _contact_haystack(self, contact) -> str:
        return " ".join([
            contact.display_name, " ".join(contact.emails), contact.organization,
            contact.phone, contact.title, contact.department,
        ]).casefold()

    def _contacts_match(self, text: str) -> bool:
        needle = text.strip().casefold()
        if not needle:
            return True
        return any(needle in self._contact_haystack(contact) for contact in self._contacts_by_row)

    def _apply_contacts_filter(self, *_args) -> None:
        # Та же правка раскладки, что и в поиске писем: «bdfyjd» → «иванов».
        needle = fix_search_layout(self.contacts_search_edit, self._contacts_match).strip().casefold()
        kind = self.contacts_kind_combo.currentIndex()  # 0 все, 1 люди, 2 группы
        shown = 0
        for row, contact in enumerate(self._contacts_by_row):
            visible = True
            if kind == 1 and contact.is_group:
                visible = False
            elif kind == 2 and not contact.is_group:
                visible = False
            if visible and needle:
                visible = needle in self._contact_haystack(contact)
            self.contacts_table.setRowHidden(row, not visible)
            card = self.contacts_card_list.item(row)
            if card is not None:
                card.setHidden(not visible)
            shown += int(visible)
        self.contacts_count_label.setText(f"{shown} из {len(self._contacts_by_row)}")

    def _set_contacts_view_mode(self, mode: str) -> None:
        self.contacts_view_mode = mode
        is_cards = mode == "cards"
        self.contacts_table.setVisible(not is_cards)
        self.contacts_card_list.setVisible(is_cards)
        self.contacts_view_table_action.setChecked(not is_cards)
        self.contacts_view_cards_action.setChecked(is_cards)
        try:
            save_contacts_view_mode(mode)
        except Exception:
            pass  # режим не запомнится между запусками — не критично

    def _on_contact_card_selected(self) -> None:
        items = self.contacts_card_list.selectedItems()
        row = items[0].data(Qt.ItemDataRole.UserRole) if items else None
        self.selected_contact = (
            self._contacts_by_row[row] if isinstance(row, int) and 0 <= row < len(self._contacts_by_row) else None
        )

    def _on_contact_card_double_clicked(self, item: QListWidgetItem) -> None:
        row = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(row, int) or not (0 <= row < len(self._contacts_by_row)):
            return
        self.on_contact_double_clicked(self.contacts_table.item(row, 0) or QTableWidgetItem())

    def on_contact_selection_changed(self) -> None:
        rows = self.contacts_table.selectionModel().selectedRows()
        self.selected_contact = self._contacts_by_row[rows[0].row()] if rows else None

    def on_contact_double_clicked(self, item: QTableWidgetItem) -> None:
        row = item.row() if item.row() >= 0 else (self.contacts_card_list.currentRow())
        contact = self._contacts_by_row[row]
        dialog = ContactDialog(self, contact=contact, contacts=self._contacts_by_row)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            contact_store.save_contact(self.contacts_path, dialog.to_contact())
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось сохранить контакт", str(exc))
            return
        self.refresh_contacts_view()

    def on_new_contact(self) -> None:
        dialog = ContactDialog(self, contacts=self._contacts_by_row)
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

        # Жалоба: "загрузка идёт без видимого процесса — нужно окно с
        # отображением хода загрузки": импорт большого файла — в фоне, с
        # окном ожидания вместо замершего интерфейса.
        progress = QProgressDialog(f"Импорт контактов из {Path(path_str).name}…", None, 0, 0, self)
        progress.setWindowTitle("Импорт контактов")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)

        def run_import() -> int:
            data = Path(path_str).read_bytes()
            return importer(self.contacts_path, data)

        worker = _CallableWorker(run_import, parent=self)

        def on_success(count: object) -> None:
            progress.close()
            self._background_workers.remove(worker)
            self.refresh_contacts_view()
            self.statusBar().showMessage(f"Импортировано контактов: {count}", 5000)

        def on_failure(error_text: str) -> None:
            progress.close()
            self._background_workers.remove(worker)
            QMessageBox.critical(self, "Ошибка импорта", error_text)

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._background_workers.append(worker)
        worker.start()

    def _save_event_from_dialog(
        self, dialog: EventDialog, *, existing: calendar_store.Event | None, scope: str | None = None
    ) -> None:
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
            # Граница — начало СЕГОДНЯШНЕГО дня, а не точный текущий момент
            # (раньше сравнивалось с now() без обрезки времени) — сама
            # защита предназначалась против назначения встречи ЗАДНИМ ЧИСЛОМ
            # (см. комментарий выше), а не против конкретного часа. С точным
            # now() любой клик/двойной клик по ещё не наступившему сегодня
            # часу (например, 9 утра, пока сейчас день) на пустом месте
            # сетки недели молча отклонялся этой проверкой — воспринималось
            # как "событие не создаётся" без какой-либо связи с выбранным
            # календарём, хотя дело было именно в времени.
            past_cutoff = datetime.now().astimezone().replace(
                hour=0, minute=0, second=0, microsecond=0
            ).astimezone(timezone.utc)
            if start < past_cutoff:
                QMessageBox.warning(self, "Прошедшее время", "Нельзя запланировать встречу на прошедшую дату/время.")
                return

        attendee_emails = dialog.attendee_emails()
        recurrence_rule = dialog.recurrence_rule()
        exdates: list[datetime] = []
        if existing is not None:
            if scope is None or not calendar_store.is_series(self.calendar_path, existing):
                scope = self._ask_series_scope(existing, "Изменить")
            if scope is None:
                return
            if scope == self.SCOPE_ONE:
                existing = calendar_store.detach_occurrence(self.calendar_path, existing)
                recurrence_rule = None  # отдельный день серии сам не повторяется
            else:
                master = calendar_store.get_event(self.calendar_path, calendar_store.series_uid(existing.uid))
                if master is not None:
                    # В окне был показан один день серии — время всей серии
                    # сдвигается на ту же разницу, на которую изменили этот день.
                    start, end = master.dtstart + (start - existing.dtstart), master.dtend + (end - existing.dtend)
                    existing = master
                exdates = list(existing.exdates)
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
            recurrence_rule=recurrence_rule,
            exdates=exdates,
            color=dialog.color(),
            calendar_id=dialog.calendar_id(),
            attendees=[calendar_store.Attendee(email=addr) for addr in attendee_emails],
            attachments=list(dialog.attachments),
            remind_minutes=dialog.remind_minutes(),
            remind_mode=dialog.remind_mode(),
        )
        calendar_store.save_event(self.calendar_path, event, needs_push=True)
        if self._is_exchange_calendar(event.calendar_id):
            # Приглашения Exchange рассылает сам, когда встреча уходит на
            # сервер — своё письмо сверху дало бы участникам дубль.
            self._schedule_calendar_sync()
        else:
            self._send_request_to_attendees(event, subject_prefix="Приглашение", body_prefix="Вас приглашают на встречу")
            if self._is_server_calendar(event.calendar_id):
                self._schedule_calendar_sync()
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

    SCOPE_ONE = "one"
    SCOPE_ALL = "all"

    def _ask_series_scope(self, event: calendar_store.Event, action: str) -> str | None:
        """Для дня повторяющейся встречи: изменить только его или всю серию.
        Не серия — сразу «вся встреча». None — пользователь передумал."""
        if not calendar_store.is_series(self.calendar_path, event):
            return self.SCOPE_ALL
        series = calendar_store.get_event(self.calendar_path, calendar_store.series_uid(event.uid))
        box = QMessageBox(self)
        box.setWindowTitle("Повторяющаяся встреча")
        box.setText(f"«{event.summary}» — повторяющаяся встреча. {action}:")
        one = box.addButton("Только этот день", QMessageBox.ButtonRole.AcceptRole)
        whole = None
        # Exchange присылает серию развёрнутой по дням — основной записи с
        # правилом у нас нет, но всю серию можно изменить на сервере
        # (жалоба: «не даёт перенести все повторения, только один день»).
        exchange_series = calendar_store.is_instance_uid(event.uid) and self._is_exchange_calendar(event.calendar_id)
        if series is not None and series.recurrence_rule or exchange_series or action.startswith("Отменить"):
            whole = box.addButton("Всю серию", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is one:
            return self.SCOPE_ONE
        if whole is not None and box.clickedButton() is whole:
            return self.SCOPE_ALL
        return None

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
        # Граница — начало сегодняшнего дня, не точный текущий момент (та же
        # правка и по той же причине, что и в _save_event_from_dialog) —
        # перенос встречи на ещё не наступивший сегодня час не должен
        # считаться "переносом в прошлое".
        past_cutoff = datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(timezone.utc)
        if new_start < past_cutoff:
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

        scope = self._ask_series_scope(event, "Перенести")
        if scope is None:
            self.refresh_calendar_view()
            return
        master_here = calendar_store.get_event(self.calendar_path, calendar_store.series_uid(event.uid))
        if scope == self.SCOPE_ALL and master_here is None and self._is_exchange_calendar(event.calendar_id)                 and calendar_store.is_instance_uid(event.uid):
            self._shift_exchange_series(event, delta, when_text)
            return
        if scope == self.SCOPE_ONE:
            target = calendar_store.detach_occurrence(self.calendar_path, event)
            updated = calendar_store.reschedule_event(self.calendar_path, target.uid, new_start, new_end)
        else:
            master = master_here or event
            # Серия сдвигается на тот же промежуток, на который перетащили день.
            updated = calendar_store.reschedule_event(
                self.calendar_path, master.uid, master.dtstart + delta, master.dtend + delta
            )
        if updated is None:
            self.refresh_calendar_view()
            return
        if self._is_exchange_calendar(updated.calendar_id):
            self._schedule_calendar_sync()  # обновление участникам разошлёт Exchange
        else:
            self._send_request_to_attendees(updated, subject_prefix="Перенесено", body_prefix="Встреча перенесена:")
            if self._is_server_calendar(updated.calendar_id):
                self._schedule_calendar_sync()
        self.refresh_calendar_view()
        self.statusBar().showMessage(f"Перенесено: «{updated.summary}» → {when_text}", 5000)

    def _shift_exchange_series(self, event: calendar_store.Event, delta: timedelta, when_text: str) -> None:
        """Перенос всей серии Exchange на сервере — в фоне: запрос к серверу
        может идти секунды, окно не должно замирать. Локальные дни серии
        обновит следующая синхронизация календаря."""
        session = self._ews_session_for_calendar()
        calendar = self._calendar_by_id(event.calendar_id)
        if session is None or calendar is None:
            QMessageBox.warning(self, "Перенос серии", "Нет подключения к Exchange — серию перенести нельзя.")
            self.refresh_calendar_view()
            return
        email = getattr(getattr(session, "account", None), "email", "") or ""
        server = _ExchangeCalendarServer(session, email, calendar.caldav_url.strip())
        worker = _CallableWorker(lambda: server.shift_series(event.uid, delta), parent=self)
        self.statusBar().showMessage(f"Переношу серию «{event.summary}»…")

        def done(_result: object = None) -> None:
            if worker in self._background_workers:
                self._background_workers.remove(worker)
            self.statusBar().showMessage(f"Серия «{event.summary}» перенесена (этот день теперь — {when_text})", 6000)
            self._schedule_calendar_sync(500)

        def failed(error_text: str) -> None:
            if worker in self._background_workers:
                self._background_workers.remove(worker)
            QMessageBox.warning(self, "Перенос серии", error_text)
            self.refresh_calendar_view()

        worker.succeeded.connect(done)
        worker.failed.connect(failed)
        self._background_workers.append(worker)
        worker.start()

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
        scope = self._ask_series_scope(event, "Отменить")
        if scope is None:
            return
        self._cancel_event_now(event, scope)

    def _cancel_event_now(self, event: calendar_store.Event, scope: str) -> None:
        """Отмена без вопросов: подтверждение и «день или серия» уже получены
        (окном или голосом через помощника)."""
        if scope == self.SCOPE_ONE and not calendar_store.is_series(self.calendar_path, event):
            scope = self.SCOPE_ALL
        if scope == self.SCOPE_ONE:
            # Отменяется один день: участникам — CANCEL с RECURRENCE-ID,
            # на сервер — исключение этого дня из серии.
            event = calendar_store.detach_occurrence(self.calendar_path, event)
        elif calendar_store.is_series(self.calendar_path, event):
            master = calendar_store.get_event(self.calendar_path, calendar_store.series_uid(event.uid))
            event = master or replace(event, uid=calendar_store.series_uid(event.uid))

        attendee_emails = [a.email for a in event.attendees]
        exchange_calendar = self._is_exchange_calendar(event.calendar_id)
        if attendee_emails and self.smtp_account and not exchange_calendar:
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

        if scope == self.SCOPE_ONE:
            calendar_store.delete_event(self.calendar_path, event.uid)
            calendar_store.add_exdate(
                self.calendar_path, calendar_store.series_uid(event.uid), calendar_store.instance_start(event.uid)
            )
        else:
            calendar_store.delete_series(self.calendar_path, event.uid)
        if self._is_server_calendar(event.calendar_id):
            # Удаление должно дойти до сервера, иначе синхронизация вернёт
            # встречу (для Exchange отмену участникам разошлёт сам Exchange).
            calendar_store.remember_server_delete(self.calendar_path, event.uid, event.calendar_id)
            self._schedule_calendar_sync()
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
        # Высота — по числу вложений (до трёх строк), а не постоянные 110 px:
        # одно вложение растягивало панель на пустое место (жалоба: "опять
        # растянул поле вложений").
        row_height = self.attachments_list.sizeHintForRow(0)
        if row_height <= 0:
            row_height = QFontMetrics(self.attachments_list.font()).height() + 8
        rows = min(len(self.current_attachments), 3)
        frame = 2 * self.attachments_list.frameWidth() + 4
        self.attachments_list.setFixedHeight(rows * row_height + frame)
        self.attachments_list.show()

    def on_open_attachment(self, item: QListWidgetItem) -> None:
        # Вложенное письмо открывается своим окном, остальное — программой,
        # назначенной этому типу файла в системе (см. open_attachment_payload).
        open_attachment_payload(self, self.current_attachments[self.attachments_list.row(item)])

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

    def _my_person_data(self) -> dict[str, str]:
        """Данные сотрудника для фирменной подписи — из его карточки в
        адресной книге (по адресу текущей учётной записи)."""
        account = self.account
        email = (getattr(account, "email", "") or getattr(account, "username", "")) if account is not None else ""
        person = {"email": email if "@" in email else ""}
        if not email:
            return person
        try:
            contact = contact_store.find_by_email(self.contacts_path, email)
        except Exception as exc:
            _log.debug("Карточка сотрудника для подписи не найдена: %s", exc)
            contact = None
        if contact is not None:
            person.update(
                name=contact.display_name, title=contact.title, department=contact.department,
                phone=contact.phone, organization=contact.organization,
            )
        return person

    def _compose_signatures(self) -> list[Signature]:
        """Подписи для окна письма; фирменная подпись оформления организации
        обновляется по шаблону и данным сотрудника перед каждым письмом.
        Если своей подписи по умолчанию нет — фирменная становится ей."""
        brand = app_theme.current_brand()
        if brand is None or not brand.signature_html.strip():
            return self.signatures
        signature_id = f"brand-{brand.id}"
        body = branding.render_signature(brand, self._my_person_data())
        existing = next((sig for sig in self.signatures if sig.id == signature_id), None)
        changed = False
        if existing is None:
            self.signatures.append(Signature(id=signature_id, name=f"Фирменная: {brand.name}", body_html=body))
            changed = True
        elif existing.body_html != body:
            existing.body_html = body
            changed = True
        try:
            if changed:
                save_signatures(self.signatures)
            if not self.default_signature_id:
                self.default_signature_id = signature_id
                save_default_signature_id(signature_id)
        except Exception as exc:
            _log.warning("Фирменная подпись не сохранена: %s", exc)
        return self.signatures

    def on_compose(self) -> None:
        if not self.smtp_account:
            QMessageBox.warning(
                self,
                "Нет исходящей почты",
                "Сначала подключитесь и укажите сервер SMTP в настройках учётной записи.",
            )
            return
        dialog = ComposeDialog(
            self,
            title="Новое письмо",
            contacts=self._load_contacts(),
            signatures=self._compose_signatures(),
            default_signature_id=self.default_signature_id,
            greeting=greeting_text(load_greeting_mode()),
        )
        self._exec_compose(dialog)

    def on_reply(self) -> None:
        if not self.selected_summary:
            QMessageBox.warning(self, "Нет письма", "Выберите письмо, на которое хотите ответить.")
            return
        self._start_reply(self.selected_summary, self.current_body)

    def on_reply_all(self) -> None:
        if not self.selected_summary:
            QMessageBox.warning(self, "Нет письма", "Выберите письмо, на которое хотите ответить.")
            return
        self._start_reply(self.selected_summary, self.current_body, content=self.current_content, reply_all=True)

    def _start_reply(
        self,
        summary: MessageSummary,
        body_text: str,
        *,
        content: MessageContent | None = None,
        reply_all: bool = False,
    ) -> None:
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

        # Жалоба: "нельзя ответить всем участникам переписки, только
        # первому" — "Ответить всем": отправитель + все из "Кому" в поле
        # "Кому", все из "Копия" — в "Копия"; себя и дубли убираем.
        to_list = [summary.sender_email] if summary.sender_email else []
        cc_list: list[str] = []
        if reply_all and content is not None:
            skip = {addr.lower() for addr in (summary.sender_email, self.account.username if self.account else "") if addr}

            def others(value: str) -> list[str]:
                return [addr for _name, addr in getaddresses([value or ""]) if addr and addr.lower() not in skip]

            to_list += others(content.to)
            cc_list = [addr for addr in others(content.cc) if addr.lower() not in {a.lower() for a in to_list}]

        dialog = ComposeDialog(
            self,
            title="Ответить всем" if reply_all else "Ответить",
            to=", ".join(dict.fromkeys(to_list)),
            cc=", ".join(dict.fromkeys(cc_list)),
            subject=subject,
            body=body,
            contacts=self._load_contacts(),
            signatures=self._compose_signatures(),
            default_signature_id=self.default_signature_id,
            greeting=greeting_text(load_greeting_mode()),
        )
        reply_source = (
            (self.active_source, self.current_folder, summary.uid)
            if self.active_source is not None and self.current_folder is not None
            else None
        )
        self._exec_compose(dialog, in_reply_to=summary.message_id or None, reply_source=reply_source)

    def on_forward(self) -> None:
        if not self.selected_summary or not self.active_source or not self.current_folder:
            QMessageBox.warning(self, "Нет письма", "Выберите письмо, которое хотите переслать.")
            return
        summary = self.selected_summary
        self._with_message_content(summary, lambda content: self._start_forward(summary, content))

    def _with_message_content(self, summary: MessageSummary, on_ready: Callable[[MessageContent], None]) -> None:
        """Тело письма для действия (переслать, открыть в окне): если это
        уже показанное письмо — берём готовое, без повторной загрузки;
        иначе грузим в фоне с индикатором. Жалоба: "при попытке переслать
        сначала всё подвисает" — загрузка шла в потоке интерфейса и ждала
        очередь IMAP, занятую фоновой синхронизацией."""
        if self.selected_summary is summary and self.current_content is not None:
            on_ready(self.current_content)
            return
        source = self.active_source
        folder = self.current_folder
        if source is None or not folder:
            return
        self._set_busy("Загрузка письма…")
        worker = _CallableWorker(source.message_content, folder, summary.uid, parent=self)

        def finish() -> None:
            self._set_busy(None)
            if worker in self._background_workers:
                self._background_workers.remove(worker)

        def on_success(content: object) -> None:
            finish()
            on_ready(content)

        def on_failure(error_text: str) -> None:
            finish()
            QMessageBox.critical(self, "Не удалось загрузить письмо", error_text)

        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._background_workers.append(worker)
        worker.start()

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
        # Жалоба: "при пересылке письма пропадает картинка" — раньше
        # пересылка ВСЕГДА брала только content.text (простой текст) и
        # никогда не передавала ни HTML, ни content.inline_images дальше
        # в ComposeDialog. Для писем, у которых текстовая альтернатива —
        # это не читаемый текст, а плейсхолдеры вида "[cid:image001.png@...]"
        # (так Outlook формирует plain-text alternative для писем с
        # картинками), пересылка получалась буквально с "хвостами" cid
        # вместо картинок — и результат ещё и мог не пройти проверку
        # содержимого на стороне корпоративного сервера (см. жалобу про
        # 500 Message rejected). Если у письма есть HTML — пересылаем его
        # как есть, вместе со встроенными картинками; иначе как раньше.
        if content.html:
            # Шапка с явными цветами: пересылаемый HTML обычно несёт свой
            # белый фон, а цвет текста редактора в тёмной теме светлый —
            # шапка без своих цветов была не видна (жалоба: "заголовок
            # пересылаемого письма не видно").
            forward_header_html = (
                "<br><br>"
                '<div style="background:#f1f3f4;color:#202124;padding:6px 10px;'
                'border-left:3px solid #9aa0a6;font-family:sans-serif;">'
                "---------- Пересланное сообщение ----------<br>"
                f"От: {html.escape(summary.sender)} &lt;{html.escape(summary.sender_email)}&gt;<br>"
                f"Дата: {html.escape(summary.date)}<br>"
                f"Тема: {html.escape(summary.subject)}"
                "</div><br>"
            )
            # В редактор — упрощённая разметка: вложенные таблицы рассылок
            # вешали окно на десятки минут (см. html_cleanup). Слишком
            # тяжёлое письмо пересылается текстом.
            simplified = html_cleanup.simplify_html_for_editor(content.html)
            if len(simplified) <= html_cleanup.MAX_EDITOR_HTML_BYTES:
                body_kwargs = {
                    "body_html": forward_header_html + simplified,
                    "inline_images": content.inline_images,
                }
            else:
                _log.warning("Пересылка: HTML %d байт слишком велик для редактора — текстом", len(simplified))
                body_kwargs = {
                    "body": f"\n\n---------- Пересланное сообщение ----------\n"
                    f"От: {summary.sender} <{summary.sender_email}>\nДата: {summary.date}\nТема: {summary.subject}\n\n"
                    + html_to_text(content.html)
                }
        else:
            forward_header = (
                f"---------- Пересланное сообщение ----------\n"
                f"От: {summary.sender} <{summary.sender_email}>\n"
                f"Дата: {summary.date}\n"
                f"Тема: {summary.subject}\n"
            )
            body_kwargs = {"body": f"\n\n{forward_header}\n{content.text}"}

        def build_dialog(kwargs: dict) -> ComposeDialog:
            return ComposeDialog(
                self,
                title="Переслать",
                subject=subject,
                contacts=self._load_contacts(),
                attachments=[
                    OutgoingAttachment(
                        filename=a.filename, content_type=a.content_type, payload=a.payload
                    )
                    for a in content.attachments
                ],
                signatures=self._compose_signatures(),
                default_signature_id=self.default_signature_id,
                greeting=greeting_text(load_greeting_mode()),
                **kwargs,
            )

        dialog = build_dialog(body_kwargs)
        if "body_html" in body_kwargs and "http" in body_kwargs["body_html"]:
            # Внешние картинки (<img src="https://…">) скачиваются в фоне и
            # подставляются в УЖЕ открытое окно (жалоба: "сначала окно о
            # загрузке изображений и только потом окно пересылки — зачем
            # так?"). Если сервер картинок недоступен — письмо уйдёт с
            # внешними ссылками, как было.
            worker = _CallableWorker(
                remote_images.embed_remote_images, body_kwargs["body_html"], body_kwargs.get("inline_images"), parent=self
            )

            def on_success(result: object) -> None:
                if worker in self._background_workers:
                    self._background_workers.remove(worker)
                html_with_cids, images = result
                try:
                    dialog.apply_embedded_images(html_with_cids, images)
                except RuntimeError:
                    pass  # окно уже закрыто

            def on_failure(_error_text: str) -> None:
                if worker in self._background_workers:
                    self._background_workers.remove(worker)

            worker.succeeded.connect(on_success)
            worker.failed.connect(on_failure)
            self._background_workers.append(worker)
            worker.start()
        self._exec_compose(dialog)

    def _exec_compose(
        self,
        dialog: ComposeDialog,
        *,
        in_reply_to: str | None = None,
        source_draft: tuple[str, int] | None = None,
        reply_source: tuple[object, str, int] | None = None,
    ) -> None:
        """Открыть окно письма немодально. Раньше — dialog.exec(): модальный
        вложенный цикл событий блокировал главное окно, а окно письма,
        открытое по каналу управления (голосовой помощник) поверх другого,
        вкладывало циклы друг в друга — главное окно «зависало» с невидимым
        окном письма (жалоба: "отправил письмо — зависло"). Теперь окна
        писем независимы, их может быть несколько, почту можно читать,
        пока пишешь. Результат обрабатывается по сигналу finished."""
        self._compose_windows.append(dialog)
        # Учётная запись, в которой начали письмо. Окно немодальное: пока
        # пишешь, можно щёлкнуть папку другой записи, и раньше письмо уходило
        # через ту, что открыта в момент «Отправить» — с чужим отправителем и
        # чужими правилами замены доменов.
        compose_account_key = self._mailbox_key()

        def on_finished(result: int) -> None:
            if dialog in self._compose_windows:
                self._compose_windows.remove(dialog)
            try:
                if result == QDialog.DialogCode.Accepted:
                    with self._account_context(compose_account_key):
                        self._on_compose_accepted(
                            dialog, in_reply_to=in_reply_to, source_draft=source_draft, reply_source=reply_source,
                            account_key=compose_account_key,
                        )
            finally:
                dialog.deleteLater()

        dialog.finished.connect(on_finished)
        dialog.setModal(False)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _on_compose_accepted(
        self,
        dialog: ComposeDialog,
        *,
        in_reply_to: str | None,
        source_draft: tuple[str, int] | None,
        reply_source: tuple[object, str, int] | None,
        account_key: str | None = None,
    ) -> None:
        if dialog.save_as_draft_requested():
            self._save_draft(dialog, source_draft=source_draft)
            return

        recipients = dialog.recipients()
        if not recipients:
            QMessageBox.warning(self, "Нет получателя", "Укажите хотя бы одного получателя.")
            return
        recipients, cc_recipients, bcc_recipients = (
            self._apply_domain_rewrites(recipients),
            self._apply_domain_rewrites(dialog.cc_recipients()),
            self._apply_domain_rewrites(dialog.bcc_recipients()),
        )

        # Колонтитул организации (например, о конфиденциальности) — только в
        # отправляемое письмо, не в черновик: иначе он копился бы при каждом
        # сохранении.
        branded_html, branded_text = branding.with_footer(app_theme.current_brand(), dialog.body_html(), dialog.body())
        message = OutgoingMessage(
            sender=self.account.username,
            to=recipients,
            cc=cc_recipients,
            bcc=bcc_recipients,
            subject=dialog.subject(),
            body=branded_text,
            html_body=branded_html,
            inline_images=dialog.inline_images(),
            in_reply_to=in_reply_to,
            attachments=dialog.attachments,
        )

        def after_send() -> None:
            with self._account_context(account_key):
                self._after_send(message, source_draft, reply_source)

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

    def _after_send(self, message, source_draft, reply_source) -> None:
        """Копия в «Отправленные», удаление черновика, отметка «отвечено» —
        в учётной записи, из которой письмо отправлено."""
        self._append_sent_copy(message)
        if source_draft is not None:
            self._delete_draft(source_draft)
        if reply_source is not None:
            self._mark_summary_answered(*reply_source)
        # "Отправленные" раньше обновлялись только по кнопке "Обновить"
        # (жалоба: "папка отправленные обновляется только принудительно,
        # должна сразу после отправки") — folder_summaries() при обычном
        # переключении на папку безусловно доверяет кэшу, не проверяя
        # exists_count на сервере, поэтому только что добавленное копией
        # письмо не появлялось само по себе. Мы только что сами
        # добавили его в "Отправленные" (см. _append_sent_copy) —
        # обновляем кэш этой папки сразу, не дожидаясь следующего
        # ручного "Обновить" или счастливого совпадения exists_count.
        if self.account_protocol == "imap" and self.sent_folder_name and self.mailbox is not None:
            # В фоне: сетевой запрос в потоке интерфейса подвешивал окно
            # сразу после отправки.
            self._sync_folders_async([self.sent_folder_name])

    def _apply_domain_rewrites(self, addresses: list[str]) -> list[str]:
        """Замена домена получателей по правилам отправляющей учётной записи
        (переезд между почтовыми системами). Что заменилось — в журнал и в
        строку состояния."""
        if not addresses:
            return addresses
        key = self._mailbox_key()
        try:
            rules = address_rules.parse_rules(load_domain_rewrites(key)) if key else []
        except Exception as exc:
            _log.warning("Правила замены домена не прочитаны: %s", exc)
            rules = []
        if not rules:
            return addresses
        rewritten = address_rules.rewrite_recipients(addresses, rules)
        changes = address_rules.describe_changes(addresses, rewritten)
        if changes:
            _log.info("Замена домена получателей: %s", "; ".join(changes))
            self.statusBar().showMessage("Адреса получателей заменены по правилам: " + "; ".join(changes), 8000)
        return rewritten

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
            html_body=dialog.body_html(),
            inline_images=dialog.inline_images(),
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
        self._sync_folders_async([self.drafts_folder_name])

    def _delete_draft(self, source_draft: tuple[str, int]) -> None:
        if self.mailbox is None:
            return
        try:
            self.mailbox.delete_messages(source_draft[0], [source_draft[1]])
        except Exception:
            pass  # письмо уже отправлено — то, что старая черновая копия не убралась, не критично

    # ------------------------------------------------------------------
    # Локальный канал управления (ipc_server.py)
    #
    # Всё, что ОТПРАВЛЯЕТ что-либо наружу (письмо, приглашение, отмену
    # встречи), обязано пройти через обычное экранное подтверждение —
    # ipc_* методы только ОТКРЫВАЮТ штатный диалог с заполненными полями,
    # а «Отправить»/«Сохранить»/«Да» жмёт человек. Отдельного пути
    # «отправить сразу, без подтверждения» здесь нет и не должно
    # появляться: команда приходит из распознанной речи, ошибиться она
    # может как угодно.
    #
    # Немедленно, без подтверждения, выполняются только команды, которые
    # ничего не шлют наружу: focus, list_mail_rules и apply_mail_rules
    # (перекладывание писем между папками того же ящика).
    # ------------------------------------------------------------------

    def _ipc_later(self, action: Callable[[], None]) -> None:
        """Отложить показ модального диалога до выхода из обработчика сокета.

        QDialog.exec() крутит вложенный цикл событий и не возвращается, пока
        окно не закроют. Вызванный прямо из readyRead он задержал бы запись
        ответа клиенту на всё время, что открыт диалог, и позволил бы
        обработать внутри себя следующие запросы рекурсивно.
        singleShot(0) ставит вызов в очередь того же (GUI) потока — он
        выполнится сразу после того, как ответ уже отправлен."""
        QTimer.singleShot(0, action)

    def ipc_focus(self, section: str | None = None) -> None:
        """Окно на передний план; section — раздел: mail, calendar, contacts
        («открой календарь» голосом)."""
        self.showNormal()
        self.raise_()
        self.activateWindow()
        pages = {
            "mail": (self.mail_mode_action, self._show_mail_page),
            "calendar": (self.calendar_mode_action, self._show_calendar_page),
            "contacts": (self.contacts_mode_action, self._show_contacts_page),
        }
        if section in pages:
            action, show = pages[section]
            action.setChecked(True)
            show()

    def ipc_show_event(self, uid: str, start: datetime | None = None) -> bool:
        """Календарь на неделе встречи, встреча выделена и открыта — клик
        по встрече в напоминалке. start нужен для серии: у всех её дней
        один uid, а открыть надо тот день, о котором напомнили. Окно
        встречи открывается после ответа на команду (модальный диалог
        иначе держал бы ответ, и напоминалка сочла бы почту закрытой).
        False — встречи в календаре нет: тогда просто открыт календарь."""
        self.ipc_focus(section="calendar")
        event = self._find_calendar_event(uid, start)
        if event is None:
            return False
        day = event.dtstart.astimezone().date()
        self.calendar_selected_day = day
        self.calendar_week_start = week_start_for(day)
        self.calendar_month_anchor = day.replace(day=1)
        self.refresh_calendar_view()
        self.selected_calendar_event = event
        self._apply_calendar_selection_highlight()
        self._calendar_scrolled_to_now = True
        self._calendar_scroll.verticalScrollBar().setValue(
            self.calendar_week_grid.scroll_position_for(event.dtstart)
        )
        self._ipc_later(lambda: self._on_calendar_event_double_clicked(event))
        return True

    def _find_calendar_event(self, uid: str, start: datetime | None) -> calendar_store.Event | None:
        """Встреча по uid; для серии — день, ближайший к start (если start
        не задан — ближайший к текущему моменту)."""
        moment = start or datetime.now(timezone.utc)
        try:
            events = calendar_store.list_events(
                self.calendar_path, start=moment - timedelta(days=2), end=moment + timedelta(days=2)
            )
        except Exception as exc:
            _log.warning("Встреча для показа не прочитана: %s", exc)
            return None
        matches = [event for event in events if event.uid == uid]
        if not matches:
            return None
        return min(matches, key=lambda event: abs((event.dtstart - moment).total_seconds()))

    def ipc_compose_email(
        self, *, to: str, subject: str = "", body: str = "", cc: str = "", bcc: str = ""
    ) -> None:
        if not self.smtp_account:
            raise RuntimeError(
                "Исходящая почта не настроена: подключитесь и укажите сервер SMTP в настройках."
            )
        # Тот же самый диалог и те же аргументы, что у кнопки «Написать
        # письмо…» (on_compose) — отличаются только заранее заполненные поля.
        dialog = ComposeDialog(
            self,
            title="Новое письмо",
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            contacts=self._load_contacts(),
            signatures=self._compose_signatures(),
            default_signature_id=self.default_signature_id,
            greeting=greeting_text(load_greeting_mode()),
        )
        self.ipc_focus()
        self._ipc_later(lambda: self._exec_compose(dialog))

    def _ipc_default_calendar_id(self) -> str:
        """Куда голосовой помощник кладёт продиктованную встречу: в основной
        календарь (локальный — это задачи, а не совещания). Календарь,
        выбранный в списке слева, перебивает — пользователь указал явно."""
        item = self.calendars_list.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        if value:
            return value
        try:
            return calendar_store.default_calendar_id(self.calendar_path)
        except Exception:
            return calendar_store.DEFAULT_CALENDAR_ID

    def _ipc_event_dialog(self, event: calendar_store.Event, *, title: str) -> EventDialog:
        """EventDialog с уже заполненными полями.

        Диалог умеет заполняться только из готового Event (аргумент event=),
        поэтому и для НОВОЙ встречи собираем черновой Event — ровно так же,
        как это давно делает on_copy_event: конструирует диалог из события,
        меняет заголовок окна и сохраняет потом с existing=None."""
        dialog = EventDialog(
            self,
            event=event,
            my_email=self.account.username if self.account else "",
            contacts=self._load_contacts(),
            calendars=self._load_calendars(),
        )
        dialog.setWindowTitle(title)
        return dialog

    def _ipc_exec_event_dialog(
        self, dialog: EventDialog, *, existing: calendar_store.Event | None
    ) -> None:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_event_from_dialog(dialog, existing=existing)

    def ipc_create_event(
        self,
        *,
        summary: str,
        start: datetime,
        duration_minutes: int,
        participants: list[str] | None = None,
        description: str = "",
        location: str = "",
        calendar: str | None = None,
    ) -> None:
        if not self.account:
            raise RuntimeError("Нет учётной записи: сначала подключитесь к почте в настройках.")
        calendar_id = self._ipc_default_calendar_id()
        if calendar:
            choices = [
                calendar_names.CalendarChoice(id=cal.id, name=cal.name, source=cal.source_type)
                for cal in self._load_calendars()
            ]
            calendar_id = calendar_names.match_calendar(calendar, choices).id
        draft = calendar_store.Event(
            uid=calendar_store.new_uid(),
            summary=summary,
            dtstart=start,
            dtend=start + timedelta(minutes=duration_minutes),
            description=description,
            location=location,
            organizer_email=self.account.username,
            organizer_name=self.account.username,
            is_organizer=True,
            my_participation="accepted",
            calendar_id=calendar_id,
            attendees=[calendar_store.Attendee(email=email) for email in (participants or [])],
        )
        dialog = self._ipc_event_dialog(draft, title="Новая встреча")
        self.ipc_focus()
        self._ipc_later(lambda: self._ipc_exec_event_dialog(dialog, existing=None))

    def ipc_update_event(
        self,
        uid: str,
        *,
        summary: str | None = None,
        start: datetime | None = None,
        duration_minutes: int | None = None,
        participants: list[str] | None = None,
        description: str | None = None,
        location: str | None = None,
    ) -> None:
        if not self.account:
            raise RuntimeError("Нет учётной записи: сначала подключитесь к почте в настройках.")
        existing = calendar_store.get_event(self.calendar_path, uid)
        if existing is None:
            raise LookupError(f"Встреча с UID {uid} не найдена в календаре.")
        if not existing.is_organizer:
            # Тот же принцип, что у двойного клика по чужой встрече в сетке:
            # редактировать можно только то, что организовали вы сами.
            raise PermissionError("Изменить можно только встречу, которую организовали вы сами.")
        new_start = start if start is not None else existing.dtstart
        if duration_minutes is not None:
            new_end = new_start + timedelta(minutes=duration_minutes)
        else:
            # Длительность не задана — сохраняем прежнюю, а не «час по умолчанию»:
            # перенос встречи не должен молча менять её продолжительность.
            new_end = new_start + (existing.dtend - existing.dtstart)
        merged = replace(
            existing,
            summary=summary if summary is not None else existing.summary,
            description=description if description is not None else existing.description,
            location=location if location is not None else existing.location,
            dtstart=new_start,
            dtend=new_end,
            attendees=(
                [calendar_store.Attendee(email=email) for email in participants]
                if participants is not None
                else existing.attendees
            ),
        )
        dialog = self._ipc_event_dialog(merged, title="Изменить встречу")
        self.ipc_focus()
        self._ipc_later(lambda: self._ipc_exec_event_dialog(dialog, existing=existing))

    def ipc_find_events(self, *, subject: str | None = None, on_date: date | None = None) -> list[dict]:
        """Поиск встреч по подстроке темы и/или дню.

        Нужен потому, что update_event/cancel_event требуют uid, а голосовая
        команда «перенеси встречу ...» знает только тему (и, может быть,
        старую дату) — этим методом голосовая сторона сначала находит uid,
        а уже потом вызывает update_event. Ничего не отправляет наружу и не
        трогает окно — читается сразу, без QTimer.singleShot, как
        list_mail_rules/apply_mail_rules."""
        if on_date is not None:
            day_start = datetime.combine(on_date, time.min).astimezone(timezone.utc)
            day_end = day_start + timedelta(days=1)
            events = calendar_store.list_events(self.calendar_path, start=day_start, end=day_end)
        else:
            events = calendar_store.list_events(self.calendar_path)
        needle = (subject or "").strip().lower()
        if needle:
            events = [event for event in events if needle in event.summary.lower()]
        return [
            {
                "uid": event.uid,
                "summary": event.summary,
                "start": event.dtstart.isoformat(),
                "end": event.dtend.isoformat(),
                "calendar_id": event.calendar_id,
                "location": event.location,
                "is_organizer": event.is_organizer,
                "status": event.status,
                "recurring": calendar_store.is_series(self.calendar_path, event),
            }
            for event in events
        ]

    # -- Пошаговая форма встречи (event_form_* в ipc_server.py) ----------
    #
    # Голосовое заполнение «на открытом окне»: ipc_event_form_open показывает
    # обычный EventDialog и держит на него ссылку, пока он открыт;
    # ipc_event_form_set меняет поля прямо в виджетах (человек видит каждое
    # изменение), ipc_event_form_save/cancel нажимают «Сохранить»/«Отмена».
    # Сохранение — тем же _save_event_from_dialog, что и у кнопки, со всеми
    # его проверками и рассылкой приглашений.
    #
    # Обработчики канала выполняются и внутри вложенного цикла exec()
    # открытого диалога (сигналы сокета обрабатываются там же), поэтому
    # трогать его виджеты отсюда безопасно — это тот же GUI-поток.

    def _ipc_exec_event_form(
        self, dialog: EventDialog, existing: calendar_store.Event | None, scope: str | None = None
    ) -> None:
        self._ipc_event_form = (dialog, existing)
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            self._ipc_event_form = None
        if accepted:
            self._save_event_from_dialog(dialog, existing=existing, scope=scope)

    def _ipc_occurrence(self, uid: str, start: datetime | None) -> calendar_store.Event:
        """Встреча по uid, а для дня серии — именно этот день (start — его
        начало, как вернул find_events)."""
        existing = calendar_store.get_event(self.calendar_path, uid)
        if existing is None:
            raise LookupError(f"Встреча с UID {uid} не найдена в календаре.")
        if start is not None and existing.recurrence_rule:
            start = start.astimezone(timezone.utc)
            existing = replace(existing, dtstart=start, dtend=start + (existing.dtend - existing.dtstart))
        return existing

    def _ipc_open_event_form(self) -> tuple[EventDialog, calendar_store.Event | None]:
        if self._ipc_event_form is None:
            raise RuntimeError("Форма встречи не открыта.")
        return self._ipc_event_form

    # ---- адресная книга на экране для формы встречи (помощник) --------------
    # Помощник при неоднозначной фамилии открывает книгу с фильтром: строки
    # пронумерованы, уже выбранные отмечены; «второй», «евгений», «все»,
    # «принять», «отмена» — команды канала ниже. Окно немодальное (канал
    # должен отвечать, пока оно открыто) и привязано к форме встречи.

    def _ipc_picker(self) -> ContactPickerDialog:
        picker = getattr(self, "_ipc_contact_picker", None)
        if picker is None:
            raise RuntimeError("Адресная книга не открыта.")
        return picker

    def ipc_contact_picker_open(self, query: str = "") -> dict:
        dialog, _existing = self._ipc_open_event_form()
        existing_picker = getattr(self, "_ipc_contact_picker", None)
        if existing_picker is not None:
            existing_picker.set_filter(query)
            existing_picker.raise_()
            return existing_picker.picker_state()
        contacts = dialog._contacts or self._load_contacts()
        if not contacts:
            raise RuntimeError("Адресная книга пуста.")
        parent = dialog if isinstance(dialog, QWidget) else self
        picker = ContactPickerDialog(parent, contacts, preselected=dialog.attendees_edit.text())
        picker.setModal(False)
        picker.setWindowTitle("Адресная книга — участники встречи")
        picker.set_filter(query)
        self._ipc_contact_picker = picker

        def on_finished(result: int) -> None:
            if getattr(self, "_ipc_contact_picker", None) is picker:
                self._ipc_contact_picker = None
            if result == QDialog.DialogCode.Accepted:
                try:
                    _apply_picker_to_field(dialog.attendees_edit, contacts, picker.selected_candidates())
                except RuntimeError:
                    pass  # форма уже закрыта
            picker.deleteLater()

        picker.finished.connect(on_finished)
        picker.show()
        picker.raise_()
        picker.activateWindow()
        return picker.picker_state()

    def ipc_contact_picker_select(
        self, *, number: int | None = None, query: str | None = None, all_visible: bool = False, checked: bool = True
    ) -> dict:
        picker = self._ipc_picker()
        touched = picker.select(number=number, query=query, all_visible=all_visible, checked=checked)
        state = picker.picker_state()
        state["touched"] = touched
        return state

    def ipc_contact_picker_state(self) -> dict:
        return self._ipc_picker().picker_state()

    def ipc_contact_picker_accept(self) -> list[dict]:
        picker = self._ipc_picker()
        selected = picker.selected_entries()
        picker.accept()  # finished → выбор ложится в поле «Участники»
        return selected

    def ipc_contact_picker_cancel(self) -> None:
        picker = getattr(self, "_ipc_contact_picker", None)
        if picker is not None:
            picker.reject()

    def ipc_event_form_open(
        self, *, uid: str | None = None, occurrence_start: datetime | None = None, scope: str | None = None, **changes
    ) -> dict:
        if not self.account:
            raise RuntimeError("Нет учётной записи: сначала подключитесь к почте в настройках.")
        if self._ipc_event_form is not None:
            # Форма уже открыта — вторую не плодим, поля применяем к ней.
            self.ipc_focus()
            return self.ipc_event_form_set(**changes) if changes else self.ipc_event_form_state()
        existing: calendar_store.Event | None = None
        if uid:
            existing = self._ipc_occurrence(uid, occurrence_start)
            if not existing.is_organizer:
                raise PermissionError("Изменить можно только встречу, которую организовали вы сами.")
            draft, title = existing, "Изменить встречу"
        else:
            # Ближайший полный час — как у пустого EventDialog по кнопке.
            start = datetime.now().astimezone().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            draft = calendar_store.Event(
                uid=calendar_store.new_uid(),
                summary="",
                dtstart=start.astimezone(timezone.utc),
                dtend=(start + timedelta(hours=1)).astimezone(timezone.utc),
                organizer_email=self.account.username,
                organizer_name=self.account.username,
                is_organizer=True,
                my_participation="accepted",
                calendar_id=self._ipc_default_calendar_id(),
            )
            title = "Новая встреча"
        dialog = self._ipc_event_dialog(draft, title=title)
        if changes:
            _apply_event_form_changes(dialog, changes)
        self.ipc_focus()
        self._ipc_later(lambda: self._ipc_exec_event_form(dialog, existing, scope))
        return _event_form_state(dialog, existing)

    def ipc_event_form_set(self, **changes) -> dict:
        dialog, existing = self._ipc_open_event_form()
        _apply_event_form_changes(dialog, changes)
        return _event_form_state(dialog, existing)

    def ipc_event_form_state(self) -> dict:
        dialog, existing = self._ipc_open_event_form()
        return _event_form_state(dialog, existing)

    def ipc_event_form_focus(self, field: str) -> dict:
        dialog, existing = self._ipc_open_event_form()
        _focus_event_form_field(dialog, field)
        return _event_form_state(dialog, existing)

    def ipc_list_calendars(self) -> list[dict]:
        """Календари в порядке списка «Мои календари» — номер в этом списке
        и есть номер, которым календарь называют голосом."""
        current = self._ipc_default_calendar_id()
        return [
            {"number": index, "id": cal.id, "name": cal.name, "source": cal.source_type, "current": cal.id == current}
            for index, cal in enumerate(self._load_calendars(), 1)
        ]

    def ipc_event_form_save(self) -> dict:
        dialog, existing = self._ipc_open_event_form()
        _validate_event_form(dialog, existing)
        state = _event_form_state(dialog, existing)
        # accept() завершит exec() в _ipc_exec_event_form уже после того,
        # как ответ ушёл клиенту, — и там сработает обычное сохранение.
        dialog.accept()
        return state

    def ipc_event_form_cancel(self) -> None:
        dialog, _existing = self._ipc_open_event_form()
        dialog.reject()

    def ipc_contacts(self) -> list[contact_store.Contact]:
        return self._load_contacts()

    def ipc_cancel_event(
        self, uid: str, *, start: datetime | None = None, scope: str | None = None, confirmed: bool = False
    ) -> None:
        event = self._ipc_occurrence(uid, start)
        if not event.is_organizer:
            raise PermissionError("Отменить можно только встречу, которую организовали вы сами.")
        if confirmed:
            # Помощник уже спросил голосом и «отменить?», и «день или серия».
            self._ipc_later(lambda: self._cancel_event_now(event, scope or self.SCOPE_ALL))
            return
        # Дальше — ровно тот же путь, что у пункта «Отменить встречу» в
        # контекстном меню события: on_cancel_event сама спросит «Отменить
        # «...» и уведомить участников?» и только по «Да» разошлёт CANCEL.
        # Здесь мы лишь выбираем встречу, как это сделал бы клик по ней.
        self.selected_calendar_event = event
        self._apply_calendar_selection_highlight()
        self.ipc_focus()
        self._ipc_later(self.on_cancel_event)

    def ipc_apply_mail_rules(self, folder: str | None = None) -> dict:
        """Применение правил сортировки — единственная команда со сразу
        видимым результатом без подтверждения: письма только переезжают
        между папками ОДНОГО и того же ящика, наружу ничего не уходит."""
        if self.mailbox is None or self.active_source is not self.mailbox:
            raise RuntimeError("Применение правил работает только в папках живого ящика.")
        source_folder = folder or self.current_folder
        if not source_folder:
            raise RuntimeError("Не выбрана папка: укажите folder или откройте папку в окне.")
        if not self.mail_rules:
            raise RuntimeError(
                "Правила сортировки не заданы: Параметры → «Правила сортировки почты…»."
            )
        if source_folder == self.current_folder:
            summaries = self.current_summaries
        else:
            summaries = self.mailbox.folder_summaries(source_folder)
        moves = _mail_rule_moves(summaries, self.mail_rules)

        moved_total = 0
        failure: Exception | None = None
        try:
            for target_folder, uids in moves.items():
                self.mailbox.move_to_folder(source_folder, uids, target_folder)
                moved_total += len(uids)
        except Exception as exc:
            failure = exc
        self._sync_folders_async(list(moves))
        if source_folder == self.current_folder:
            try:
                summaries = self.mailbox.refresh_folder(source_folder)
            except Exception:
                summaries = None
            if summaries is not None:
                self._render_folder(summaries)
        if failure is not None:
            raise RuntimeError(f"{failure} (перемещено до сбоя: {moved_total})") from failure
        self.statusBar().showMessage(f"По правилам перемещено писем: {moved_total}", 5000)
        return {
            "folder": source_folder,
            "moved": moved_total,
            "moves": {target: len(uids) for target, uids in moves.items()},
        }

    def ipc_list_mail_rules(self) -> list[dict]:
        return [asdict(rule) for rule in self.mail_rules]

    def closeEvent(self, event) -> None:
        self._sync_stop.set()
        for worker in list(self._background_workers):
            if isinstance(worker, _SyncWorker):
                worker.wait(3000)
        try:
            self.ipc_server.stop()
        except Exception:
            pass  # закрытие окна не должно падать из-за необязательного канала
        self.poll_timer.stop()
        # Сетевые соединения закрываются в отдельном потоке с ограничением
        # по времени: окно не должно висеть на LOGOUT, если сервер молчит
        # или соединение занято фоном (жалоба: "опять висит приложение").
        mailboxes = list(self.mailboxes.values())
        closer = threading.Thread(target=lambda: [_close_quietly(m) for m in mailboxes], daemon=True)
        closer.start()
        closer.join(CLOSE_WAIT_SECONDS)
        if closer.is_alive():
            _log.warning("Закрытие соединений не уложилось в %d с — выходим без ожидания", CLOSE_WAIT_SECONDS)
        for archive in self.archives.values():
            archive.close()
        for temp_dir in self._temp_attachment_dirs:
            shutil.rmtree(temp_dir, ignore_errors=True)
        try:
            save_window_geometry(bytes(self.saveGeometry()))
            save_mail_columns_state(bytes(self.table.horizontalHeader().saveState()), MAIL_COLUMN_COUNT)
            # Раньше положение сплиттеров (ширина списка папок, доля
            # списка писем/панели чтения) нигде не сохранялось вовсе —
            # всегда сбрасывалось на жёстко заданные умолчания при
            # следующем запуске (жалоба: "настройка окна со списком почты
            # не сохраняется"), а вместе с ЗАКРЕПЛЁННОЙ шириной "Даты" (см.
            # _date_column_pinned) это давало особенно кривой результат:
            # список писем сжимался в узкую полоску умолчаний, а "Дата"
            # оставалась той же широкой, что и в прошлый раз, выдавливая
            # остальные колонки.
            save_mail_splitters_state(
                {
                    "main": bytes(self.main_splitter.saveState()),
                    "right": bytes(self.right_splitter.saveState()),
                }
            )
        except Exception:
            pass  # расположение окна/колонок не запомнится между запусками — не критично
        super().closeEvent(event)
