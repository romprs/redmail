"""Связь напоминалки с почтой: жалоба «кнопка открыть календарь пишет —
откройте почту», хотя почта была открыта."""
from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path

import pytest

from redmail import voice_client

pytestmark = pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or os.name == "nt", reason="unix-сокеты")


def test_focus_falls_back_to_default_socket_when_address_file_is_missing(tmp_path, monkeypatch) -> None:
    """При перезапуске почты старый экземпляр стирал файл адреса нового —
    напоминалка должна найти почту и без него."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    sock_path = tmp_path / "redmail-ipc"
    monkeypatch.setattr(voice_client, "_DEFAULT_MAIL_SOCKETS", (str(sock_path),))
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)
    got: list[bytes] = []

    def accept() -> None:
        conn, _ = server.accept()
        got.append(conn.recv(1000))
        conn.close()

    thread = threading.Thread(target=accept)
    thread.start()
    try:
        assert voice_client.focus_mail_client(section="calendar") is True
        thread.join(timeout=3)
        assert json.loads(got[0])["args"]["section"] == "calendar"
    finally:
        server.close()


def test_focus_reports_closed_mail_when_nothing_listens(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(voice_client, "_DEFAULT_MAIL_SOCKETS", (str(tmp_path / "нет-такого"),))
    assert voice_client.focus_mail_client() is False
