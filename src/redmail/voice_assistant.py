"""Управление голосовым помощником audioreferent из настроек почты.

Договорённость: два самостоятельных продукта (почта и помощник), помощник —
рекомендуемая зависимость пакета почты; из настроек почты его можно
включить/выключить, посмотреть состояние, открыть его настройки и журнал.
Общение между ними — только через локальный канал управления
(ipc_server.py), сюда это не относится.

Помощник живёт как пользовательский сервис systemd (audioreferent.service,
`systemctl --user`), поэтому «включить» = enable --now, «выключить» =
disable --now. Всё через subprocess с таймаутами: настройки не должны
зависать, если systemd пользователя недоступен (запуск не из сессии).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from redmail.applog import get_logger

_log = get_logger("voice")

SERVICE = "audioreferent.service"
BINARY = "audioreferent"
_TIMEOUT = 15


@dataclass
class AssistantState:
    installed: bool
    active: bool = False
    enabled: bool = False
    wake_word: str = ""
    version: str = ""
    detail: str = ""

    @property
    def status_text(self) -> str:
        if not self.installed:
            return "не установлен"
        if self.active:
            return "работает" + (", автозапуск включён" if self.enabled else ", автозапуск выключен")
        if self.enabled:
            return "включён, но сейчас не работает"
        return "выключен"


def _run(args: list[str], *, timeout: int = _TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def is_installed() -> bool:
    return shutil.which(BINARY) is not None


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return _run(["systemctl", "--user", *args])


def config_path() -> Path:
    return Path.home() / ".config" / "audioreferent" / "config.yaml"


def read_wake_word() -> str:
    """Ключевое слово из конфига помощника; без разбора YAML целиком —
    только строка wake_word (у помощника свой формат и своё окно настроек)."""
    try:
        for line in config_path().read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("wake_word:"):
                value = stripped.split(":", 1)[1].strip().strip("'\"")
                return value
    except OSError:
        pass
    return ""


def state() -> AssistantState:
    if not is_installed():
        return AssistantState(installed=False, detail="Пакет audioreferent не установлен")
    result = AssistantState(installed=True, wake_word=read_wake_word())
    try:
        active = _systemctl("is-active", SERVICE)
        result.active = active.stdout.strip() == "active"
        enabled = _systemctl("is-enabled", SERVICE)
        result.enabled = enabled.stdout.strip() == "enabled"
    except (OSError, subprocess.TimeoutExpired) as exc:
        result.detail = f"systemd пользователя недоступен: {exc}"
    try:
        version = _run(["rpm", "-q", "--qf", "%{VERSION}-%{RELEASE}", "audioreferent"], timeout=10)
        if version.returncode == 0:
            result.version = version.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return result


def set_enabled(enabled: bool) -> tuple[bool, str]:
    """Включить (enable --now) или выключить (disable --now) сервис.
    Возвращает (успех, сообщение для пользователя)."""
    if not is_installed():
        return False, "Голосовой помощник не установлен"
    try:
        if enabled:
            result = _systemctl("enable", "--now", SERVICE)
        else:
            result = _systemctl("disable", "--now", SERVICE)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.error("Помощник: systemctl не выполнился: %s", exc)
        return False, f"Не удалось обратиться к systemd: {exc}"
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip() or f"код {result.returncode}"
        _log.error("Помощник: %s не удалось: %s", "включение" if enabled else "выключение", message)
        return False, message
    _log.info("Помощник %s", "включён" if enabled else "выключен")
    return True, "Помощник включён" if enabled else "Помощник выключен"


def restart() -> tuple[bool, str]:
    try:
        result = _systemctl("restart", SERVICE)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return result.returncode == 0, (result.stderr or "").strip()


def open_settings() -> bool:
    """Окно настроек самого помощника (его продукт — его окно)."""
    if not is_installed():
        return False
    try:
        subprocess.Popen([BINARY, "settings"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        _log.error("Помощник: не удалось открыть настройки: %s", exc)
        return False
    return True


def recent_log(lines: int = 80) -> str:
    try:
        result = _run(["journalctl", "--user", "-u", SERVICE, "--no-pager", "-n", str(lines), "-o", "cat"], timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Журнал недоступен: {exc}"
    return result.stdout or "(журнал пуст)"


def ipc_endpoint_published() -> bool:
    """Опубликовала ли почта свой канал (ipc-endpoint.json) — по нему
    помощник находит окно; для кнопки «Проверить связь»."""
    from redmail.ipc_server import endpoint_file_path

    try:
        data = json.loads(endpoint_file_path().read_text(encoding="utf-8"))
        return bool(data.get("full_server_name"))
    except (OSError, ValueError):
        return False
