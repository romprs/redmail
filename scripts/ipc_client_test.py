#!/usr/bin/env python3
"""Ручная проверка локального канала управления redmail (см. src/redmail/ipc_server.py).

Нужен, чтобы канал можно было потрогать руками ДО того, как появится
голосовая часть: запускаем обычный redmail, из соседнего терминала шлём
команду и смотрим ответ/реакцию окна.

Qt здесь намеренно НЕ используется — сокет QLocalServer это обычный
примитив ОС: на Linux/macOS unix-сокет (файл), на Windows именованный
канал \\\\.\\pipe\\<имя>. Обходимся socket/open из стандартной библиотеки,
поэтому скрипт запускается любым системным python3 без зависимостей.

Адрес не угадывается: работающий redmail записывает фактический
QLocalServer.fullServerName() в <каталог настроек>/ipc-endpoint.json, его и
читаем. Если файла нет (старая сборка, каталог настроек переопределён) —
пробуем обычные для платформы места, см. _candidate_endpoints().

Примеры:
    python3 scripts/ipc_client_test.py ping
    python3 scripts/ipc_client_test.py focus
    python3 scripts/ipc_client_test.py compose_email --to ivan@example.com \\
        --subject "Отчёт" --body "Привет!" --cc boss@example.com
    python3 scripts/ipc_client_test.py create_event --subject "Планёрка" \\
        --start 2026-09-10T15:00 --duration 30 --participants a@e.com,b@e.com
    python3 scripts/ipc_client_test.py update_event --uid <UID> --start 2026-09-10T16:00
    python3 scripts/ipc_client_test.py find_events --subject Планёрка --date 2026-09-10
    python3 scripts/ipc_client_test.py cancel_event --uid <UID>
    python3 scripts/ipc_client_test.py event_form_open --subject Планёрка --date 2026-09-15 --time 08:30
    python3 scripts/ipc_client_test.py find_contacts Шилкина
    python3 scripts/ipc_client_test.py event_form_set --add-participants shilkin@example.com --recurrence weekly
    python3 scripts/ipc_client_test.py event_form_save        # или event_form_cancel
    python3 scripts/ipc_client_test.py apply_mail_rules --folder INBOX
    python3 scripts/ipc_client_test.py list_mail_rules
    python3 scripts/ipc_client_test.py raw '{"action": "ping"}'
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from redmail.paths import app_dir  # noqa: E402 — после правки sys.path

DEFAULT_SERVER_NAME = "redmail-ipc"
TIMEOUT_SECONDS = 10.0


# ---------------------------------------------------------------------------
# Поиск адреса сокета
# ---------------------------------------------------------------------------


def _server_name() -> str:
    return os.environ.get("REDMAIL_IPC_NAME") or DEFAULT_SERVER_NAME


def _endpoint_from_file() -> str | None:
    path = app_dir() / "ipc-endpoint.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get("full_server_name")
    return value if isinstance(value, str) and value else None


def _candidate_endpoints(name: str) -> list[str]:
    """Запасные варианты, если ipc-endpoint.json недоступен.

    Windows: QLocalServer делает именованный канал \\\\.\\pipe\\<имя>.
    Linux/macOS: unix-сокет с именем <имя> в $XDG_RUNTIME_DIR, иначе в /tmp
    (порядок — как у самой Qt).
    """
    if sys.platform == "win32":
        return [rf"\\.\pipe\{name}"]
    candidates = []
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        candidates.append(str(Path(runtime_dir) / name))
    candidates.append(str(Path("/tmp") / name))
    return candidates


def endpoints() -> list[str]:
    name = _server_name()
    found = _endpoint_from_file()
    result = [found] if found else []
    for candidate in _candidate_endpoints(name):
        if candidate not in result:
            result.append(candidate)
    return result


# ---------------------------------------------------------------------------
# Транспорт: одна строка запроса → одна строка ответа
# ---------------------------------------------------------------------------


class _WindowsPipe:
    """Минимальная обёртка над именованным каналом, с тем же интерфейсом
    (sendall/recv/close), что и socket, — чтобы код обмена был один."""

    def __init__(self, path: str):
        self._file = open(path, "r+b", buffering=0)

    def sendall(self, data: bytes) -> None:
        self._file.write(data)
        self._file.flush()

    def recv(self, size: int) -> bytes:
        return self._file.read1(size) if hasattr(self._file, "read1") else self._file.read(size)

    def close(self) -> None:
        self._file.close()


def _connect(endpoint: str):
    if sys.platform == "win32":
        return _WindowsPipe(endpoint)
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(TIMEOUT_SECONDS)
    conn.connect(endpoint)
    return conn


def send_request(request: dict) -> dict:
    errors = []
    for endpoint in endpoints():
        try:
            conn = _connect(endpoint)
        except OSError as exc:
            errors.append(f"{endpoint}: {exc}")
            continue
        try:
            conn.sendall((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
            buffer = b""
            while b"\n" not in buffer:
                chunk = conn.recv(65536)
                if not chunk:
                    raise ConnectionError("соединение закрыто без ответа")
                buffer += chunk
        finally:
            conn.close()
        return json.loads(buffer.split(b"\n", 1)[0].decode("utf-8"))
    joined = "\n  ".join(errors) or "адрес сокета не найден"
    raise SystemExit(
        "Не удалось подключиться к redmail. Запущено ли приложение?\n  " + joined
    )


# ---------------------------------------------------------------------------
# Командная строка
# ---------------------------------------------------------------------------


def _split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def build_request(ns: argparse.Namespace) -> dict:
    action = ns.action
    if action == "raw":
        request = json.loads(ns.payload)
        if not isinstance(request, dict):
            raise SystemExit("raw ожидает JSON-объект")
        return request
    args: dict = {}
    if action == "compose_email":
        args["to"] = ns.to
        if ns.subject:
            args["subject"] = ns.subject
        if ns.body:
            args["body"] = ns.body
        if ns.cc:
            args["cc"] = ns.cc
        if ns.bcc:
            args["bcc"] = ns.bcc
    elif action in ("create_event", "update_event"):
        if action == "update_event":
            args["uid"] = ns.uid
        if ns.subject:
            args["subject"] = ns.subject
        if ns.start:
            args["start"] = ns.start
        if ns.duration:
            args["duration_minutes"] = ns.duration
        if ns.participants is not None:
            args["participants"] = _split_list(ns.participants)
        if ns.description:
            args["description"] = ns.description
        if ns.location:
            args["location"] = ns.location
    elif action == "find_events":
        if ns.subject:
            args["subject"] = ns.subject
        if ns.date:
            args["date"] = ns.date
    elif action in ("event_form_open", "event_form_set"):
        if action == "event_form_open" and ns.uid:
            args["uid"] = ns.uid
        for key in ("subject", "date", "time", "start", "recurrence", "location", "description"):
            value = getattr(ns, key)
            if value:
                args[key] = value
        if ns.duration:
            args["duration_minutes"] = ns.duration
        if ns.participants is not None:
            args["participants"] = _split_list(ns.participants)
        if ns.add_participants is not None:
            args["add_participants"] = _split_list(ns.add_participants)
    elif action == "find_contacts":
        args["query"] = ns.query
    elif action == "cancel_event":
        args["uid"] = ns.uid
    elif action == "apply_mail_rules":
        if ns.folder:
            args["folder"] = ns.folder
    return {"action": action, "args": args}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("ping", help="проверка связи и версии приложения")
    sub.add_parser("focus", help="вывести окно redmail на передний план")
    sub.add_parser("list_mail_rules", help="показать настроенные правила сортировки")

    compose = sub.add_parser("compose_email", help="открыть окно нового письма с полями")
    compose.add_argument("--to", required=True, help="получатели через запятую")
    compose.add_argument("--subject", default="")
    compose.add_argument("--body", default="")
    compose.add_argument("--cc", default="")
    compose.add_argument("--bcc", default="")

    for name, help_text in (
        ("create_event", "открыть окно новой встречи с полями"),
        ("update_event", "открыть окно правки встречи по UID"),
    ):
        event = sub.add_parser(name, help=help_text)
        if name == "update_event":
            event.add_argument("--uid", required=True)
            event.add_argument("--subject", default="")
        else:
            event.add_argument("--subject", required=True)
        event.add_argument("--start", default="", help="ISO 8601, например 2026-09-10T15:00")
        event.add_argument("--duration", type=int, default=0, help="длительность в минутах")
        event.add_argument("--participants", default=None, help="адреса через запятую")
        event.add_argument("--description", default="")
        event.add_argument("--location", default="")

    find = sub.add_parser("find_events", help="найти встречи по теме и/или дню (без подтверждения)")
    find.add_argument("--subject", default="", help="подстрока темы")
    find.add_argument("--date", default="", help="YYYY-MM-DD; по умолчанию — сегодня")

    for name, help_text in (
        ("event_form_open", "открыть пошаговую форму встречи (новую или свою по --uid)"),
        ("event_form_set", "изменить поля уже открытой формы"),
    ):
        form = sub.add_parser(name, help=help_text)
        if name == "event_form_open":
            form.add_argument("--uid", default="")
        form.add_argument("--subject", default="")
        form.add_argument("--date", default="", help="YYYY-MM-DD (время остаётся)")
        form.add_argument("--time", default="", help="HH:MM (дата остаётся)")
        form.add_argument("--start", default="", help="ISO 8601 — и дата, и время")
        form.add_argument("--duration", type=int, default=0, help="длительность в минутах")
        form.add_argument("--recurrence", default="", help="none/daily/weekly/monthly/yearly")
        form.add_argument("--participants", default=None, help="адреса через запятую (заменить)")
        form.add_argument("--add-participants", dest="add_participants", default=None, help="добавить адреса")
        form.add_argument("--location", default="")
        form.add_argument("--description", default="")
    sub.add_parser("event_form_state", help="показать поля открытой формы")
    sub.add_parser("event_form_save", help="нажать «Сохранить» в открытой форме")
    sub.add_parser("event_form_cancel", help="нажать «Отмена» в открытой форме")

    contacts = sub.add_parser("find_contacts", help="контакты по фамилии/имени, как на слух (с падежом)")
    contacts.add_argument("query")

    cancel = sub.add_parser("cancel_event", help="запустить отмену встречи по UID (с подтверждением в окне)")
    cancel.add_argument("--uid", required=True)

    apply_rules = sub.add_parser("apply_mail_rules", help="применить правила сортировки почты")
    apply_rules.add_argument("--folder", default="", help="папка; по умолчанию — открытая сейчас")

    raw = sub.add_parser("raw", help="отправить произвольный JSON-запрос как есть")
    raw.add_argument("payload")

    ns = parser.parse_args(argv)
    response = send_request(build_request(ns))
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
