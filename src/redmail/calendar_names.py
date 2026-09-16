"""Выбор календаря по названию, сказанному голосом.

Помощник слышит не «Exchange: rsponomarev@amurgpz.ru», а «эксчейндж» или
«рабочий», не «CalDAV», а «вк». Поэтому календарь ищется и по словам своего
названия, и по словам, которыми называют его источник, и по номеру в
списке.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from redmail import calendar_store

#: Как люди называют календарь по его источнику.
_SOURCE_WORDS = {
    calendar_store.SOURCE_EWS: (
        "exchange", "эксчейндж", "эксчендж", "иксчейндж", "иксчендж", "эксченж", "обменник",
        "outlook", "аутлук", "рабочий", "рабочем", "рабочую", "рабочая",
    ),
    calendar_store.SOURCE_CALDAV: (
        "caldav", "калдав", "кэлдав", "vk", "вк", "вэка", "вконтакте", "вкшный", "вкшной",
    ),
    calendar_store.SOURCE_ICS: ("google", "гугл", "гугла", "гугле", "подписка", "подписку", "подписки"),
    calendar_store.SOURCE_LOCAL: (
        "локальный", "локальном", "локальную", "личный", "личном", "мой", "мои", "моих", "встречи",
    ),
}

_NUMBER_WORDS = {
    "один": 1, "первый": 1, "первом": 1, "первую": 1, "1": 1,
    "два": 2, "второй": 2, "втором": 2, "вторую": 2, "2": 2,
    "три": 3, "третий": 3, "третьем": 3, "третью": 3, "3": 3,
    "четыре": 4, "четвертый": 4, "четвертом": 4, "четвертую": 4, "4": 4,
    "пять": 5, "пятый": 5, "пятом": 5, "пятую": 5, "5": 5,
}

_FILLERS = {"в", "во", "на", "календарь", "календаре", "календаря", "календарю", "номер"}


@dataclass
class CalendarChoice:
    id: str
    name: str
    source: str


class CalendarNotFound(LookupError):
    """Календарь не найден или названию подходят несколько."""


def _normalize(text: str) -> str:
    return (text or "").casefold().replace("ё", "е")


def _words(text: str) -> list[str]:
    return [word for word in re.split(r"[^0-9a-zа-я]+", _normalize(text)) if word]


def _stem(word: str) -> str:
    return word[:5] if len(word) > 5 else word


def spoken_list(calendars: list[CalendarChoice]) -> str:
    return ", ".join(f"{index} — {cal.name}" for index, cal in enumerate(calendars, 1))


def match_calendar(query: str, calendars: list[CalendarChoice]) -> CalendarChoice:
    """Календарь по сказанному названию, источнику или номеру в списке."""
    if not calendars:
        raise CalendarNotFound("Календарей нет")
    words = [word for word in _words(query) if word not in _FILLERS]
    if not words:
        raise CalendarNotFound(f"Назовите календарь: {spoken_list(calendars)}")

    exact = [cal for cal in calendars if cal.id == query or _normalize(cal.name) == _normalize(query)]
    if len(exact) == 1:
        return exact[0]

    if len(words) == 1 and words[0] in _NUMBER_WORDS:
        number = _NUMBER_WORDS[words[0]]
        if 1 <= number <= len(calendars):
            return calendars[number - 1]
        raise CalendarNotFound(f"Календаря номер {number} нет. Есть: {spoken_list(calendars)}")

    query_stems = {_stem(word) for word in words}
    by_name = [cal for cal in calendars if query_stems & {_stem(word) for word in _words(cal.name)}]
    if len(by_name) == 1:
        return by_name[0]

    by_source = [
        cal for cal in (by_name or calendars)
        if query_stems & {_stem(word) for word in _SOURCE_WORDS.get(cal.source, ())}
    ]
    if len(by_source) == 1:
        return by_source[0]

    candidates = by_source or by_name
    if candidates:
        raise CalendarNotFound(f"Подходят несколько календарей: {spoken_list(candidates)}. Назовите номер")
    raise CalendarNotFound(f"Календарь «{query.strip()}» не найден. Есть: {spoken_list(calendars)}")
