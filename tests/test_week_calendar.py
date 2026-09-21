"""Недельная сетка календаря: пересекающиеся встречи — рядом, а не поверх.

Жалоба: «несколько событий на одно время нечитабельны. Надо размещать
рядом, а не поверх, и при наведении мыши выдавать полное название в
подсказку»."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from redmail.calendar_store import Event  # noqa: E402
from redmail.ui import week_calendar as wc  # noqa: E402


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_separate_meetings_keep_full_width() -> None:
    assert wc.overlap_columns([(9 * 60, 10 * 60), (11 * 60, 12 * 60)]) == [(0, 1), (0, 1)]


def test_two_meetings_at_same_time_go_side_by_side() -> None:
    assert wc.overlap_columns([(600, 660), (600, 660)]) == [(0, 2), (1, 2)]


def test_three_way_overlap_and_reused_column() -> None:
    """9–11 пересекается и с 9:30–10, и с 10–10:30; последние две друг с
    другом не пересекаются и встают в одну колонку."""
    columns = wc.overlap_columns([(540, 660), (570, 600), (600, 630)])
    assert columns == [(0, 2), (1, 2), (1, 2)]


def test_back_to_back_meetings_do_not_share_width() -> None:
    """Встреча, начинающаяся ровно в конец предыдущей, не пересекается с
    ней — обе во всю ширину."""
    assert wc.overlap_columns([(540, 600), (600, 660)]) == [(0, 1), (0, 1)]


def test_group_width_does_not_leak_to_later_meetings() -> None:
    """После группы из трёх пересекающихся встреч следующая, отдельная,
    снова во всю ширину."""
    columns = wc.overlap_columns([(540, 600), (540, 600), (540, 600), (720, 780)])
    assert columns[:3] == [(0, 3), (1, 3), (2, 3)]
    assert columns[3] == (0, 1)


def _event(uid: str, start: datetime, minutes: int = 60, summary: str = "Планёрка") -> Event:
    return Event(
        uid=uid, summary=summary, dtstart=start.astimezone(timezone.utc),
        dtend=(start + timedelta(minutes=minutes)).astimezone(timezone.utc),
        organizer_email="orlov@example.com", organizer_name="Орлов Олег",
    )


def test_week_grid_places_overlapping_blocks_next_to_each_other() -> None:
    _app()
    grid = wc.WeekGridWidget()
    grid.resize(7 * 150 + grid.TIME_AXIS_WIDTH, grid.HOUR_HEIGHT * 24)
    monday = wc.week_start_for(datetime.now().date())
    at_ten = datetime.combine(monday, datetime.min.time()).astimezone().replace(hour=10)
    grid.set_week(monday, [_event("a", at_ten), _event("b", at_ten)])
    try:
        first, second = sorted((block.geometry() for block in grid._blocks), key=lambda g: g.x())
        assert not first.intersects(second)  # не поверх друг друга
        assert first.y() == second.y()        # одно и то же время
        assert first.width() < 100 and second.width() < 100  # каждая — примерно половина колонки
    finally:
        grid.close()


def test_tooltip_shows_full_title_and_escapes_markup() -> None:
    start = datetime(2026, 9, 21, 10, tzinfo=timezone.utc)
    event = _event("a", start, summary="<b>Совещание</b> по проекту «Феникс» с подрядчиком")
    event.location = "Переговорная <3>"
    tooltip = wc.event_tooltip(event)
    assert "по проекту «Феникс» с подрядчиком" in tooltip
    assert "&lt;b&gt;Совещание&lt;/b&gt;" in tooltip  # тема из письма не превращается в разметку
    assert "Переговорная &lt;3&gt;" in tooltip
    assert "Орлов Олег" in tooltip  # чужая встреча — с организатором


def test_tooltip_over_a_block_is_readable() -> None:
    """Жалоба: «подсказка — чёрный квадрат, текста нет». Подсказка
    наследовала от надписи в карточке прозрачный фон (на X11 — чёрный) и
    тёмный текст. Проверяем то, что видит человек: наведение на надпись
    даёт подсказку со светлым фоном и тёмным текстом на нём."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtWidgets import QLabel, QToolTip

    from redmail.ui import theme

    app = _app()
    theme.apply_theme(app, "light")
    grid = wc.WeekGridWidget()
    grid.resize(7 * 150 + grid.TIME_AXIS_WIDTH, grid.HOUR_HEIGHT * 24)
    monday = wc.week_start_for(datetime.now().date())
    at_ten = datetime.combine(monday, datetime.min.time()).astimezone().replace(hour=10)
    grid.set_week(monday, [_event("a", at_ten, summary="Очень длинная тема совещания по проекту")])
    grid.show()
    try:
        block = grid._blocks[0]
        label = block.findChild(QLabel)
        # Надпись пропускает мышь сквозь себя — наведение достаётся карточке.
        from PySide6.QtCore import Qt
        assert label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        QApplication.sendEvent(block, QHelpEvent(QHelpEvent.Type.ToolTip, QPoint(5, 5), block.mapToGlobal(QPoint(5, 5))))
        assert "Очень длинная тема совещания по проекту" in QToolTip.text()
        tip = None
        for widget in QApplication.topLevelWidgets():
            try:
                if widget.metaObject().className() == "QTipLabel" and "Очень длинная" in widget.text():
                    tip = widget
            except RuntimeError:
                continue  # подсказка от прошлого теста уже удалена
        assert tip is not None
        # Снимок сразу: без мыши в тестовом окружении подсказка вскоре
        # закрывается сама и удаляется.
        image = tip.grab().toImage()
        colors = [image.pixelColor(x, y) for x in range(0, image.width(), 2) for y in range(0, image.height(), 2)]
        light = sum(1 for c in colors if c.lightness() > 200)
        dark = sum(1 for c in colors if c.lightness() < 90)
        assert light > len(colors) // 2  # фон светлый, а не чёрный
        assert dark > 0                  # и на нём есть текст
    finally:
        QToolTip.hideText()
        grid.close()


