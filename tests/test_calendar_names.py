from __future__ import annotations

import pytest

from redmail import calendar_store
from redmail.calendar_names import CalendarChoice, CalendarNotFound, match_calendar

CALENDARS = [
    CalendarChoice(id="default", name="Мои встречи", source=calendar_store.SOURCE_LOCAL),
    CalendarChoice(id="vk", name="CalDAV", source=calendar_store.SOURCE_CALDAV),
    CalendarChoice(id="ex", name="Exchange: rsponomarev@amurgpz.ru", source=calendar_store.SOURCE_EWS),
    CalendarChoice(id="work", name="Работа", source=calendar_store.SOURCE_LOCAL),
]


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("эксчейндж", "ex"),
        ("в календарь эксчейндж", "ex"),
        ("рабочий", "ex"),
        ("вк", "vk"),
        ("калдав", "vk"),
        ("мои встречи", "default"),
        ("работа", "work"),
        ("два", "vk"),
        ("третий", "ex"),
        ("Exchange: rsponomarev@amurgpz.ru", "ex"),
        ("ex", "ex"),
    ],
)
def test_calendar_is_found_by_how_people_call_it(spoken: str, expected: str) -> None:
    assert match_calendar(spoken, CALENDARS).id == expected


def test_unknown_calendar_lists_what_exists() -> None:
    with pytest.raises(CalendarNotFound) as error:
        match_calendar("гугл", CALENDARS)
    assert "1 — Мои встречи" in str(error.value) and "3 — Exchange" in str(error.value)


def test_number_out_of_range_is_reported() -> None:
    with pytest.raises(CalendarNotFound) as error:
        match_calendar("пять", CALENDARS)
    assert "номер 5" in str(error.value)


def test_ambiguous_name_asks_for_number() -> None:
    two_vk = CALENDARS + [CalendarChoice(id="vk2", name="VK Отпуска", source=calendar_store.SOURCE_CALDAV)]
    with pytest.raises(CalendarNotFound) as error:
        match_calendar("вк", two_vk)
    assert "Назовите номер" in str(error.value)
