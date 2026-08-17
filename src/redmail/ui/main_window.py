from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
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

from redmail.imap_client import Account, ImapSession, MessageSummary
from redmail.smtp_client import OutgoingMessage, SmtpAccount, send_message


class AccountDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Учётная запись почты")

        self.host_edit = QLineEdit()
        self.port_edit = QSpinBox()
        self.port_edit.setRange(1, 65535)
        self.port_edit.setValue(993)
        self.user_edit = QLineEdit()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ssl_check = QCheckBox("Использовать SSL")
        self.ssl_check.setChecked(True)

        imap_form = QFormLayout()
        imap_form.addRow("Сервер", self.host_edit)
        imap_form.addRow("Порт", self.port_edit)
        imap_form.addRow("Логин", self.user_edit)
        imap_form.addRow("Пароль", self.password_edit)
        imap_form.addRow(self.ssl_check)
        imap_group = QGroupBox("Входящая почта (IMAP)")
        imap_group.setLayout(imap_form)

        self.smtp_host_edit = QLineEdit()
        self.smtp_port_edit = QSpinBox()
        self.smtp_port_edit.setRange(1, 65535)
        self.smtp_port_edit.setValue(587)
        self.smtp_ssl_check = QCheckBox("SSL напрямую (порт 465) вместо STARTTLS")

        smtp_form = QFormLayout()
        smtp_form.addRow("Сервер", self.smtp_host_edit)
        smtp_form.addRow("Порт", self.smtp_port_edit)
        smtp_form.addRow(self.smtp_ssl_check)
        smtp_group = QGroupBox("Исходящая почта (SMTP) — тот же логин и пароль")
        smtp_group.setLayout(smtp_form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(imap_group)
        layout.addWidget(smtp_group)
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
        self.resize(560, 420)

        self.to_edit = QLineEdit(to)
        self.to_edit.setPlaceholderText("Через запятую, если получателей несколько")
        self.subject_edit = QLineEdit(subject)
        self.body_edit = QPlainTextEdit(body)

        form = QFormLayout()
        form.addRow("Кому", self.to_edit)
        form.addRow("Тема", self.subject_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Отправить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.body_edit)
        layout.addWidget(buttons)

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
        self.resize(1100, 640)

        self.account: Account | None = None
        self.session: ImapSession | None = None
        self.smtp_account: SmtpAccount | None = None
        self.current_folder: str | None = None
        self.current_summaries: list[MessageSummary] = []
        self.current_body: str = ""
        self.selected_summary: MessageSummary | None = None

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

        toolbar.addSeparator()

        compose_action = QAction("Написать письмо…", self)
        compose_action.triggered.connect(self.on_compose)
        toolbar.addAction(compose_action)

        reply_action = QAction("Ответить", self)
        reply_action.triggered.connect(self.on_reply)
        toolbar.addAction(reply_action)

        self.setStatusBar(QStatusBar(self))

    def on_connect(self) -> None:
        dialog = AccountDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        account = dialog.account()
        if not account.host or not account.username:
            QMessageBox.warning(self, "Не хватает данных", "Укажите сервер IMAP и логин.")
            return

        try:
            session = ImapSession(account)
            folders = session.list_folders()
        except Exception as exc:  # показываем пользователю любую ошибку подключения как есть
            QMessageBox.critical(self, "Ошибка подключения", str(exc))
            return

        if self.session:
            self.session.close()

        self.account = account
        self.session = session
        smtp_account = dialog.smtp_account()
        self.smtp_account = smtp_account if smtp_account.host else None

        self.folder_list.clear()
        for name in folders:
            self.folder_list.addItem(QListWidgetItem(name))

        default_row = folders.index("INBOX") if "INBOX" in folders else 0
        if folders:
            self.folder_list.setCurrentRow(default_row)

    def on_folder_selected(self, folder: str) -> None:
        if not self.session or not folder:
            return
        self.current_folder = folder
        self.reading_pane.clear()
        self.selected_summary = None
        try:
            summaries = self.session.fetch_folder_summaries(folder)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка загрузки папки", str(exc))
            return

        self.current_summaries = summaries
        self.table.setRowCount(len(summaries))
        for row, summary in enumerate(summaries):
            self.table.setItem(row, 0, QTableWidgetItem(summary.sender))
            self.table.setItem(row, 1, QTableWidgetItem(summary.subject))
            self.table.setItem(row, 2, QTableWidgetItem(summary.date))

        self.statusBar().showMessage(f"{folder}: писем {len(summaries)}", 5000)

    def on_message_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or not self.session or not self.current_folder:
            return
        row = rows[0].row()
        summary = self.current_summaries[row]
        self.selected_summary = summary
        try:
            body = self.session.fetch_message_body(self.current_folder, summary.uid)
        except Exception as exc:
            self.current_body = ""
            self.reading_pane.setPlainText(f"Не удалось загрузить письмо: {exc}")
            return
        self.current_body = body
        self.reading_pane.setPlainText(body)

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
        )
        try:
            send_message(self.smtp_account, message)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка отправки", str(exc))
            return

        self.statusBar().showMessage(f"Письмо отправлено: {', '.join(recipients)}", 5000)

    def closeEvent(self, event) -> None:
        if self.session:
            self.session.close()
        super().closeEvent(event)
