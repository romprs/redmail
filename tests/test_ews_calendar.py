from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from redmail import ews_calendar
from redmail.calendar_store import Attendee, Event


class FakeMailbox:
    def __init__(self, email: str, name: str = "") -> None:
        self.email_address = email
        self.name = name


class FakeAttendee:
    def __init__(self, email: str, response: str, name: str = "") -> None:
        self.mailbox = FakeMailbox(email, name)
        self.response_type = response


class FakeItem:
    """Минимум полей CalendarItem, которые читает наш разбор."""

    def __init__(self, **kwargs) -> None:
        self.uid = kwargs.get("uid", "uid-1")
        self.id = kwargs.get("id", "AAA")
        self.subject = kwargs.get("subject", "тест")
        self.start = kwargs.get("start")
        self.end = kwargs.get("end")
        self.location = kwargs.get("location", "")
        self.text_body = kwargs.get("text_body", "")
        self.is_all_day = kwargs.get("is_all_day", False)
        self.organizer = kwargs.get("organizer")
        self.my_response_type = kwargs.get("my_response_type", "NoResponseReceived")
        self.required_attendees = kwargs.get("required_attendees", [])
        self.optional_attendees = kwargs.get("optional_attendees", [])
        self.is_cancelled = kwargs.get("is_cancelled", False)


@pytest.fixture(autouse=True)
def _calendar_item_is_fake(monkeypatch):
    # fetch_events фильтрует по типу CalendarItem — в тестах это FakeItem.
    monkeypatch.setattr(ews_calendar, "CalendarItem", FakeItem)


def test_item_to_event_maps_fields_and_participation() -> None:
    start = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    item = FakeItem(
        subject="Планёрка",
        start=start,
        end=start + timedelta(hours=2),
        location="Переговорная",
        text_body="повестка",
        organizer=FakeMailbox("boss@amurgpz.ru", "Начальник"),
        my_response_type="Accept",
        required_attendees=[FakeAttendee("me@amurgpz.ru", "Accept", "Я"), FakeAttendee("colleague@amurgpz.ru", "NoResponseReceived")],
        optional_attendees=[FakeAttendee("optional@amurgpz.ru", "Decline")],
    )
    event = ews_calendar.item_to_event(item, "me@amurgpz.ru")
    assert event.summary == "Планёрка" and event.location == "Переговорная"
    assert event.dtstart == start and event.dtend == start + timedelta(hours=2)
    assert event.organizer_email == "boss@amurgpz.ru" and event.is_organizer is False
    assert event.my_participation == "accepted"
    assert [(a.email, a.participation) for a in event.attendees] == [
        ("me@amurgpz.ru", "accepted"),
        ("colleague@amurgpz.ru", "needs-action"),
        ("optional@amurgpz.ru", "declined"),
    ]


def test_item_to_event_marks_own_meeting_and_cancelled() -> None:
    start = datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc)
    item = FakeItem(start=start, end=start + timedelta(hours=1),
                    organizer=FakeMailbox("Me@AmurGPZ.ru"), is_cancelled=True)
    event = ews_calendar.item_to_event(item, "me@amurgpz.ru")
    assert event.is_organizer is True and event.my_participation == "accepted"
    assert event.status == "cancelled"


