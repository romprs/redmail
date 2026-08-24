from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

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
_SELECTED_DAY_COLOR = "#34A853"


def week_start_for(day: date) -> date:
    return day - timedelta(days=day.weekday())


def month_grid_range(anchor: date) -> tuple[date, date]:
    """(начало, конец-исключая) 6-недельной сетки месяца, содержащего
    `anchor` — начинается с понедельника той недели, куда попадает 1-е
    число, всегда ровно 42 дня (6 строк × 7 дней), как в Google
    Calendar/Outlook — не только "чистые" дни месяца, чтобы сетка не
    прыгала по высоте от месяца к месяцу."""
    first_of_month = anchor.replace(day=1)
    grid_start = week_start_for(first_of_month)
    return grid_start, grid_start + timedelta(days=42)


def _event_color(calendar_event: Event) -> str:
    # Ручной цвет (см. референс VK Mail — "Цвет события") имеет приоритет
    # над автоцветом по роли; автоцвет остаётся по умолчанию для событий,
    # где цвет никогда не задавали (включая все существующие до этой
    # возможности).
    if calendar_event.color:
        return calendar_event.color
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
    contextMenuRequested = Signal(object, object)  # (calendar_event, global_pos)

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
        self._radius = "11px" if pill else "4px"
        self._pill = pill
        self._selected = False
        self._apply_style()
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Перетаскивать можно только свои встречи (я организатор) — чужие
        # переносить нечем: у участника нет права менять чужое время, только
        # отвечать на приглашение (см. invite bar в почте). Таблетки в виде
        # месяца (pill=True) тоже не перетаскиваются: они лежат в обычном
        # QVBoxLayout (MonthCellWidget.events_layout) без переноса дня по
        # горизонтали — layout вернул бы таблетку на место после любой
        # перерисовки, а перенос между днями там вообще не реализован.
        # Раньше курсор/логика драга включались и там, из-за чего таблетку
        # можно было чуть сдвинуть мышью без всякого эффекта — выглядело как
        # "событие не переносится" в виде месяца.
        self._draggable = calendar_event.is_organizer and not calendar_event.all_day and not pill
        self._drag_start_mouse = None
        self._drag_start_geom = None
        self._dragging = False
        self._suppress_click = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 2, 7, 2)
        layout.setSpacing(0)
        start_local = calendar_event.dtstart.astimezone()
        end_local = calendar_event.dtend.astimezone()
        if calendar_event.all_day:
            time_text = ""
        elif pill:
            time_text = start_local.strftime("%H:%M")  # компактная таблетка — только начало, как в референсе
        else:
            # Полная карточка в недельной сетке — начало-конец, как в
            # референсе ("08:30-10:45 Собрание"); раньше показывалось
            # только начало, продолжительность была не видна.
            time_text = f"{start_local.strftime('%H:%M')}-{end_local.strftime('%H:%M')}"
        text = f"{time_text} {calendar_event.summary or '(без темы)'}".strip()
        label = QLabel(text, self)
        text_color = "white" if pill else "#202124"
        label.setStyleSheet(f"color: {text_color}; background: transparent; font-size: 11px;")
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
        # ВАЖНО: принять событие ПОСЛЕ super() и уже здесь, в конце — иначе
        # Qt считает клик "необработанным" (QFrame по умолчанию его
        # игнорирует) и пробрасывает его дальше родительскому виджету
        # сетки; там есть свой обработчик клика по ПУСТОМУ месту (создание
        # события) — из-за этого клик по уже существующему событию
        # одновременно открывал диалог создания нового и ломал
        # перетаскивание (drag так и не успевал начаться). Найдено по
        # жалобе "нельзя переместить событие мышью — сразу открывается
        # окно события".
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._drag_start_mouse is None:
            super().mouseMoveEvent(event)
            event.accept()
            return
        delta = event.globalPosition().toPoint() - self._drag_start_mouse
        if not self._dragging and delta.manhattanLength() > _DRAG_THRESHOLD_PX:
            self._dragging = True
            self.raise_()
        if self._dragging:
            self.move(self._drag_start_geom.topLeft() + delta)
        super().mouseMoveEvent(event)
        event.accept()

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
        event.accept()

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.contextMenuRequested.emit(self.calendar_event, event.globalPos())

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt override
        # Qt шлёт Press-Release-DoubleClick-Release на двойной клик — без
        # этого второй Release снова дошёл бы до "else" в mouseReleaseEvent
        # и породил бы лишний clicked поверх doubleClicked.
        self._suppress_click = True
        self.doubleClicked.emit(self.calendar_event)
        super().mouseDoubleClickEvent(event)
        event.accept()

    def set_selected(self, selected: bool) -> None:
        # Раньше "Отменить встречу" действовал на self.selected_calendar_event
        # без какой-либо видимой подсветки — пользователь не мог понять,
        # какое событие сейчас выбрано. Белая рамка — видимый маркер выбора.
        self._selected = selected
        self._apply_style()

    def _apply_style(self) -> None:
        # Карточка события в сетке — светлый фон с цветной полосой слева и
        # тёмным текстом, а не сплошная заливка цветом: так выглядят
        # события в референсе (VK Mail), в отличие от прежнего сплошного
        # цветного блока с белым текстом. Таблетки "весь день" — маленькие
        # плашки, там сплошная заливка читается лучше (компактно, мало
        # текста), поэтому для них старый стиль сохранён.
        # Была неверная догадка, что "внутренняя рамка" — это тонкая
        # обводка самой карточки, и её убирали, оставляя только border-left
        # (жалоба на результат: "убрал все рамки... оставив слева 2
        # черты" — border-radius с односторонним border-left у Qt рисуется
        # именно так, кривым обрубком). Внешняя обводка карточки — то, что
        # нужно было оставить, — восстановлена как было; настоящая "рамка,
        # обрамлявшая текст" — это стандартная рамка QPlainTextEdit у поля
        # описания в EventDialog (см. description_edit.setFrameShape ниже).
        if self._pill:
            outline = "2px solid #1A73E8" if self._selected else "1px solid transparent"
            self.setStyleSheet(
                f"background-color: {self._color}; border-radius: {self._radius}; border: {outline};"
            )
        else:
            outline = "2px solid #1A73E8" if self._selected else "1px solid #dadce0"
            self.setStyleSheet(
                f"background-color: #ffffff; border-radius: {self._radius}; border: {outline}; "
                f"border-left: 4px solid {self._color};"
            )


