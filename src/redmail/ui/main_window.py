from __future__ import annotations

import mimetypes
import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QDateTime, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QColor, QCursor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
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
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from redmail import archive_store, calendar_store, itip
from redmail.config_store import (
    load_account,
    load_font_scale,
    load_open_archives,
    load_pane_orientation,
    load_poll_interval_minutes,
    save_account,
    save_font_scale,
    save_open_archives,
    save_pane_orientation,
    save_poll_interval_minutes,
)
from redmail.imap_client import Account, Attachment, FolderInfo, ImapSession, MessageContent, MessageSummary
from redmail.mailbox import ArchiveSource, CachedMailbox
from redmail.paths import app_dir
from redmail.smtp_client import OutgoingAttachment, OutgoingMessage, SmtpAccount, send_message
from redmail.ui.week_calendar import AllDayRowWidget, WeekGridWidget, WeekHeaderWidget, week_start_for

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

# Ключ "источника" для узлов дерева папок живого ящика (см. UserRole ниже) —
# отличает их от узлов открытых архивов, чей ключ — строковый путь к файлу.
_LIVE_SOURCE_KEY = "__live__"

_PARTICIPATION_LABELS: dict[str, str] = {
    "accepted": "Принял(а) участие",
    "declined": "Отклонил(а)",
    "tentative": "Участие под вопросом",
    "needs-action": "Ещё не ответил(а)",
}
_REPLY_VERBS: dict[str, str] = {"accepted": "Принято", "declined": "Отклонено", "tentative": "Под вопросом"}

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


def _importance_mark(importance: str) -> str:
    if importance == "high":
        return "!"
    if importance == "low":
        return "↓"
    return ""


def _marker_icon(color: str, diameter: int = _MARKER_ICON_SIZE) -> QIcon:
    pixmap = QPixmap(diameter, diameter)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(_MARKER_HEX[color]))
    painter.drawEllipse(1, 1, diameter - 2, diameter - 2)
    painter.end()
    return QIcon(pixmap)


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

        imap_form = QFormLayout()
        imap_form.addRow("Сервер", self.host_edit)
        imap_form.addRow("Порт", self.port_edit)
        imap_form.addRow("Логин", self.user_edit)
        imap_form.addRow("Пароль", self.password_edit)
        imap_form.addRow(self.ssl_check)
        imap_group = QGroupBox("Входящая почта (IMAP)")
        imap_group.setLayout(imap_form)

        self.smtp_host_edit = QLineEdit(smtp.host if smtp else "")
        self.smtp_port_edit = QSpinBox()
        self.smtp_port_edit.setRange(1, 65535)
        self.smtp_port_edit.setValue(smtp.port if smtp else 587)
        self.smtp_ssl_check = QCheckBox("SSL напрямую (порт 465) вместо STARTTLS")
        self.smtp_ssl_check.setChecked(smtp.use_ssl if smtp else False)

        smtp_form = QFormLayout()
        smtp_form.addRow("Сервер", self.smtp_host_edit)
        smtp_form.addRow("Порт", self.smtp_port_edit)
        smtp_form.addRow(self.smtp_ssl_check)
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

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(imap_group)
        layout.addWidget(smtp_group)
        layout.addWidget(general_group)
        layout.addWidget(buttons)

    def account(self) -> Account:
        return Account(
            host=self.host_edit.text().strip(),
            username=self.user_edit.text().strip(),
            password=self.password_edit.text(),
            port=self.port_edit.value(),
            use_ssl=self.ssl_check.isChecked(),
        )

    def smtp_account(self) -> SmtpAccount:
        return SmtpAccount(
            host=self.smtp_host_edit.text().strip(),
            username=self.user_edit.text().strip(),
            password=self.password_edit.text(),
            port=self.smtp_port_edit.value(),
            use_ssl=self.smtp_ssl_check.isChecked(),
        )

    def poll_interval_minutes(self) -> int:
        return self.interval_edit.value()

    def pane_orientation(self) -> str:
        return "horizontal" if self.orientation_horizontal.isChecked() else "vertical"


class ComposeDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        title: str = "Новое письмо",
        to: str = "",
        subject: str = "",
        body: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 460)
        self.attachments: list[OutgoingAttachment] = []

        self.to_edit = QLineEdit(to)
        self.to_edit.setPlaceholderText("Через запятую, если получателей несколько")
        self.subject_edit = QLineEdit(subject)
        self.body_edit = QPlainTextEdit(body)

        form = QFormLayout()
        form.addRow("Кому", self.to_edit)
        form.addRow("Тема", self.subject_edit)

        self.attachments_list = QListWidget()
        self.attachments_list.setMaximumHeight(70)

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

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(attach_row)
        layout.addWidget(self.attachments_list)
        layout.addWidget(self.body_edit)
        layout.addWidget(buttons)

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
        return [addr.strip() for addr in self.to_edit.text().split(",") if addr.strip()]

    def subject(self) -> str:
        return self.subject_edit.text().strip()

    def body(self) -> str:
        return self.body_edit.toPlainText()


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

    def __init__(self, parent=None, *, event: calendar_store.Event | None = None, my_email: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Изменить встречу" if event else "Новая встреча")
        self.resize(480, 420)

        self.summary_edit = QLineEdit(event.summary if event else "")
        self.location_edit = QLineEdit(event.location if event else "")
        other_attendees = [a.email for a in event.attendees if a.email != my_email] if event else []
        self.attendees_edit = QLineEdit(", ".join(other_attendees))
        self.attendees_edit.setPlaceholderText("Участники через запятую (необязательно)")
        self.description_edit = QPlainTextEdit(event.description if event else "")

        default_start = datetime.now().astimezone().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        start_local = event.dtstart.astimezone() if event else default_start
        end_local = event.dtend.astimezone() if event else start_local + timedelta(hours=1)

        self.start_edit = QDateTimeEdit(QDateTime(start_local.date(), start_local.time()), self)
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.end_edit = QDateTimeEdit(QDateTime(end_local.date(), end_local.time()), self)
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setDisplayFormat("dd.MM.yyyy HH:mm")

        self.recurrence_combo = QComboBox(self)
        for label, value in _RECURRENCE_OPTIONS:
            self.recurrence_combo.addItem(label, value)
        if event and event.recurrence_rule:
            index = self.recurrence_combo.findData(event.recurrence_rule)
            self.recurrence_combo.setCurrentIndex(index if index >= 0 else 0)

        form = QFormLayout()
        form.addRow("Тема", self.summary_edit)
        form.addRow("Начало", self.start_edit)
        form.addRow("Окончание", self.end_edit)
        form.addRow("Повтор", self.recurrence_combo)
        form.addRow("Место", self.location_edit)
        form.addRow("Участники", self.attendees_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("Описание", self))
        layout.addWidget(self.description_edit)
        layout.addWidget(buttons)

    def summary(self) -> str:
        return self.summary_edit.text().strip()

    def location(self) -> str:
        return self.location_edit.text().strip()

    def description(self) -> str:
        return self.description_edit.toPlainText()

    def attendee_emails(self) -> list[str]:
        return [addr.strip() for addr in self.attendees_edit.text().split(",") if addr.strip()]

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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Почтовый клиент RED OS — прототип")
        self.resize(1200, 640)

        self.account: Account | None = None
        self.mailbox: CachedMailbox | None = None
        self.account_root: QTreeWidgetItem | None = None
        self.archives: dict[str, ArchiveSource] = {}
        self.archive_tree_roots: dict[str, QTreeWidgetItem] = {}
        self.active_source: CachedMailbox | ArchiveSource | None = None
        self.smtp_account: SmtpAccount | None = None
        self.trash_folder_name: str | None = None
        self.current_folder: str | None = None
        self.current_summaries: list[MessageSummary] = []
        self.summaries_by_uid: dict[int, MessageSummary] = {}
        self.current_body: str = ""
        self.current_attachments: list[Attachment] = []
        self.selected_summary: MessageSummary | None = None
        self.poll_interval_minutes = load_poll_interval_minutes()
        self.pane_orientation = load_pane_orientation()
        self.filter_column = COL_SUBJECT
        self.marker_filter: str | None = None
        self._temp_attachment_dirs: list[Path] = []
        self._base_font_point_size = QApplication.instance().font().pointSizeF() or 10.0

        # Один локальный календарь на пользователя (не на учётную запись —
        # как и почтовый кэш, это просто локальное состояние приложения).
        self.calendar_path = app_dir() / "calendar.rmcal"
        self.current_invite: itip.IncomingInvite | None = None

        self.folder_tree = QTreeWidget(self)
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.currentItemChanged.connect(self.on_folder_item_changed)

        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText(f"Фильтр: {_FILTER_COLUMNS[self.filter_column]}")
        self.filter_edit.textChanged.connect(self.on_filter_changed)

        self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels(["", _FLAG_MARK, "!", _ATTACHMENT_MARK, "От кого", "Тема", "Дата"])
        self._update_marker_filter_indicator()
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_SUBJECT, QHeaderView.ResizeMode.Stretch)
        for col in (COL_CHECK, COL_FLAG, COL_IMPORTANCE, COL_ATTACHMENT):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionsMovable(True)
        header.sectionClicked.connect(self._set_filter_column)
        self.table.setIconSize(QSize(_MARKER_ICON_SIZE, _MARKER_ICON_SIZE))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.itemSelectionChanged.connect(self.on_message_selected)
        self.table.itemClicked.connect(self.on_table_item_clicked)
        self.table.currentCellChanged.connect(self.on_current_cell_changed)

        table_container = QWidget(self)
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(self.filter_edit)
        table_layout.addWidget(self.table)

        self.attachments_list = QListWidget(self)
        self.attachments_list.setMaximumHeight(70)
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

        self.reading_pane = QPlainTextEdit(self)
        self.reading_pane.setReadOnly(True)
        self.reading_pane.setPlaceholderText("Выберите письмо, чтобы увидеть текст")

        reading_container = QWidget(self)
        reading_layout = QVBoxLayout(reading_container)
        reading_layout.setContentsMargins(0, 0, 0, 0)
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

        self.calendar_week_label = QLabel(self)
        self.calendar_week_label.setStyleSheet("font-weight: 600;")

        calendar_toolbar = QToolBar("Календарь", self)
        prev_week_action = QAction("‹", self)
        prev_week_action.setToolTip("Предыдущая неделя")
        prev_week_action.triggered.connect(self.on_calendar_prev_week)
        calendar_toolbar.addAction(prev_week_action)
        today_action = QAction("Сегодня", self)
        today_action.triggered.connect(self.on_calendar_today)
        calendar_toolbar.addAction(today_action)
        next_week_action = QAction("›", self)
        next_week_action.setToolTip("Следующая неделя")
        next_week_action.triggered.connect(self.on_calendar_next_week)
        calendar_toolbar.addAction(next_week_action)
        calendar_toolbar.addWidget(self.calendar_week_label)
        calendar_toolbar.addSeparator()
        new_event_action = QAction("Новая встреча…", self)
        new_event_action.triggered.connect(self.on_new_event)
        calendar_toolbar.addAction(new_event_action)
        cancel_event_action = QAction("Отменить встречу", self)
        cancel_event_action.setToolTip("Только для встреч, которые организовали вы сами")
        cancel_event_action.triggered.connect(self.on_cancel_event)
        calendar_toolbar.addAction(cancel_event_action)
        calendar_refresh_action = QAction("Обновить", self)
        calendar_refresh_action.triggered.connect(self.refresh_calendar_view)
        calendar_toolbar.addAction(calendar_refresh_action)

        self.calendar_week_header = WeekHeaderWidget(self)
        self.calendar_all_day_row = AllDayRowWidget(self)
        self.calendar_all_day_row.eventClicked.connect(self._on_calendar_event_clicked)
        self.calendar_all_day_row.eventDoubleClicked.connect(self._on_calendar_event_double_clicked)
        self.calendar_week_grid = WeekGridWidget(self)
        self.calendar_week_grid.eventClicked.connect(self._on_calendar_event_clicked)
        self.calendar_week_grid.eventDoubleClicked.connect(self._on_calendar_event_double_clicked)

        calendar_scroll = QScrollArea(self)
        calendar_scroll.setWidget(self.calendar_week_grid)
        calendar_scroll.setWidgetResizable(True)
        calendar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._calendar_scroll = calendar_scroll

        calendar_page = QWidget(self)
        calendar_layout = QVBoxLayout(calendar_page)
        calendar_layout.setContentsMargins(0, 0, 0, 0)
        calendar_layout.setSpacing(0)
        calendar_layout.addWidget(calendar_toolbar)
        calendar_layout.addWidget(self.calendar_week_header)
        calendar_layout.addWidget(self.calendar_all_day_row)
        calendar_layout.addWidget(calendar_scroll)

        self.pages = QStackedWidget(self)
        self.pages.addWidget(main_splitter)  # 0: почта
        self.pages.addWidget(calendar_page)  # 1: календарь
        self.setCentralWidget(self.pages)

        toolbar = QToolBar("Основная", self)
        self.addToolBar(toolbar)

        mode_group = QActionGroup(self)
        mode_group.setExclusive(True)
        self.mail_mode_action = QAction("Почта", self)
        self.mail_mode_action.setCheckable(True)
        self.mail_mode_action.setChecked(True)
        self.mail_mode_action.triggered.connect(lambda: self.pages.setCurrentIndex(0))
        self.calendar_mode_action = QAction("Календарь", self)
        self.calendar_mode_action.setCheckable(True)
        self.calendar_mode_action.triggered.connect(self._show_calendar_page)
        mode_group.addAction(self.mail_mode_action)
        mode_group.addAction(self.calendar_mode_action)
        toolbar.addAction(self.mail_mode_action)
        toolbar.addAction(self.calendar_mode_action)

        toolbar.addSeparator()

        refresh_action = QAction("Обновить", self)
        refresh_action.triggered.connect(self.on_refresh)
        toolbar.addAction(refresh_action)

        toolbar.addSeparator()

        compose_action = QAction("Написать письмо…", self)
        compose_action.triggered.connect(self.on_compose)
        toolbar.addAction(compose_action)

        reply_action = QAction("Ответить", self)
        reply_action.triggered.connect(self.on_reply)
        toolbar.addAction(reply_action)

        delete_action = QAction("Удалить", self)
        delete_action.setToolTip("В корзину. Shift+Удалить — безвозвратно.")
        delete_action.triggered.connect(self.on_delete_selected)
        toolbar.addAction(delete_action)

        toolbar.addSeparator()

        open_archive_action = QAction("Открыть архив…", self)
        open_archive_action.triggered.connect(self.on_open_archive)
        toolbar.addAction(open_archive_action)

        import_action = QAction("Импортировать…", self)
        import_action.setToolTip("Импортировать mbox/Maildir (Evolution) или .pst (Outlook) в архив")
        import_action.triggered.connect(self.on_import)
        toolbar.addAction(import_action)

        archive_selected_action = QAction("В архив…", self)
        archive_selected_action.setToolTip("Выгрузить отмеченные письма в архив (копия или перемещение)")
        archive_selected_action.triggered.connect(self.on_archive_selected)
        toolbar.addAction(archive_selected_action)

        toolbar.addSeparator()

        settings_action = QAction("Параметры…", self)
        settings_action.triggered.connect(self.on_settings)
        toolbar.addAction(settings_action)

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
            saved = load_account()
        except Exception:
            saved = None
        if not saved:
            return
        account, smtp_account = saved
        try:
            session = ImapSession(account)
            folders = session.list_folders()
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Не удалось войти с сохранёнными данными",
                f"{account.username}: {exc}\n\nПодключитесь заново вручную.",
            )
            return
        self._apply_connection(account, smtp_account, session, folders)
        self.statusBar().showMessage(f"Восстановлено подключение: {account.username}", 5000)

    def _apply_connection(
        self,
        account: Account,
        smtp_account: SmtpAccount | None,
        session: ImapSession,
        folders: list[FolderInfo],
    ) -> None:
        if self.mailbox:
            self.mailbox.close()

        self.account = account
        self.mailbox = CachedMailbox(session, account)
        self.smtp_account = smtp_account
        self.trash_folder_name = session.trash_folder()

        self._populate_folder_tree(folders)

    def _populate_folder_tree(self, folders: list[FolderInfo]) -> None:
        # Только узел живого ящика пересоздаём — открытые архивы (отдельные
        # top-level узлы) переподключение никак не затрагивает.
        if self.account_root is not None:
            index = self.folder_tree.indexOfTopLevelItem(self.account_root)
            if index != -1:
                self.folder_tree.takeTopLevelItem(index)

        root = QTreeWidgetItem([self.account.username])
        root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.folder_tree.insertTopLevelItem(0, root)
        self.account_root = root

        nodes: dict[tuple[str, ...], QTreeWidgetItem] = {(): root}
        inbox_item: QTreeWidgetItem | None = None
        first_selectable: QTreeWidgetItem | None = None

        for info in folders:
            delimiter = info.delimiter or "/"
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
            parent.setData(0, Qt.ItemDataRole.UserRole, (_LIVE_SOURCE_KEY, info.name))
            if first_selectable is None:
                first_selectable = parent
            if info.name == "INBOX":
                inbox_item = parent

        self.folder_tree.expandAll()
        default_item = inbox_item or first_selectable
        if default_item is not None:
            self.folder_tree.setCurrentItem(default_item)

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
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Создать архив", filter="Архивы RedMail (*.rmarchive)"
        )
        if not path_str:
            return None
        return self._normalize_archive_path(path_str)

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

    def _refresh_archive_folders(self, key: str) -> None:
        root = self.archive_tree_roots.get(key)
        source = self.archives.get(key)
        if root is None or source is None:
            return
        root.takeChildren()
        for folder_name in archive_store.list_folders(source.path):
            node = QTreeWidgetItem([folder_name])
            node.setData(0, Qt.ItemDataRole.UserRole, (key, folder_name))
            root.addChild(node)

    def _pick_archive_target(
        self, *, title: str, ask_folder: bool = False, default_folder: str = "", ask_move_copy: bool = False
    ) -> tuple[str, str, bool] | None:
        archive_names = {key: Path(source.path).stem for key, source in self.archives.items()}
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

        try:
            importer = archive_store.import_maildir if is_maildir else archive_store.import_mbox
            count = importer(archive_path, Path(source_path_str), folder_name)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка импорта", str(exc))
            return

        self._refresh_archive_folders(archive_key)
        self.statusBar().showMessage(f"Импортировано писем: {count}", 5000)

    def _import_pst(self) -> None:
        source_path_str, _ = QFileDialog.getOpenFileName(self, "Выбрать файл .pst", filter="Outlook PST (*.pst)")
        if not source_path_str:
            return

        result = self._pick_archive_target(title="Импорт .pst")
        if result is None:
            return
        archive_key, _folder_name, _move = result
        archive_path = self.archives[archive_key].path

        try:
            count = archive_store.import_pst(archive_path, Path(source_path_str))
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка импорта", str(exc))
            return

        self._refresh_archive_folders(archive_key)
        self.statusBar().showMessage(f"Импортировано писем: {count}", 5000)

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

    def on_settings(self) -> None:
        dialog = SettingsDialog(
            self,
            account=self.account,
            smtp=self.smtp_account,
            poll_interval_minutes=self.poll_interval_minutes,
            pane_orientation=self.pane_orientation,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.poll_interval_minutes = dialog.poll_interval_minutes()
        self.pane_orientation = dialog.pane_orientation()
        try:
            save_poll_interval_minutes(self.poll_interval_minutes)
            save_pane_orientation(self.pane_orientation)
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
        self._apply_connection(new_account, new_smtp, session, folders)

        try:
            save_account(new_account, new_smtp)
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
        self._set_filter_column(current_column)

    def on_folder_item_changed(self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None) -> None:
        if current is None:
            return
        data = current.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return  # промежуточный узел иерархии (учётная запись/архив), не настоящая папка
        source_key, folder_name = data
        source = self.mailbox if source_key == _LIVE_SOURCE_KEY else self.archives.get(source_key)
        if source is None:
            return
        self.active_source = source
        self.current_folder = folder_name
        self._clear_reading_pane()
        try:
            summaries = source.folder_summaries(folder_name)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка загрузки папки", str(exc))
            return
        self._render_folder(summaries)

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
        self.current_attachments = []
        self.attachments_list.clear()
        self.attachments_list.hide()
        self.current_invite = None
        self.invite_bar.hide()

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
            self.table.setItem(row, COL_SENDER, QTableWidgetItem(summary.sender))
            self.table.setItem(row, COL_SUBJECT, QTableWidgetItem(summary.subject))
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

    def on_table_item_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() != COL_FLAG or not self.active_source or not self.current_folder:
            return
        summary = self._summary_for_row(item.row())
        if summary is None:
            return
        self._open_marker_menu(item, summary)

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
            self.active_source.set_marker(self.current_folder, summary.uid, new_color)
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось изменить маркер", str(exc))
            return

        summary.marker_color = new_color
        item.setIcon(_marker_icon(new_color) if new_color else QIcon())

    def _checked_uids(self) -> list[int]:
        return [
            self.table.item(row, COL_CHECK).data(Qt.ItemDataRole.UserRole)
            for row in range(self.table.rowCount())
            if self.table.item(row, COL_CHECK).checkState() == Qt.CheckState.Checked
        ]

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
            self.reading_pane.setPlainText(f"Не удалось загрузить письмо: {exc}")
            self.attachments_list.clear()
            self.attachments_list.hide()
            return
        self.current_body = content.text
        self.current_attachments = content.attachments
        self.reading_pane.setPlainText(content.text)
        self._update_invite_bar(content)
        self._refresh_attachments_list()

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
            when = self._format_event_time(event)
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

    @staticmethod
    def _format_event_time(event: calendar_store.Event) -> str:
        start_local = event.dtstart.astimezone()
        end_local = event.dtend.astimezone()
        if event.all_day:
            return f"{start_local.strftime('%d.%m.%Y')} (весь день)"
        if start_local.date() == end_local.date():
            return f"{start_local.strftime('%d.%m.%Y %H:%M')}–{end_local.strftime('%H:%M')}"
        return f"{start_local.strftime('%d.%m.%Y %H:%M')} – {end_local.strftime('%d.%m.%Y %H:%M')}"

    def on_invite_response(self, participation: str) -> None:
        if self.current_invite is None or not self.account:
            return
        if not self.smtp_account:
            QMessageBox.warning(
                self, "Нет исходящей почты", "Укажите сервер SMTP в настройках, чтобы ответить на приглашение."
            )
            return
        uid = self.current_invite.event.uid
        event = calendar_store.set_my_participation(self.calendar_path, uid, participation)
        if event is None:
            return

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
        try:
            send_message(self.smtp_account, message)
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось отправить ответ", str(exc))
            return

        self.current_invite = None
        self.invite_label.setText(f"«{event.summary}» — {_PARTICIPATION_LABELS[participation]}")
        for button in (self.invite_accept_button, self.invite_tentative_button, self.invite_decline_button):
            button.setEnabled(False)
        self.statusBar().showMessage(f"Ответ на приглашение отправлен: {verb.lower()}", 5000)

    def _show_calendar_page(self) -> None:
        self.pages.setCurrentIndex(1)
        self.refresh_calendar_view()
        if not self._calendar_scrolled_to_now:
            self._calendar_scrolled_to_now = True
            self._calendar_scroll.verticalScrollBar().setValue(self.calendar_week_grid.scroll_position_for_now())

    def on_calendar_prev_week(self) -> None:
        self.calendar_week_start -= timedelta(days=7)
        self.refresh_calendar_view()

    def on_calendar_next_week(self) -> None:
        self.calendar_week_start += timedelta(days=7)
        self.refresh_calendar_view()

    def on_calendar_today(self) -> None:
        self.calendar_week_start = week_start_for(date.today())
        self.refresh_calendar_view()

    def refresh_calendar_view(self) -> None:
        # Локальный календарь ничего не опрашивает по сети — "Обновить"
        # здесь просто перечитывает файл (например, после того как в
        # почте были приняты новые приглашения).
        week_start_date = self.calendar_week_start
        week_start_local = datetime(
            week_start_date.year, week_start_date.month, week_start_date.day
        ).astimezone()
        window_start = week_start_local.astimezone(timezone.utc)
        window_end = (week_start_local + timedelta(days=7)).astimezone(timezone.utc)
        try:
            events = calendar_store.list_events(self.calendar_path, start=window_start, end=window_end)
        except Exception as exc:
            # Без этого исключение из слота Qt тихо проглатывалось — сетка
            # просто оставалась пустой без единого сообщения об ошибке
            # (так был найден баг миграции схемы calendar.rmcal).
            QMessageBox.critical(self, "Не удалось загрузить календарь", str(exc))
            return
        events = [e for e in events if e.status != "cancelled"]
        timed_events = [e for e in events if not e.all_day]
        all_day_events = [e for e in events if e.all_day]

        end_label = self.calendar_week_start + timedelta(days=6)
        self.calendar_week_label.setText(
            f"{self.calendar_week_start.strftime('%d.%m')} – {end_label.strftime('%d.%m.%Y')}"
        )
        self.calendar_week_header.set_week_start(self.calendar_week_start)
        self.calendar_all_day_row.set_week(self.calendar_week_start, all_day_events)
        self.calendar_week_grid.set_week(self.calendar_week_start, timed_events)

    def _on_calendar_event_clicked(self, event: calendar_store.Event) -> None:
        self.selected_calendar_event = event

    def _on_calendar_event_double_clicked(self, event: calendar_store.Event) -> None:
        self.selected_calendar_event = event
        if not event.is_organizer:
            details = (
                f"{self._format_event_time(event)}\n{event.location}\n\n{event.description}\n\n"
                f"Организатор: {event.organizer_name or event.organizer_email}\n"
                f"Моё участие: {_PARTICIPATION_LABELS.get(event.my_participation, event.my_participation)}"
            )
            QMessageBox.information(self, event.summary or "(без темы)", details)
            return
        dialog = EventDialog(self, event=event, my_email=self.account.username if self.account else "")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_event_from_dialog(dialog, existing=event)

    def on_new_event(self) -> None:
        if not self.account:
            QMessageBox.warning(self, "Нет учётной записи", "Сначала подключитесь к почте в настройках.")
            return
        dialog = EventDialog(self, my_email=self.account.username)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_event_from_dialog(dialog, existing=None)

    def _save_event_from_dialog(self, dialog: EventDialog, *, existing: calendar_store.Event | None) -> None:
        start = dialog.start_utc()
        end = dialog.end_utc()
        if end <= start:
            QMessageBox.warning(self, "Некорректное время", "Окончание должно быть позже начала.")
            return

        attendee_emails = dialog.attendee_emails()
        event = calendar_store.Event(
            uid=existing.uid if existing else calendar_store.new_uid(),
            summary=dialog.summary() or "(без темы)",
            description=dialog.description(),
            location=dialog.location(),
            dtstart=start,
            dtend=end,
            organizer_email=self.account.username,
            organizer_name=self.account.username,
            is_organizer=True,
            my_participation="accepted",
            sequence=(existing.sequence + 1) if existing else 0,
            recurrence_rule=dialog.recurrence_rule(),
            attendees=[calendar_store.Attendee(email=addr) for addr in attendee_emails],
        )
        calendar_store.save_event(self.calendar_path, event)

        if attendee_emails:
            if not self.smtp_account:
                QMessageBox.warning(
                    self,
                    "Встреча сохранена, но не разослана",
                    "Событие сохранено локально, но SMTP не настроен — приглашения участникам не отправлены.",
                )
            else:
                ics = itip.build_request_ics(event, self.account.username, self.account.username)
                message = OutgoingMessage(
                    sender=self.account.username,
                    to=attendee_emails,
                    subject=f"Приглашение: {event.summary}",
                    body=f"Вас приглашают на встречу «{event.summary}».\n{self._format_event_time(event)}",
                    attachments=[
                        OutgoingAttachment(
                            filename="invite.ics",
                            content_type="text/calendar",
                            payload=ics,
                            content_type_params={"method": "REQUEST"},
                        )
                    ],
                )
                try:
                    send_message(self.smtp_account, message)
                except Exception as exc:
                    QMessageBox.critical(self, "Встреча сохранена, но не разослана", str(exc))

        self.refresh_calendar_view()

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
            try:
                send_message(self.smtp_account, message)
            except Exception as exc:
                QMessageBox.warning(self, "Не удалось уведомить участников", str(exc))

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
            temp_path = temp_dir / attachment.filename
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
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить вложение", attachment.filename)
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
        dialog = ComposeDialog(self, title="Новое письмо")
        self._exec_compose(dialog)

    def on_reply(self) -> None:
        if not self.smtp_account:
            QMessageBox.warning(
                self,
                "Нет исходящей почты",
                "Сначала подключитесь и укажите сервер SMTP в настройках учётной записи.",
            )
            return
        if not self.selected_summary:
            QMessageBox.warning(self, "Нет письма", "Выберите письмо, на которое хотите ответить.")
            return

        summary = self.selected_summary
        subject = summary.subject if summary.subject.lower().startswith("re:") else f"Re: {summary.subject}"
        quote_header = f"{summary.date}, {summary.sender} писал(а):"
        quoted = "\n".join(f"> {line}" for line in self.current_body.splitlines())
        body = f"\n\n{quote_header}\n{quoted}"

        dialog = ComposeDialog(self, title="Ответить", to=summary.sender_email, subject=subject, body=body)
        self._exec_compose(dialog, in_reply_to=summary.message_id or None)

    def _exec_compose(self, dialog: ComposeDialog, *, in_reply_to: str | None = None) -> None:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        recipients = dialog.recipients()
        if not recipients:
            QMessageBox.warning(self, "Нет получателя", "Укажите хотя бы одного получателя.")
            return

        message = OutgoingMessage(
            sender=self.account.username,
            to=recipients,
            subject=dialog.subject(),
            body=dialog.body(),
            in_reply_to=in_reply_to,
            attachments=dialog.attachments,
        )
        try:
            send_message(self.smtp_account, message)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка отправки", str(exc))
            return

        self.statusBar().showMessage(f"Письмо отправлено: {', '.join(recipients)}", 5000)

    def closeEvent(self, event) -> None:
        self.poll_timer.stop()
        if self.mailbox:
            self.mailbox.close()
        for archive in self.archives.values():
            archive.close()
        for temp_dir in self._temp_attachment_dirs:
            shutil.rmtree(temp_dir, ignore_errors=True)
        super().closeEvent(event)
