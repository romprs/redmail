from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from redmail import branding

_SAMPLE = {
    "id": "gpp-test",
    "name": "ГПП Тест",
    "organization": "ООО «Тест»",
    "base": "light",
    "colors": {"accent": "#0079C2", "window": "#F4F7FA"},
    "font": {"family": "PT Sans", "size": 10},
    "letter": {"font_family": "PT Serif", "font_size": 12, "text_color": "#1B1B1B", "footer_html": "<p>Конфиденциально</p>"},
    "signature_html": "<p>С уважением,<br>{name}<br>{title}<br>{organization}<br>Тел.: {phone}</p>",
}


def _write(directory: Path, data: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{data['id']}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_parse_brand_reads_all_fields() -> None:
    brand = branding.parse_brand(_SAMPLE)
    assert brand.theme_value == "brand:gpp-test"
    assert brand.colors["accent"] == "#0079C2"
    assert (brand.font_family, brand.font_size) == ("PT Sans", 10)
    assert (brand.letter_font_family, brand.letter_font_size, brand.letter_text_color) == ("PT Serif", 12, "#1B1B1B")


@pytest.mark.parametrize(
    "change",
    [
        {"id": "Газпром"},
        {"name": ""},
        {"base": "blue"},
        {"colors": {"accent": "blue"}},
        {"colors": {"unknown": "#000000"}},
        {"font": {"size": 100}},
        {"letter": {"text_color": "red"}},
        {"letter": {"font_size": "big"}},
    ],
)
def test_parse_brand_rejects_bad_values(change: dict) -> None:
    with pytest.raises(ValueError):
        branding.parse_brand({**_SAMPLE, **change})


def test_brand_round_trips_through_dict() -> None:
    brand = branding.parse_brand(_SAMPLE)
    assert branding.parse_brand(branding.brand_to_dict(brand)) == brand


def test_load_brands_system_wins_and_bad_files_skipped(tmp_path: Path) -> None:
    system, user = tmp_path / "system", tmp_path / "user"
    _write(system, _SAMPLE)
    _write(user, {**_SAMPLE, "name": "Подмена"})
    _write(user, {"id": "other", "name": "Другая", "default": True})
    (user / "broken.json").write_text("{", encoding="utf-8")
    brands = branding.load_brands([system, user])
    assert [b.name for b in brands] == ["ГПП Тест", "Другая"]
    assert branding.find_brand("brand:gpp-test", [system, user]).name == "ГПП Тест"
    assert branding.find_brand("light", [system, user]) is None
    assert branding.default_theme([system, user]) == "brand:other"


def test_render_signature_drops_empty_lines_and_escapes() -> None:
    brand = branding.parse_brand(_SAMPLE)
    result = branding.render_signature(brand, {"name": "Иванов <И.И.>", "title": "", "phone": ""})
    assert result == "<p>С уважением,<br>Иванов &lt;И.И.&gt;<br>ООО «Тест»</p>"


def test_letter_html_adds_color_once() -> None:
    brand = branding.parse_brand(_SAMPLE)
    body = '<html><body style=" font-family:\'PT Serif\';"><p>Текст</p></body></html>'
    colored = branding.letter_html(brand, body)
    assert "color:#1B1B1B;" in colored
    assert branding.letter_html(brand, colored) == colored
    assert branding.letter_html(None, body) == body


def test_with_footer_appends_to_html_and_text() -> None:
    brand = branding.parse_brand(_SAMPLE)
    html_body, text = branding.with_footer(brand, "<html><body><p>Текст</p></body></html>", "Текст")
    assert html_body.endswith("<br><p>Конфиденциально</p></body></html>")
    assert text == "Текст\n\nКонфиденциально"
    assert branding.with_footer(None, "<p>x</p>", "x") == ("<p>x</p>", "x")


def test_save_and_delete_user_brand(tmp_path: Path) -> None:
    brand = branding.parse_brand(_SAMPLE)
    with patch("redmail.branding.user_brands_dir", return_value=tmp_path):
        path = branding.save_user_brand(brand)
        assert branding.parse_brand(json.loads(path.read_text(encoding="utf-8"))) == brand
        branding.delete_user_brand(brand.id)
        assert not path.exists()


def test_load_theme_uses_admin_default_brand(tmp_path: Path) -> None:
    from redmail import config_store

    _write(tmp_path / "brands", {**_SAMPLE, "default": True})
    with patch("redmail.config_store._settings_path", return_value=tmp_path / "settings.json"), patch(
        "redmail.branding.brand_dirs", return_value=[tmp_path / "brands"]
    ):
        assert config_store.load_theme() == "brand:gpp-test"
        config_store.save_theme("dark")
        assert config_store.load_theme() == "dark"


def test_apply_theme_uses_brand_colors(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication

    from redmail.ui import theme

    app = QApplication.instance() or QApplication([])
    _write(tmp_path, _SAMPLE)
    with patch("redmail.branding.brand_dirs", return_value=[tmp_path]):
        theme.apply_theme(app, "brand:gpp-test")
        assert theme.current_brand().id == "gpp-test"
        assert app.palette().color(QPalette.ColorRole.Highlight).name().lower() == "#0079c2"
        theme.apply_theme(app, "light")
        assert theme.current_brand() is None
