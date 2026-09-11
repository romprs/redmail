from __future__ import annotations

import json
import os

# Qt должен подняться без экрана — тесты гоняются и в CI, и по ssh.
# Переменную ставим ДО первого импорта PySide6, иначе платформенный плагин
# уже выбран и менять поздно.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from redmail import calendar_store, ipc_server
from redmail.config_store import MailRule
from redmail.imap_client import MessageSummary
from redmail.ipc_server import IpcServer, handle_request, parse_iso_datetime
from redmail.ui.main_window import MainWindow, _mail_rule_moves


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeController:
    """Подставной MainWindow: запоминает, что и с какими аргументами у него
    попросили. Так проверяется весь разбор запроса без единого виджета."""

    def __init__(self, *, rules=None, apply_result=None, found_events=None):
        self.calls: list[tuple[str, dict]] = []
        self._rules = rules if rules is not None else []
        self._apply_result = apply_result or {"folder": "INBOX", "moved": 0, "moves": {}}
        self._found_events = found_events if found_events is not None else []

    def ipc_focus(self):
        self.calls.append(("focus", {}))

    def ipc_compose_email(self, **kwargs):
        self.calls.append(("compose_email", kwargs))

    def ipc_create_event(self, **kwargs):
        self.calls.append(("create_event", kwargs))

    def ipc_update_event(self, uid, **kwargs):
        self.calls.append(("update_event", dict(kwargs, uid=uid)))

    def ipc_find_events(self, *, subject=None, on_date=None):
        self.calls.append(("find_events", {"subject": subject, "on_date": on_date}))
        return self._found_events

    # -- пошаговая форма встречи: запоминаем поля, отдаём «состояние» --
    def ipc_event_form_open(self, *, uid=None, **changes):
        self.calls.append(("event_form_open", dict(changes, uid=uid)))
        self.form = dict(changes)
        return dict(self.form)

    def ipc_event_form_set(self, **changes):
        self.calls.append(("event_form_set", dict(changes)))
        self.form.update(changes)
        return dict(self.form)

    def ipc_event_form_state(self):
        self.calls.append(("event_form_state", {}))
        return dict(getattr(self, "form", {}))

    def ipc_event_form_save(self):
        self.calls.append(("event_form_save", {}))
        return dict(getattr(self, "form", {}))

    def ipc_event_form_cancel(self):
        self.calls.append(("event_form_cancel", {}))

    def ipc_contacts(self):
        return getattr(self, "contacts", [])

    def ipc_cancel_event(self, uid):
        self.calls.append(("cancel_event", {"uid": uid}))

    def ipc_apply_mail_rules(self, folder=None):
        self.calls.append(("apply_mail_rules", {"folder": folder}))
        return self._apply_result

    def ipc_list_mail_rules(self):
        self.calls.append(("list_mail_rules", {}))
        return self._rules


# ---------------------------------------------------------------------------
# Разбор запроса
# ---------------------------------------------------------------------------


def test_ping_reports_version_and_protocol() -> None:
    response = handle_request(FakeController(), {"action": "ping"})
    assert response["ok"] is True
    assert response["pong"] is True
    assert response["protocol"] == ipc_server.PROTOCOL
    assert isinstance(response["version"], str) and response["version"]


def test_focus_calls_controller() -> None:
    controller = FakeController()
    assert handle_request(controller, {"action": "focus"}) == {"ok": True, "focused": True}
    assert controller.calls == [("focus", {})]


def test_unknown_action() -> None:
    response = handle_request(FakeController(), {"action": "самоуничтожение"})
    assert response == {"ok": False, "error": "unknown action: самоуничтожение"}


def test_missing_action() -> None:
    assert handle_request(FakeController(), {})["ok"] is False
    assert handle_request(FakeController(), {"action": ""})["ok"] is False
    assert handle_request(FakeController(), [1, 2])["ok"] is False


def test_args_must_be_object() -> None:
    response = handle_request(FakeController(), {"action": "focus", "args": [1]})
    assert response["ok"] is False
    assert "args" in response["error"]


def test_args_may_be_absent_or_null() -> None:
    assert handle_request(FakeController(), {"action": "focus"})["ok"] is True
    assert handle_request(FakeController(), {"action": "focus", "args": None})["ok"] is True


def test_controller_exception_becomes_error_not_crash() -> None:
    class Boom(FakeController):
        def ipc_focus(self):
            raise RuntimeError("SMTP не настроен")

    assert handle_request(Boom(), {"action": "focus"}) == {
        "ok": False,
        "error": "SMTP не настроен",
    }


def test_compose_email_passes_all_fields() -> None:
    controller = FakeController()
    response = handle_request(
        controller,
        {
            "action": "compose_email",
            "args": {
                "to": "a@example.com, b@example.com",
                "subject": "Отчёт",
                "body": "Привет",
                "cc": "c@example.com",
                "bcc": "d@example.com",
            },
        },
    )
    assert response == {"ok": True, "opened": "compose_email"}
    assert controller.calls == [
        (
            "compose_email",
            {
                "to": "a@example.com, b@example.com",
                "subject": "Отчёт",
                "body": "Привет",
                "cc": "c@example.com",
                "bcc": "d@example.com",
            },
        )
    ]


def test_compose_email_accepts_recipient_list() -> None:
    controller = FakeController()
    handle_request(
        controller,
        {"action": "compose_email", "args": {"to": ["a@example.com", "b@example.com"]}},
    )
    assert controller.calls[0][1]["to"] == "a@example.com, b@example.com"


def test_compose_email_requires_recipient() -> None:
    response = handle_request(FakeController(), {"action": "compose_email", "args": {}})
    assert response["ok"] is False
    assert "to" in response["error"]


