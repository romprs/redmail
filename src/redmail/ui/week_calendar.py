from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from redmail.calendar_store import Event

# Недельная сетка "как в Google Calendar/Outlook" (см. присланный
# пользователем скриншот) — часы по вертикали, дни по горизонтали, события
# позиционированы блоками по времени, а не строками плоского списка.

_DAY_NAMES = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
_ORGANIZER_COLOR = "#3B6FB6"  # мои встречи (я организатор)
_ATTENDEE_COLOR = "#8B5CB6"  # встречи, куда меня пригласили
_ALL_DAY_COLOR = "#7986CB"
_TODAY_COLOR = "#1A73E8"
_NOW_LINE_COLOR = "#E64A4A"


def week_start_for(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _event_color(calendar_event: Event) -> str:
    if calendar_event.all_day:
        return _ALL_DAY_COLOR
    return _ORGANIZER_COLOR if calendar_event.is_organizer else _ATTENDEE_COLOR


_DRAG_THRESHOLD_PX = 6


class _EventBlock(QFrame):
    clicked = Signal(object)
    doubleClicked = Signal(object)
    # (calendar_event, geometry-до-перетаскивания, geometry-после) — считать
    # день/минуты переноса из разницы геометрий удобнее делать в сетке,
    # которая уже знает ширину колонки/масштаб часа, а не здесь.
    dragFinished = Signal(object, object, object)

    def __init__(self, calendar_event: Event, parent: QWidget | None = None, *, pill: bool = False):
        super().__init__(parent)
        # ВАЖНО: не называть этот атрибут self.event — QWidget.event() уже
        # существует как виртуальный метод самого Qt (обрабатывает всю
        # доставку событий виджету); присвоив self.event обычное значение,
        # затираем метод, и первый же внутренний вызов self.event(...) из
        # недр Qt падает с "'Event' object is not callable". Поймано именно
        # так при первом же офлайн-смоук-тесте.
        self.calendar_event = calendar_event
        self._color = _event_color(calendar_event)
        self._radius = "11px" if pill else "6px"
        self._selected = False
        self._apply_style()
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Перетаскивать можно только свои встречи (я организатор) — чужие
        # переносить нечем: у участника нет права менять чужое время, только
        # отвечать на приглашение (см. invite bar в почте).
        self._draggable = calendar_event.is_organizer and not calendar_event.all_day
        self._drag_start_mouse = None
        self._drag_start_geom = None
        self._dragging = False
        self._suppress_click = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 2, 7, 2)
        layout.setSpacing(0)
        start_local = calendar_event.dtstart.astimezone()
        time_text = "" if calendar_event.all_day else start_local.strftime("%H:%M")
        text = f"{time_text} {calendar_event.summary or '(без темы)'}".strip()
        label = QLabel(text, self)
        label.setStyleSheet("color: white; background: transparent;")
        # Таблетке "весь день" перенос только мешает — узкая колонка и
        # заголовок пары строк сминались в кашу; здесь одна строка,
        # обрезанная по ширине, как в референсе.
        label.setWordWrap(not pill)
        layout.addWidget(label)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self._draggable:
            self._drag_start_mouse = event.globalPosition().toPoint()
            self._drag_start_geom = self.geometry()
        self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._drag_start_mouse is None:
            super().mouseMoveEvent(event)
            return
        delta = event.globalPosition().toPoint() - self._drag_start_mouse
        if not self._dragging and delta.manhattanLength() > _DRAG_THRESHOLD_PX:
            self._dragging = True
            self.raise_()
        if self._dragging:
            self.move(self._drag_start_geom.topLeft() + delta)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._dragging:
            self._dragging = False
            self.dragFinished.emit(self.calendar_event, self._drag_start_geom, self.geometry())
        elif self._suppress_click:
            self._suppress_click = False
        else:
            self.clicked.emit(self.calendar_event)
        self._drag_start_mouse = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt override
        # Qt шлёт Press-Release-DoubleClick-Release на двойной клик — без
        # этого второй Release снова дошёл бы до "else" в mouseReleaseEvent
        # и породил бы лишний clicked поверх doubleClicked.
        self._suppress_click = True
        self.doubleClicked.emit(self.calendar_event)
        super().mouseDoubleClickEvent(event)

    def set_selected(self, selected: bool) -> None:
        # Раньше "Отменить встречу" действовал на self.selected_calendar_event
        # без какой-либо видимой подсветки — пользователь не мог понять,
        # какое событие сейчас выбрано. Белая рамка — видимый маркер выбора.
        self._selected = selected
        self._apply_style()

    def _apply_style(self) -> None:
        border = "2px solid white" if self._selected else "1px solid transparent"
        self.setStyleSheet(
            f"background-color: {self._color}; border-radius: {self._radius}; border: {border};"
        )