class WeekHeaderWidget(QWidget):
    """Строка дат над сеткой — не прокручивается вместе с часами. Дата
    сегодняшнего дня — залитый кружок, как в референсе пользователя."""

    TIME_AXIS_WIDTH = 52
    _CIRCLE_DIAMETER = 28
    # Клик по дате в шапке — "выбрать день" (жалоба: "в календаре нет
    # возможности выбрать день"): подсвечивает колонку в сетке и становится
    # днём по умолчанию для кнопки "Новое событие" на панели инструментов.
    dayClicked = Signal(object)  # date

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._week_start = week_start_for(date.today())
        self._selected_day: date | None = None

    def set_week_start(self, week_start: date) -> None:
        self._week_start = week_start
        self.update()

    def set_selected_day(self, day: date | None) -> None:
        self._selected_day = day
        self.update()

    def _day_column_width(self) -> float:
        return max(1.0, (self.width() - self.TIME_AXIS_WIDTH) / 7)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        x = event.position().x()
        if x >= self.TIME_AXIS_WIDTH:
            day_index = int((x - self.TIME_AXIS_WIDTH) / self._day_column_width())
            if 0 <= day_index < 7:
                self.dayClicked.emit(self._week_start + timedelta(days=day_index))
        super().mousePressEvent(event)

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
            elif day == self._selected_day:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                pen = QPen(QColor(_SELECTED_DAY_COLOR))
                pen.setWidth(2)
                painter.setPen(pen)
                painter.drawEllipse(circle_rect)
                painter.setPen(text_color)
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
    eventContextMenuRequested = Signal(object, object)  # (calendar_event, global_pos)

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
            block.contextMenuRequested.connect(self.eventContextMenuRequested.emit)
            block.show()
            self._blocks.append(block)

        self.setFixedHeight(max(1, max_stack * (row_height + 2)))


