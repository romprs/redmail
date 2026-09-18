"""Резидент напоминаний: значок в трее, окно напоминания, голос.

Отдельная программа (redmail-reminder), а не часть почтового клиента:
напоминать о встрече нужно и тогда, когда клиент закрыт. Читает тот же
профиль (календарь), ничего в него не пишет, кроме отметок о показанных
напоминаниях.

Голос — просьба голосовому помощнику проговорить текст (см.
voice_client.speak): своего синтеза речи здесь нет и быть не должно.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
)

from redmail import applog, profile, reminders, voice_client
from redmail.applog import get_logger

_log = get_logger("reminder")

#: Как часто заглядывать в календарь. Полминуты хватает: напоминание за N
#: минут с точностью до полуминуты человеку незаметно, а нагрузки нет.
POLL_SECONDS = 30

#: На сколько откладывает кнопка «Отложить».
SNOOZE_MINUTES = 5


def _bell_icon(size: int = 22) -> QIcon:
    """Колокольчик рисуем сами — как и значки в окне встречи: на этой
    платформе уже находились пробелы в покрытии эмодзи-шрифтом."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#3B6FB6"))
    painter.setPen(Qt.PenStyle.NoPen)
    path = QPainterPath()
    path.moveTo(size * 0.22, size * 0.68)
    path.cubicTo(size * 0.30, size * 0.60, size * 0.26, size * 0.18, size * 0.5, size * 0.16)
    path.cubicTo(size * 0.74, size * 0.18, size * 0.70, size * 0.60, size * 0.78, size * 0.68)
    path.closeSubpath()
    painter.drawPath(path)
    painter.drawEllipse(size * 0.42, size * 0.70, size * 0.16, size * 0.16)
    painter.end()
    return QIcon(pixmap)


