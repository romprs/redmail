"""Встраивание внешних картинок письма перед пересылкой.

Жалоба (повторная): "опять есть проблема с отображением картинок при
пересылке" — на скриншоте пересланное уведомление Google с пустым
местом вместо логотипа. Встроенные (cid:) картинки пересылка уже
переносит; но у рассылок картинки обычно ВНЕШНИЕ (<img
src="https://...">), а редактор письма (QTextEdit) сам по сети не ходит,
и на стороне получателя такие ссылки тоже часто режутся. Здесь такие
картинки скачиваются один раз и превращаются во встроенные (cid:), то есть
уходят внутри письма — как это делает Outlook при пересылке.

Ограничения намеренные: только http(s), не больше MAX_IMAGES штук, не
больше MAX_BYTES каждая, короткий таймаут — пересылка не должна
подвисать из-за медленного или мёртвого сервера картинок. Что не удалось
скачать, остаётся внешней ссылкой, как было.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from collections.abc import Callable
from urllib.parse import urlparse

import requests

MAX_IMAGES = 30
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT_SECONDS = 8

_IMG_SRC_RE = re.compile(r"""(<img\b[^>]*?\bsrc\s*=\s*)(["'])(https?://[^"'>\s]+)\2""", re.IGNORECASE)
_ALLOWED_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/svg+xml")


def _is_public_host(url: str) -> bool:
    """Не ходим по адресам внутренней сети/локальной машины: письмо со
    ссылкой вида http://10.0.0.5/… при пересылке иначе утянуло бы
    содержимое внутреннего ресурса наружу вместе с письмом."""
    host = urlparse(url).hostname or ""
    if not host or host.lower() == "localhost":
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not address.is_global:
            return False
    return True


def _download(url: str) -> tuple[str, bytes] | None:
    if not _is_public_host(url):
        return None
    try:
        with requests.get(url, timeout=TIMEOUT_SECONDS, stream=True, headers={"User-Agent": "Mozilla/5.0"}) as resp:
            if resp.status_code != 200:
                return None
            content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type not in _ALLOWED_TYPES:
                return None
            chunks: list[bytes] = []
            size = 0
            for chunk in resp.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    return None
                chunks.append(chunk)
            data = b"".join(chunks)
    except (requests.RequestException, OSError, ValueError):
        return None
    return (content_type, data) if data else None


def embed_remote_images(
    html: str,
    inline_images: dict[str, tuple[str, bytes]] | None,
    *,
    fetch: Callable[[str], tuple[str, bytes] | None] = _download,
) -> tuple[str, dict[str, tuple[str, bytes]]]:
    """Возвращает (html с cid:-ссылками вместо скачанных внешних картинок,
    дополненный словарь inline_images). Одинаковые URL скачиваются один
    раз. Исходные аргументы не меняются."""
    images: dict[str, tuple[str, bytes]] = dict(inline_images or {})
    cid_by_url: dict[str, str | None] = {}

    def replace(match: re.Match) -> str:
        prefix, quote, url = match.group(1), match.group(2), match.group(3)
        if url not in cid_by_url:
            if len([v for v in cid_by_url.values() if v]) >= MAX_IMAGES:
                cid_by_url[url] = None
            else:
                downloaded = fetch(url)
                if downloaded is None:
                    cid_by_url[url] = None
                else:
                    cid = "fwd-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16] + "@redmail"
                    images[cid] = downloaded
                    cid_by_url[url] = cid
        cid = cid_by_url[url]
        if cid is None:
            return match.group(0)
        return f"{prefix}{quote}cid:{cid}{quote}"

    return _IMG_SRC_RE.sub(replace, html), images