def test_create_event_parses_start_and_duration() -> None:
    controller = FakeController()
    response = handle_request(
        controller,
        {
            "action": "create_event",
            "args": {
                "subject": "Планёрка",
                "start": "2026-09-10T15:00:00+03:00",
                "duration_minutes": 45,
                "participants": ["a@example.com"],
                "description": "повестка",
                "location": "переговорная",
            },
        },
    )
    assert response == {"ok": True, "opened": "create_event"}
    _, kwargs = controller.calls[0]
    assert kwargs["summary"] == "Планёрка"
    assert kwargs["start"] == datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    assert kwargs["duration_minutes"] == 45
    assert kwargs["participants"] == ["a@example.com"]
    assert kwargs["description"] == "повестка"
    assert kwargs["location"] == "переговорная"


def test_create_event_default_duration_is_an_hour() -> None:
    controller = FakeController()
    handle_request(
        controller,
        {"action": "create_event", "args": {"subject": "X", "start": "2026-09-10T15:00Z"}},
    )
    assert controller.calls[0][1]["duration_minutes"] == 60


def test_create_event_requires_subject_and_start() -> None:
    no_subject = handle_request(
        FakeController(), {"action": "create_event", "args": {"start": "2026-09-10T15:00Z"}}
    )
    assert no_subject["ok"] is False and "subject" in no_subject["error"]
    no_start = handle_request(FakeController(), {"action": "create_event", "args": {"subject": "X"}})
    assert no_start["ok"] is False and "start" in no_start["error"]


def test_create_event_rejects_bad_duration() -> None:
    for bad in (0, -5, "30", 1.5):
        response = handle_request(
            FakeController(),
            {
                "action": "create_event",
                "args": {"subject": "X", "start": "2026-09-10T15:00Z", "duration_minutes": bad},
            },
        )
        assert response["ok"] is False, bad
        assert "duration_minutes" in response["error"]


def test_update_event_sends_only_given_fields() -> None:
    controller = FakeController()
    response = handle_request(
        controller,
        {"action": "update_event", "args": {"uid": "uid-1", "start": "2026-09-10T15:00Z"}},
    )
    assert response == {"ok": True, "opened": "update_event", "uid": "uid-1"}
    action, kwargs = controller.calls[0]
    assert action == "update_event"
    assert kwargs == {"uid": "uid-1", "start": datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc)}


def test_update_event_distinguishes_empty_string_from_absent() -> None:
    controller = FakeController()
    handle_request(controller, {"action": "update_event", "args": {"uid": "u", "description": ""}})
    assert controller.calls[0][1] == {"uid": "u", "description": ""}


def test_update_event_requires_uid() -> None:
    response = handle_request(FakeController(), {"action": "update_event", "args": {}})
    assert response["ok"] is False and "uid" in response["error"]


def test_find_events_passes_subject_and_explicit_date() -> None:
    controller = FakeController(found_events=[{"uid": "u1", "summary": "Планёрка"}])
    response = handle_request(
        controller,
        {"action": "find_events", "args": {"subject": "планёрка", "date": "2026-09-10"}},
    )
    assert response == {"ok": True, "events": [{"uid": "u1", "summary": "Планёрка"}]}
    assert controller.calls == [
        ("find_events", {"subject": "планёрка", "on_date": date(2026, 9, 10)})
    ]


def test_find_events_defaults_date_to_today() -> None:
    controller = FakeController()
    fixed_now = datetime(2026, 9, 8, 21, 0, tzinfo=timezone.utc).astimezone()
    with patch("redmail.ipc_server.datetime") as mock_datetime:
        mock_datetime.now.return_value = fixed_now
        mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
        handle_request(controller, {"action": "find_events", "args": {}})
    assert controller.calls == [("find_events", {"subject": None, "on_date": fixed_now.date()})]


def test_find_events_empty_subject_becomes_none() -> None:
    controller = FakeController()
    handle_request(controller, {"action": "find_events", "args": {"date": "2026-09-10"}})
    assert controller.calls[0][1]["subject"] is None


# ---------------------------------------------------------------------------
# Пошаговая форма встречи
# ---------------------------------------------------------------------------


def test_event_form_open_parses_fields() -> None:
    controller = FakeController()
    response = handle_request(
        controller,
        {
            "action": "event_form_open",
            "args": {
                "subject": "Планёрка",
                "date": "2026-09-15",
                "time": "08:30",
                "duration_minutes": 120,
                "recurrence": "weekly",
                "participants": ["a@example.com"],
                "location": "каб. 121",
            },
        },
    )
    assert response["ok"] is True and response["opened"] == "event_form"
    action, kwargs = controller.calls[0]
    assert action == "event_form_open"
    assert kwargs == {
        "uid": None,
        "summary": "Планёрка",
        "date": date(2026, 9, 15),
        "time": (8, 30),
        "duration_minutes": 120,
        "recurrence": "FREQ=WEEKLY",
        "participants": ["a@example.com"],
        "location": "каб. 121",
    }


def test_event_form_open_by_uid_without_fields() -> None:
    controller = FakeController()
    handle_request(controller, {"action": "event_form_open", "args": {"uid": "uid-1"}})
    assert controller.calls[0] == ("event_form_open", {"uid": "uid-1"})


def test_event_form_set_requires_at_least_one_field() -> None:
    response = handle_request(FakeController(), {"action": "event_form_set", "args": {}})
    assert response["ok"] is False and "нечего менять" in response["error"]


