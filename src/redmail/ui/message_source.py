"""Окно «Исходный текст письма»: оригинал письма с сервера или из архива —
заголовки и тело как есть (RFC 822), плюс разобранные заголовки
таблицей. Нужно для разбора проблем с показом писем, проверки маршрута
письма (Received), подписи и спам-заголовков."""
from __future__ import annotations

from email import message_from_bytes
from email.policy import default as default_policy
from pathlib import Path

from PySide6.QtGui import QFontDatabase, QKeySequence, QShortcut, QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from redmail import mail_export

#: Больше этого в окне не показываем: QPlainTextEdit на десятках мегабайт
#: base64-вложений заметно тормозит. Полностью — через «Сохранить как .eml».
MAX_SHOWN_BYTES = 2 * 1024 * 1024


def split_headers(raw: bytes) -> tuple[str, str]:
    """(заголовки, тело) исходного письма — текстом, без разбора MIME."""
    text = raw.decode("utf-8", errors="replace")
    for separator in ("\r\n\r\n", "\n\n"):
        index = text.find(separator)
        if index >= 0:
            return text[:index], text[index + len(separator):]
    return text, ""


def decoded_headers(raw: bytes) -> list[tuple[str, str]]:
    """Заголовки с раскодированными =?utf-8?...?= — для таблицы. Кривой
    заголовок не должен ронять окно: такой показывается как есть."""
    headers_text, _body = split_headers(raw)
    message = message_from_bytes(headers_text.encode("utf-8", errors="replace") + b"\r\n\r\n", policy=default_policy)
    result = []
    for name, value in message.raw_items():
        try:
            shown = str(default_policy.header_fetch_parse(name, value))
        except Exception:
            shown = str(value)
        result.append((name, " ".join(shown.split())))
    return result


class MessageSourceWindow(QWidget):
    def __init__(self, title: str, raw: bytes, *, original: bool = True, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"Исходный текст — {title}")
        self.resize(900, 700)
        self._raw = raw
        self._title = title

        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        notes = []
        if not original:
            notes.append(
                "Сервер недоступен или письма на нём уже нет — показано письмо, собранное из локальной копии. "
                "Это не оригинал: служебные заголовки (Received, подписи) не сохранились."
            )
        shown = raw
        if len(raw) > MAX_SHOWN_BYTES:
            shown = raw[:MAX_SHOWN_BYTES]
            notes.append(
                f"Показано {MAX_SHOWN_BYTES // (1024 * 1024)} МБ из {len(raw) / (1024 * 1024):.1f} МБ — "
                "полностью письмо можно сохранить кнопкой «Сохранить как .eml»."
            )
        self.note_label = QLabel("\n\n".join(notes), self)
        self.note_label.setWordWrap(True)
        self.note_label.setVisible(bool(notes))

        self.source_edit = QPlainTextEdit(self)
        self.source_edit.setReadOnly(True)
        self.source_edit.setFont(mono)
        self.source_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.source_edit.setPlainText(shown.decode("utf-8", errors="replace"))

        self.headers_table = QTableWidget(self)
        self.headers_table.setColumnCount(2)
        self.headers_table.setHorizontalHeaderLabels(["Заголовок", "Значение"])
        self.headers_table.verticalHeader().setVisible(False)
        self.headers_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.headers_table.setWordWrap(True)
        rows = decoded_headers(raw)
        self.headers_table.setRowCount(len(rows))
        for row, (name, value) in enumerate(rows):
            self.headers_table.setItem(row, 0, QTableWidgetItem(name))
            self.headers_table.setItem(row, 1, QTableWidgetItem(value))
        header = self.headers_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.headers_table.resizeRowsToContents()

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self.source_edit, "Исходный текст")
        self.tabs.addTab(self.headers_table, "Заголовки")

        self.find_edit = QLineEdit(self)
        self.find_edit.setPlaceholderText("Найти в исходном тексте (Enter — далее, Shift+Enter — назад)")
        self.find_edit.returnPressed.connect(lambda: self.find_next(backward=False))
        QShortcut(QKeySequence.StandardKey.Find, self, activated=self._focus_find)
        QShortcut(QKeySequence("Shift+Return"), self.find_edit, activated=lambda: self.find_next(backward=True))
        copy_button = QPushButton("Копировать всё", self)
        copy_button.clicked.connect(self._copy)
        save_button = QPushButton("Сохранить как .eml…", self)
        save_button.clicked.connect(self._save)
        save_button.setEnabled(original)
        save_button.setToolTip("Сохранить оригинал письма файлом" if original else "Оригинал недоступен")
        close_button = QPushButton("Закрыть", self)
        close_button.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.find_edit, 1)
        buttons.addWidget(copy_button)
        buttons.addWidget(save_button)
        buttons.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.note_label)
        layout.addWidget(self.tabs, 1)
        layout.addLayout(buttons)

    def _focus_find(self) -> None:
        self.tabs.setCurrentWidget(self.source_edit)
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    def find_next(self, *, backward: bool) -> bool:
        text = self.find_edit.text()
        if not text:
            return False
        self.tabs.setCurrentWidget(self.source_edit)
        flags = QTextDocument.FindFlag.FindBackward if backward else QTextDocument.FindFlag(0)
        if self.source_edit.find(text, flags):
            return True
        # С начала (или с конца) документа — поиск по кругу.
        cursor = self.source_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End if backward else cursor.MoveOperation.Start)
        self.source_edit.setTextCursor(cursor)
        return self.source_edit.find(text, flags)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._raw.decode("utf-8", errors="replace"))

    def _save(self) -> None:
        name = mail_export.safe_name(self._title, 60) + ".eml"
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить письмо", name, "Письмо (*.eml)")
        if not path:
            return
        try:
            Path(path).write_bytes(self._raw)
        except OSError as exc:
            QMessageBox.warning(self, "Сохранить письмо", str(exc))