def test_tooltip_puts_each_field_on_its_own_unwrapped_line() -> None:
    """Пожелание: «тема в 1 строку, время в другой, место в 3-ю» — каждая
    строка без переноса, чтобы длинная тема была видна целиком."""
    from redmail.calendar_store import Attendee

    start = datetime(2026, 9, 21, 10, tzinfo=timezone.utc)
    event = _event("a", start, summary="Совещание по проекту Феникс с подрядчиком и заказчиком")
    event.location = "Переговорная 3"
    event.attendees = [Attendee(email=f"u{n}@x.ru", name=f"Участник {n}") for n in range(7)]
    lines = wc.event_tooltip(event).split("<br>")
    assert "Совещание по проекту Феникс с подрядчиком и заказчиком" in lines[0]
    assert f"{start.astimezone():%H:%M}" in lines[1] and f"{start.astimezone():%d.%m.%Y}" in lines[1]
    assert lines[2] == "<nobr>Место: Переговорная 3</nobr>"
    assert "Организатор: Орлов Олег" in lines[3]
    assert "Участник 0" in lines[4] and "и ещё 2" in lines[4]
    assert all(line.startswith("<nobr>") for line in lines)


def test_grid_stretches_to_fill_a_taller_window() -> None:
    """Жалоба: «растягиваю вниз — внизу пустота». Час растягивается под
    высоту видимой области, но не мельче прежнего."""
    _app()
    grid = wc.WeekGridWidget()
    grid.set_viewport_height(24 * 60)  # высокое окно — по 60 пикселей на час
    assert grid.hour_height() == 60
    assert grid.height() == 24 * 60 or grid.minimumHeight() == 24 * 60
    grid.set_viewport_height(300)  # низкое окно — час не мельче прежних 36
    assert grid.hour_height() == grid.HOUR_HEIGHT


def _at(day_offset: int, hour: int, minute: int = 0) -> datetime:
    monday = wc.week_start_for(datetime.now().date())
    return (datetime.combine(monday, datetime.min.time()).astimezone() + timedelta(days=day_offset)).replace(
        hour=hour, minute=minute
    )


def test_compact_mode_follows_earliest_and_latest_meeting_of_the_week() -> None:
    """Пожелание: сжатый режим «в динамике — смотрим неделю, раннее и
    позднее задания». Самая ранняя встреча недели в 9:00, самая поздняя
    кончается в 18:30 — видны 9:00–19:00, и они растягиваются на окно."""
    _app()
    grid = wc.WeekGridWidget()
    grid.set_viewport_height(10 * 60)
    monday = wc.week_start_for(datetime.now().date())
    grid.set_compact(True)
    grid.set_week(monday, [_event("a", _at(0, 9)), _event("b", _at(3, 17, 30)), _event("c", _at(1, 12))])
    assert grid.visible_hours() == (9, 19)
    assert grid.hour_height() == 60  # 10 часов на 600 пикселей
    grid.set_compact(False)
    assert grid.visible_hours() == (0, 24)


def test_compact_mode_range_changes_with_the_week() -> None:
    _app()
    grid = wc.WeekGridWidget()
    monday = wc.week_start_for(datetime.now().date())
    grid.set_compact(True)
    grid.set_week(monday, [_event("a", _at(0, 6)), _event("b", _at(2, 21))])
    assert grid.visible_hours() == (6, 22)
    grid.set_week(monday, [_event("a", _at(0, 10)), _event("b", _at(2, 15))])
    assert grid.visible_hours() == (10, 16)


def test_compact_mode_keeps_a_minimum_span_and_falls_back_for_empty_week() -> None:
    _app()
    grid = wc.WeekGridWidget()
    monday = wc.week_start_for(datetime.now().date())
    grid.set_compact(True)
    grid.set_week(monday, [_event("a", _at(0, 10))])  # одна встреча на час
    first, last = grid.visible_hours()
    assert first <= 10 and last >= 11 and last - first == wc.WeekGridWidget.COMPACT_MIN_HOURS
    grid.set_week(monday, [_event("a", _at(0, 22))])  # поздняя — раздвигаем вверх, не за полночь
    assert grid.visible_hours() == (18, 24)
    grid.set_week(monday, [])
    assert grid.visible_hours() == (wc.WeekGridWidget.COMPACT_FIRST_HOUR, wc.WeekGridWidget.COMPACT_LAST_HOUR)


def test_clicking_an_empty_slot_in_compact_mode_gives_real_time() -> None:
    """Клик по пустому месту: в сжатом режиме верх сетки — это 7:00, а не
    полночь, и новая встреча должна создаваться на то время, куда кликнули."""
    from PySide6.QtCore import QPoint

    _app()
    grid = wc.WeekGridWidget()
    grid.resize(7 * 100 + grid.TIME_AXIS_WIDTH, 800)
    grid.set_compact(True)
    day, minutes = grid._slot_at(QPoint(grid.TIME_AXIS_WIDTH + 10, int(grid.hour_height() * 2)))
    assert minutes == 9 * 60  # два часа от верха сжатой сетки — 9:00