def test_event_form_set_recurrence_aliases_and_raw_rule() -> None:
    controller = FakeController()
    for given, expected in (("none", None), ("daily", "FREQ=DAILY"), ("FREQ=MONTHLY", "FREQ=MONTHLY"), ("", None)):
        handle_request(controller, {"action": "event_form_set", "args": {"recurrence": given}})
        assert controller.calls[-1][1] == {"recurrence": expected}, given
    bad = handle_request(controller, {"action": "event_form_set", "args": {"recurrence": "каждую пятницу"}})
    assert bad["ok"] is False and "recurrence" in bad["error"]


def test_event_form_set_rejects_bad_time() -> None:
    response = handle_request(FakeController(), {"action": "event_form_set", "args": {"time": "25:00"}})
    assert response["ok"] is False and "HH:MM" in response["error"]


def test_event_form_state_save_cancel() -> None:
    controller = FakeController()
    handle_request(controller, {"action": "event_form_open", "args": {"subject": "X"}})
    assert handle_request(controller, {"action": "event_form_state"}) == {"ok": True, "form": {"summary": "X"}}
    assert handle_request(controller, {"action": "event_form_save"}) == {
        "ok": True,
        "saving": True,
        "form": {"summary": "X"},
    }
    assert handle_request(controller, {"action": "event_form_cancel"}) == {"ok": True, "cancelled": True}


def _contact(name: str, *emails: str):
    from redmail.contact_store import Contact

    return Contact(display_name=name, emails=list(emails))


CONTACTS = [
    _contact("Шилкин Иван Петрович", "shilkin@example.com"),
    _contact("Пономарёв Роман", "ponomarev@example.com"),
    _contact("Будько Анна", "budko@example.com"),
    _contact("Шапошникова Мария", "m.shaposhnikova@example.com"),
    _contact("Точилин Сергей", "tochilin@example.com"),
    _contact("Ли Дмитрий", "li@example.com"),
    _contact("Без адреса"),
]


@pytest.mark.parametrize(
    "spoken, expected",
    [
        ("Шилкина", "shilkin@example.com"),  # родительный падеж
        ("шилкин", "shilkin@example.com"),
        ("Пономарева", "ponomarev@example.com"),  # е вместо ё и падеж
        ("Будько", "budko@example.com"),  # несклоняемая
        ("Шапошникову", "m.shaposhnikova@example.com"),  # женская, винительный
        ("Точилина", "tochilin@example.com"),
        ("Ивана Шилкина", "shilkin@example.com"),  # два слова — оба должны совпасть
    ],
)
def test_match_contacts_by_spoken_surname(spoken: str, expected: str) -> None:
    matches = ipc_server.match_contacts(CONTACTS, spoken)
    assert [c.emails[0] for c in matches] == [expected]


def test_match_contacts_short_query_needs_exact_stem() -> None:
    assert [c.emails[0] for c in ipc_server.match_contacts(CONTACTS, "Ли")] == ["li@example.com"]
    assert ipc_server.match_contacts(CONTACTS, "Ш") == []
    assert ipc_server.match_contacts(CONTACTS, "Сидоров") == []
    assert ipc_server.match_contacts(CONTACTS, "Без адреса") == []  # без email участник бесполезен


def test_find_contacts_handler() -> None:
    controller = FakeController()
    controller.contacts = CONTACTS
    response = handle_request(controller, {"action": "find_contacts", "args": {"query": "Шилкина"}})
    assert response == {
        "ok": True,
        "contacts": [{"name": "Шилкин Иван Петрович", "email": "shilkin@example.com", "emails": ["shilkin@example.com"]}],
    }
    assert handle_request(controller, {"action": "find_contacts", "args": {}})["ok"] is False


def _fresh_event_dialog(qapp):
    from redmail.ui.main_window import EventDialog

    start = datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc)
    draft = calendar_store.Event(uid="draft", summary="", dtstart=start, dtend=start + timedelta(hours=1))
    return EventDialog(None, event=draft, my_email="me@example.com")


def test_apply_event_form_changes_on_real_dialog(qapp) -> None:
    from redmail.ui.main_window import _apply_event_form_changes, _event_form_state

    dialog = _fresh_event_dialog(qapp)
    _apply_event_form_changes(
        dialog,
        {
            "summary": "Планёрка",
            "date": date(2026, 9, 15),
            "time": (8, 30),
            "recurrence": "FREQ=WEEKLY",
            "participants": ["a@example.com"],
            "location": "каб. 121",
            "description": "утренняя",
        },
    )
    state = _event_form_state(dialog, None)
    assert state["summary"] == "Планёрка"
    assert state["start"] == "2026-09-15T08:30:00"
    assert state["duration_minutes"] == 60  # длительность при смене даты/времени сохранилась
    assert state["recurrence"] == "FREQ=WEEKLY"
    assert state["participants"] == ["a@example.com"]
    assert state["location"] == "каб. 121" and state["description"] == "утренняя"

    _apply_event_form_changes(dialog, {"duration_minutes": 120, "add_participants": ["b@example.com", "a@example.com"]})
    state = _event_form_state(dialog, None)
    assert state["end"] == "2026-09-15T10:30:00"
    assert state["participants"] == ["a@example.com", "b@example.com"]

    _apply_event_form_changes(dialog, {"recurrence": None})
    assert _event_form_state(dialog, None)["recurrence"] is None
    with pytest.raises(ValueError):
        _apply_event_form_changes(dialog, {"recurrence": "FREQ=HOURLY"})


