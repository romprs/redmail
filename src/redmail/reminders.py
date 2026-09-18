"""Что и когда напомнить: чистая логика без Qt.

Отдельно от окна в трее (см. ui/reminder_tray.py) намеренно: решение «пора
ли напоминать и не напоминали ли уже» проверяется тестами без графики, а
резидент остаётся тонким.

Состояние (о чём уже напомнили и что отложено) лежит в профиле рядом с
календарём: после перезапуска резидента напоминания не сыплются заново за
весь день.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from redmail import calendar_store
from redmail.applog import get_logger

_log = get_logger("reminders")

STATE_FILE = "reminders.json"

#: Сколько хранить отметки о показанных напоминаниях: встреча давно
#: прошла — запись о ней в состоянии не нужна.
_KEEP_DAYS = 7


@dataclass(frozen=True)
class Reminder:
    """Одно напоминание: встреча, время её начала и выбранный способ."""

    uid: str
    summary: str
    dtstart: datetime
    dtend: datetime
    location: str
    mode: str

    @property
    def key(self) -> str:
        """Ключ в состоянии: у серии у каждого дня свой (uid одинаковый)."""
        return f"{self.uid}@{self.dtstart.astimezone(timezone.utc).isoformat()}"

    @property
    def speaks(self) -> bool:
        return self.mode in (calendar_store.REMIND_VOICE, calendar_store.REMIND_BOTH)

    @property
    def shows_window(self) -> bool:
        return self.mode in (calendar_store.REMIND_WINDOW, calendar_store.REMIND_BOTH)


class ReminderState:
    """О чём уже напомнили и что отложено. Файл читается и пишется целиком:
    записей мало (встречи одного дня), а простая запись переживает
    одновременную работу окна и резидента лучше, чем частичные обновления."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._shown: dict[str, str] = {}
        self._snoozed: dict[str, str] = {}
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        self._shown = {k: v for k, v in (raw.get("shown") or {}).items() if isinstance(v, str)}
        self._snoozed = {k: v for k, v in (raw.get("snoozed") or {}).items() if isinstance(v, str)}

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"shown": self._shown, "snoozed": self._snoozed}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            _log.warning("Напоминания: состояние не сохранено: %s", exc)

    def mark_shown(self, reminder: Reminder, now: datetime) -> None:
        self._shown[reminder.key] = now.astimezone(timezone.utc).isoformat()
        self._snoozed.pop(reminder.key, None)
        self.save()

    def snooze(self, reminder: Reminder, until: datetime) -> None:
        """Отложить: до этого времени напоминание не повторяем, а отметку о
        показе снимаем — иначе после паузы оно бы не вернулось."""
        self._snoozed[reminder.key] = until.astimezone(timezone.utc).isoformat()
        self._shown.pop(reminder.key, None)
        self.save()

    def is_pending(self, reminder: Reminder, now: datetime) -> bool:
        """Напоминание ещё не показано (или показано, но отложено и пауза
        кончилась)."""
        snoozed_until = self._snoozed.get(reminder.key)
        if snoozed_until:
            try:
                if now < datetime.fromisoformat(snoozed_until):
                    return False
            except ValueError:
                pass
        return reminder.key not in self._shown

    def forget_old(self, now: datetime) -> None:
        cutoff = (now - timedelta(days=_KEEP_DAYS)).astimezone(timezone.utc).isoformat()
        self._shown = {k: v for k, v in self._shown.items() if v >= cutoff}
        self._snoozed = {k: v for k, v in self._snoozed.items() if v >= cutoff}
        self.save()


def due_reminders(calendar_path: Path, state: ReminderState, now: datetime) -> list[Reminder]:
    """Напоминания, которые пора показать прямо сейчас."""
    try:
        events = calendar_store.due_reminders(calendar_path, now)
    except Exception as exc:
        _log.warning("Напоминания: календарь не прочитан: %s", exc)
        return []
    result: list[Reminder] = []
    for event in events:
        reminder = Reminder(
            uid=event.uid,
            summary=event.summary or "(без темы)",
            dtstart=event.dtstart,
            dtend=event.dtend,
            location=event.location,
            mode=event.remind_mode,
        )
        if state.is_pending(reminder, now):
            result.append(reminder)
    return result


def spoken_text(reminder: Reminder, now: datetime) -> str:
    """Что произносит голосовой помощник. Время — местное, как его слышит
    человек, а не UTC из хранилища."""
    start = reminder.dtstart.astimezone()
    minutes = round((reminder.dtstart - now).total_seconds() / 60)
    if minutes > 1:
        when = f"через {minutes} минут" if minutes % 10 != 1 or minutes % 100 == 11 else f"через {minutes} минуту"
    elif minutes >= 0:
        when = "сейчас"
    else:
        when = "уже идёт"
    place = f", место — {reminder.location}" if reminder.location else ""
    return f"Напоминание: {reminder.summary} {when}, в {start:%H:%M}{place}"


def today_events(calendar_path: Path, now: datetime) -> list[calendar_store.Event]:
    """Встречи текущего дня — для меню в трее (что сегодня)."""
    local_now = now.astimezone()
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    try:
        events = calendar_store.list_events(calendar_path, start, end)
    except Exception as exc:
        _log.warning("Напоминания: список дня не получен: %s", exc)
        return []
    return sorted(
        (event for event in events if event.status != "cancelled"),
        key=lambda event: event.dtstart,
    )
