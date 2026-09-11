"""Журнал подключений и синхронизаций (пожелание: "писать лог подключений
и синхронизаций, чтобы отладить подключения и на перспективу контроля").

Один файл ~/.config/redmail/logs/redmail.log с ротацией (5 × 2 МБ): в него
пишут модули imap_client/smtp_client/ews_client/caldav_sync/gssapi_sasl/
mailbox через стандартный logging с именами "redmail.<модуль>" — входы и
переподключения, отправки, синхронизации календаря, ошибки. Паролей и тел
писем в журнале нет: только серверы, логины, папки, счётчики и тексты
ошибок. Уровень INFO; переменная окружения REDMAIL_DEBUG=1 включает DEBUG.
Открыть журнал из программы: Справка → «Журнал подключений…»."""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from redmail.paths import app_dir

LOG_FILE_NAME = "redmail.log"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 5
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

_configured = False


def log_dir() -> Path:
    return app_dir() / "logs"


def log_path() -> Path:
    return log_dir() / LOG_FILE_NAME


def setup_logging() -> Path | None:
    """Настраивает файловый журнал; повторные вызовы безвредны. Возвращает
    путь к файлу или None, если каталог недоступен на запись (журнал не
    должен мешать запуску программы)."""
    global _configured
    root = logging.getLogger("redmail")
    if _configured:
        return log_path()
    level = logging.DEBUG if os.environ.get("REDMAIL_DEBUG") else logging.INFO
    root.setLevel(level)
    root.propagate = False
    try:
        log_dir().mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_path(), maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(handler)
    _configured = True
    _install_excepthook()
    return log_path()


def _install_excepthook() -> None:
    """Необработанные исключения — тоже в журнал (жалоба: "при первом
    открытии выдал ошибку, но потом запустился" — без файла такие разовые
    сбои нечем было разбирать)."""
    previous = sys.excepthook
    logger = logging.getLogger("redmail.app")

    def hook(exc_type, exc, tb) -> None:
        logger.error("Необработанное исключение", exc_info=(exc_type, exc, tb))
        previous(exc_type, exc, tb)

    sys.excepthook = hook


def tail_text(max_bytes: int = 128 * 1024) -> str:
    """Хвост журнала для окна просмотра; пустая строка, если файла нет."""
    path = log_path()
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # до начала целой строки
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"redmail.{name}")
