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
from dataclasses import dataclass, replace
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
    #: Кто позвал. Для чужой встречи (и для встречи из календаря коллеги)
    #: это главное, чего не хватает в напоминании: по теме «Планёрка» не
    #: понять, чья она — поэтому автора называем сами, без настройки.
    organizer: str = ""
    #: Встречу организовали мы сами — тогда автора называть незачем.
    mine: bool = True
    #: Календарь, откуда встреча (свой, коллеги, Exchange).
    calendar_name: str = ""

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


@dataclass(frozen=True)
class OthersPolicy:
    """Что делать с ЧУЖИМИ встречами — теми, где организатор не мы
    (приглашение с сервера, встреча из календаря коллеги). Своей встрече
    способ напоминания выбирают в окне встречи, а в приглашении выбирать
    некому: без этой настройки о чужих встречах не напомнили бы вовсе.

    authors пуст — напоминать обо всех чужих; иначе только о встречах этих
    организаторов (имя или адрес, сравнение без учёта регистра)."""

    mode: str = calendar_store.REMIND_WINDOW
    minutes: int = 15
    authors: tuple[str, ...] = ()

    @classmethod
    def load(cls) -> "OthersPolicy":
        try:
            from redmail import config_store

            mode, minutes, authors = config_store.load_others_reminder()
            return cls(mode=mode, minutes=minutes, authors=authors)
        except Exception as exc:
            _log.warning("Напоминания: настройка чужих встреч не прочитана: %s", exc)
            return cls()

    def applies_to(self, event) -> bool:
        if self.mode == calendar_store.REMIND_NONE:
            return False
        if not self.authors:
            return True
        who = f"{event.organizer_name} {event.organizer_email}".casefold()
        return any(author.casefold() in who for author in self.authors)


def _with_others_policy(events, policy: OthersPolicy, now: datetime) -> list:
    """Чужим встречам без своего выбора проставляем способ из настройки —
    и тогда они попадают в напоминания наравне со своими."""
    result = []
    for event in events:
        if event.remind_mode != calendar_store.REMIND_NONE and event.remind_minutes >= 0:
            # Способ выбран руками — такую встречу уже отобрал по времени
            # calendar_store.due_reminders, настройка её не перебивает и
            # раньше срока не показывает.
            continue
        if event.is_organizer or not policy.applies_to(event):
            continue
        moment = event.dtstart - timedelta(minutes=policy.minutes)
        if moment <= now < event.dtend:
            result.append(replace(event, remind_minutes=policy.minutes, remind_mode=policy.mode))
    return result


def due_reminders(
    calendar_path: Path, state: ReminderState, now: datetime, policy: OthersPolicy | None = None
) -> list[Reminder]:
    """Напоминания, которые пора показать прямо сейчас."""
    policy = OthersPolicy() if policy is None else policy
    try:
        events = calendar_store.due_reminders(calendar_path, now)
        if policy.mode != calendar_store.REMIND_NONE:
            # Чужие встречи в calendar_store.due_reminders не попадают (у них
            # способ не выбран) — добираем их отдельно по настройке.
            window = calendar_store.list_events(
                calendar_path, now - timedelta(hours=24), now + timedelta(hours=24)
            )
            known = {event.uid for event in events}
            extra = [event for event in window if event.uid not in known and event.status != "cancelled"]
            events = events + _with_others_policy(extra, policy, now)
    except Exception as exc:
        _log.warning("Напоминания: календарь не прочитан: %s", exc)
        return []
    calendar_names: dict[str, str] = {}
    try:
        calendar_names = {cal.id: cal.name for cal in calendar_store.list_calendars(calendar_path)}
    except Exception:
        pass
    result: list[Reminder] = []
    for event in events:
        reminder = Reminder(
            uid=event.uid,
            summary=event.summary or "(без темы)",
            dtstart=event.dtstart,
            dtend=event.dtend,
            location=event.location,
            mode=event.remind_mode,
            organizer=(event.organizer_name or event.organizer_email or "").strip(),
            mine=bool(event.is_organizer),
            calendar_name=calendar_names.get(event.calendar_id, ""),
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
    # Чужую встречу называем по автору: «Планёрка» без имени организатора
    # ничего не говорит, когда таких планёрок несколько.
    author = f", позвал {reminder.organizer}" if not reminder.mine and reminder.organizer else ""
    return f"Напоминание: {reminder.summary} {when}, в {start:%H:%M}{author}{place}"


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
