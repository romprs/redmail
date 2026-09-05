from __future__ import annotations

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
    app.processEvents()

    def report(message: str) -> None:
        splash.showMessage(
            message, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, QColor(_SPLASH_FG)
        )
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
