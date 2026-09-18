from __future__ import annotations

import subprocess
from pathlib import Path
import os
import sys
from importlib.metadata import PackageNotFoundError, version

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

# Модули программы здесь НЕ импортируются: их загрузка занимает заметную
# долю секунды, а до неё на экране нет ничего (жалоба: «после запуска
# проходит 3–5 секунд, прежде чем появляется окно с описанием — ощущение,
# что реакции нет»). Сначала показываем заставку, потом импортируем, и
# каждый шаг виден на ней. Из PySide6 берём только то, что нужно самой
# заставке.

_SPLASH_SIZE = (420, 240)
_SPLASH_BG = "#1a73e8"
_SPLASH_FG = "white"


def app_version() -> str:
    """Версия для заставки и "О программе" — жалоба: "добавь в справку о
    программе версию, сейчас там 0.0.1", т.е. одна и та же версия пакета
    для КАЖДОЙ сборки (в pyproject.toml она не меняется между RPM-релизами,
    там всегда "0.0.1" — реальный номер сборки живёт только в Release: у
    .spec). На RED OS сначала спрашиваем сам установленный RPM-пакет
    (даёт "0.0.1-30", ровно то, что нужно, чтобы отличить сборки друг от
    друга); вне RED OS (разработка, другой дистрибутив) rpm просто нет —
    тогда версия пакета Python как раньше."""
    # Спрашиваем пакет, которому принадлежит ЗАПУЩЕННЫЙ файл (rpm -qf), а
    # не пакет по имени: во время обновления в базе rpm недолго числятся
    # обе версии, и `rpm -q redmail` отдаёт две строки — показывалась
    # старая (жалоба: "тянет старые версии"). -qf всегда про тот код,
    # который сейчас работает.
    for args in (
        ["rpm", "-qf", "--queryformat", "%{VERSION}-%{RELEASE}\n", str(Path(__file__).resolve())],
        ["rpm", "-q", "--queryformat", "%{VERSION}-%{RELEASE}\n", "redmail"],
    ):
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=2)
        except (OSError, subprocess.SubprocessError):
            break
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if result.returncode == 0 and lines:
            return lines[-1]  # при нескольких версиях — последняя (новая)
    try:
        return version("redmail")
    except PackageNotFoundError:
        return "?"


