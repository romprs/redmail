from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from redmail import config_store
from redmail.ui import theme
from redmail.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    theme.apply_theme(app, config_store.load_theme())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
