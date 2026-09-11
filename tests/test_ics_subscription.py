from __future__ import annotations

import pytest

from redmail import ics_subscription

_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Google Inc//Google Calendar 70.9054//EN
BEGIN:VEVENT
UID:abc123@google.com
DTSTART:20260915T070000Z
DTEND:20260915T080000Z
SUMMARY:Планёрка
ORGANIZER;CN=Иван:mailto:ivan@gmail.com
END:VEVENT
END:VCALENDAR
""".encode("utf-8")


def test_google_private_ical_url_is_downloaded_and_parsed() -> None:
    # Пожелание: "добавь подключение календаря Google" — закрытый адрес в
    # формате iCal (webcal:// у Google — тот же HTTPS) скачивается и
    # разбирается тем же парсером, что и CalDAV/приглашения.
    seen: list[str] = []

    def download(url: str) -> bytes:
        seen.append(url)
        return _ICS

    events = ics_subscription.fetch_events(
        "webcal://calendar.google.com/calendar/ical/x/private-abc/basic.ics", "ivan@gmail.com", download=download
    )
    assert seen == ["https://calendar.google.com/calendar/ical/x/private-abc/basic.ics"]
    assert [e.summary for e in events] == ["Планёрка"]
    assert events[0].uid == "abc123@google.com"


def test_non_calendar_response_is_reported_clearly() -> None:
    with pytest.raises(ics_subscription.IcsSubscriptionError, match="не календарь"):
        ics_subscription.fetch_events("https://example.com/login", "me@x.ru", download=lambda url: b"<html>login</html>")


def test_url_without_scheme_is_rejected_before_download() -> None:
    with pytest.raises(ics_subscription.IcsSubscriptionError, match="https://"):
        ics_subscription.fetch_events("calendar.google.com/basic.ics", "me@x.ru", download=lambda url: _ICS)
