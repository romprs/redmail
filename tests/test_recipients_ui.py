from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtWidgets import QApplication, QLineEdit

from redmail import calendar_store
from redmail.contact_store import Contact
from redmail.ui.main_window import (
    EventDialog,
    RecipientListView,
    _apply_event_form_changes,
    _contact_name_for,
    _recipients_tooltip,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


BOOK = [
    Contact(display_name="Шилкин Евгений Александрович", emails=["shilkin.e@example.com"]),
    Contact(display_name="Пономарев Роман", emails=["ponomarev@example.com"]),
]


def test_contact_name_for_ignores_case_and_unknown() -> None:
    assert _contact_name_for("SHILKIN.E@example.com", BOOK) == "Шилкин Евгений Александрович"
    assert _contact_name_for("nobody@example.com", BOOK) == ""


def test_tooltip_lists_names_with_addresses() -> None:
    tip = _recipients_tooltip("shilkin.e@example.com, nobody@example.com", BOOK)
    assert tip == "Адресатов: 2\nШилкин Евгений Александрович — shilkin.e@example.com\nnobody@example.com"


def test_recipient_list_view_mirrors_field_with_names(qapp) -> None:
    field = QLineEdit("shilkin.e@example.com, Пономарев Роман <ponomarev@example.com>, Иван <ivan@typed.example>")
    view = RecipientListView(field, BOOK)
    rows = [view.item(i).text() for i in range(view.count())]
    assert rows == [
        "Шилкин Евгений Александрович — shilkin.e@example.com",
        "Пономарев Роман — ponomarev@example.com",
        "Иван — ivan@typed.example",  # имя, набранное в поле, тоже показывается
    ]
    assert view.isVisibleTo(view.parentWidget() or view)
    view.item(0).setSelected(True)
    view._remove_selected()
    assert field.text() == "Пономарев Роман <ponomarev@example.com>, Иван <ivan@typed.example>"
    assert view.count() == 2
    field.clear()
    assert view.count() == 0 and view.height() == 0


def test_add_participants_by_ipc_shows_names(qapp) -> None:
    start = datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc)
    draft = calendar_store.Event(uid="draft", summary="", dtstart=start, dtend=start + timedelta(hours=1))
    dialog = EventDialog(None, event=draft, my_email="me@example.com", contacts=BOOK)
    _apply_event_form_changes(dialog, {"add_participants": ["shilkin.e@example.com", "unknown@example.com"]})
    assert dialog.attendees_edit.text() == "Шилкин Евгений Александрович <shilkin.e@example.com>, unknown@example.com"
    assert dialog.attendee_emails() == ["shilkin.e@example.com", "unknown@example.com"]
    _apply_event_form_changes(dialog, {"add_participants": ["SHILKIN.E@example.com"]})  # повтор не дублируется
    assert dialog.attendee_emails() == ["shilkin.e@example.com", "unknown@example.com"]
    # список под полем отражает поле
    rows = [dialog.attendees_list_view.item(i).text() for i in range(dialog.attendees_list_view.count())]
    assert rows == ["Шилкин Евгений Александрович — shilkin.e@example.com", "unknown@example.com"]