class WeekHeaderWidget(QWidget):
    """Строка дат над сеткой — не прокручивается вместе с часами. Дата
    сегодняшнего дня — залитый кружок, как в референсе пользователя."""

    TIME_AXIS_WIDTH = 52
    _CIRCLE_DIAMETER = 28

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(52)
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
        text_color = self.palette().windowText().color()
        for i, name in enumerate(_DAY_NAMES):
            day = self._week_start + timedelta(days=i)
            x = self.TIME_AXIS_WIDTH + i * col_w
            is_today = day == today

            name_rect = QRectF(x, 2, col_w, 18)
            painter.setPen(QColor(_TODAY_COLOR) if is_today else text_color)
            painter.drawText(name_rect, Qt.AlignmentFlag.AlignCenter, name)

            circle_rect = QRectF(
                x + col_w / 2 - self._CIRCLE_DIAMETER / 2, 20, self._CIRCLE_DIAMETER, self._CIRCLE_DIAMETER
            )
            if is_today:
                painter.setBrush(QColor(_TODAY_COLOR))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(circle_rect)
                painter.setPen(QColor("white"))
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(text_color)
            painter.drawText(circle_rect, Qt.AlignmentFlag.AlignCenter, str(day.day))
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

            block = _EventBlock(ev, self, pill=True)
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
    _SNAP_MINUTES = 15
    eventClicked = Signal(object)
    eventDoubleClicked = Signal(object)
    # (calendar_event, day_delta, minute_delta) — сколько дней/минут
    # перенесли перетаскиванием; сама запись в calendar_store и рассылка
    # обновления — забота MainWindow (там есть SMTP-аккаунт).
    eventDragRescheduled = Signal(object, int, int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(self.HOUR_HEIGHT * 24)
        self._week_start = week_start_for(date.today())
        self._events: list[Event] = []
        self._blocks: list[_EventBlock] = []

        # Красная линия "сейчас" должна сама сдвигаться, пока приложение
        # открыто — минутной точности достаточно, не гоняем чаще раза в минуту.
        self._now_timer = QTimer(self)
        self._now_timer.timeout.connect(self.update)
        self._now_timer.start(60_000)

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
            block.dragFinished.connect(self._on_block_drag_finished)
            block.show()
            self._blocks.append(block)
        self.update()

    def _on_block_drag_finished(self, calendar_event: Event, old_geom, new_geom) -> None:
        col_w = self._day_column_width()
        day_delta = round((new_geom.x() - old_geom.x()) / col_w)
        minutes_per_pixel = 60 / self.HOUR_HEIGHT
        raw_minute_delta = (new_geom.y() - old_geom.y()) * minutes_per_pixel
        minute_delta = round(raw_minute_delta / self._SNAP_MINUTES) * self._SNAP_MINUTES
        if day_delta == 0 and minute_delta == 0:
            self._relayout()  # перетащили и отпустили почти на том же месте — просто вернуть на место
            return
        self.eventDragRescheduled.emit(calendar_event, day_delta, minute_delta)

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

            now = datetime.now()
            now_y = (now.hour * 60 + now.minute) / 60 * self.HOUR_HEIGHT
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            now_pen = QPen(QColor(_NOW_LINE_COLOR))
            now_pen.setWidth(2)
            painter.setPen(now_pen)
            painter.setBrush(QColor(_NOW_LINE_COLOR))
            painter.drawEllipse(QRectF(x - 4, now_y - 4, 8, 8))
            painter.drawLine(int(x), int(now_y), int(x + col_w), int(now_y))

        painter.end()
