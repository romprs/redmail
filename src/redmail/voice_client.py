"""Связь резидента напоминаний с голосовым помощником и почтовым клиентом.

Оба соседа могут быть не запущены — это нормальное состояние, а не ошибка:
напоминание тогда показывается окном, а «открыть календарь» честно говорит,
что почта закрыта. Поэтому здесь нет исключений наружу, только True/False.
"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

from redmail.applog import get_logger

_log = get_logger("voice")

_TIMEOUT_SECONDS = 5.0

#: Сокет голосового помощника: он слушает просьбы произнести текст.
#: Переменная окружения — чтобы запустить помощника и резидент в другом
#: сеансе (например, в тестовом профиле), не трогая общий путь.
VOICE_SOCKET_ENV = "AUDIOREFERENT_SOCKET"
VOICE_SOCKET_NAME = "audioreferent.sock"


def _runtime_dir() -> Path:
    value = os.environ.get("XDG_RUNTIME_DIR")
    return Path(value) if value else Path("/tmp")


def voice_socket_path() -> Path:
    override = os.environ.get(VOICE_SOCKET_ENV)
    return Path(override) if override else _runtime_dir() / VOICE_SOCKET_NAME


#: Где почтовый клиент слушает по умолчанию (QLocalServer кладёт сокет во
#: временный каталог под этим именем) — если файла с адресом нет.
_DEFAULT_MAIL_SOCKETS = ("/tmp/redmail-ipc",)


def _mail_endpoints() -> list[str]:
    """Адреса канала почтового клиента: из файла, который он пишет при
    старте, и запасной по умолчанию. Запасной нужен на случай, когда файла
    нет — так было при перезапуске почты (старый экземпляр стирал адрес
    нового), и кнопка «Открыть календарь» считала почту закрытой."""
    endpoints: list[str] = []
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    try:
        data = json.loads((base / "redmail" / "ipc-endpoint.json").read_text(encoding="utf-8"))
        value = data.get("full_server_name")
        if isinstance(value, str) and value:
            endpoints.append(value)
    except (OSError, ValueError) as exc:
        _log.info("Адрес почты из файла не прочитан: %s", exc)
    for fallback in _DEFAULT_MAIL_SOCKETS:
        if fallback not in endpoints and Path(fallback).exists():
            endpoints.append(fallback)
    return endpoints


def _mail_endpoint() -> str | None:
    endpoints = _mail_endpoints()
    return endpoints[0] if endpoints else None


def _send_line(address: str, payload: dict, *, wait_reply: bool = False) -> bool:
    """Отправить строку JSON. wait_reply — дождаться ответа и вернуть его
    «ok»: почтовый клиент отвечает на каждую команду, и если закрыть
    соединение, не дождавшись ответа, он успевает увидеть обрыв раньше,
    чем прочтёт команду, — и отбрасывает её (так «Открыть календарь» из
    напоминалки молча ничего не делал)."""
    try:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(_TIMEOUT_SECONDS)
        conn.connect(address)
    except OSError as exc:
        _log.info("Канал %s недоступен: %s", address, exc)
        return False
    try:
        conn.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        if not wait_reply:
            return True
        reply = b""
        while not reply.endswith(b"\n"):
            chunk = conn.recv(4096)
            if not chunk:
                break
            reply += chunk
        try:
            answer = json.loads(reply.decode("utf-8") or "{}")
        except ValueError:
            _log.info("Канал %s: непонятный ответ: %r", address, reply[:200])
            return False
        if not answer.get("ok"):
            _log.info("Канал %s: команда не выполнена: %s", address, answer.get("error") or answer)
        return bool(answer.get("ok"))
    except OSError as exc:
        _log.info("Канал %s: запрос не выполнен: %s", address, exc)
        return False
    finally:
        conn.close()


def speak(text: str) -> bool:
    """Попросить голосового помощника произнести текст. False — помощник не
    запущен: напоминание останется только на экране."""
    return _send_line(str(voice_socket_path()), {"action": "speak", "args": {"text": text}})


def focus_mail_client(*, section: str = "calendar") -> bool:
    """Поднять окно почтового клиента на нужном разделе."""
    endpoints = _mail_endpoints()
    if not endpoints:
        _log.info("Почтовый клиент не найден: нет ни файла адреса, ни сокета по умолчанию")
        return False
    request = {"action": "focus", "args": {"section": section}}
    return any(_send_line(endpoint, request, wait_reply=True) for endpoint in endpoints)
