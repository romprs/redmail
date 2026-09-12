from __future__ import annotations

from redmail.html_cleanup import nesting_depth, simplify_html_for_editor


def _nested_tables(depth: int, cells: int = 3) -> str:
    inner = "<p>текст</p><img src=\"cid:pic\" width=\"200\">"
    for _ in range(depth):
        cell = f"<td width=\"33%\" style=\"padding:4px\">{inner}</td>"
        inner = f"<table width=\"100%\" cellpadding=\"0\"><tr>{cell * cells}</tr></table>"
    return f"<html><head><style>td{{color:red}}</style></head><body>{inner}</body></html>"


def test_simplify_removes_tables_styles_and_keeps_content() -> None:
    # Жалоба: "завис при попытке переслать" — редактор раскладывал вложенные
    # таблицы рассылки экспоненциально долго. В редактор идут блоки.
    html = _nested_tables(5)
    assert nesting_depth(html) == 5
    simplified = simplify_html_for_editor(html)
    assert nesting_depth(simplified) == 0
    assert "<table" not in simplified.lower() and "<td" not in simplified.lower()
    assert "<style" not in simplified.lower() and "<head" not in simplified.lower()
    assert simplified.count("<img") == html.count("<img")
    assert 'width="200"' in simplified  # размеры картинок остаются
    assert "текст" in simplified


def test_simplify_strips_size_and_class_attrs_from_blocks() -> None:
    simplified = simplify_html_for_editor('<div class="x" width="600" style="color:red"><p align="center" id="p1">a</p></div>')
    assert simplified == '<div style="color:red"><p>a</p></div>'


def test_simplify_handles_empty_and_plain_html() -> None:
    assert simplify_html_for_editor("") == ""
    assert simplify_html_for_editor("<p>Привет</p>") == "<p>Привет</p>"
    assert simplify_html_for_editor("<!-- c --><!DOCTYPE html><b>x</b>") == "<b>x</b>"
