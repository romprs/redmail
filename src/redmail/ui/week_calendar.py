from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from redmail.calendar_store import Event

# Недельная сетка "как в Google Calendar/Outlook" (см. присланный
# пользователем скриншот) — часы по вертикали, дни по горизонтали, события
# позиционированы блоками по времени, а не строками плоского списка.

_DAY_NAMES = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
_EVENT_COLOR = "#3B6FB6"
_TODAY_COLOR = "#1A73E8"


def week_start_for(day: date) -> date:
    return day - timedelta(days=day.weekday())


class _EventBlock(QFrame):
    clicked = Signal(object)
    doubleClicked = Signal(object)

    def __init__(self, calendar_event: Event, parent: QWidget | None = None):
        super().__init__(parent)
        # ВАЖНО: не называть этот атрибут self.event — QWidget.event() уже
        # существует как виртуальный метод самого Qt (обрабатывает всю
        # доставку событий виджету); присвоив self.event обычное значение,
        # затираем метод, и первый же внутренний вызов self.event(...) из
        # недр Qt падает с "'Event' object is not callable". Поймано именно
        # так при первом же офлайн-смоук-тесте.
        self.calendar_event = calendar_event
        self.setStyleSheet(
            f"background-color: {_EVENT_COLOR}; border-radius: 4px;"
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(0)
        start_local = calendar_event.dtstart.astimezone()
        time_text = "" if calendar_event.all_day else start_local.strftime("%H:%M")
        text = f"{time_text} {calendar_event.summary or '(без темы)'}".strip()
        label = QLabel(text, self)
        label.setStyleSheet("color: white; background: transparent;")
        label.setWordWrap(True)
        layout.addWidget(label)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.clicked.emit(self.calendar_event)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.doubleClicked.emit(self.calendar_event)
        super().mouseDoubleClickEvent(event)


class WeekHeaderWidget(QWidget):
    """Строка дат над сеткой — не прокручивается вместе с часами."""

    TIME_AXIS_WIDTH = 52

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self._week_start = week_start_for(date.today())

    def set_week_start(self, week_start: date) -> None:
        self._week_start = week_start
        self.update()

    def _day_column_width(self) -> float:
        return max(1.0, (self.width() - self.TIME_AXIS_WIDTH) / 7)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        col_w = self._day_column_width()
        today = date.today()
        for i, name in enumerate(_DAY_NAMES):
            day = self._week_start + timedelta(days=i)
            x = self.TIME_AXIS_WIDTH + i * col_w
            rect = QRectF(x, 0, col_w, self.height())
            if day == today:
                painter.setPen(QColor(_TODAY_COLOR))
            else:
                painter.setPen(self.palette().windowText().color())
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{name} {day.day}")
        painter.end()


class AllDayRowWidget(QWidget):
    """Полоса событий "весь день" над часовой сеткой — как в референсе
    пользователя (день рождения растянут на всю ширину дня сверху)."""

    TIME_AXIS_WIDTH = WeekHeaderWidget.TIME_AXIS_WIDTH
    eventClicked = Signal(object)
    eventDoubleClicked = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._week_start = week_start_for(date.today())
        self._events: list[Event] = []
        self._blocks: list[_EventBlock] = []
        self.setMinimumHeight(1)

    def set_week(self, week_start: date, all_day_events: list[Event]) -> None:
        self._week_start = week_start
        self._events = all_day_events
        self._relayout()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._relayout()

    def _day_column_width(self) -> float:
        return max(1.0, (self.width() - self.TIME_AXIS_WIDTH) / 7)

    def _relayout(self) -> None:
        for block in self._blocks:
            block.deleteLater()
        self._blocks = []

        col_w = self._day_column_width()
        row_height = 22
        stack_by_day: dict[int, int] = {}
        max_stack = 0
        for ev in self._events:
            day_index = (ev.dtstart.astimezone().date() - self._week_start).days
            if not (0 <= day_index < 7):
                continue
            row = stack_by_day.get(day_index, 0)
            stack_by_day[day_index] = row + 1
            max_stack = max(max_stack, row + 1)

            block = _EventBlock(ev, self)
            x = self.TIME_AXIS_WIDTH + day_index * col_w
            block.setGeometry(int(x) + 2, row * (row_height + 2), int(col_w) - 4, row_height)
            block.clicked.connect(self.eventClicked.emit)
            block.doubleClicked.connect(self.eventDoubleClicked.emit)
            block.show()
            self._blocks.append(block)

        self.setFixedHeight(max(1, max_stack * (row_height + 2)))


class WeekGridWidget(QWidget):
    """Часовая сетка одной недели с блоками событий, позиционированными по
    времени начала/длительности внутри своей колонки-дня."""

    HOUR_HEIGHT = 48
    TIME_AXIS_WIDTH = WeekHeaderWidget.TIME_AXIS_WIDTH
    eventClicked = Signal(object)
    eventDoubleClicked = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(self.HOUR_HEIGHT * 24)
        self._week_start = week_start_for(date.today())
        self._events: list[Event] = []
        self._blocks: list[_EventBlock] = []

    def set_week(self, week_start: date, timed_events: list[Event]) -> None:
        self._week_start = week_start
        self._events = timed_events
        self._relayout()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._relayout()

    def _day_column_width(self) -> float:
        return max(1.0, (self.width() - self.TIME_AXIS_WIDTH) / 7)

    def _relayout(self) -> None:
        for block in self._blocks:
            block.deleteLater()
        self._blocks = []

        col_w = self._day_column_width()
        for ev in self._events:
            start_local = ev.dtstart.astimezone()
            end_local = ev.dtend.astimezone()
            day_index = (start_local.date() - self._week_start).days
            if not (0 <= day_index < 7):
                continue

            start_minutes = start_local.hour * 60 + start_local.minute
            duration_minutes = max(20, (end_local - start_local).total_seconds() / 60)
            y = start_minutes / 60 * self.HOUR_HEIGHT
            h = duration_minutes / 60 * self.HOUR_HEIGHT
            x = self.TIME_AXIS_WIDTH + day_index * col_w

            block = _EventBlock(ev, self)
            block.setGeometry(int(x) + 2, int(y), int(col_w) - 4, max(20, int(h)))
            block.clicked.connect(self.eventClicked.emit)
            block.doubleClicked.connect(self.eventDoubleClicked.emit)
            block.show()
            self._blocks.append(block)
        self.update()

    def scroll_position_for_now(self) -> int:
        now = datetime.now()
        return max(0, int((now.hour - 1) * self.HOUR_HEIGHT))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        grid_color = self.palette().mid().color()
        pen = QPen(grid_color)
        painter.setPen(pen)

        col_w = self._day_column_width()
        today_index = (date.today() - self._week_start).days

        for hour in range(25):
            y = hour * self.HOUR_HEIGHT
            painter.drawLine(self.TIME_AXIS_WIDTH, y, self.width(), y)
            if hour < 24:
                painter.drawText(
                    QRectF(0, y + 2, self.TIME_AXIS_WIDTH - 6, self.HOUR_HEIGHT),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                    f"{hour:02d}:00",
                )

        for i in range(8):
            x = self.TIME_AXIS_WIDTH + i * col_w
            painter.drawLine(int(x), 0, int(x), self.height())

        if 0 <= today_index < 7:
            highlight = QColor(_TODAY_COLOR)
            highlight.setAlpha(18)
            x = self.TIME_AXIS_WIDTH + today_index * col_w
            painter.fillRect(QRectF(x, 0, col_w, self.height()), highlight)

        painter.end()