def test_validate_event_form_rejects_past_and_inverted(qapp) -> None:
    from redmail.ui.main_window import _apply_event_form_changes, _validate_event_form

    dialog = _fresh_event_dialog(qapp)
    _apply_event_form_changes(dialog, {"date": date(2020, 1, 1)})
    with pytest.raises(ValueError, match="прошедшую"):
        _validate_event_form(dialog, None)

    dialog = _fresh_event_dialog(qapp)
    _apply_event_form_changes(dialog, {"date": date.today() + timedelta(days=30)})
    dialog.end_edit.setDateTime(dialog.start_edit.dateTime().addSecs(-60))
    with pytest.raises(ValueError, match="позже начала"):
        _validate_event_form(dialog, None)


def test_cancel_event() -> None:
    controller = FakeController()
    response = handle_request(controller, {"action": "cancel_event", "args": {"uid": "uid-9"}})
    assert response == {"ok": True, "opened": "cancel_event", "uid": "uid-9"}
    assert controller.calls == [("cancel_event", {"uid": "uid-9"})]


def test_apply_mail_rules_defaults_to_current_folder() -> None:
    controller = FakeController(apply_result={"folder": "INBOX", "moved": 3, "moves": {"Счета": 3}})
    response = handle_request(controller, {"action": "apply_mail_rules"})
    assert response == {"ok": True, "folder": "INBOX", "moved": 3, "moves": {"Счета": 3}}
    assert controller.calls == [("apply_mail_rules", {"folder": None})]


def test_apply_mail_rules_with_explicit_folder() -> None:
    controller = FakeController()
    handle_request(controller, {"action": "apply_mail_rules", "args": {"folder": "Архив"}})
    assert controller.calls == [("apply_mail_rules", {"folder": "Архив"})]


def test_list_mail_rules() -> None:
    rules = [{"field": "from", "contains": "bank", "target_folder": "Счета"}]
    response = handle_request(FakeController(rules=rules), {"action": "list_mail_rules"})
    assert response == {"ok": True, "rules": rules}


# ---------------------------------------------------------------------------
# Разбор даты
# ---------------------------------------------------------------------------


def test_parse_iso_datetime_with_offset() -> None:
    assert parse_iso_datetime("2026-09-10T15:00:00+03:00") == datetime(
        2026, 9, 10, 12, 0, tzinfo=timezone.utc
    )


def test_parse_iso_datetime_accepts_z_suffix() -> None:
    # datetime.fromisoformat() понимает "Z" только с Python 3.11 — проект
    # заявлен с 3.9, поэтому суффикс обрабатывается вручную.
    assert parse_iso_datetime("2026-09-10T15:00:00Z") == datetime(
        2026, 9, 10, 15, 0, tzinfo=timezone.utc
    )


def test_parse_iso_datetime_naive_is_local() -> None:
    parsed = parse_iso_datetime("2026-09-10T15:00")
    expected = datetime(2026, 9, 10, 15, 0).astimezone().astimezone(timezone.utc)
    assert parsed == expected


def test_parse_iso_datetime_rejects_garbage() -> None:
    for bad in ("", "завтра в три", "10.09.2026 15:00"):
        with pytest.raises(ValueError):
            parse_iso_datetime(bad)


# ---------------------------------------------------------------------------
# Транспорт: настоящий QLocalServer + настоящий QLocalSocket
# ---------------------------------------------------------------------------


def _unique_name() -> str:
    return f"redmail-ipc-test-{uuid.uuid4().hex[:12]}"


def _pump(_app, predicate, timeout_ms: int = 5000) -> bool:
    """Ждём выполнения условия в НАСТОЯЩЕМ цикле событий (QEventLoop.exec()),
    а не в цикле с app.processEvents().

    Разница принципиальна для этого файла: команды канала откладывают показ
    модального окна, а модальное окно — это вложенный QEventLoop. Вложить
    его внутрь processEvents() нельзя: в offscreen-процессе с загруженным
    QtWebEngine это стабильно роняет интерпретатор (access violation).
    В настоящем приложении отложенный вызов приходит из app.exec(), и здесь
    воспроизводится ровно та же схема."""
    if predicate():
        return True
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(5)
    poll.timeout.connect(lambda: predicate() and loop.quit())
    poll.start()
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    deadline.start(timeout_ms)
    loop.exec()
    poll.stop()
    deadline.stop()
    return bool(predicate())


class _Client:
    """Настоящий QLocalSocket-клиент с построчным чтением ответов."""

    def __init__(self, app, name: str, on_response=None):
        self._app = app
        self._on_response = on_response
        self.socket = QLocalSocket()
        self.socket.connectToServer(name)
        assert self.socket.waitForConnected(3000), self.socket.errorString()
        self._buffer = bytearray()
        self.responses: list[dict] = []
        self.socket.readyRead.connect(self._on_ready_read)

    def _on_ready_read(self) -> None:
        self._buffer += bytes(self.socket.readAll().data())
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                return
            line = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            response = json.loads(line.decode("utf-8"))
            self.responses.append(response)
            if self._on_response is not None:
                self._on_response(response)

    def send_raw(self, payload: bytes) -> None:
        self.socket.write(payload)
        self.socket.flush()

    def send(self, request: dict) -> None:
        self.send_raw((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))

    def wait_for(self, count: int, timeout_ms: int = 5000) -> list[dict]:
        assert _pump(self._app, lambda: len(self.responses) >= count, timeout_ms), (
            f"ждали {count} ответов, получили {self.responses}"
        )
        return self.responses

    def close(self) -> None:
        self.socket.abort()


