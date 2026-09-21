"""«Открыть календарь» из напоминалки: почта закрыта — запускается сама и
открывается на календаре; клик по встрече открывает саму встречу."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from redmail import calendar_store, ipc_server, voice_client  # noqa: E402

START = datetime(2026, 9, 14, 6, 0, tzinfo=timezone.utc)


def _closed_mail(monkeypatch, launched: list) -> None:
    monkeypatch.setattr(voice_client, "_mail_endpoints", lambda: [])
    monkeypatch.setattr(voice_client, "_mail_binary", lambda: "/usr/bin/redmail")
    monkeypatch.setattr(voice_client, "_launch_detached", lambda argv: launched.append(argv) or True)
    monkeypatch.setattr(voice_client, "_last_launch", None)


def test_closed_mail_is_launched_on_the_event(monkeypatch) -> None:
    launched: list = []
    _closed_mail(monkeypatch, launched)
    assert voice_client.open_mail_calendar("-uid-1", START) == voice_client.LAUNCHED
    assert launched == [["/usr/bin/redmail", "--show-event=-uid-1", f"--event-start={START.isoformat()}"]]
    # Почта ещё стартует — повторный клик вторую копию не запускает.
    assert voice_client.open_mail_calendar() == voice_client.LAUNCHED
    assert len(launched) == 1


def test_closed_mail_without_event_opens_calendar(monkeypatch) -> None:
    launched: list = []
    _closed_mail(monkeypatch, launched)
    assert voice_client.open_mail_calendar() == voice_client.LAUNCHED
    assert launched == [["/usr/bin/redmail", "--calendar"]]


def test_running_mail_gets_show_event(monkeypatch) -> None:
    sent: list = []
    monkeypatch.setattr(voice_client, "_mail_endpoints", lambda: ["/tmp/x"])
    monkeypatch.setattr(voice_client, "_send_line", lambda address, payload, wait_reply=False: sent.append(payload) or True)
    monkeypatch.setattr(voice_client, "_launch_detached", lambda argv: (_ for _ in ()).throw(AssertionError("запуск")))
    assert voice_client.open_mail_calendar("u1", START) == voice_client.OPENED
    assert sent == [{"action": "show_event", "args": {"uid": "u1", "start": START.isoformat()}}]


def test_no_program_reports_failure(monkeypatch) -> None:
    monkeypatch.setattr(voice_client, "_mail_endpoints", lambda: [])
    monkeypatch.setattr(voice_client, "_mail_binary", lambda: None)
    assert voice_client.open_mail_calendar() == voice_client.FAILED


def test_launch_request_from_argv() -> None:
    assert ipc_server.launch_request(["redmail"]) is None
    assert ipc_server.launch_request(["redmail", "--calendar"]) == {"action": "focus", "args": {"section": "calendar"}}
    assert ipc_server.launch_request(["redmail", "--show-event=--export-mail", "--event-start=2026-09-14T06:00:00+00:00"]) == {
        "action": "show_event", "args": {"uid": "--export-mail", "start": "2026-09-14T06:00:00+00:00"},
    }
    # Без «=» значение не берём: следующий аргумент не станет uid.
    assert ipc_server.launch_request(["redmail", "--show-event", "x"]) is None


def test_launch_args_do_not_trigger_cli() -> None:
    from redmail import cli

    assert not cli.wants_cli(["redmail", "--show-event=--export-mail", "--calendar"])


class _Controller:
    def __init__(self) -> None:
        self.calls: list = []

    def ipc_show_event(self, uid, start):
        self.calls.append((uid, start))
        return True


def test_show_event_command() -> None:
    controller = _Controller()
    reply = ipc_server.handle_request(
        controller, {"action": "show_event", "args": {"uid": "u1", "start": "2026-09-14T06:00:00Z"}}
    )
    assert reply["ok"] is True and reply["found"] is True
    assert controller.calls == [("u1", START)]
    bad = ipc_server.handle_request(controller, {"action": "show_event", "args": {"uid": ""}})
    assert bad["ok"] is False


def test_series_day_nearest_to_reminder_is_found(tmp_path: Path) -> None:
    from redmail.ui.main_window import MainWindow

    path = tmp_path / "c.rmcal"
    calendar_store.save_event(path, calendar_store.Event(
        uid="daily", summary="Оперативка", dtstart=START, dtend=START + timedelta(hours=1),
        recurrence_rule="FREQ=DAILY;COUNT=5", calendar_id="local",
    ))
    fake = SimpleNamespace(calendar_path=path)
    found = MainWindow._find_calendar_event(fake, "daily", START + timedelta(days=2))
    assert found is not None and found.dtstart == START + timedelta(days=2)
    assert MainWindow._find_calendar_event(fake, "нет-такой", START) is None