class WeekGridWidget(QWidget):
    """Часовая сетка одной недели с блоками событий, позиционированными по
    времени начала/длительности внутри своей колонки-дня."""

    # 48px/час умещал в обычную высоту окна (по умолчанию 640px) только
    # около 10 часов — приходилось много прокручивать, чтобы увидеть
    # остальной день (жалоба: "график ограничен 10 часами").
    HOUR_HEIGHT = 36
    TIME_AXIS_WIDTH = WeekHeaderWidget.TIME_AXIS_WIDTH
    _SNAP_MINUTES = 15
    eventClicked = Signal(object)
    eventDoubleClicked = Signal(object)
    eventContextMenuRequested = Signal(object, object)  # (calendar_event, global_pos)
    # (calendar_event, day_delta, minute_delta) — сколько дней/минут
    # перенесли перетаскиванием; сама запись в calendar_store и рассылка
    # обновления — забота MainWindow (там есть SMTP-аккаунт).
    eventDragRescheduled = Signal(object, int, int)
    # Клик/правый клик по ПУСТОМУ месту сетки (не по событию — блоки
    # событий перехватывают свои события мыши сами, сюда попадают только
    # клики мимо них) — день + минуты от полуночи, чтобы создать встречу
    # прямо на этом месте, как в Google Calendar/Outlook.
    emptySlotClicked = Signal(object, int)  # (date, minutes_from_midnight)
    emptySlotContextMenuRequested = Signal(object, int, object)  # (date, minutes, global_pos)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(self.HOUR_HEIGHT * 24)
        self._week_start = week_start_for(date.today())
        self._events: list[Event] = []
        self._blocks: list[_EventBlock] = []
        self._selected_day: date | None = None

        # Красная линия "сейчас" должна сама сдвигаться, пока приложение
        # открыто — минутной точности достаточно, не гоняем чаще раза в минуту.
        self._now_timer = QTimer(self)
        self._now_timer.timeout.connect(self.update)
        self._now_timer.start(60_000)

    def set_week(self, week_start: date, timed_events: list[Event]) -> None:
        self._week_start = week_start
        self._events = timed_events
        self._relayout()

    def set_selected_day(self, day: date | None) -> None:
        self._selected_day = day
        self.update()

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
            block.contextMenuRequested.connect(self.eventContextMenuRequested.emit)
            block.show()
            self._blocks.append(block)
        self.update()

    def _slot_at(self, pos) -> tuple[date, int] | None:
        """Переводит точку в координатах виджета в (день, минуты от
        полуночи, округлённые до 15 минут) — используется и для создания
        события кликом по пустому месту сетки, и для контекстного меню там
        же. Возвращает None вне часовой сетки (например, клик по оси времени
        слева)."""
        if pos.x() < self.TIME_AXIS_WIDTH:
            return None
        col_w = self._day_column_width()
        day_index = int((pos.x() - self.TIME_AXIS_WIDTH) / col_w)
        if not (0 <= day_index < 7):
            return None
        minutes = max(0, min(24 * 60 - self._SNAP_MINUTES, int(pos.y() / self.HOUR_HEIGHT * 60)))
        minutes = round(minutes / self._SNAP_MINUTES) * self._SNAP_MINUTES
        return self._week_start + timedelta(days=day_index), minutes

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        # Клики по самим событиям сюда не долетают — их обрабатывает
        # _EventBlock (дочерний виджет) сам; сюда попадают только клики
        # мимо блоков, то есть по пустому месту сетки.
        if event.button() == Qt.MouseButton.LeftButton:
            slot = self._slot_at(event.position().toPoint())
            if slot is not None:
                self.emptySlotClicked.emit(*slot)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt override
        slot = self._slot_at(event.pos())
        if slot is not None:
            self.emptySlotContextMenuRequested.emit(slot[0], slot[1], event.globalPos())

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
        # Раньше подписи часов рисовались тем же бледным пером, что и сами
        # линии сетки (palette().mid() — по жалобе "часы плохо видно" на
        # реальном мониторе это оказалось действительно едва различимо).
        # Линии — по-прежнему приглушённые, подписи — контрастным текстом.
        # Полупрозрачный текстовый цвет вместо palette().mid() — тот на
        # некоторых системных темах оказывается почти не виден на фоне сетки.
        text_color = self.palette().windowText().color()
        grid_color = QColor(text_color)
        grid_color.setAlpha(60)
        grid_pen = QPen(grid_color)

        col_w = self._day_column_width()
        today_index = (date.today() - self._week_start).days

        painter.setPen(grid_pen)
        for hour in range(25):
            y = hour * self.HOUR_HEIGHT
            painter.drawLine(self.TIME_AXIS_WIDTH, y, self.width(), y)

        for i in range(8):
            x = self.TIME_AXIS_WIDTH + i * col_w
            painter.drawLine(int(x), 0, int(x), self.height())

        painter.setPen(text_color)
        for hour in range(24):
            y = hour * self.HOUR_HEIGHT
            painter.drawText(
                QRectF(0, y + 2, self.TIME_AXIS_WIDTH - 6, self.HOUR_HEIGHT),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                f"{hour:02d}:00",
            )

        selected_index = (self._selected_day - self._week_start).days if self._selected_day else -1
        if 0 <= selected_index < 7 and selected_index != today_index:
            selected_highlight = QColor(_SELECTED_DAY_COLOR)
            selected_highlight.setAlpha(18)
            sx = self.TIME_AXIS_WIDTH + selected_index * col_w
            painter.fillRect(QRectF(sx, 0, col_w, self.height()), selected_highlight)

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