@pytest.fixture
def running_server(qapp, tmp_path):
    """IpcServer на уникальном имени, с ipc-endpoint.json в tmp_path."""
    controller = FakeController()
    with patch("redmail.ipc_server.app_dir", return_value=tmp_path):
        server = IpcServer(controller, name=_unique_name())
        assert server.start(), "сервер не смог занять имя сокета"
        try:
            yield server, controller
        finally:
            server.stop()
            # Дать Qt доработать отложенные удаления, пока сервер ещё жив:
            # иначе они пришлись бы на цикл событий следующего теста, когда
            # сборщик мусора Python уже снёс и сам QLocalServer.
            qapp.processEvents()


def test_real_round_trip_ping(qapp, running_server) -> None:
    """Не мок: настоящий сокет, настоящая сериализация, настоящий ответ."""
    server, _ = running_server
    client = _Client(qapp, server.name)
    try:
        client.send({"action": "ping"})
        response = client.wait_for(1)[0]
    finally:
        client.close()
    assert response["ok"] is True
    assert response["pong"] is True
    assert response["protocol"] == ipc_server.PROTOCOL


def test_real_round_trip_reaches_controller(qapp, running_server) -> None:
    server, controller = running_server
    client = _Client(qapp, server.name)
    try:
        client.send({"action": "compose_email", "args": {"to": "ivan@example.com"}})
        response = client.wait_for(1)[0]
    finally:
        client.close()
    assert response == {"ok": True, "opened": "compose_email"}
    assert controller.calls[0][1]["to"] == "ivan@example.com"


def test_several_requests_in_one_write_get_several_responses(qapp, running_server) -> None:
    """Кадрирование по "\\n": склеенные в один write запросы должны
    разобраться по одному, а не как одна битая строка."""
    server, _ = running_server
    client = _Client(qapp, server.name)
    try:
        client.send_raw(b'{"action": "ping"}\n{"action": "focus"}\n{"action": "ping"}\n')
        responses = client.wait_for(3)
    finally:
        client.close()
    assert [r["ok"] for r in responses] == [True, True, True]
    assert responses[1] == {"ok": True, "focused": True}


def test_request_split_across_writes_is_reassembled(qapp, running_server) -> None:
    server, _ = running_server
    client = _Client(qapp, server.name)
    try:
        client.send_raw(b'{"action": "pi')
        qapp.processEvents()
        client.send_raw(b'ng"}\n')
        response = client.wait_for(1)[0]
    finally:
        client.close()
    assert response["pong"] is True


def test_broken_json_gets_error_and_server_survives(qapp, running_server) -> None:
    server, _ = running_server
    client = _Client(qapp, server.name)
    try:
        client.send_raw("не json вовсе\n".encode("utf-8"))
        first = client.wait_for(1)[0]
        client.send({"action": "ping"})
        second = client.wait_for(2)[1]
    finally:
        client.close()
    assert first["ok"] is False
    assert "JSON" in first["error"]
    assert second["ok"] is True  # канал жив после кривого запроса


def test_blank_lines_are_ignored(qapp, running_server) -> None:
    server, _ = running_server
    client = _Client(qapp, server.name)
    try:
        client.send_raw(b'\n\n{"action": "ping"}\n')
        responses = client.wait_for(1)
        qapp.processEvents()
    finally:
        client.close()
    assert len(responses) == 1


def test_oversized_line_is_rejected(qapp, running_server) -> None:
    server, _ = running_server
    client = _Client(qapp, server.name)
    try:
        client.send_raw(b"x" * (ipc_server.MAX_LINE_BYTES + 1024))
        response = client.wait_for(1)[0]
    finally:
        client.close()
    assert response["ok"] is False
    assert "длинный" in response["error"]


def test_second_server_does_not_steal_a_live_name(qapp, running_server) -> None:
    """Второй запущенный экземпляр не должен отбирать канал у первого —
    иначе команды уходили бы не в то окно. removeServer() поэтому зовётся
    только когда по имени НИКТО не отвечает (осталось от упавшего запуска)."""
    server, _ = running_server
    intruder = IpcServer(FakeController(), name=server.name)
    try:
        assert intruder.start() is False
    finally:
        intruder.stop()
    # первый сервер по-прежнему работает
    client = _Client(qapp, server.name)
    try:
        client.send({"action": "ping"})
        assert client.wait_for(1)[0]["ok"] is True
    finally:
        client.close()


def test_stale_socket_is_reclaimed(qapp, tmp_path) -> None:
    """Упавший прошлый запуск оставил запись имени, которую никто не
    слушает: сервер обязан её подобрать (removeServer + listen), а не
    отказаться стартовать навсегда."""
    name = _unique_name()
    removed: list[str] = []
    real_remove = QLocalServer.removeServer

    def spy_remove(value: str) -> bool:
        removed.append(value)
        return real_remove(value)

    with patch("redmail.ipc_server.app_dir", return_value=tmp_path), patch.object(
        QLocalServer, "removeServer", staticmethod(spy_remove)
    ), patch("redmail.ipc_server._is_alive", return_value=False):
        server = IpcServer(FakeController(), name=name)
        assert server.start() is True
        try:
            assert server.is_listening()
            assert removed[0] == name  # «мёртвая» запись снята перед listen()
        finally:
            server.stop()


def test_endpoint_file_written_and_removed(qapp, tmp_path) -> None:
    name = _unique_name()
    with patch("redmail.ipc_server.app_dir", return_value=tmp_path):
        server = IpcServer(FakeController(), name=name)
        assert server.start()
        endpoint = tmp_path / "ipc-endpoint.json"
        data = json.loads(endpoint.read_text(encoding="utf-8"))
        assert data["name"] == name
        assert data["protocol"] == ipc_server.PROTOCOL
        assert data["full_server_name"] == server.full_server_name()
        assert data["pid"] == os.getpid()
        server.stop()
        assert not endpoint.exists()


