from __future__ import annotations

import os

# Qt поднимается без экрана: тест гоняется и в CI, и по ssh.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

from redmail import calendar_store
from redmail.ui.main_window import MainWindow


class FakeWindow(SimpleNamespace):
    """Подставное окно: методу нужны только путь к базе календарей и две
    перерисовки, виджеты для проверки не требуются."""

    def __init__(self, calendar_path):
        super().__init__(calendar_path=calendar_path, refreshed=[], redrawn=0)

    def _refresh_calendars_list(self, select_id=None):
        self.refreshed.append(select_id)

    def refresh_calendar_view(self):
        self.redrawn += 1


def _account(email="rsponomarev@amurgpz.ru"):
    return SimpleNamespace(email=email, server="svb-mail.corp.amurgpz.ru")


def test_ews_account_creates_calendar(tmp_path):
    path = tmp_path / "calendar.db"
    calendar_store.create_calendar(path)
    window = FakeWindow(path)

    MainWindow._ensure_ews_calendar(window, _account())

    calendars = [c for c in calendar_store.list_calendars(path) if c.source_type == calendar_store.SOURCE_EWS]
    assert len(calendars) == 1
    assert calendars[0].name == "Exchange: rsponomarev@amurgpz.ru"
    assert window.refreshed == [calendars[0].id]
    assert window.redrawn == 1


def test_second_connection_does_not_duplicate(tmp_path):
    path = tmp_path / "calendar.db"
    calendar_store.create_calendar(path)
    window = FakeWindow(path)

    MainWindow._ensure_ews_calendar(window, _account())
    MainWindow._ensure_ews_calendar(window, _account())

    calendars = [c for c in calendar_store.list_calendars(path) if c.source_type == calendar_store.SOURCE_EWS]
    assert len(calendars) == 1
    assert window.redrawn == 1  # второй раз ничего не создаём и не перерисовываем


def test_failure_does_not_break_connection(tmp_path, monkeypatch):
    """Сбой базы календарей не должен мешать подключению почты."""
    path = tmp_path / "calendar.db"
    calendar_store.create_calendar(path)
    window = FakeWindow(path)
    monkeypatch.setattr(
        calendar_store, "create_user_calendar",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("база занята")),
    )

    MainWindow._ensure_ews_calendar(window, _account())

    assert window.refreshed == [] and window.redrawn == 0
