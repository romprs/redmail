from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QCheckBox,
    QFormLayout,
    QHeaderView,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QSpinBox,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
)

from redmail.imap_client import Account, fetch_inbox_summaries


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
        self.resize(900, 560)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["От кого", "Тема", "Дата"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setCentralWidget(self.table)

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
            summaries = fetch_inbox_summaries(account)
        except Exception as exc:  # показываем пользователю любую ошибку подключения как есть
            QMessageBox.critical(self, "Ошибка подключения", str(exc))
            return

        self.table.setRowCount(len(summaries))
        for row, summary in enumerate(summaries):
            self.table.setItem(row, 0, QTableWidgetItem(summary.sender))
            self.table.setItem(row, 1, QTableWidgetItem(summary.subject))
            self.table.setItem(row, 2, QTableWidgetItem(summary.date))

        self.statusBar().showMessage(f"Загружено писем: {len(summaries)}", 5000)