class ReminderWindow(QDialog):
    """Окно напоминания: тема, время, место и что с этим делать."""

    def __init__(self, reminder: reminders.Reminder, parent=None) -> None:
        super().__init__(parent)
        self.reminder = reminder
        self.snoozed = False
        self.setWindowTitle("Напоминание")
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowCloseButtonHint
        )
        start = reminder.dtstart.astimezone()
        end = reminder.dtend.astimezone()

        summary = QLabel(reminder.summary, self)
        # Тема и место приходят из письма-приглашения, то есть их пишет кто
        # угодно: QLabel сам распознаёт разметку, и тема с тегами
        # отрисовалась бы как HTML. Показываем ровно тот текст, что есть.
        summary.setTextFormat(Qt.TextFormat.PlainText)
        font = summary.font()
        font.setPointSize(font.pointSize() + 3)
        font.setBold(True)
        summary.setFont(font)
        summary.setWordWrap(True)

        when = QLabel(f"{start:%d.%m.%Y %H:%M} — {end:%H:%M}", self)
        place = QLabel(reminder.location, self)
        place.setTextFormat(Qt.TextFormat.PlainText)
        place.setWordWrap(True)
        place.setVisible(bool(reminder.location))

        open_button = QPushButton("Открыть календарь", self)
        open_button.clicked.connect(self._open_calendar)
        snooze_button = QPushButton(f"Отложить на {SNOOZE_MINUTES} минут", self)
        snooze_button.clicked.connect(self._snooze)
        close_button = QPushButton("Закрыть", self)
        close_button.clicked.connect(self.accept)
        close_button.setDefault(True)

        buttons = QHBoxLayout()
        buttons.addWidget(open_button)
        buttons.addWidget(snooze_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(summary)
        layout.addWidget(when)
        layout.addWidget(place)
        layout.addLayout(buttons)
        self.resize(420, 180)

    def _open_calendar(self) -> None:
        if not voice_client.focus_mail_client(section="calendar"):
            QMessageBox.information(
                self, "Почтовый клиент закрыт", "Запустите почтовый клиент, чтобы открыть календарь."
            )
            return
        self.accept()

    def _snooze(self) -> None:
        self.snoozed = True
        self.accept()


class ReminderTray:
    """Значок в трее: следит за календарём текущего дня и напоминает."""

    def __init__(self, app: QApplication) -> None:
        self._app = app
        self._calendar_path = profile.calendar_db_path()
        self._state = reminders.ReminderState(profile.profile_dir() / reminders.STATE_FILE)
        self._windows: list[ReminderWindow] = []
        self._enabled = True

        self.tray = QSystemTrayIcon(_bell_icon(), app)
        self.tray.setToolTip("Напоминания о встречах")
        self.menu = QMenu()
        self._today_menu = self.menu.addMenu("Сегодня")
        self.menu.addSeparator()
        self._enabled_action = QAction("Напоминать", self.menu, checkable=True, checked=True)
        self._enabled_action.toggled.connect(self._on_enabled_toggled)
        self.menu.addAction(self._enabled_action)
        open_action = QAction("Открыть календарь", self.menu)
        open_action.triggered.connect(lambda: voice_client.focus_mail_client(section="calendar"))
        self.menu.addAction(open_action)
        self.menu.addSeparator()
        quit_action = QAction("Выход", self.menu)
        quit_action.triggered.connect(app.quit)
        self.menu.addAction(quit_action)
        self.menu.aboutToShow.connect(self._refresh_today_menu)
        self.tray.setContextMenu(self.menu)
        self.tray.show()

        self._timer = QTimer(app)
        self._timer.setInterval(POLL_SECONDS * 1000)
        self._timer.timeout.connect(self.check_now)
        self._timer.start()
        QTimer.singleShot(0, self.check_now)

    def _on_enabled_toggled(self, enabled: bool) -> None:
        self._enabled = enabled
        _log.info("Напоминания %s", "включены" if enabled else "выключены")

    def _refresh_today_menu(self) -> None:
        self._today_menu.clear()
        events = reminders.today_events(self._calendar_path, datetime.now().astimezone())
        if not events:
            empty = self._today_menu.addAction("Встреч нет")
            empty.setEnabled(False)
            return
        for event in events:
            start = event.dtstart.astimezone()
            # «&» в теме встречи QMenu считает подчёркиванием буквы — удваиваем.
            title = (event.summary or "(без темы)").replace("&", "&&")
            action = self._today_menu.addAction(f"{start:%H:%M}  {title}")
            action.triggered.connect(lambda _checked=False: voice_client.focus_mail_client(section="calendar"))

    def check_now(self) -> None:
        if not self._enabled:
            return
        now = datetime.now().astimezone()
        for reminder in reminders.due_reminders(self._calendar_path, self._state, now):
            self._fire(reminder, now)

    def _fire(self, reminder: reminders.Reminder, now: datetime) -> None:
        _log.info("Напоминание: %s в %s (%s)", reminder.summary, reminder.dtstart.astimezone(), reminder.mode)
        self._state.mark_shown(reminder, now)
        if reminder.speaks:
            voice_client.speak(reminders.spoken_text(reminder, now))
        if reminder.shows_window:
            self._show_window(reminder)
        elif reminder.speaks:
            # Голосом попросили, но окна не надо — всплывающая подсказка у
            # значка остаётся единственным следом на экране.
            self.tray.showMessage("Напоминание", reminder.summary, _bell_icon(), 10_000)

    def _show_window(self, reminder: reminders.Reminder) -> None:
        window = ReminderWindow(reminder)
        self._windows.append(window)

        def finished(_result: int = 0) -> None:
            if window.snoozed:
                self._state.snooze(reminder, datetime.now().astimezone() + timedelta(minutes=SNOOZE_MINUTES))
            if window in self._windows:
                self._windows.remove(window)

        window.finished.connect(finished)
        window.show()
        window.raise_()
        window.activateWindow()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    applog.setup_logging()
    app = QApplication(argv)
    app.setApplicationName("redmail-reminder")
    app.setQuitOnLastWindowClosed(False)  # окна напоминаний приходят и уходят, резидент живёт
    if not QSystemTrayIcon.isSystemTrayAvailable():
        _log.error("В этой сессии нет системного лотка — напоминания показывать негде")
        return 1
    ReminderTray(app)
    _log.info("Резидент напоминаний запущен (профиль %s)", profile.profile_dir())
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
