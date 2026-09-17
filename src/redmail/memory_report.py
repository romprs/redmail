"""Расход памяти программы — цифрами, а не догадками.

Что считается: сам процесс (RSS, из него «своя» память и отображённые
файлы библиотек), процессы встроенного просмотра писем (QtWebEngine — у
них свои процессы, в мониторе системы они отдельными строками), куча
glibc (занято и удерживается свободным), Python-объекты по типам, потоки.
Данные интерфейса (письма в списке, контакты, кэши) добавляет окно.

free_memory() — сборка мусора и возврат свободной памяти системе
(malloc_trim): после автоархива и загрузки тел писем через память проходят
сотни мегабайт, и без возврата процесс долго держит их у себя.
"""
from __future__ import annotations

import ctypes
import gc
import os
import sys
import threading
from collections import Counter
from pathlib import Path

from redmail.applog import get_logger

_log = get_logger("memory")


def _status(pid: int | str = "self") -> dict[str, int]:
    """Поля /proc/<pid>/status в килобайтах (VmRSS, RssAnon, RssFile, Threads)."""
    values: dict[str, int] = {}
    try:
        text = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return values
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in ("VmRSS", "RssAnon", "RssFile", "RssShmem", "Threads", "PPid", "Name"):
            parts = rest.split()
            if parts and parts[0].isdigit():
                values[key] = int(parts[0])
    return values


def _descendants(root: int) -> list[int]:
    """Дочерние процессы на любую глубину (рендереры QtWebEngine — внуки)."""
    parents: dict[int, int] = {}
    for status in Path("/proc").glob("[0-9]*/status"):
        info = _status(status.parent.name)
        if "PPid" in info:
            parents[int(status.parent.name)] = info["PPid"]
    result, frontier = [], {root}
    while frontier:
        children = {pid for pid, ppid in parents.items() if ppid in frontier and pid not in result}
        result.extend(sorted(children))
        frontier = children
    return result


class _MallInfo2(ctypes.Structure):
    _fields_ = [(name, ctypes.c_size_t) for name in (
        "arena", "ordblks", "smblks", "hblks", "hblkhd", "usmblks", "fsmblks", "uordblks", "fordblks", "keepcost",
    )]


def _libc():
    if not sys.platform.startswith("linux"):
        return None
    try:
        return ctypes.CDLL("libc.so.6")
    except OSError:
        return None


def glibc_heap() -> dict[str, float] | None:
    """Куча glibc в МБ: всего под кучей, занято, свободно, но удерживается."""
    libc = _libc()
    if libc is None or not hasattr(libc, "mallinfo2"):
        return None
    libc.mallinfo2.restype = _MallInfo2
    info = libc.mallinfo2()
    mb = 1024 * 1024
    return {
        "heap": (info.arena + info.hblkhd) / mb,
        "in_use": (info.uordblks + info.hblkhd) / mb,
        "free_held": info.fordblks / mb,
    }


def free_memory() -> None:
    gc.collect()
    libc = _libc()
    if libc is not None:
        try:
            libc.malloc_trim(0)
        except Exception as exc:  # не критично
            _log.debug("malloc_trim не выполнен: %s", exc)


def python_objects(top: int = 12) -> list[tuple[str, int, float]]:
    """(тип, число объектов, МБ по sys.getsizeof) — самые объёмные типы.
    Размеры приблизительные: вложенные объекты считаются отдельно."""
    counts: Counter = Counter()
    sizes: Counter = Counter()
    for obj in gc.get_objects():
        name = type(obj).__name__
        counts[name] += 1
        try:
            sizes[name] += sys.getsizeof(obj)
        except Exception:
            pass
    return [(name, counts[name], size / (1024 * 1024)) for name, size in sizes.most_common(top)]


def process_lines() -> list[str]:
    me = _status()
    lines = []
    if me:
        lines.append(
            f"Процесс программы: {me.get('VmRSS', 0) / 1024:.0f} МБ — своя память {me.get('RssAnon', 0) / 1024:.0f} МБ, "
            f"библиотеки и файлы {me.get('RssFile', 0) / 1024:.0f} МБ; потоков {me.get('Threads', threading.active_count())}"
        )
        children = [_status(pid) for pid in _descendants(os.getpid())]
        web = [info for info in children if info]
        if web:
            lines.append(
                f"Просмотр писем (процессы QtWebEngine: {len(web)}): {sum(i.get('VmRSS', 0) for i in web) / 1024:.0f} МБ, "
                f"своя память {sum(i.get('RssAnon', 0) for i in web) / 1024:.0f} МБ"
            )
    else:
        lines.append("Сведения о процессе недоступны (не Linux)")
    heap = glibc_heap()
    if heap is not None:
        lines.append(
            f"Куча: {heap['heap']:.0f} МБ, из них занято {heap['in_use']:.0f} МБ, "
            f"свободно, но не возвращено системе {heap['free_held']:.0f} МБ"
        )
    lines.append(f"Python: блоков памяти {sys.getallocatedblocks():,}".replace(",", " "))
    return lines
