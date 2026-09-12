"""Упрощение HTML письма перед вставкой в редактор (QTextEdit).

Зачем: QTextDocument раскладывает вложенные таблицы за время, растущее
экспоненциально от глубины вложенности, — на HTML-рассылке (таблица в
таблице в таблице, у каждой ширина в процентах) пересылка вешала окно на
десятки минут при 100 % главного потока (py-spy: QTextTableCell::column,
QTextLine::layout_helper). Панель чтения на QtWebEngine с этим
справляется, а редактор — нет.

Поэтому в редактор письма идёт линейная разметка: таблицы превращаются в
блоки (<div>), скрипты/стили/заголовок документа убираются, размеры и
классы у блочных элементов снимаются. Текст, ссылки, картинки, жирный/
курсив, списки остаются — пересылаемое письмо читается, хотя и без
«вёрстки» рассылки.
"""
from __future__ import annotations

import re

# Табличные теги → блоки. tbody/thead/tfoot/tr/td/th/table/caption.
_TABLE_OPEN = re.compile(r"(?is)<(table|tbody|thead|tfoot|tr|td|th|caption|colgroup)\b[^>]*>")
_TABLE_CLOSE = re.compile(r"(?is)</(table|tbody|thead|tfoot|tr|td|th|caption|colgroup)\s*>")
_COL = re.compile(r"(?is)<col\b[^>]*>")
_DROP_BLOCKS = re.compile(r"(?is)<(script|style|head|title|meta|link|noscript|iframe|object|embed)\b[^>]*>.*?</\1\s*>")
_DROP_VOID = re.compile(r"(?is)<(meta|link|base)\b[^>]*/?>")
_COMMENTS = re.compile(r"(?s)<!--.*?-->")
_DOCTYPE = re.compile(r"(?is)<!doctype[^>]*>")
# У блочных элементов убираем атрибуты размеров/классов/ид: ширины в
# процентах и фиксированные высоты на вложенных блоках тоже тянут
# раскладку; на <img> размеры оставляем.
_BLOCK_ATTRS = re.compile(
    r'(?is)<(div|p|span|center|section|article|header|footer|nav|ul|ol|li|blockquote)\b([^>]*)>'
)
_ATTR = re.compile(r'(?is)\s+(width|height|class|id|align|valign|bgcolor|background|cellpadding|cellspacing|border|role|lang|dir)\s*=\s*("[^"]*"|\'[^\']*\'|[^\s>]+)')
_EMPTY_DIVS = re.compile(r"(?is)<div>\s*</div>")

# Порог, после которого письмо считается слишком тяжёлым для редактора и
# пересылается текстом (с картинками как вложениями решает вызывающий код).
MAX_EDITOR_HTML_BYTES = 600_000


def _strip_block_attrs(match: re.Match) -> str:
    tag, attrs = match.group(1), match.group(2)
    return f"<{tag}{_ATTR.sub('', attrs)}>"


def simplify_html_for_editor(html: str) -> str:
    """Линейная разметка для QTextEdit: без таблиц, стилей и скриптов."""
    if not html:
        return ""
    text = _DOCTYPE.sub("", html)
    text = _COMMENTS.sub("", text)
    text = _DROP_BLOCKS.sub("", text)
    text = _DROP_VOID.sub("", text)
    text = _COL.sub("", text)
    text = _TABLE_OPEN.sub("<div>", text)
    text = _TABLE_CLOSE.sub("</div>", text)
    text = _BLOCK_ATTRS.sub(_strip_block_attrs, text)
    text = _EMPTY_DIVS.sub("", text)
    return text


def nesting_depth(html: str, tag: str = "table") -> int:
    """Максимальная глубина вложенности тега — для тестов и диагностики."""
    depth = best = 0
    for match in re.finditer(rf"(?is)<(/?){tag}\b", html):
        if match.group(1):
            depth = max(0, depth - 1)
        else:
            depth += 1
            best = max(best, depth)
    return best
