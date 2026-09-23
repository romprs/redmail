"""Резидент напоминаний: значок в трее, окно напоминания, голос.

Отдельная программа (redmail-reminder), а не часть почтового клиента:
напоминать о встрече нужно и тогда, когда клиент закрыт. Читает тот же
профиль (календарь), ничего в него не пишет, кроме отметок о показанных
напоминаниях.

Голос — просьба голосовому помощнику проговорить текст (см.
voice_client.speak): своего синтеза речи здесь нет и быть не должно.
"""
from __future__ import annotations

import html
import sys
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QTextBrowser,
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


def _dot_icon(color: str, size: int = 12) -> QIcon:
    """Цветной кружок — маркер календаря в меню «Сегодня»."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color or "#3B6FB6"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return QIcon(pixmap)


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


def description_html(text: str) -> str:
    """Текст встречи для окна напоминания: всё экранировано, ссылки http(s)
    кликабельны, переносы строк сохранены."""
    if not text:
        return ""
    parts: list[str] = []
    position = 0
    for url in reminders.links_in(text):
        index = text.find(url, position)
        if index < 0:
            continue
        parts.append(html.escape(text[position:index]))
        parts.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(url)}</a>')
        position = index + len(url)
    parts.append(html.escape(text[position:]))
    return "<div style='white-space: pre-wrap'>" + "".join(parts) + "</div>"


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

        # Чужая встреча: кто позвал и в каком календаре она лежит. По одной
        # теме этого не понять, а для встречи из календаря коллеги это
        # главное (пожелание: «про чужие события упоминать по автору»).
        details = []
        if not reminder.mine and reminder.organizer:
            details.append(f"Организатор: {reminder.organizer}")
        if reminder.calendar_name:
            details.append(f"Календарь: {reminder.calendar_name}")
        author = QLabel(" · ".join(details), self)
        author.setTextFormat(Qt.TextFormat.PlainText)
        author.setWordWrap(True)
        author.setVisible(bool(details))
        place = QLabel(reminder.location, self)
        place.setTextFormat(Qt.TextFormat.PlainText)
        place.setWordWrap(True)
        place.setVisible(bool(reminder.location))

        # Текст встречи — прямо в напоминании: в нём бывают ссылки на
        # видеовстречу, и открывать ради них календарь незачем (пожелание
        # пользователя). Текст пишет автор приглашения, поэтому он
        # экранируется, а ссылками становятся только http(s).
        self.description = QTextBrowser(self)
        self.description.setOpenLinks(False)
        self.description.anchorClicked.connect(self._open_link)
        self.description.setHtml(description_html(reminder.description))
        self.description.setVisible(bool(reminder.description.strip()))
        self.description.setMinimumHeight(90)

        self.join_link = reminders.meeting_link(reminder.description)
        join_button = QPushButton("Подключиться к встрече", self)
        join_button.setVisible(bool(self.join_link))
        join_button.clicked.connect(lambda: self._open_link(QUrl(self.join_link)))

        open_button = QPushButton("Открыть календарь", self)
        open_button.clicked.connect(self._open_calendar)
        snooze_button = QPushButton(f"Отложить на {SNOOZE_MINUTES} минут", self)
        snooze_button.clicked.connect(self._snooze)
        close_button = QPushButton("Закрыть", self)
        close_button.clicked.connect(self.accept)
        close_button.setDefault(True)

        buttons = QHBoxLayout()
        buttons.addWidget(join_button)
        buttons.addWidget(open_button)
        buttons.addWidget(snooze_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(summary)
        layout.addWidget(when)
        layout.addWidget(author)
        layout.addWidget(place)
        layout.addWidget(self.description, 1)
        layout.addLayout(buttons)
        self.resize(520, 320 if reminder.description.strip() else 180)

    def _open_link(self, url: QUrl) -> None:
        """Открыть ссылку в браузере — только http(s)."""
        if url.scheme().lower() not in ("http", "https"):
            return
        QDesktopServices.openUrl(url)

    def _open_calendar(self) -> None:
        """Календарь на этой встрече; почта закрыта — запускается сама."""
        result = voice_client.open_mail_calendar(self.reminder.uid, self.reminder.dtstart)
        if result == voice_client.FAILED:
            QMessageBox.warning(self, "Календарь", "Не удалось запустить почтовый клиент.")
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
        self._tasks_path = profile.tasks_db_path()
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
        open_action.triggered.connect(lambda: self._open_calendar_from_menu())
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
        """Встречи сегодня — по календарям: заголовок календаря и у каждой
        встречи цветной кружок его цвета (пожелание: «разделение по
        календарям не мешало бы или маркер» — одна и та же встреча из двух
        календарей выглядела дублем)."""
        self._today_menu.clear()
        events = reminders.today_events(self._calendar_path, datetime.now().astimezone())
        if not events:
            empty = self._today_menu.addAction("Встреч нет")
            empty.setEnabled(False)
            return
        for calendar, calendar_events in reminders.group_by_calendar(self._calendar_path, events):
            self._today_menu.addSection(calendar.name.replace("&", "&&"))
            icon = _dot_icon(calendar.color)
            for event in calendar_events:
                start = event.dtstart.astimezone()
                # «&» в теме встречи QMenu считает подчёркиванием буквы — удваиваем.
                title = (event.summary or "(без темы)").replace("&", "&&")
                action = self._today_menu.addAction(icon, f"{start:%H:%M}  {title}")
                action.triggered.connect(
                    lambda _checked=False, uid=event.uid, when=event.dtstart: self._open_calendar_from_menu(uid, when)
                )

    def _open_calendar_from_menu(self, uid: str = "", start: datetime | None = None) -> None:
        """Календарь (а по клику на встречу — и она сама). Почта закрыта —
        запускаем её, как голосовое «создать встречу»."""
        result = voice_client.open_mail_calendar(uid, start)
        if result == voice_client.LAUNCHED:
            self.tray.showMessage("Календарь", "Запускаю почтовый клиент…")
        elif result == voice_client.FAILED:
            self.tray.showMessage("Календарь", "Не удалось запустить почтовый клиент.")

    def check_now(self) -> None:
        if not self._enabled:
            return
        now = datetime.now().astimezone()
        # Настройку чужих встреч читаем каждый раз: её меняют в почте, а
        # резидент живёт отдельно и перезапускать его ради этого незачем.
        policy = reminders.OthersPolicy.load()
        for reminder in reminders.due_reminders(self._calendar_path, self._state, now, policy):
            self._fire(reminder, now)
        # Сроки задач ежедневника — та же напоминалка.
        for reminder in reminders.task_reminders(self._tasks_path, self._state, now):
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
    applog.setup_logging(applog.REMINDER_LOG_FILE_NAME)
    app = QApplication(argv)
    app.setApplicationName("redmail-reminder")
    app.setQuitOnLastWindowClosed(False)  # окна напоминаний приходят и уходят, резидент живёт
    if not QSystemTrayIcon.isSystemTrayAvailable():
        _log.error("В этой сессии нет системного лотка — напоминания показывать негде")
        return 1
    # Ссылка на резидент обязательна до конца работы приложения. Без неё
    # Python удалял объект сразу после создания: значок в трее оставался,
    # а таймер проверки календаря и пункты меню молча переставали работать —
    # напоминание не всплывало, «Сегодня» и «Открыть календарь» не
    # отзывались (найдено на .80: встреча с напоминанием в 22:38 так и не
    # напомнила).
    tray = ReminderTray(app)
    _log.info("Резидент напоминаний запущен (профиль %s)", profile.profile_dir())
    exit_code = app.exec()
    del tray
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