def test_modal_loop_does_not_block_the_response(qapp, tmp_path) -> None:
    """Ключевая проверка требования «диалог не должен ломать цикл событий».

    Контроллер делает ровно то же, что MainWindow.ipc_compose_email:
    откладывает через QTimer.singleShot(0) показ модального окна. Модальность
    здесь воспроизведена вложенным QEventLoop — это буквально то, чем и
    является QDialog.exec() внутри, но без настоящего окна: живой QDialog в
    offscreen-процессе, где уже загружен QtWebEngine, роняет интерпретатор
    (access violation), и падал бы сам тест, а не проверяемое свойство.

    Проверяем два факта: ответ клиенту ушёл, ПОКА вложенный цикл ещё крутится
    (то есть модальное окно ничего не задерживает), и канал остался рабочим
    после того, как вложенный цикл завершился."""
    state = {"loop_started": False, "loop_done": False}
    when_answered: list[bool] = []

    class NestedLoopController(FakeController):
        def ipc_compose_email(self, **kwargs):
            super().ipc_compose_email(**kwargs)

            def open_modal() -> None:
                state["loop_started"] = True
                loop = QEventLoop()
                QTimer.singleShot(300, loop.quit)  # «пользователь закрыл окно»
                loop.exec()
                state["loop_done"] = True

            QTimer.singleShot(0, open_modal)

    controller = NestedLoopController()
    with patch("redmail.ipc_server.app_dir", return_value=tmp_path):
        server = IpcServer(controller, name=_unique_name())
        assert server.start()
        client = _Client(
            qapp, server.name, on_response=lambda _r: when_answered.append(state["loop_done"])
        )
        try:
            client.send({"action": "compose_email", "args": {"to": "a@example.com"}})
            response = client.wait_for(1)[0]
            assert response == {"ok": True, "opened": "compose_email"}
            # ответ доставлен до того, как вложенный цикл успел завершиться
            assert when_answered == [False]
            assert _pump(qapp, lambda: state["loop_done"], 5000)
            # канал жив после того, как вложенный цикл событий отработал
            client.send({"action": "ping"})
            assert client.wait_for(2)[1]["ok"] is True
        finally:
            client.close()
            server.stop()


# ---------------------------------------------------------------------------
# Подбор писем по правилам сортировки
# ---------------------------------------------------------------------------


def _summary(uid: int, sender_email: str = "", subject: str = "") -> MessageSummary:
    return MessageSummary(
        uid=uid,
        subject=subject,
        sender="",
        sender_email=sender_email,
        date="2026-09-06",
        message_id=f"<{uid}@example.com>",
    )


def test_mail_rule_moves_matches_by_sender_and_subject() -> None:
    rules = [
        MailRule(field="from", contains="bank", target_folder="Счета"),
        MailRule(field="subject", contains="отчёт", target_folder="Отчёты"),
    ]
    summaries = [
        _summary(1, sender_email="noreply@bank.example"),
        _summary(2, subject="Квартальный ОТЧЁТ"),
        _summary(3, sender_email="ivan@example.com", subject="Привет"),
    ]
    assert _mail_rule_moves(summaries, rules) == {"Счета": [1], "Отчёты": [2]}


def test_mail_rule_moves_uses_first_matching_rule_only() -> None:
    rules = [
        MailRule(field="from", contains="bank", target_folder="Счета"),
        MailRule(field="from", contains="bank", target_folder="Другое"),
    ]
    assert _mail_rule_moves([_summary(1, sender_email="a@bank.ru")], rules) == {"Счета": [1]}


def test_mail_rule_moves_without_rules_is_empty() -> None:
    assert _mail_rule_moves([_summary(1, sender_email="a@bank.ru")], []) == {}


# ---------------------------------------------------------------------------
# Методы MainWindow: что именно попадёт в диалог
#
# Диалоги здесь НЕ показываются: exec()/show() не вызывается вовсе, класс
# диалога подменён записывающей заглушкой, а откладывание через
# QTimer.singleShot заменено на список отложенных вызовов. Это вариант (b)
# из требований — проверяем, ЧТО было бы открыто и чем заполнено.
# ---------------------------------------------------------------------------


class _RecordingDialog:
    """Заглушка вместо ComposeDialog/EventDialog: только запоминает
    аргументы конструктора, ничего не рисует и не крутит цикл событий."""

    instances: list["_RecordingDialog"] = []

    def __init__(self, parent=None, **kwargs):
        self.parent = parent
        self.kwargs = kwargs
        self.window_title = kwargs.get("title", "")
        _RecordingDialog.instances.append(self)

    def setWindowTitle(self, title):  # noqa: N802 — как у QWidget
        self.window_title = title


@pytest.fixture
def recording_dialogs():
    _RecordingDialog.instances = []
    with patch("redmail.ui.main_window.ComposeDialog", _RecordingDialog), patch(
        "redmail.ui.main_window.EventDialog", _RecordingDialog
    ):
        yield _RecordingDialog.instances


