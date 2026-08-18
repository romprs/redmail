from __future__ import annotations

import mimetypes
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QCursor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
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
    QSlider,
    QSpinBox,
    QSplitter,
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

from redmail.config_store import (
    load_account,
    load_font_scale,
    load_pane_orientation,
    load_poll_interval_minutes,
    save_account,
    save_font_scale,
    save_pane_orientation,
    save_poll_interval_minutes,
)
from redmail.imap_client import Account, Attachment, FolderInfo, ImapSession, MessageSummary
from redmail.mailbox import CachedMailbox
from redmail.smtp_client import OutgoingAttachment, OutgoingMessage, SmtpAccount, send_message

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

_MARKER_ICON_SIZE = 16

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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Почтовый клиент RED OS — прототип")
        self.resize(1200, 640)

        self.account: Account | None = None
        self.mailbox: CachedMailbox | None = None
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
        self._temp_attachment_dirs: list[Path] = []
        self._base_font_point_size = QApplication.instance().font().pointSizeF() or 10.0

        self.folder_tree = QTreeWidget(self)
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.currentItemChanged.connect(self.on_folder_item_changed)

        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText(f"Фильтр: {_FILTER_COLUMNS[self.filter_column]}")
        self.filter_edit.textChanged.connect(self.on_filter_changed)

        self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels(["", _FLAG_MARK, "!", _ATTACHMENT_MARK, "От кого", "Тема", "Дата"])
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

        self.reading_pane = QPlainTextEdit(self)
        self.reading_pane.setReadOnly(True)
        self.reading_pane.setPlaceholderText("Выберите письмо, чтобы увидеть текст")

        reading_container = QWidget(self)
        reading_layout = QVBoxLayout(reading_container)
        reading_layout.setContentsMargins(0, 0, 0, 0)
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
        self.setCentralWidget(main_splitter)

        toolbar = QToolBar("Основная", self)
        self.addToolBar(toolbar)

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

    def _restart_poll_timer(self) -> None:
        self.poll_timer.start(self.poll_interval_minutes * 60_000)

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
        self.folder_tree.clear()
        root = QTreeWidgetItem([self.account.username])
        root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.folder_tree.addTopLevelItem(root)

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
            parent.setData(0, Qt.ItemDataRole.UserRole, info.name)
            if first_selectable is None:
                first_selectable = parent
            if info.name == "INBOX":
                inbox_item = parent

        self.folder_tree.expandAll()
        default_item = inbox_item or first_selectable
        if default_item is not None:
            self.folder_tree.setCurrentItem(default_item)

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
        if column not in _FILTER_COLUMNS:
            return
        self.filter_column = column
        self.filter_edit.setPlaceholderText(f"Фильтр: {_FILTER_COLUMNS[column]}")
        self.on_filter_changed(self.filter_edit.text())

    def on_current_cell_changed(
        self, current_row: int, current_column: int, _previous_row: int, _previous_column: int
    ) -> None:
        self._set_filter_column(current_column)

    def on_folder_item_changed(self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None) -> None:
        if current is None or not self.mailbox:
            return
        folder_name = current.data(0, Qt.ItemDataRole.UserRole)
        if not folder_name:
            return  # промежуточный узел иерархии, не настоящая папка
        self.current_folder = folder_name
        self._clear_reading_pane()
        try:
            summaries = self.mailbox.folder_summaries(folder_name)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка загрузки папки", str(exc))
            return
        self._render_folder(summaries)

    def on_refresh(self) -> None:
        if not self.mailbox or not self.current_folder:
            return
        try:
            summaries = self.mailbox.refresh_folder(self.current_folder)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка обновления", str(exc))
            return
        self._render_folder(summaries)
        self.statusBar().showMessage(f"Обновлено: {self.current_folder}", 3000)

    def _on_periodic_refresh(self) -> None:
        # Тихая фоновая проверка по таймеру — без модальных окон об ошибках,
        # чтобы не перебивать пользователя, если тот занят (например, пишет письмо).
        if not self.mailbox or not self.current_folder:
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

    def on_filter_changed(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.table.rowCount()):
            if not needle:
                self.table.setRowHidden(row, False)
                continue
            value = self.table.item(row, self.filter_column).text().lower()
            self.table.setRowHidden(row, needle not in value)

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
        if item.column() != COL_FLAG or not self.mailbox or not self.current_folder:
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
            self.mailbox.set_marker(self.current_folder, summary.uid, new_color)
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось изменить маркер", str(exc))
            return

        summary.marker_color = new_color
        item.setIcon(_marker_icon(new_color) if new_color else QIcon())

    def on_delete_selected(self) -> None:
        if not self.mailbox or not self.current_folder:
            return
        checked_uids = [
            uid
            for row in range(self.table.rowCount())
            if self.table.item(row, COL_CHECK).checkState() == Qt.CheckState.Checked
            for uid in [self.table.item(row, COL_CHECK).data(Qt.ItemDataRole.UserRole)]
        ]
        if not checked_uids:
            QMessageBox.information(self, "Нечего удалять", "Отметьте галочками письма, которые нужно удалить.")
            return

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

        if self.selected_summary and self.selected_summary.uid in checked_uids:
            self._clear_reading_pane()
        try:
            summaries = self.mailbox.refresh_folder(self.current_folder)
        except Exception as exc:
            QMessageBox.warning(self, "Письма удалены, но обновить список не удалось", str(exc))
            return
        self._render_folder(summaries)
        self.statusBar().showMessage(status_text, 5000)

    def on_message_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or not self.mailbox or not self.current_folder:
            return
        summary = self._summary_for_row(rows[0].row())
        if summary is None:
            return
        self.selected_summary = summary
        try:
            content = self.mailbox.message_content(self.current_folder, summary.uid)
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
        self._refresh_attachments_list()

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
        for temp_dir in self._temp_attachment_dirs:
            shutil.rmtree(temp_dir, ignore_errors=True)
        super().closeEvent(event)
