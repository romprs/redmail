"""Календарь по подписке на .ics-ссылку (в первую очередь Google Календарь).

Пожелание: "добавь подключение календаря Google". У Google CalDAV
доступен только по OAuth 2.0 с клиентом, зарегистрированным в Google
Cloud, — пароль приложения там не работает. Зато у каждого календаря
Google есть «Закрытый адрес в формате iCal» (Настройки календаря →
Интеграция календаря): по нему весь календарь отдаётся одним .ics-файлом
без входа. Здесь он периодически скачивается и разбирается тем же
itip.parse_ics_events, что и CalDAV/приглашения. Подписка односторонняя:
события читаются, изменения на сервер не уходят.
"""
from __future__ import annotations

from collections.abc import Callable

import requests

from redmail import itip
from redmail.applog import get_logger
from redmail.calendar_store import Event

_log = get_logger("ics")

TIMEOUT_SECONDS = 30
MAX_BYTES = 50 * 1024 * 1024


class IcsSubscriptionError(Exception):
    pass


def normalize_url(url: str) -> str:
    """Google выдаёт закрытый адрес как webcal:// или https:// — оба
    означают обычный HTTPS-GET."""
    text = (url or "").strip()
    if text.lower().startswith("webcal://"):
        return "https://" + text[len("webcal://"):]
    return text


def _download(url: str) -> bytes:
    try:
        with requests.get(url, timeout=TIMEOUT_SECONDS, stream=True, headers={"User-Agent": "redmail-ics/1.0"}) as resp:
            if resp.status_code != 200:
                raise IcsSubscriptionError(f"Сервер ответил {resp.status_code}")
            chunks: list[bytes] = []
            size = 0
            for chunk in resp.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise IcsSubscriptionError("Файл календаря слишком большой")
                chunks.append(chunk)
            return b"".join(chunks)
    except requests.RequestException as exc:
        raise IcsSubscriptionError(f"Не удалось скачать календарь: {exc}") from exc


def fetch_events(url: str, my_email: str, *, download: Callable[[str], bytes] = _download) -> list[Event]:
    """Все события по ссылке. Ошибки скачивания/разбора — IcsSubscriptionError."""
    target = normalize_url(url)
    if not target.lower().startswith(("http://", "https://")):
        raise IcsSubscriptionError("Адрес подписки должен начинаться с https:// или webcal://")
    raw = download(target)
    if b"BEGIN:VCALENDAR" not in raw[:4096].upper() and b"BEGIN:VCALENDAR" not in raw.upper():
        _log.error("ICS %s: ответ не похож на календарь (%d байт)", target, len(raw))
        raise IcsSubscriptionError("По ссылке получен не календарь (.ics). Проверьте адрес.")
    try:
        events = itip.parse_ics_events(raw, my_email)
    except Exception as exc:
        _log.error("ICS %s: не удалось разобрать: %s", target, exc)
        raise IcsSubscriptionError(f"Не удалось разобрать календарь: {exc}") from exc
    _log.info("ICS %s: получено событий %d (%d байт)", target, len(events), len(raw))
    return events