def build_splash_pixmap(version: str) -> QPixmap:
    """Заставка при запуске (жалоба: "открывай сразу информационное окно
    до открытия основного окна... название, автора и ход загрузки") —
    название/версия/автор совпадают с тем, что уже показывает "О
    программе…", чтобы не заводить два разных источника правды."""
    width, height = _SPLASH_SIZE
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor(_SPLASH_BG))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor(_SPLASH_FG))

    title_font = QFont()
    title_font.setPointSize(22)
    title_font.setBold(True)
    painter.setFont(title_font)
    painter.drawText(pixmap.rect().adjusted(24, 28, -24, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, "RedMail")

    text_font = QFont()
    text_font.setPointSize(11)
    painter.setFont(text_font)
    lines = (
        "Почтовый клиент для RED OS",
        f"Версия {version}",
        "Автор: Пономарев Роман Сергеевич",
    )
    for i, line in enumerate(lines):
        painter.drawText(
            pixmap.rect().adjusted(24, 74 + i * 22, -24, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            line,
        )
    painter.end()
    return pixmap


def seconds_since_process_start() -> float | None:
    """Сколько секунд прошло с момента запуска процесса. Нужно, чтобы в
    журнале было видно, где именно уходит время до заставки: интерпретатор
    и его модули стартуют ДО первой строчки нашего кода, и изнутри
    программы это время иначе не измерить. Только Linux (/proc); на других
    системах — None."""
    try:
        with open("/proc/self/stat", encoding="ascii") as handle:
            fields = handle.read().rsplit(") ", 1)[1].split()
        start_ticks = int(fields[19])  # 22-е поле stat, первые два уже отрезаны
        with open("/proc/uptime", encoding="ascii") as handle:
            uptime = float(handle.read().split()[0])
        return max(0.0, uptime - start_ticks / os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError, IndexError):
        return None


def _ensure_session_bus_address() -> None:
    """Запуск не из сессии рабочего стола (ssh, автозапуск до экспорта
    переменных) — без DBUS_SESSION_BUS_ADDRESS keyring не найдёт
    Secret Service и решит, что хранилища паролей нет. Стандартный адрес
    шины пользователя — $XDG_RUNTIME_DIR/bus; если файл есть, подставляем."""
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}" if hasattr(os, "getuid") else ""
    if runtime_dir and os.path.exists(os.path.join(runtime_dir, "bus")):
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + os.path.join(runtime_dir, "bus")


def main() -> int:
    from redmail import cli

    if cli.wants_cli(sys.argv):
        return cli.run(sys.argv)
    app = QApplication(sys.argv)
    # Заставка — первое, что делает программа после создания приложения:
    # версия («…») и модули подтягиваются уже при ней на экране.
    splash = QSplashScreen(build_splash_pixmap("…"))
    splash.show()
    splash.repaint()
    app.processEvents()

    def report(message: str) -> None:
        splash.showMessage(
            message, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, QColor(_SPLASH_FG)
        )
        splash.repaint()
        app.processEvents()

    _splash_delay = seconds_since_process_start()

    report("Загрузка модулей…")
    from redmail import applog, config_store, profile, tls_trust  # noqa: F401 - profile нужен ниже
    from redmail.ui import theme

    log_file = applog.setup_logging()
    _ensure_session_bus_address()
    # HTTPS (CalDAV, Exchange, подписка на календарь) — доверенные корни из
    # файла, указанного в настройках, иначе из системного хранилища:
    # корпоративный ЦС неизвестен requests сам по себе.
    try:
        tls_trust.apply_trust(config_store.load_tls_ca_file())
    except Exception as exc:  # настройки не должны мешать запуску
        applog.get_logger("app").warning("Доверенные корни HTTPS: %s", exc)
    applog.get_logger("app").info("Запуск программы (журнал: %s)", log_file)
    if _splash_delay is not None:
        # Жалоба: «после запуска проходит 3–5 секунд, прежде чем появляется
        # окно с описанием». Записываем, сколько на самом деле прошло от
        # запуска процесса до заставки — иначе не понять, где время.
        applog.get_logger("app").info("Заставка показана через %.1f с после запуска", _splash_delay)

    # Долгое — после того, как заставка уже на экране: импорт интерфейса
    # тянет PySide6.QtWebEngine* (инициализация Chromium — секунды), а
    # app_version() спрашивает rpm (до 2 с на медленной машине).
    version = app_version()
    applog.get_logger("app").info("Версия %s", version)
    splash.setPixmap(build_splash_pixmap(version))
    report("Применение темы оформления…")
    theme.apply_theme(app, config_store.load_theme())

    report("Загрузка интерфейса…")
    from redmail.ui.main_window import MainWindow

    from redmail import ipc_server

    if ipc_server.focus_running_instance():
        # Уже запущен — поднять существующее окно и выйти, второго не открывать.
        applog.get_logger("app").info("Уже запущен другой экземпляр — показываю его окно и выхожу")
        splash.close()
        return 0

    pending_error = None
    try:
        from redmail import profile_transfer

        if profile_transfer.pending_import() is not None:
            report("Загружаю перенесённый профиль…")
            backup = profile_transfer.apply_pending_import(progress=lambda name: report(f"Загружаю профиль: {name}"))
            applog.get_logger("app").info("Перенесённый профиль загружен, прежние данные: %s", backup)
    except Exception as exc:
        applog.get_logger("app").error("Перенесённый профиль не загружен: %s", exc)
        pending_error = str(exc)
    profile_path = profile.ensure_profile()
    applog.get_logger("app").info("Профиль: %s", profile_path)
    try:
        from redmail import cache_store

        if cache_store.needs_initial_vacuum():
            # Один раз: перевод базы в режим инкрементального ужатия. На базе
            # в несколько ГБ это минуты — под заставкой, пока окна ещё нет.
            report("Ужимаю базу почты (один раз, несколько минут)…")
            applog.get_logger("app").info("Первичное ужатие базы почты (VACUUM)…")
            cache_store.initial_vacuum()
            applog.get_logger("app").info("Первичное ужатие базы почты завершено")
    except Exception as exc:
        applog.get_logger("app").error("Первичное ужатие базы не удалось: %s", exc)
    window = MainWindow()

    report("Готово")
    window.show()
    splash.finish(window)
    if pending_error:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(window, "Перенос профиля", f"Профиль не загружен, данные остались прежними:\n{pending_error}")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
