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


def _mail_endpoint() -> str | None:
    """Адрес канала почтового клиента — он пишет его при старте."""
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    try:
        data = json.loads((base / "redmail" / "ipc-endpoint.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("full_server_name")
    return value if isinstance(value, str) and value else None


def _send_line(address: str, payload: dict) -> bool:
    try:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(_TIMEOUT_SECONDS)
        conn.connect(address)
    except OSError as exc:
        _log.info("Канал %s недоступен: %s", address, exc)
        return False
    try:
        conn.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        return True
    except OSError as exc:
        _log.info("Канал %s: запрос не отправлен: %s", address, exc)
        return False
    finally:
        conn.close()


def speak(text: str) -> bool:
    """Попросить голосового помощника произнести текст. False — помощник не
    запущен: напоминание останется только на экране."""
    return _send_line(str(voice_socket_path()), {"action": "speak", "args": {"text": text}})


def focus_mail_client(*, section: str = "calendar") -> bool:
    """Поднять окно почтового клиента на нужном разделе."""
    endpoint = _mail_endpoint()
    if not endpoint:
        return False
    return _send_line(endpoint, {"action": "focus", "args": {"section": section}})
