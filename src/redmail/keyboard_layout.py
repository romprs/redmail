"""Текст, набранный не в той раскладке: «ghbdtn» → «привет» и обратно.

Раскладки стандартные: английская QWERTY и русская ЙЦУКЕН, включая
символы с Shift. Направление определяется по самому тексту: где больше
латинских букв — переводим в русскую раскладку, где кириллицы — в
английскую.
"""
from __future__ import annotations

_EN = "`qwertyuiop[]asdfghjkl;'zxcvbnm,./" + '~QWERTYUIOP{}ASDFGHJKL:"ZXCVBNM<>?' + "@#$^&"
_RU = "ёйцукенгшщзхъфывапролджэячсмитьбю." + "ЁЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ," + '"№;:?'

_EN_TO_RU = str.maketrans(_EN, _RU)
_RU_TO_EN = str.maketrans(_RU, _EN)


def to_russian(text: str) -> str:
    return text.translate(_EN_TO_RU)


def to_english(text: str) -> str:
    return text.translate(_RU_TO_EN)


def _latin_letters(text: str) -> int:
    return sum(1 for char in text if "a" <= char.lower() <= "z")


def _cyrillic_letters(text: str) -> int:
    return sum(1 for char in text if "а" <= char.lower() <= "я" or char in "ёЁ")


def switch_layout(text: str) -> str:
    """В другую раскладку — по преобладающему алфавиту текста."""
    if _latin_letters(text) >= _cyrillic_letters(text):
        return to_russian(text)
    return to_english(text)


def alternatives(text: str) -> list[str]:
    """Варианты строки поиска в другой раскладке (для подсказки адресатов)."""
    result = []
    for variant in (to_russian(text), to_english(text)):
        if variant != text and variant not in result:
            result.append(variant)
    return result