def _window_stub(**overrides):
    """Минимальный «MainWindow» для вызова несвязанных ipc_* методов.

    Настоящее окно тут не нужно: методы читают ровно несколько атрибутов и
    вызывают несколько своих же методов — их и подставляем. Так тест не
    зависит ни от QtWebEngine, ни от реального каталога настроек.
    """
    deferred: list = []
    stub = SimpleNamespace(
        account=SimpleNamespace(username="me@example.com"),
        smtp_account=object(),
        signatures=[],
        default_signature_id=None,
        mail_rules=[],
        mailbox=None,
        active_source=None,
        current_folder=None,
        current_summaries=[],
        selected_calendar_event=None,
        calendar_path="calendar.rmcal",
        deferred=deferred,
        _load_contacts=lambda: [],
        _load_calendars=lambda: [],
        _ipc_default_calendar_id=lambda: calendar_store.DEFAULT_CALENDAR_ID,
        _apply_calendar_selection_highlight=lambda: None,
        ipc_focus=lambda: None,
        _ipc_later=deferred.append,
        _exec_compose=lambda dialog: None,
        _save_event_from_dialog=lambda dialog, existing=None: None,
        on_cancel_event=lambda: None,
    )
    stub._ipc_event_dialog = lambda event, *, title: MainWindow._ipc_event_dialog(
        stub, event, title=title
    )
    for key, value in overrides.items():
        setattr(stub, key, value)
    return stub


def test_ipc_compose_email_prefills_the_real_dialog(recording_dialogs) -> None:
    stub = _window_stub()
    MainWindow.ipc_compose_email(
        stub, to="ivan@example.com", subject="Тема", body="Текст", cc="c@e.com", bcc="b@e.com"
    )
    assert len(recording_dialogs) == 1
    kwargs = recording_dialogs[0].kwargs
    assert kwargs["to"] == "ivan@example.com"
    assert kwargs["subject"] == "Тема"
    assert kwargs["body"] == "Текст"
    assert kwargs["cc"] == "c@e.com"
    assert kwargs["bcc"] == "b@e.com"
    assert kwargs["title"] == "Новое письмо"
    # exec() только ОТЛОЖЕН — из обработчика сокета его звать нельзя
    assert len(stub.deferred) == 1


def test_ipc_compose_email_without_smtp_raises_before_opening(recording_dialogs) -> None:
    stub = _window_stub(smtp_account=None)
    with pytest.raises(RuntimeError):
        MainWindow.ipc_compose_email(stub, to="ivan@example.com")
    assert recording_dialogs == []


def test_ipc_create_event_builds_draft_event(recording_dialogs) -> None:
    stub = _window_stub()
    start = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    MainWindow.ipc_create_event(
        stub,
        summary="Планёрка",
        start=start,
        duration_minutes=45,
        participants=["a@example.com", "b@example.com"],
        description="повестка",
        location="переговорная",
    )
    dialog = recording_dialogs[0]
    event = dialog.kwargs["event"]
    assert dialog.window_title == "Новая встреча"
    assert event.summary == "Планёрка"
    assert event.dtstart == start
    assert event.dtend == start + timedelta(minutes=45)
    assert [a.email for a in event.attendees] == ["a@example.com", "b@example.com"]
    assert event.description == "повестка"
    assert event.location == "переговорная"
    assert event.is_organizer is True
    assert event.organizer_email == "me@example.com"
    assert len(stub.deferred) == 1  # ничего не сохранено — только отложен показ


def test_ipc_create_event_without_account_raises(recording_dialogs) -> None:
    stub = _window_stub(account=None)
    with pytest.raises(RuntimeError):
        MainWindow.ipc_create_event(
            stub, summary="X", start=datetime.now(timezone.utc), duration_minutes=30
        )
    assert recording_dialogs == []


def _stored_event(tmp_path, **overrides) -> tuple:
    path = tmp_path / "calendar.rmcal"
    calendar_store.create_calendar(path)
    start = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    event = calendar_store.Event(
        uid="uid-1",
        summary="Старое название",
        dtstart=start,
        dtend=start + timedelta(minutes=30),
        description="старое описание",
        location="старое место",
        organizer_email="me@example.com",
        is_organizer=True,
        attendees=[calendar_store.Attendee(email="old@example.com")],
        **overrides,
    )
    calendar_store.save_event(path, event)
    return path, event


def test_ipc_update_event_merges_overrides(tmp_path, recording_dialogs) -> None:
    path, _ = _stored_event(tmp_path)
    stub = _window_stub(calendar_path=path)
    MainWindow.ipc_update_event(stub, "uid-1", summary="Новое название", location="кабинет 5")
    merged = recording_dialogs[0].kwargs["event"]
    assert merged.uid == "uid-1"
    assert merged.summary == "Новое название"
    assert merged.location == "кабинет 5"
    # не переданные поля берутся из уже сохранённой встречи
    assert merged.description == "старое описание"
    assert [a.email for a in merged.attendees] == ["old@example.com"]
    assert merged.dtstart == datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    assert merged.dtend - merged.dtstart == timedelta(minutes=30)


def test_ipc_update_event_keeps_duration_when_only_start_moves(tmp_path, recording_dialogs) -> None:
    path, _ = _stored_event(tmp_path)
    stub = _window_stub(calendar_path=path)
    new_start = datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc)
    MainWindow.ipc_update_event(stub, "uid-1", start=new_start)
    merged = recording_dialogs[0].kwargs["event"]
    assert merged.dtstart == new_start
    assert merged.dtend == new_start + timedelta(minutes=30)


def test_ipc_update_event_unknown_uid(tmp_path, recording_dialogs) -> None:
    path, _ = _stored_event(tmp_path)
    stub = _window_stub(calendar_path=path)
    with pytest.raises(LookupError):
        MainWindow.ipc_update_event(stub, "нет-такого", summary="X")
    assert recording_dialogs == []


