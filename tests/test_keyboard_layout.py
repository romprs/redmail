from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

from redmail import keyboard_layout  # noqa: E402
from redmail.ui import main_window as mw  # noqa: E402


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_switch_layout_both_directions() -> None:
    assert keyboard_layout.switch_layout("ghbdtn? rfr ltkf") == "привет, как дела"
    assert keyboard_layout.switch_layout(";ehyfk 'ktrnhjyysq") == "журнал электронный"
    assert keyboard_layout.switch_layout("руддщ цщкдв") == "hello world"
    assert keyboard_layout.to_russian("Bdfyjd") == "Иванов"
    assert keyboard_layout.alternatives("bdfyjd") == ["иванов"]


def test_recipient_search_uses_other_layout_only_when_needed() -> None:
    candidates = ["Иванов Иван <ivanov@x.ru>", "Bob <bob@x.ru>"]
    assert mw._recipient_search_prefix("bdfyjd", candidates) == "иванов"
    assert mw._recipient_search_prefix("ivanov", candidates) == "ivanov"  # латиница нашлась в адресе
    assert mw._recipient_search_prefix("ищи", candidates) == "bob"
    assert mw._recipient_search_prefix("zzz", candidates) == "zzz"


def test_switch_layout_in_body_converts_selection_or_last_word() -> None:
    _app()
    dialog = mw.ComposeDialog(None)
    dialog.body_edit.setPlainText("Добрый день ;ehyfk")
    cursor = dialog.body_edit.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    dialog.body_edit.setTextCursor(cursor)
    assert mw.switch_layout_in_widget(dialog.body_edit)
    assert dialog.body_edit.toPlainText() == "Добрый день журнал"

    dialog.body_edit.setPlainText("ghbdtn rfr ltkf")
    dialog.body_edit.selectAll()
    assert mw.switch_layout_in_widget(dialog.body_edit)
    assert dialog.body_edit.toPlainText() == "привет как дела"

    subject = QLineEdit("Pfzdrf yf gjxne")
    subject.setCursorPosition(len(subject.text()))
    assert mw.switch_layout_in_widget(subject)
    assert subject.text() == "Pfzdrf yf почту"
    subject.selectAll()
    assert mw.switch_layout_in_widget(subject)
    assert subject.text() == "Заявка на почту"  # латиница переведена, кириллица осталась как была
    assert dialog.switch_layout_action.shortcuts()
