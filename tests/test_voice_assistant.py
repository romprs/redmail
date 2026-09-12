from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from redmail import voice_assistant


def _cp(stdout: str = "", rc: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


def test_state_when_assistant_not_installed() -> None:
    with patch("redmail.voice_assistant.shutil.which", return_value=None):
        st = voice_assistant.state()
    assert st.installed is False and st.status_text == "не установлен"


def test_state_reads_service_and_wake_word(tmp_path: Path, monkeypatch) -> None:
    # Состояние из systemctl --user, ключевое слово из конфига помощника,
    # версия из rpm — всё для раздела «Голосовой помощник» в настройках.
    (tmp_path / "config.yaml").write_text("wake_word: 'Вика'\nfoo: 1\n", encoding="utf-8")
    monkeypatch.setattr(voice_assistant, "config_path", lambda: tmp_path / "config.yaml")

    def fake_run(args, **kwargs):
        if args[:2] == ["systemctl", "--user"] and args[2] == "is-active":
            return _cp("active\n")
        if args[:2] == ["systemctl", "--user"] and args[2] == "is-enabled":
            return _cp("enabled\n")
        if args[0] == "rpm":
            return _cp("0.1.0-3.red80")
        return _cp("", 1)

    with patch("redmail.voice_assistant.shutil.which", return_value="/usr/bin/audioreferent"), \
         patch("redmail.voice_assistant.subprocess.run", side_effect=fake_run):
        st = voice_assistant.state()
    assert st.installed and st.active and st.enabled
    assert st.wake_word == "Вика" and st.version == "0.1.0-3.red80"
    assert st.status_text == "работает, автозапуск включён"


def test_set_enabled_uses_enable_now_and_disable_now() -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return _cp()

    with patch("redmail.voice_assistant.shutil.which", return_value="/usr/bin/audioreferent"), \
         patch("redmail.voice_assistant.subprocess.run", side_effect=fake_run):
        assert voice_assistant.set_enabled(True)[0] is True
        assert voice_assistant.set_enabled(False)[0] is True
    assert calls == [
        ["systemctl", "--user", "enable", "--now", "audioreferent.service"],
        ["systemctl", "--user", "disable", "--now", "audioreferent.service"],
    ]


def test_set_enabled_reports_systemctl_failure_without_raising() -> None:
    with patch("redmail.voice_assistant.shutil.which", return_value="/usr/bin/audioreferent"), \
         patch("redmail.voice_assistant.subprocess.run", return_value=_cp("", 1, "Failed to connect to bus")):
        ok, message = voice_assistant.set_enabled(True)
    assert ok is False and "bus" in message
