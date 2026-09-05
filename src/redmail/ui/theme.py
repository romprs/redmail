from __future__ import annotations

# Раньше приложение вообще не задавало собственный стиль — его вид
# полностью зависел от темы рабочего стола/GTK хоста (жалоба: "сделай фон
# программы независимым"), вплоть до нечитаемых сочетаний на тёмных
# системных темах (жёстко белые фоны в календаре и т.п. остаются белыми
# независимо от системы, а текст мог наследовать светлый цвет от системной
# палитры). Здесь — две полностью свои темы, применяются через
# QApplication.setStyleSheet(), не зависят от хоста вообще.

_LIGHT = {
    "window": "#f5f6f8",
    "base": "#ffffff",
    "alt_base": "#f1f3f4",
    "text": "#202124",
    "border": "#dadce0",
    "accent": "#1a73e8",
    "accent_text": "#ffffff",
    "disabled_text": "#9aa0a6",
}

_DARK = {
    "window": "#202124",
    "base": "#2b2c2e",
    "alt_base": "#323335",
    "text": "#e8eaed",
    "border": "#5f6368",
    "accent": "#8ab4f8",
    "accent_text": "#202124",
    "disabled_text": "#80868b",
}

_QSS_TEMPLATE = """
QWidget {{
    background-color: {window};
    color: {text};
}}
QMainWindow, QDialog {{
    background-color: {window};
}}
QToolBar {{
    background-color: {alt_base};
    border: none;
    spacing: 2px;
}}
QToolButton {{
    background-color: transparent;
    color: {text};
    border: none;
    padding: 4px 6px;
    border-radius: 4px;
}}
QToolButton:hover, QToolButton:pressed {{
    background-color: {border};
}}
QToolButton:checked {{
    background-color: {accent};
    color: {accent_text};
}}
QPushButton {{
    background-color: {base};
    color: {text};
    border: 1px solid {border};
    border-radius: 4px;
    padding: 4px 12px;
}}
QPushButton:hover {{
    border-color: {accent};
}}
QPushButton:disabled {{
    color: {disabled_text};
}}
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox, QDateTimeEdit {{
    background-color: {base};
    color: {text};
    border: 1px solid {border};
    border-radius: 3px;
    padding: 2px 4px;
}}
QComboBox QAbstractItemView {{
    background-color: {base};
    color: {text};
    selection-background-color: {accent};
    selection-color: {accent_text};
}}
QTableWidget, QTreeWidget, QListWidget {{
    background-color: {base};
    alternate-background-color: {alt_base};
    color: {text};
    border: 1px solid {border};
    gridline-color: {border};
}}
QHeaderView::section {{
    background-color: {alt_base};
    color: {text};
    border: none;
    border-bottom: 1px solid {border};
    padding: 3px;
}}
QTableWidget::item:selected, QTreeWidget::item:selected, QListWidget::item:selected {{
    background-color: {accent};
    color: {accent_text};
}}
QMenu {{
    background-color: {base};
    color: {text};
    border: 1px solid {border};
}}
QMenu::item:selected {{
    background-color: {accent};
    color: {accent_text};
}}
QGroupBox {{
    border: 1px solid {border};
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}}
QScrollBar:vertical, QScrollBar:horizontal {{
    background: {alt_base};
    border: none;
}}
QScrollBar::handle {{
    background: {border};
    border-radius: 4px;
}}
QStatusBar {{
    background-color: {alt_base};
}}
QSplitter::handle {{
    background-color: {border};
}}
"""


def _qss(palette: dict[str, str]) -> str:
    return _QSS_TEMPLATE.format(**palette)


_is_dark = False


def is_dark() -> bool:
    """Читают виджеты с собственной покраской через inline setStyleSheet()
    (карточки событий и т.п. в week_calendar.py) — их не достаёт общий QSS
    приложения, так как их собственный stylesheet имеет более высокий
    приоритет и полностью его перекрывает."""
    return _is_dark


def apply_theme(app, theme: str) -> None:
    global _is_dark
    _is_dark = theme == "dark"
    app.setStyleSheet(_qss(_DARK if _is_dark else _LIGHT))