def test_fetch_events_skips_broken_items_and_uses_window() -> None:
    start = datetime(2026, 9, 15, tzinfo=timezone.utc)
    good = FakeItem(uid="ok", start=start, end=start + timedelta(hours=1), organizer=FakeMailbox("a@x.ru"))
    class BrokenItem(FakeItem):
        """Встреча, разбор которой падает: у поля участников битое значение."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.required_attendees = _Exploding()

    class _Exploding:
        def __iter__(self):
            raise RuntimeError("битое поле")

        def __bool__(self):
            return True

    broken = BrokenItem(uid="bad", start=start, end=start + timedelta(hours=1))

    calls = {}

    class FakeCalendar:
        def view(self, start, end):
            calls["window"] = (start, end)
            return [good, broken, object()]

    account = SimpleNamespace(calendar=FakeCalendar())
    events = ews_calendar.fetch_events(account, start, start + timedelta(days=7), "me@x.ru")
    assert [e.uid for e in events] == ["ok"]  # битая встреча и мусор без типа пропущены
    assert calls["window"][0].year == 2026


def test_fetch_events_stops_asking_when_server_asks_to_wait() -> None:
    """Сервер просит притормозить — остальные части окна сейчас ответят тем
    же. На журнале пользователя такой перебор давал два десятка отказов
    подряд и пустой календарь; берём то, что уже получили."""
    start = datetime(2026, 9, 15, tzinfo=timezone.utc)
    good = FakeItem(uid="ok", start=start, end=start + timedelta(hours=1), organizer=FakeMailbox("a@x.ru"))
    windows: list[tuple] = []

    class ThrottlingCalendar:
        def view(self, start, end):
            windows.append((start, end))
            if len(windows) == 1:
                return [good]
            raise RuntimeError("Max timeout reached (gave up when asked to back off 80.000 seconds)")

    events = ews_calendar.fetch_events(
        SimpleNamespace(calendar=ThrottlingCalendar()), start, start + timedelta(days=200), "me@x.ru"
    )
    assert [e.uid for e in events] == ["ok"]
    assert len(windows) == 2  # первая часть получена, на второй остановились


def test_throttling_is_told_apart_from_real_errors() -> None:
    assert ews_calendar._is_throttled(RuntimeError("ErrorServerBusy: server too busy"))
    assert ews_calendar._is_throttled(RuntimeError("Max timeout reached (gave up when asked to back off 80s)"))
    assert not ews_calendar._is_throttled(RuntimeError("The request timed out."))
    assert not ews_calendar._is_throttled(RuntimeError("EWS недоступен"))


def test_fetch_events_wraps_server_error() -> None:
    class FailingCalendar:
        def view(self, start, end):
            raise RuntimeError("EWS недоступен")

    with pytest.raises(ews_calendar.EwsCalendarError) as info:
        ews_calendar.fetch_events(SimpleNamespace(calendar=FailingCalendar()), datetime.now(timezone.utc),
                                  datetime.now(timezone.utc) + timedelta(days=7), "me@x.ru")
    assert "EWS недоступен" in str(info.value)


def test_push_event_updates_existing_by_uid(monkeypatch) -> None:
    saved = {}

    class FakeExisting:
        id = "AAA"
        changekey = "CK"

    class FakeStored:
        def __init__(self) -> None:
            self.subject = ""
            self.start = None
            self.end = None
            self.location = None
            self.body = None
            self.required_attendees = None

        def save(self, send_meeting_invitations=None):
            saved["mode"] = send_meeting_invitations
            saved["subject"] = self.subject

    class FakeQuery:
        def only(self, *fields):
            return self

        def first(self):
            return FakeExisting()

    class FakeCalendar:
        def filter(self, **kwargs):
            saved["uid"] = kwargs.get("uid")
            return FakeQuery()

        def get(self, id):  # noqa: A002 - имя параметра как в exchangelib
            return FakeStored()

    account = SimpleNamespace(calendar=FakeCalendar())
    start = datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc)
    event = Event(uid="uid-1", summary="Перенос", dtstart=start, dtend=start + timedelta(hours=1),
                  attendees=[Attendee(email="a@x.ru", name="А", participation="needs-action")])
    ews_calendar.push_event(account, event)
    assert saved["uid"] == "uid-1" and saved["subject"] == "Перенос"
    assert saved["mode"] == "SendToAllAndSaveCopy"  # есть участники — приглашения уходят


def test_exchange_datetime_is_converted_without_tzinfo_error() -> None:
    """EWSDateTime.astimezone() не принимает обычный timezone.utc — из-за
    этого пропускались ВСЕ встречи Exchange (в журнале за день 7354
    «встреча пропущена: 'tzinfo' … must be of type EWSTimeZone»)."""
    from exchangelib import EWSDateTime, EWSTimeZone

    yakutsk = EWSDateTime(2026, 9, 18, 7, 0, tzinfo=EWSTimeZone("Asia/Yakutsk"))
    assert ews_calendar._to_utc(yakutsk) == datetime(2026, 9, 17, 22, 0, tzinfo=timezone.utc)
    assert ews_calendar._ews_datetime(yakutsk).hour == 22

    item = FakeItem(uid="x", start=yakutsk, end=yakutsk, organizer=FakeMailbox("a@x.ru"))
    event = ews_calendar.item_to_event(item, "me@x.ru")
    assert event.dtstart == datetime(2026, 9, 17, 22, 0, tzinfo=timezone.utc)


def test_failed_window_chunk_does_not_lose_the_rest(monkeypatch) -> None:
    monkeypatch.setattr(ews_calendar, "CalendarItem", FakeItem)
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    calls = {"n": 0}

    class Calendar:
        def view(self, start, end):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("The request timed out")
            moment = start + timedelta(hours=1)
            return [FakeItem(uid=f"uid-{calls['n']}", start=moment, end=moment, organizer=FakeMailbox("a@x.ru"))]

    events = ews_calendar.fetch_events(SimpleNamespace(calendar=Calendar()), start, start + timedelta(days=42), "me@x.ru")
    assert calls["n"] == 3 and len(events) == 2  # часть окна без ответа — остальные встречи получены


def test_shared_mailbox_calendar_uses_own_credentials(monkeypatch) -> None:
    """Подписка на календарь коллеги: тот же сеанс (наша учётная запись),
    но ящик — его; пароль владельца не нужен."""
    monkeypatch.setattr(ews_calendar, "CalendarItem", FakeItem)
    start = datetime(2026, 9, 18, tzinfo=timezone.utc)
    opened: list[str] = []

    class Calendar:
        def __init__(self, uid):
            self.uid = uid

        def view(self, start, end):
            moment = start + timedelta(hours=1)
            return [FakeItem(uid=self.uid, start=moment, end=moment, organizer=FakeMailbox("a@x.ru"))]

    mine, colleague = SimpleNamespace(calendar=Calendar("my")), SimpleNamespace(calendar=Calendar("theirs"))

    def mailbox_account(email):
        opened.append(email)
        return colleague

    session = SimpleNamespace(_account=mine, mailbox_account=mailbox_account)
    events = ews_calendar.fetch_events(session, start, start + timedelta(days=1), "me@x.ru")
    assert [e.uid for e in events] == ["my"] and opened == []
    events = ews_calendar.fetch_events(session, start, start + timedelta(days=1), "me@x.ru", "ivanov@x.ru")
    assert [e.uid for e in events] == ["theirs"] and opened == ["ivanov@x.ru"]


def test_calendar_pass_stops_after_three_timeouts_in_a_row(monkeypatch) -> None:
    """Журнал пользователя: каждая часть окна висела по минуте, проход длился
    10–18 минут и всё это время держал соединение с Exchange. После трёх
    таймаутов подряд проход прекращается до следующей синхронизации."""
    monkeypatch.setattr(ews_calendar, "CalendarItem", FakeItem)
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    calls = {"n": 0}

    class Calendar:
        def view(self, start, end):
            calls["n"] += 1
            raise RuntimeError("The request timed out.")

    with pytest.raises(ews_calendar.EwsCalendarError):
        ews_calendar.fetch_events(SimpleNamespace(calendar=Calendar()), start, start + timedelta(days=200), "me@x.ru")
    assert calls["n"] == 3


def test_calendar_view_asks_only_for_needed_fields(monkeypatch) -> None:
    """Просмотр календаря по умолчанию тянет встречи целиком — с HTML-телом и
    вложениями; на настоящем сервере двухнедельная часть окна так не
    успевала за минуту. Просим только то, что читаем."""
    monkeypatch.setattr(ews_calendar, "CalendarItem", FakeItem)
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    asked: list[tuple] = []

    class View(list):
        def only(self, *fields):
            asked.append(fields)
            return self

    class Calendar:
        def view(self, start, end):
            return View()

    ews_calendar.fetch_events(SimpleNamespace(calendar=Calendar()), start, start + timedelta(days=7), "me@x.ru")
    assert asked and "body" not in asked[0] and "attachments" not in asked[0]
    assert {"uid", "start", "end", "subject", "organizer"} <= set(asked[0])
