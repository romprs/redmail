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
    # WCAG 2.2 AA (4.5:1 для обычного текста) — прежний #9aa0a6 давал только
    # 2.44:1 на белом фоне (см. дизайн-аудит: "WCAG 2.2 AA Compliant"),
    # реально проваливая контраст даже для крупного текста (нужно ≥3:1).
    "disabled_text": "#5f6368",
}

_DARK = {
    "window": "#202124",
    "base": "#2b2c2e",
    "alt_base": "#323335",
    "text": "#e8eaed",
    "border": "#5f6368",
    "accent": "#8ab4f8",
    "accent_text": "#202124",
    # См. комментарий у _LIGHT: прежний #80868b давал 4.37:1/3.79:1 на
    # window/base — чуть ниже требуемых 4.5:1 для обычного текста.
    "disabled_text": "#9096a0",
}

_QSS_TEMPLATE = """
QToolBar {{
    background-color: {alt_base};
    border: none;
    spacing: 6px;
}}
/* Кнопки-иконки с явной рамкой, а не полностью "невидимые" до наведения
   (по мотивам дизайн-ревью: на референсе иконки инструментов — не голые
   значки в ряд, а отдельные обозначенные кнопки с границей и увеличенным
   зазором между ними). */
QToolButton {{
    background-color: transparent;
    border: 1px solid {border};
    padding: 4px 6px;
    border-radius: 6px;
}}
QToolButton:hover, QToolButton:pressed {{
    background-color: {border};
}}
QToolButton:checked {{
    background-color: {accent};
    color: {accent_text};
    border: 1px solid {accent};
}}
QMenu {{
    border: 1px solid {border};
}}
/* Мини-календарь слева от сетки (QCalendarWidget): жалоба "у меню убери
   рамку" — кнопки месяца/года в его навигационной панели наследовали общую
   рамку QToolButton и выглядели как обведённое меню; стрелки и кнопки
   месяца/года здесь — без рамок, панель без фона-плашки. Выделенный день
   — акцентом темы, иначе в тёмной теме выбор был почти неразличим
   (жалоба: "выделение календаря работает криво"). */
QCalendarWidget QWidget#qt_calendar_navigationbar {{
    background-color: transparent;
    border: none;
}}
QCalendarWidget QToolButton {{
    border: none;
    background-color: transparent;
    padding: 2px 6px;
    /* Явный цвет текста: навигационная панель QCalendarWidget по умолчанию
       рисует подписи цветом HighlightedText поверх заливки Highlight; без
       заливки в тёмной теме это тёмный текст на тёмном фоне. */
    color: {text};
}}
QCalendarWidget QToolButton:hover {{
    background-color: {alt_base};
}}
QCalendarWidget QToolButton::menu-indicator {{
    image: none;
}}
QCalendarWidget QAbstractItemView:enabled {{
    selection-background-color: {accent};
    selection-color: {accent_text};
    background-color: {base};
    color: {text};
    outline: none;
}}
/* Поля ввода: явная рамка и фон base (жалоба: "в тёмной теме всё
   сливается" — поле «Кому»/«Тема» на фоне окна не отличалось от него). */
QLineEdit {{
    border: 1px solid {border};
    border-radius: 4px;
    padding: 4px 6px;
    background-color: {base};
    selection-background-color: {accent};
    selection-color: {accent_text};
}}
QLineEdit:focus {{
    border: 1px solid {accent};
}}
QLineEdit:disabled {{
    color: {disabled_text};
}}
QTextEdit#composeBody, QPlainTextEdit#composeBody {{
    border: 1px solid {border};
    border-radius: 4px;
    background-color: {base};
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
/* Полосы прокрутки: явная дорожка и ползунок (жалоба: "скролл в перечне
   писем для тёмной темы не виден" — Fusion рисовал ползунок почти в цвет
   фона). */
QScrollBar:vertical {{
    background: {alt_base};
    width: 12px;
    margin: 0;
    border: none;
}}
QScrollBar:horizontal {{
    background: {alt_base};
    height: 12px;
    margin: 0;
    border: none;
}}
QScrollBar::handle {{
    background: {disabled_text};
    border-radius: 5px;
    min-height: 24px;
    min-width: 24px;
    margin: 2px;
}}
QScrollBar::handle:hover {{
    background: {accent};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
    border: none;
    background: none;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}
/* Кнопки разделов (Почта/Календарь/Контакты/Параметры/Справка) — без
   рамок, выделяется только активный раздел (пожелание: "убери рамку у
   кнопок меню"). */
QToolBar#modeBar {{
    padding: 6px 4px;  /* отступы сверху и снизу у панели разделов (пожелание пользователя) */
}}
QToolBar#modeBar QToolButton {{
    border: none;
    background-color: transparent;
    padding: 4px 10px;
}}
QToolBar#modeBar QToolButton:hover {{
    background-color: {border};
}}
QToolBar#modeBar QToolButton:checked {{
    background-color: {accent};
    color: {accent_text};
}}
QSplitter::handle {{
    background-color: {border};
}}
/* Поле поиска/фильтра — скруглённая "таблетка" со значком лупы слева
   (addAction в LeadingPosition, см. main_window.py), а не обычное прямое
   текстовое поле — по референсу дизайн-системы. Только по имени объекта
   "searchField", остальные QLineEdit (Тема, Кому и т.п.) не затронуты. */
QLineEdit#searchField {{
    border: 1px solid {border};
    border-radius: 13px;
    padding: 3px 10px;
    background-color: {base};
}}
QLineEdit#searchField:focus {{
    border: 1px solid {accent};
}}
/* Дерево папок: скруглённое выделение текущей папки вместо прямоугольной
   заливки палитрой во всю ширину строки. */
QTreeView::item {{
    padding: 3px 4px;
    border-radius: 4px;
}}
QTreeView::item:selected {{
    background-color: {accent};
    color: {accent_text};
}}
QTreeView::item:hover:!selected {{
    background-color: {alt_base};
}}
/* Чекбокс отметки письма: на Fusion-стиле индикатор рисуется другой, гораздо
   более тёмной заливкой, когда сама строка выделена - получался сплошной
   тёмный "провал" без признаков чекбокса поверх светло-синей подсветки
   выделения (жалоба: "выбор письма в тёмной теме опять же невиден -
   непонятно куда ставить галочку"), то есть именно тогда, когда письмо
   открыто, то есть почти всегда при чтении почты. Явный QSS только для
   этого индикатора (а не общий QWidget-селектор, который раньше и вызвал
   замедление всего приложения) держит вид чекбокса одинаковым независимо
   от состояния выделения строки. */
QTableView::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {text};
    border-radius: 3px;
    background-color: transparent;
}}
QTableView::indicator:checked {{
    background-color: {accent};
    border: 1px solid {accent};
}}
/* "Primary Button" компонент дизайн-системы — одно чёткое основное
   действие диалога (Отправить/Сохранить), выделенное акцентным цветом,
   вместо ряда одинаковых по виду кнопок (жалоба по мотивам дизайн-ревью:
   борьба с визуальным шумом, единая система компонентов). Только для
   QPushButton с явно проставленным свойством primary=true (см.
   _mark_primary в main_window.py) — обычные кнопки не затронуты. */
QPushButton[primary="true"] {{
    background-color: {accent};
    color: {accent_text};
    border: none;
    border-radius: 4px;
    padding: 5px 14px;
    font-weight: 600;
}}
QPushButton[primary="true"]:hover {{
    background-color: {accent_hover};
}}
QPushButton[primary="true"]:disabled {{
    background-color: {border};
    color: {disabled_text};
}}
"""


def _qss(palette: dict[str, str]) -> str:
    accent_hover = QColor(palette["accent"]).darker(112).name()
    return _QSS_TEMPLATE.format(**palette, accent_hover=accent_hover)


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
    # Без этого Light/Midlight/Dark/Mid/Shadow остаются на светлых
    # умолчаниях Qt даже в тёмной теме — стиль Fusion рисует ими рамку и
    # объёмную обводку у чекбоксов/кнопок (не текст и не заливку, те уже
    # покрашены выше), и на тёмном фоне это выглядело как "невидно
    # выделения писем для удаления" (чекбокс в столбце отметки почти не
    # отличался от фона). Считаем от base, а не жёстко прописываем на
    # тему — так рамка/тень всегда остаются на несколько тонов темнее и
    # светлее самого фона, независимо от конкретных цветов темы.
    palette.setColor(QPalette.ColorRole.Light, base.lighter(150))
    palette.setColor(QPalette.ColorRole.Midlight, base.lighter(120))
    palette.setColor(QPalette.ColorRole.Dark, base.darker(150))
    palette.setColor(QPalette.ColorRole.Mid, base.darker(120))
    palette.setColor(QPalette.ColorRole.Shadow, base.darker(200))
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
