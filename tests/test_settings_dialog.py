"""Окно параметров должно открываться — целиком, со всеми вкладками.

Пробел в проверках: диалог не собирался ни одним тестом, и опечатка в
порядке строк (группа создавалась ПОСЛЕ того, как её клали на вкладку)
дошла до пользователя — окно параметров падало с UnboundLocalError на
каждом открытии.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from redmail import calendar_store  # noqa: E402
from redmail.ui import main_window as mw  # noqa: E402


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _dialog(**overrides) -> mw.SettingsDialog:
    defaults = dict(
        poll_interval_minutes=5,
        pane_orientation="vertical",
        archive_storage_dir="/tmp/archives",
        theme="light",
        profile_dir="/tmp/profile",
        body_max_size_mb=5,
        auto_archive_size_mb=500,
        storage_stats={"db_bytes": 1024, "messages": 10, "with_body": 5},
        auto_archive_enabled=True,
        maintenance_window=(True, 22, 7),
        tls_ca_file="",
        accounts=(),
        disabled_accounts=(),
        font_scale=1.0,
        greeting_mode="ask",
        plugins_enabled={},
        category_store=None,
    )
    defaults.update(overrides)
    return mw.SettingsDialog(None, **defaults)


def test_settings_dialog_opens_with_all_tabs() -> None:
    _app()
    dialog = _dialog()
    try:
        tabs = dialog.findChildren(mw.QTabWidget)[0]
        titles = [tabs.tabText(i) for i in range(tabs.count())]
        assert "Общие" in titles and "Хранилище" in titles
    finally:
        dialog.close()


def test_others_reminder_settings_round_trip() -> None:
    """Настройка напоминаний о чужих встречах читается из окна такой же,
    какой её туда положили."""
    _app()
    dialog = _dialog(others_reminder=("voice", 30, ("Орлов", "petrov@corp.ru")))
    try:
        mode, minutes, authors = dialog.others_reminder()
        assert mode == calendar_store.REMIND_VOICE
        assert minutes == 30
        assert authors == ["Орлов", "petrov@corp.ru"]
    finally:
        dialog.close()


def test_others_reminder_defaults_to_window_in_fifteen_minutes() -> None:
    _app()
    dialog = _dialog()
    try:
        mode, minutes, authors = dialog.others_reminder()
        assert mode == calendar_store.REMIND_WINDOW and minutes == 15 and authors == []
    finally:
        dialog.close()
