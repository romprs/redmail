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


def test_tooltip_appears_when_hovering_the_text_inside_a_block() -> None:
    """Жалоба: «подсказка на встрече не высвечивается». Мышь почти всегда
    оказывается над надписью внутри карточки, а не над рамкой — подсказка
    должна всплыть и там."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtWidgets import QLabel, QToolTip

    _app()
    grid = wc.WeekGridWidget()
    grid.resize(7 * 150 + grid.TIME_AXIS_WIDTH, grid.HOUR_HEIGHT * 24)
    monday = wc.week_start_for(datetime.now().date())
    at_ten = datetime.combine(monday, datetime.min.time()).astimezone().replace(hour=10)
    grid.set_week(monday, [_event("a", at_ten, summary="Очень длинная тема совещания по проекту")])
    grid.show()
    try:
        label = grid._blocks[0].findChild(QLabel)
        event = QHelpEvent(QHelpEvent.Type.ToolTip, QPoint(5, 5), label.mapToGlobal(QPoint(5, 5)))
        QApplication.sendEvent(label, event)
        assert "Очень длинная тема совещания по проекту" in QToolTip.text()
    finally:
        QToolTip.hideText()
        grid.close()