def test_ipc_update_event_refuses_foreign_meeting(tmp_path, recording_dialogs) -> None:
    path = tmp_path / "calendar.rmcal"
    calendar_store.create_calendar(path)
    start = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    calendar_store.save_event(
        path,
        calendar_store.Event(
            uid="uid-foreign",
            summary="Чужая встреча",
            dtstart=start,
            dtend=start + timedelta(minutes=30),
            organizer_email="boss@example.com",
            is_organizer=False,
        ),
    )
    stub = _window_stub(calendar_path=path)
    with pytest.raises(PermissionError):
        MainWindow.ipc_update_event(stub, "uid-foreign", summary="X")
    assert recording_dialogs == []


def test_ipc_cancel_event_selects_and_defers_existing_flow(tmp_path) -> None:
    """Отмена не шлёт CANCEL сама: она лишь выбирает встречу и откладывает
    штатный on_cancel_event, который спросит подтверждение в окне."""
    path, event = _stored_event(tmp_path)
    stub = _window_stub(calendar_path=path)
    MainWindow.ipc_cancel_event(stub, "uid-1")
    assert stub.selected_calendar_event.uid == "uid-1"
    assert stub.deferred == [stub.on_cancel_event]


def test_ipc_cancel_event_unknown_uid(tmp_path) -> None:
    path, _ = _stored_event(tmp_path)
    stub = _window_stub(calendar_path=path)
    with pytest.raises(LookupError):
        MainWindow.ipc_cancel_event(stub, "нет-такого")
    assert stub.deferred == []


class _FakeMailbox:
    def __init__(self, summaries_by_folder=None, fail=False):
        self._summaries = summaries_by_folder or {}
        self.moves: list[tuple[str, list[int], str]] = []
        self._fail = fail

    def folder_summaries(self, folder, limit=50):
        return self._summaries.get(folder, [])

    def refresh_folder(self, folder, limit=50):
        return self._summaries.get(folder, [])

    def move_to_folder(self, folder, uids, target_folder):
        if self._fail:
            raise OSError("сервер отвалился")
        self.moves.append((folder, list(uids), target_folder))


def _rules_window_stub(mailbox, **overrides):
    defaults = {
        "mailbox": mailbox,
        "active_source": mailbox,
        "current_folder": "INBOX",
        "mail_rules": [MailRule(field="from", contains="bank", target_folder="Счета")],
        "current_summaries": [
            _summary(1, sender_email="a@bank.ru"),
            _summary(2, sender_email="x@y.z"),
        ],
        "_render_folder": lambda summaries: None,
        "statusBar": lambda: SimpleNamespace(showMessage=lambda *a, **k: None),
    }
    defaults.update(overrides)
    return _window_stub(**defaults)


def test_ipc_apply_mail_rules_moves_in_current_folder() -> None:
    mailbox = _FakeMailbox()
    stub = _rules_window_stub(mailbox)
    result = MainWindow.ipc_apply_mail_rules(stub)
    assert result == {"folder": "INBOX", "moved": 1, "moves": {"Счета": 1}}
    assert mailbox.moves == [("INBOX", [1], "Счета")]


def test_ipc_apply_mail_rules_explicit_other_folder() -> None:
    mailbox = _FakeMailbox({"Архив": [_summary(7, sender_email="q@bank.ru")]})
    stub = _rules_window_stub(mailbox)
    result = MainWindow.ipc_apply_mail_rules(stub, "Архив")
    assert result == {"folder": "Архив", "moved": 1, "moves": {"Счета": 1}}
    assert mailbox.moves == [("Архив", [7], "Счета")]


def test_ipc_apply_mail_rules_requires_live_mailbox() -> None:
    stub = _rules_window_stub(_FakeMailbox(), active_source=object())
    with pytest.raises(RuntimeError):
        MainWindow.ipc_apply_mail_rules(stub)


def test_ipc_apply_mail_rules_requires_configured_rules() -> None:
    stub = _rules_window_stub(_FakeMailbox(), mail_rules=[])
    with pytest.raises(RuntimeError):
        MainWindow.ipc_apply_mail_rules(stub)


def test_ipc_apply_mail_rules_reports_partial_failure() -> None:
    stub = _rules_window_stub(_FakeMailbox(fail=True))
    with pytest.raises(RuntimeError) as excinfo:
        MainWindow.ipc_apply_mail_rules(stub)
    assert "перемещено до сбоя: 0" in str(excinfo.value)


def test_ipc_list_mail_rules_returns_plain_dicts() -> None:
    stub = _window_stub(
        mail_rules=[MailRule(field="subject", contains="счёт", target_folder="Счета")]
    )
    assert MainWindow.ipc_list_mail_rules(stub) == [
        {"field": "subject", "contains": "счёт", "target_folder": "Счета"}
    ]
    # результат обязан быть сериализуем в JSON — он уходит в ответ клиенту
    json.dumps(MainWindow.ipc_list_mail_rules(stub))


def test_focus_running_instance_talks_to_live_server_and_is_false_without_one(qapp, monkeypatch):
    import threading
    import time
    from unittest.mock import MagicMock

    from redmail import ipc_server

    name = f"redmail-ipc-test-single-{os.getpid()}"
    monkeypatch.setenv("REDMAIL_IPC_NAME", name)
    assert ipc_server.focus_running_instance() is False  # никто не слушает

    controller = MagicMock()
    server = ipc_server.IpcServer(controller)
    assert server.start()
    try:
        # Второй экземпляр — отдельный процесс; здесь его роль играет поток,
        # а главный поток крутит цикл событий первого экземпляра.
        outcome: list[bool] = []
        client = threading.Thread(target=lambda: outcome.append(ipc_server.focus_running_instance()))
        client.start()
        deadline = time.time() + 5
        while (client.is_alive() or not controller.ipc_focus.called) and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        client.join(1)
        assert outcome == [True]
        assert controller.ipc_focus.called
    finally:
        server.stop()
