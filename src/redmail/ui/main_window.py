from __future__ import annotations

import mimetypes
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from redmail.config_store import (
    load_account,
    load_poll_interval_minutes,
    save_account,
    save_poll_interval_minutes,
)
from redmail.imap_client import Account, Attachment, ImapSession, MessageSummary
from redmail.mailbox import CachedMailbox
from redmail.smtp_client import OutgoingAttachment, OutgoingMessage, SmtpAccount, send_message

COL_CHECK = 0
COL_FLAG = 1
COL_IMPORTANCE = 2
COL_ATTACHMENT = 3
COL_SENDER = 4
COL_SUBJECT = 5
COL_DATE = 6

_FLAG_MARK = "⚑"  # ⚑ — компактнее и надёжнее по шрифтам, чем цветной эмодзи-флаг
_ATTACHMENT_MARK = "\U0001F4CE"  # 📎 — по запросу именно скрепка


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
        return "↓"  # ↓
    return ""


class AccountDialog(QDialog):
    def __init__(self, parent=None, *, account: Account | None = None, smtp: SmtpAccount | None = None):
        super().__init__(parent)
        self.setWindowTitle("Учётная запись почты")

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


class SettingsDialog(QDialog):
    def __init__(self, parent=None, *, poll_interval_minutes: int = 5):
        super().__init__(parent)
        self.setWindowTitle("Параметры")

        self.interval_edit = QSpinBox()
        self.interval_edit.setRange(1, 180)
        self.interval_edit.setSuffix(" мин.")
        self.interval_edit.setValue(poll_interval_minutes)

        form = QFormLayout()
        form.addRow("Проверять почту каждые", self.interval_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def poll_interval_minutes(self) -> int:
        return self.interval_edit.value()


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
        self.current_folder: str | None = None
        self.current_summaries: list[MessageSummary] = []
        self.current_body: str = ""
        self.current_attachments: list[Attachment] = []
        self.selected_summary: MessageSummary | None = None
        self.poll_interval_minutes = load_poll_interval_minutes()

        self.folder_list = QListWidget(self)
        self.folder_list.currentTextChanged.connect(self.on_folder_selected)

        self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels(["", _FLAG_MARK, "!", _ATTACHMENT_MARK, "От кого", "Тема", "Дата"])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_SUBJECT, QHeaderView.ResizeMode.Stretch)
        for col in (COL_CHECK, COL_FLAG, COL_IMPORTANCE, COL_ATTACHMENT):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self.on_message_selected)
        self.table.itemClicked.connect(self.on_table_item_clicked)

        self.attachments_list = QListWidget(self)
        self.attachments_list.setMaximumHeight(70)
        self.attachments_list.itemDoubleClicked.connect(self.on_save_attachment)
        self.attachments_list.hide()

        self.reading_pane = QPlainTextEdit(self)
        self.reading_pane.setReadOnly(True)
        self.reading_pane.setPlaceholderText("Выберите письмо, чтобы увидеть текст")

        reading_container = QWidget(self)
        reading_layout = QVBoxLayout(reading_container)
        reading_layout.setContentsMargins(0, 0, 0, 0)
        reading_layout.addWidget(self.attachments_list)
        reading_layout.addWidget(self.reading_pane)

        right_splitter = QSplitter(Qt.Orientation.Vertical, self)
        right_splitter.addWidget(self.table)
        right_splitter.addWidget(reading_container)
        right_splitter.setStretchFactor(0, 2)
        right_splitter.setStretchFactor(1, 1)

        main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        main_splitter.addWidget(self.folder_list)
        main_splitter.addWidget(right_splitter)
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([200, 1000])
        self.setCentralWidget(main_splitter)

        toolbar = QToolBar("Основная", self)
        self.addToolBar(toolbar)

        connect_action = QAction("Подключиться…", self)
        connect_action.triggered.connect(self.on_connect)
        toolbar.addAction(connect_action)

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
        delete_action.triggered.connect(self.on_delete_selected)
        toolbar.addAction(delete_action)

        toolbar.addSeparator()

        settings_action = QAction("Параметры…", self)
        settings_action.triggered.connect(self.on_settings)
        toolbar.addAction(settings_action)

        self.setStatusBar(QStatusBar(self))

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
        folders: list[str],
    ) -> None:
        if self.mailbox:
            self.mailbox.close()

        self.account = account
        self.mailbox = CachedMailbox(session, account)
        self.smtp_account = smtp_account

        self.folder_list.clear()
        for name in folders:
            self.folder_list.addItem(QListWidgetItem(name))

        default_row = folders.index("INBOX") if "INBOX" in folders else 0
        if folders:
            self.folder_list.setCurrentRow(default_row)

    def on_connect(self) -> None:
        saved = None
        try:
            saved = load_account()
        except Exception:
            pass
        dialog = AccountDialog(self, account=saved[0] if saved else None, smtp=saved[1] if saved else None)
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

        smtp_account = dialog.smtp_account()
        smtp_account = smtp_account if smtp_account.host else None
        self._apply_connection(account, smtp_account, session, folders)

        try:
            save_account(account, smtp_account)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Не удалось сохранить настройки",
                f"Подключение работает, но запомнить его для следующего запуска не вышло: {exc}",
            )

    def on_settings(self) -> None:
        dialog = SettingsDialog(self, poll_interval_minutes=self.poll_interval_minutes)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.poll_interval_minutes = dialog.poll_interval_minutes()
        try:
            save_poll_interval_minutes(self.poll_interval_minutes)
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось сохранить параметры", str(exc))
        self._restart_poll_timer()

    def on_folder_selected(self, folder: str) -> None:
        if not self.mailbox or not folder:
            return
        self.current_folder = folder
        self._clear_reading_pane()
        try:
            summaries = self.mailbox.folder_summaries(folder)
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

    def _render_folder(self, summaries: list[MessageSummary]) -> None:
        previously_selected_uid = self.selected_summary.uid if self.selected_summary else None

        self.current_summaries = summaries
        self.table.setRowCount(len(summaries))
        for row, summary in enumerate(summaries):
            check_item = QTableWidgetItem()
            check_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check_item.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(row, COL_CHECK, check_item)

            self.table.setItem(row, COL_FLAG, self._readonly_item(_FLAG_MARK if summary.flagged else ""))
            self.table.setItem(row, COL_IMPORTANCE, self._readonly_item(_importance_mark(summary.importance)))
            self.table.setItem(
                row, COL_ATTACHMENT, self._readonly_item(_ATTACHMENT_MARK if summary.has_attachments else "")
            )
            self.table.setItem(row, COL_SENDER, QTableWidgetItem(summary.sender))
            self.table.setItem(row, COL_SUBJECT, QTableWidgetItem(summary.subject))
            self.table.setItem(row, COL_DATE, QTableWidgetItem(summary.date))

        self.statusBar().showMessage(f"{self.current_folder}: писем {len(summaries)}", 5000)

        if previously_selected_uid is not None:
            for row, summary in enumerate(summaries):
                if summary.uid == previously_selected_uid:
                    self.table.selectRow(row)
                    break

    @staticmethod
    def _readonly_item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return item

    def on_table_item_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() != COL_FLAG or not self.mailbox or not self.current_folder:
            return
        row = item.row()
        summary = self.current_summaries[row]
        new_state = not summary.flagged
        try:
            self.mailbox.toggle_flag(self.current_folder, summary.uid, new_state)
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось изменить флаг", str(exc))
            return
        summary.flagged = new_state
        item.setText(_FLAG_MARK if new_state else "")

    def on_delete_selected(self) -> None:
        if not self.mailbox or not self.current_folder:
            return
        checked_rows = [
            row
            for row in range(self.table.rowCount())
            if self.table.item(row, COL_CHECK).checkState() == Qt.CheckState.Checked
        ]
        if not checked_rows:
            QMessageBox.information(self, "Нечего удалять", "Отметьте галочками письма, которые нужно удалить.")
            return

        uids = [self.current_summaries[row].uid for row in checked_rows]
        confirm = QMessageBox.question(
            self,
            "Удалить письма",
            f"Удалить выбранные письма ({len(uids)})? Это действие нельзя отменить.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            self.mailbox.delete_messages(self.current_folder, uids)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка удаления", str(exc))
            return

        if self.selected_summary and self.selected_summary.uid in uids:
            self._clear_reading_pane()
        try:
            summaries = self.mailbox.refresh_folder(self.current_folder)
        except Exception as exc:
            QMessageBox.warning(self, "Письма удалены, но обновить список не удалось", str(exc))
            return
        self._render_folder(summaries)
        self.statusBar().showMessage(f"Удалено писем: {len(uids)}", 5000)

    def on_message_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or not self.mailbox or not self.current_folder:
            return
        row = rows[0].row()
        summary = self.current_summaries[row]
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
        for attachment in self.current_attachments:
            self.attachments_list.addItem(f"Вложение: {attachment.filename} ({_format_size(attachment.size)})")
        self.attachments_list.show()

    def on_save_attachment(self, item: QListWidgetItem) -> None:
        index = self.attachments_list.row(item)
        attachment = self.current_attachments[index]
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
        super().closeEvent(event)
