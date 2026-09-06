from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

# Раньше приложение вообще не задавало собственный стиль — его вид
# полностью зависел от темы рабочего стола/GTK хоста (жалоба: "сделай фон
# программы независимым"), вплоть до нечитаемых сочетаний на тёмных
# системных темах. Первая версия этой темы красила ВСЁ через один сплошной
# QSS-селектор "QWidget { background-color: ...; color: ...; }" — жалоба
# после реального теста: "сильное замедление при работе в почтовом
# клиенте, письма читаются медленно, переключение между окнами тоже".
# Это известная ловушка Qt: широкий стиль на весь QWidget заставляет
# ЛЮБОЙ виджет в приложении (включая каждую ячейку списка писем) идти
# через более медленный движок отрисовки стилей вместо быстрой нативной
# отрисовки. Правильный способ покрасить весь стандартный набор виджетов
# (фон/текст/выделение/чекбоксы и т.п.) без потери скорости — QPalette,
# это тот же механизм, которым Qt рисует виджеты по умолчанию, а не
# отдельный слой поверх. QSS здесь остался только для нескольких мелких
# элементов, для которых голой палитры не хватает (обводка тулбара/меню/
# групп, подсветка выбранной кнопки режима).

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
QToolBar {{
    background-color: {alt_base};
    border: none;
    spacing: 2px;
}}
QToolButton {{
    background-color: transparent;
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
QMenu {{
    border: 1px solid {border};
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
QHeaderView::section {{
    background-color: {alt_base};
    border: none;
    border-bottom: 1px solid {border};
    padding: 3px;
}}
QScrollBar::handle {{
    background: {border};
    border-radius: 4px;
}}
QSplitter::handle {{
    background-color: {border};
}}
"""


def _qss(palette: dict[str, str]) -> str:
    return _QSS_TEMPLATE.format(**palette)


def _build_palette(colors: dict[str, str]) -> QPalette:
    window = QColor(colors["window"])
    base = QColor(colors["base"])
    alt_base = QColor(colors["alt_base"])
    text = QColor(colors["text"])
    accent = QColor(colors["accent"])
    accent_text = QColor(colors["accent_text"])
    disabled_text = QColor(colors["disabled_text"])

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.AlternateBase, alt_base)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, base)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.ToolTipBase, base)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.Highlight, accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, accent_text)
    palette.setColor(QPalette.ColorRole.PlaceholderText, disabled_text)
    palette.setColor(QPalette.ColorRole.Link, accent)
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled_text)
    return palette


_is_dark = False


def is_dark() -> bool:
    """Читают виджеты с собственной покраской через inline setStyleSheet()
    (карточки событий и т.п. в week_calendar.py) — палитра приложения их
    не достаёт, так как их собственный inline stylesheet имеет более
    высокий приоритет и полностью его перекрывает."""
    return _is_dark


def apply_theme(app, theme: str) -> None:
    global _is_dark
    _is_dark = theme == "dark"
    colors = _DARK if _is_dark else _LIGHT
    app.setPalette(_build_palette(colors))
    app.setStyleSheet(_qss(colors))