_MONTH_CELL_MAX_EVENTS = 3


class MonthCellWidget(QFrame):
    """Одна ячейка сетки месяца: число + до нескольких компактных "таблеток"
    событий (переиспользует _EventBlock в pill-режиме — та же логика
    клика/двойного клика/контекстного меню, что и в недельной сетке, без
    дублирования)."""

    dayClicked = Signal(object)  # date — клик по пустому месту ячейки
    dayDoubleClicked = Signal(object)  # date — двойной клик: перейти к неделе с этим днём
    eventClicked = Signal(object)
    eventDoubleClicked = Signal(object)
    eventContextMenuRequested = Signal(object, object)

    def __init__(self, day: date, parent: QWidget | None = None):
        super().__init__(parent)
        self.day = day
        self._in_month = True
        self._is_today = False
        self._is_selected = False
        self._event_blocks: list[_EventBlock] = []
        self.setMinimumHeight(84)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.day_label = QLabel(str(day.day), self)
        self.day_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

        self.events_layout = QVBoxLayout()
        self.events_layout.setContentsMargins(0, 0, 0, 0)
        self.events_layout.setSpacing(1)

        self.more_label = QLabel("", self)
        self.more_label.setStyleSheet("color: #5f6368; font-size: 10px; background: transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 2, 3, 2)
        layout.setSpacing(1)
        layout.addWidget(self.day_label)
        layout.addLayout(self.events_layout)
        layout.addWidget(self.more_label)
        layout.addStretch(1)

        self._apply_style()

    def set_state(self, *, in_month: bool, is_today: bool, is_selected: bool) -> None:
        self._in_month = in_month
        self._is_today = is_today
        self._is_selected = is_selected
        self._apply_style()

    def _apply_style(self) -> None:
        bg = "#ffffff" if self._in_month else "#f8f9fa"
        border = f"2px solid {_SELECTED_DAY_COLOR}" if self._is_selected else "1px solid #e0e0e0"
        self.setStyleSheet(f"MonthCellWidget {{ background-color: {bg}; border: {border}; }}")
        if self._is_today:
            self.day_label.setStyleSheet(
                f"background: {_TODAY_COLOR}; color: white; border-radius: 9px; "
                f"font-weight: 600; padding: 0px 5px; max-width: 18px;"
            )
        else:
            color = "#202124" if self._in_month else "#9aa0a6"
            self.day_label.setStyleSheet(f"color: {color}; background: transparent;")

    def set_events(self, events: list[Event]) -> None:
        for block in self._event_blocks:
            block.deleteLater()
        self._event_blocks = []
        visible = events[:_MONTH_CELL_MAX_EVENTS]
        for ev in visible:
            block = _EventBlock(ev, self, pill=True)
            block.setFixedHeight(15)
            block.clicked.connect(self.eventClicked.emit)
            block.doubleClicked.connect(self.eventDoubleClicked.emit)
            block.contextMenuRequested.connect(self.eventContextMenuRequested.emit)
            self.events_layout.addWidget(block)
            self._event_blocks.append(block)
        extra = len(events) - len(visible)
        self.more_label.setText(f"+{extra} ещё" if extra > 0 else "")

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.dayClicked.emit(self.day)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.dayDoubleClicked.emit(self.day)
        super().mouseDoubleClickEvent(event)


class MonthGridWidget(QWidget):
    """Вид "Месяц" — сетка 6×7 (всегда 6 недель, см. month_grid_range), в
    каждой ячейке компактный список событий этого дня."""

    eventClicked = Signal(object)
    eventDoubleClicked = Signal(object)
    eventContextMenuRequested = Signal(object, object)
    dayClicked = Signal(object)
    dayDoubleClicked = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._month_anchor = date.today().replace(day=1)
        self._events: list[Event] = []
        self._selected_day: date | None = None
        self._cells: list[MonthCellWidget] = []

        grid = QGridLayout(self)
        grid.setSpacing(2)
        grid.setContentsMargins(4, 4, 4, 4)

        for col, name in enumerate(_DAY_NAMES):
            header = QLabel(name, self)
            header.setAlignment(Qt.AlignmentFlag.AlignCenter)
            header.setStyleSheet("font-weight: 600; color: #5f6368;")
            grid.addWidget(header, 0, col)

        for row in range(6):
            for col in range(7):
                cell = MonthCellWidget(date.today(), self)
                cell.eventClicked.connect(self.eventClicked.emit)
                cell.eventDoubleClicked.connect(self.eventDoubleClicked.emit)
                cell.eventContextMenuRequested.connect(self.eventContextMenuRequested.emit)
                cell.dayClicked.connect(self.dayClicked.emit)
                cell.dayDoubleClicked.connect(self.dayDoubleClicked.emit)
                grid.addWidget(cell, row + 1, col)
                self._cells.append(cell)

        for col in range(7):
            grid.setColumnStretch(col, 1)
        for row in range(1, 7):
            grid.setRowStretch(row, 1)

    @property
    def blocks(self) -> list[_EventBlock]:
        return [block for cell in self._cells for block in cell._event_blocks]

    def set_month(self, month_anchor: date, events: list[Event]) -> None:
        self._month_anchor = month_anchor.replace(day=1)
        self._events = events
        self._relayout()

    def set_selected_day(self, day: date | None) -> None:
        self._selected_day = day
        self._relayout()

    def _relayout(self) -> None:
        grid_start, _grid_end = month_grid_range(self._month_anchor)
        today = date.today()
        events_by_day: dict[date, list[Event]] = {}
        for ev in self._events:
            day = ev.dtstart.astimezone().date()
            events_by_day.setdefault(day, []).append(ev)

        for i, cell in enumerate(self._cells):
            day = grid_start + timedelta(days=i)
            cell.day = day
            cell.day_label.setText(str(day.day))
            cell.set_state(
                in_month=(day.month == self._month_anchor.month),
                is_today=(day == today),
                is_selected=(day == self._selected_day),
            )
            day_events = sorted(events_by_day.get(day, []), key=lambda e: (not e.all_day, e.dtstart))
            cell.set_events(day_events)
