from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from redmail import config_store  # noqa: E402
from redmail.config_store import Greeting  # noqa: E402


def test_pick_greeting_by_time_order_and_midnight() -> None:
    items = [
        Greeting("Доброй ночи, коллеги!", "22:00", "04:00"),
        Greeting("Доброе утро!", "04:00", "12:00"),
        Greeting("Здравствуйте!"),
    ]
    at = lambda h, m=0: datetime(2026, 9, 17, h, m)  # noqa: E731
    assert config_store.pick_greeting(items, at(23, 30)) == "Доброй ночи, коллеги!"
    assert config_store.pick_greeting(items, at(3, 59)) == "Доброй ночи, коллеги!"
    assert config_store.pick_greeting(items, at(4)) == "Доброе утро!"
    assert config_store.pick_greeting(items, at(15)) == "Здравствуйте!"
    assert config_store.pick_greeting([Greeting("Утро", "04:00", "12:00")], at(15)) == ""


@pytest.mark.parametrize("bad", [Greeting(""), Greeting("x", "25:00", "26:00"), Greeting("x", "9", "10:00"), Greeting("x", "09:00", "")])
def test_invalid_greetings_rejected(bad: Greeting) -> None:
    with pytest.raises(ValueError):
        config_store.validate_greeting(bad)


def test_list_mode_saved_and_used(tmp_path: Path) -> None:
    with patch("redmail.config_store._settings_path", return_value=tmp_path / "settings.json"):
        assert [g.text for g in config_store.load_greetings()][0] == "Доброе утро!"  # по умолчанию — по времени суток
        config_store.save_greetings([Greeting("Приветствую!", "09:00", "18:00"), Greeting("Добрый вечер!")])
        config_store.save_greeting_mode(config_store.GREETING_LIST)
        assert config_store.load_greeting_mode() == "list"
        assert config_store.greeting_text("list", datetime(2026, 9, 17, 10)) == "Приветствую!"
        assert config_store.greeting_text("list", datetime(2026, 9, 17, 20)) == "Добрый вечер!"
        assert config_store.greeting_choices() == ["Приветствую!", "Добрый вечер!", "Здравствуйте!"]
        with pytest.raises(ValueError):
            config_store.save_greetings([Greeting("", "", "")])


def test_compose_greeting_can_be_changed_removed_and_added(tmp_path: Path) -> None:
    from PySide6.QtWidgets import QApplication

    from redmail.ui import main_window as mw

    QApplication.instance() or QApplication([])
    with patch("redmail.config_store._settings_path", return_value=tmp_path / "settings.json"):
        dialog = mw.ComposeDialog(None, body="\n\n> цитата", greeting="Добрый день!")
        combo = dialog.greeting_combo
        text = dialog.body_edit.toPlainText
        assert combo.currentData() == "Добрый день!" and text() == "Добрый день!\n\n\n\n> цитата"
        combo.setCurrentIndex(combo.findData("Здравствуйте!"))
        assert text() == "Здравствуйте!\n\n\n\n> цитата"
        combo.setCurrentIndex(combo.findData(""))
        assert text() == "\n\n> цитата"  # как было без приветствия
        combo.setCurrentIndex(combo.findData("Доброе утро!"))
        assert text() == "Доброе утро!\n\n\n\n> цитата"


def test_greetings_dialog_collects_and_validates(tmp_path: Path, monkeypatch) -> None:
    from PySide6.QtWidgets import QApplication, QTableWidgetItem

    from redmail.ui import main_window as mw

    QApplication.instance() or QApplication([])
    warnings = []
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda *a: warnings.append(a[-1]))
    dialog = mw.GreetingsDialog(None, [Greeting("Привет", "08:00", "12:00")])
    dialog._add_row(Greeting("Здравствуйте!"))
    dialog.table.setCurrentCell(1, 0)
    dialog._move_row(-1)
    assert [g.text for g in dialog.greetings()] == ["Здравствуйте!", "Привет"]
    dialog.table.setItem(1, 2, QTableWidgetItem(""))  # конец времени стёрли
    dialog.accept()
    assert warnings and dialog.result() == 0
