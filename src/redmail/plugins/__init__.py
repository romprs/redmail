"""Подключаемые модули почты.

Модуль — необязательная часть программы, которую включают и выключают в
«Параметрах» → «Модули». Выключенный модуль не загружается и ни на что не
влияет. Встроенные модули перечислены в BUILTIN; сторонние пакеты могут
добавить свои через точку входа `redmail.plugins` (importlib.metadata) —
объект с теми же полями, что PluginInfo.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata

from redmail.applog import get_logger

_log = get_logger("plugins")


@dataclass(frozen=True)
class PluginInfo:
    id: str
    title: str
    description: str
    enabled_by_default: bool = False


CATEGORIES = PluginInfo(
    id="categories",
    title="Категории писем",
    description=(
        "Раскладывает письма по категориям: уведомления и рассылки, обращения и запросы, реклама, "
        "а также ваши собственные. Учитывает адресатов и слова из описания категории, признаки "
        "массовых рассылок и то, как вы сами размечаете письма, — и дообучается на этом. "
        "Всё хранится и считается только на этом компьютере."
    ),
    enabled_by_default=True,
)

VOICE_ASSISTANT = PluginInfo(
    id="voice_assistant",
    title="Голосовой помощник",
    description=(
        "Управление почтой и календарём голосом: открыть почту, создать встречу разговором, перенести "
        "или отменить встречу, выбрать участников и календарь. Распознавание речи — на этом компьютере, "
        "без сети. Ставится отдельным пакетом audioreferent; включается службой пользователя."
    ),
    enabled_by_default=False,
)

BUILTIN: tuple[PluginInfo, ...] = (CATEGORIES, VOICE_ASSISTANT)


def available_plugins() -> list[PluginInfo]:
    """Встроенные модули и модули, установленные отдельными пакетами."""
    plugins = list(BUILTIN)
    known = {plugin.id for plugin in plugins}
    try:
        entry_points = metadata.entry_points(group="redmail.plugins")
    except Exception as exc:  # повреждённые метаданные пакетов не должны ронять окно
        _log.warning("Сторонние модули не перечислены: %s", exc)
        entry_points = []
    for entry in entry_points:
        try:
            info = entry.load()
        except Exception as exc:
            _log.warning("Модуль %s не загружен: %s", entry.name, exc)
            continue
        if isinstance(info, PluginInfo) and info.id not in known:
            plugins.append(info)
            known.add(info.id)
    return plugins
