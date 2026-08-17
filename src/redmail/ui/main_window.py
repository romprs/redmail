from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QCheckBox,
    QFormLayout,
    QHeaderView,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
)
from PySide6.QtCore import Qt

from redmail.imap_client import Account, fetch_folder_summaries, fetch_message_body, list_folders


class AccountDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Учётная запись IMAP")

        self.host_edit = QLineEdit()
        self.port_edit = QSpinBox()
        self.port_edit.setRange(1, 65535)
        self.port_edit.setValue(993)
        self.user_edit = QLineEdit()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ssl_check = QCheckBox("Использовать SSL")
        self.ssl_check.setChecked(True)

        form = QFormLayout()
        form.addRow("Сервер", self.host_edit)
        form.addRow("Порт", self.port_edit)
        form.addRow("Логин", self.user_edit)
        form.addRow("Пароль", self.password_edit)
        form.addRow(self.ssl_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def account(self) -> Account:
        return Account(
            host=self.host_edit.text().strip(),
            username=self.user_edit.text().strip(),
            password=self.password_edit.text(),
            port=self.port_edit.value(),
            use_ssl=self.ssl_check.isChecked(),
        )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Почтовый клиент RED OS — прототип")
        self.resize(1100, 640)

        self.account: Account | None = None
        self.current_folder: str | None = None

        self.folder_list = QListWidget(self)
        self.folder_list.currentTextChanged.connect(self.on_folder_selected)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["От кого", "Тема", "Дата"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self.on_message_selected)

        self.reading_pane = QPlainTextEdit(self)
        self.reading_pane.setReadOnly(True)
        self.reading_pane.setPlaceholderText("Выберите письмо, чтобы увидеть текст")

        right_splitter = QSplitter(Qt.Orientation.Vertical, self)
        right_splitter.addWidget(self.table)
        right_splitter.addWidget(self.reading_pane)
        right_splitter.setStretchFactor(0, 2)
        right_splitter.setStretchFactor(1, 1)

        main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        main_splitter.addWidget(self.folder_list)
        main_splitter.addWidget(right_splitter)
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([200, 900])
        self.setCentralWidget(main_splitter)

        toolbar = QToolBar("Основная", self)
        self.addToolBar(toolbar)
        connect_action = QAction("Подключиться…", self)
        connect_action.triggered.connect(self.on_connect)
        toolbar.addAction(connect_action)

        self.setStatusBar(QStatusBar(self))

    def on_connect(self) -> None:
        dialog = AccountDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        account = dialog.account()
        if not account.host or not account.username:
            QMessageBox.warning(self, "Не хватает данных", "Укажите сервер и логин.")
            return

        try:
            folders = list_folders(account)
        except Exception as exc:  # показываем пользователю любую ошибку подключения как есть
            QMessageBox.critical(self, "Ошибка подключения", str(exc))
            return

        self.account = account
        self.folder_list.clear()
        for name in folders:
            self.folder_list.addItem(QListWidgetItem(name))

        default_row = folders.index("INBOX") if "INBOX" in folders else 0
        if folders:
            self.folder_list.setCurrentRow(default_row)

    def on_folder_selected(self, folder: str) -> None:
        if not self.account or not folder:
            return
        self.current_folder = folder
        self.reading_pane.clear()
        try:
            summaries = fetch_folder_summaries(self.account, folder)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка загрузки папки", str(exc))
            return

        self.table.setRowCount(len(summaries))
        for row, summary in enumerate(summaries):
            self.table.setItem(row, 0, QTableWidgetItem(summary.sender))
            self.table.setItem(row, 1, QTableWidgetItem(summary.subject))
            self.table.setItem(row, 2, QTableWidgetItem(summary.date))
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, summary.uid)

        self.statusBar().showMessage(f"{folder}: писем {len(summaries)}", 5000)

    def on_message_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or not self.account or not self.current_folder:
            return
        uid = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        try:
            body = fetch_message_body(self.account, self.current_folder, uid)
        except Exception as exc:
            self.reading_pane.setPlainText(f"Не удалось загрузить письмо: {exc}")
            return
        self.reading_pane.setPlainText(body)
