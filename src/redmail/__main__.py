from __future__ import annotations

import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

from redmail import config_store
from redmail.ui import theme
from redmail.ui.main_window import MainWindow

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
    try:
        result = subprocess.run(
            ["rpm", "-q", "--queryformat", "%{VERSION}-%{RELEASE}", "redmail"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        return version("redmail")
    except PackageNotFoundError:
        return "?"


def build_splash_pixmap() -> QPixmap:
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
        f"Версия {app_version()}",
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


def main() -> int:
    app = QApplication(sys.argv)

    splash = QSplashScreen(build_splash_pixmap())
    splash.show()
    # Жалоба: "информационное окно выводится не сразу и иногда не успевает
    # отрисоваться вообще" — processEvents() сам по себе лишь разбирает уже
    # накопившуюся очередь событий, а не гарантирует, что отложенное окно
    # успело быть замаплено/отрисовано оконным менеджером именно к этому
    # моменту (особенно на X11 без композитора). repaint() — синхронная
    # немедленная перерисовка виджета, без ожидания цикла событий, поэтому
    # заставка гарантированно на экране ДО того, как MainWindow() ниже
    # надолго заблокирует поток своей инициализацией.
    splash.repaint()
    app.processEvents()

    def report(message: str) -> None:
        splash.showMessage(
            message, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, QColor(_SPLASH_FG)
        )
        splash.repaint()
        app.processEvents()

    report("Применение темы оформления…")
    theme.apply_theme(app, config_store.load_theme())

    report("Загрузка интерфейса…")
    window = MainWindow()

    report("Готово")
    window.show()
    splash.finish(window)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
