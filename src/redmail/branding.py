"""Фирменное оформление организации: цвета, шрифт и стандартная подпись.

Оформление — отдельный файл JSON, а не часть пакета: знаки и брендбуки
принадлежат организациям, а программа остаётся нейтральной. Файлы кладёт
администратор в системный каталог (для всех пользователей машины) или
пользователь — в свой каталог настроек. У каждой дочерней организации свой
файл: свои цвета, шрифт и шаблон подписи.

Пример файла — docs/brands/пример-организации.json. Поля:

    id              — латиницей, уникальный: "gpp-blagoveshchensk"
    name            — как показывать в «Параметрах»
    organization    — полное название для подписи
    base            — "light" или "dark": от какой темы брать недостающие цвета
    colors          — window, base, alt_base, text, border, accent,
                      accent_text, disabled_text (любые из них, "#RRGGBB")
    font            — {"family": "PT Sans", "size": 10}
    signature_html  — шаблон подписи; подстановки {name}, {title},
                      {department}, {organization}, {phone}, {email}.
                      Строка, где подстановка пустая, в подпись не попадает.
    letter          — оформление письма: {"font_family": "PT Sans",
                      "font_size": 11, "text_color": "#1B1B1B",
                      "footer_html": "<p>Конфиденциально…</p>"}; колонтитул
                      добавляется в конец письма при отправке
    default         — true: оформление по умолчанию для тех, кто тему ещё
                      не выбирал
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from redmail.applog import get_logger
from redmail.paths import app_dir

_log = get_logger("branding")

#: Каталог, куда администратор кладёт оформления для всех пользователей.
SYSTEM_BRANDS_DIR = Path("/etc/redmail/brands")
THEME_PREFIX = "brand:"
PLACEHOLDERS = ("name", "title", "department", "organization", "phone", "email")

_COLOR_KEYS = ("window", "base", "alt_base", "text", "border", "accent", "accent_text", "disabled_text")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass
class Brand:
    id: str
    name: str
    base: str = "light"
    colors: dict[str, str] = field(default_factory=dict)
    font_family: str = ""
    font_size: float = 0.0
    organization: str = ""
    signature_html: str = ""
    default: bool = False
    source: str = ""
    letter_font_family: str = ""
    letter_font_size: float = 0.0
    letter_text_color: str = ""
    letter_footer_html: str = ""

    @property
    def theme_value(self) -> str:
        return THEME_PREFIX + self.id


def user_brands_dir() -> Path:
    return app_dir() / "brands"


def brand_dirs() -> list[Path]:
    return [SYSTEM_BRANDS_DIR, user_brands_dir()]


def parse_brand(data: dict, source: str = "") -> Brand:
    """Проверяет и разбирает одно оформление. Ошибки — ValueError с
    понятным текстом: файл готовит администратор, ему нужно знать, что не так."""
    if not isinstance(data, dict):
        raise ValueError("ожидается объект JSON")
    brand_id = str(data.get("id", "")).strip()
    if not _ID.match(brand_id):
        raise ValueError("id — латинские строчные буквы, цифры, «-» и «_»")
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("не указано name")
    base = str(data.get("base", "light"))
    if base not in ("light", "dark"):
        raise ValueError("base — light или dark")
    colors = {}
    for key, value in (data.get("colors") or {}).items():
        if key not in _COLOR_KEYS:
            raise ValueError(f"неизвестный цвет {key!r}; допустимы: {', '.join(_COLOR_KEYS)}")
        if not isinstance(value, str) or not _HEX.match(value):
            raise ValueError(f"цвет {key}: ожидается #RRGGBB")
        colors[key] = value
    font = data.get("font") or {}
    family = str(font.get("family", "")).strip()
    try:
        size = float(font.get("size", 0) or 0)
    except (TypeError, ValueError):
        raise ValueError("font.size — число") from None
    if size and not 6 <= size <= 30:
        raise ValueError("font.size — от 6 до 30")
    letter = data.get("letter") or {}
    if not isinstance(letter, dict):
        raise ValueError("letter — объект")
    letter_color = str(letter.get("text_color", "") or "")
    if letter_color and not _HEX.match(letter_color):
        raise ValueError("letter.text_color: ожидается #RRGGBB")
    try:
        letter_size = float(letter.get("font_size", 0) or 0)
    except (TypeError, ValueError):
        raise ValueError("letter.font_size — число") from None
    if letter_size and not 6 <= letter_size <= 40:
        raise ValueError("letter.font_size — от 6 до 40")
    return Brand(
        id=brand_id, name=name, base=base, colors=colors, font_family=family, font_size=size,
        organization=str(data.get("organization", "")).strip(),
        signature_html=str(data.get("signature_html", "")),
        default=bool(data.get("default", False)), source=source,
        letter_font_family=str(letter.get("font_family", "") or "").strip(),
        letter_font_size=letter_size,
        letter_text_color=letter_color,
        letter_footer_html=str(letter.get("footer_html", "") or ""),
    )


def brand_to_dict(brand: Brand) -> dict:
    """Обратно в JSON — для редактора оформления и выгрузки администратору."""
    data: dict = {"id": brand.id, "name": brand.name, "organization": brand.organization, "base": brand.base}
    if brand.colors:
        data["colors"] = dict(brand.colors)
    if brand.font_family or brand.font_size:
        data["font"] = {"family": brand.font_family, "size": brand.font_size}
    letter = {
        "font_family": brand.letter_font_family, "font_size": brand.letter_font_size,
        "text_color": brand.letter_text_color, "footer_html": brand.letter_footer_html,
    }
    if any(letter.values()):
        data["letter"] = letter
    if brand.signature_html:
        data["signature_html"] = brand.signature_html
    if brand.default:
        data["default"] = True
    return data


def save_user_brand(brand: Brand) -> Path:
    directory = user_brands_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{brand.id}.json"
    path.write_text(json.dumps(brand_to_dict(brand), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def delete_user_brand(brand_id: str) -> None:
    path = user_brands_dir() / f"{brand_id}.json"
    if path.exists():
        path.unlink()


def letter_html(brand: Brand | None, body_html: str) -> str:
    """Цвет текста письма из оформления — в стиль body, чтобы его увидел
    получатель (шрифт Qt пишет туда сам)."""
    if brand is None or not brand.letter_text_color or "<body" not in body_html:
        return body_html
    body_tag = re.search(r"<body[^>]*>", body_html)
    if body_tag and f"color:{brand.letter_text_color}" in body_tag.group(0):
        return body_html  # черновик сохраняется не раз — цвет не дублируем
    return re.sub(
        r"<body([^>]*?)style=\"", lambda m: f'<body{m.group(1)}style=" color:{brand.letter_text_color};', body_html, count=1,
    ) if re.search(r"<body[^>]*style=\"", body_html) else body_html.replace(
        "<body", f'<body style="color:{brand.letter_text_color};"', 1,
    )


def with_footer(brand: Brand | None, body_html: str, body_text: str) -> tuple[str, str]:
    """Колонтитул письма из оформления — в конец HTML и текстовой версии."""
    if brand is None or not brand.letter_footer_html.strip():
        return body_html, body_text
    footer = brand.letter_footer_html
    plain = re.sub(r"<br\s*/?>|</p>", "\n", footer)
    plain = html.unescape(re.sub(r"<[^>]+>", "", plain)).strip()
    if "</body>" in body_html:
        body_html = body_html.replace("</body>", f"<br>{footer}</body>", 1)
    elif body_html:
        body_html = body_html + "<br>" + footer
    return body_html, (body_text.rstrip() + "\n\n" + plain) if plain else body_text


def load_brands(dirs: list[Path] | None = None) -> list[Brand]:
    """Все оформления из каталогов. При совпадении id пользовательский файл
    не подменяет системный: оформление организации задаёт администратор."""
    brands: dict[str, Brand] = {}
    for directory in dirs if dirs is not None else brand_dirs():
        try:
            files = sorted(Path(directory).glob("*.json"))
        except OSError:
            continue
        for path in files:
            try:
                brand = parse_brand(json.loads(path.read_text(encoding="utf-8")), str(path))
            except (OSError, ValueError) as exc:
                _log.warning("Оформление %s пропущено: %s", path, exc)
                continue
            brands.setdefault(brand.id, brand)
    return sorted(brands.values(), key=lambda brand: brand.name.casefold())


def find_brand(theme_value: str, dirs: list[Path] | None = None) -> Brand | None:
    if not theme_value.startswith(THEME_PREFIX):
        return None
    brand_id = theme_value[len(THEME_PREFIX):]
    return next((brand for brand in load_brands(dirs) if brand.id == brand_id), None)


def default_theme(dirs: list[Path] | None = None) -> str | None:
    """Оформление по умолчанию, если администратор его отметил."""
    brand = next((brand for brand in load_brands(dirs) if brand.default), None)
    return brand.theme_value if brand is not None else None


def render_signature(brand: Brand, person: dict[str, str]) -> str:
    """Подпись по шаблону оформления. Данные сотрудника экранируются; строка
    шаблона, в которой нужная подстановка пуста, выпадает целиком — чтобы в
    подписи не оставалось «Тел.: » без номера."""
    values = {key: str(person.get(key, "") or "").strip() for key in PLACEHOLDERS}
    if not values["organization"]:
        values["organization"] = brand.organization
    lines = re.split(r"(<br\s*/?>|\n)", brand.signature_html)
    result = []
    for part in lines:
        if re.fullmatch(r"<br\s*/?>|\n", part or ""):
            result.append(part)
            continue
        used = re.findall(r"\{(" + "|".join(PLACEHOLDERS) + r")\}", part)
        if used and not all(values[key] for key in used):
            if result and re.fullmatch(r"<br\s*/?>|\n", result[-1]):
                result.pop()  # вместе со строкой убираем и её перенос
            # Теги строки остаются: иначе с последней строкой пропал бы и </p>.
            result.append("".join(re.findall(r"<[^>]+>", part)))
            continue
        result.append(re.sub(
            r"\{(" + "|".join(PLACEHOLDERS) + r")\}", lambda m: html.escape(values[m.group(1)]), part,
        ))
    return "".join(result)
