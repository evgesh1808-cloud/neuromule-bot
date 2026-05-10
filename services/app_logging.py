"""
Настройка логов для production: ротация файлов, отдельный журнал ошибок.

Общий поток — ``app.log``; только ERROR и выше с полным traceback — ``errors.log``.
"""

from __future__ import annotations

import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import Settings


class _ErrorsLogFormatter(logging.Formatter):
    """
    Форматирует строку лога; если к записи приложено исключение (``exc_info``),
    дописывает полный Python-traceback — чтобы в ``errors.log`` было видно первопричину сбоя.
    """

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        if record.exc_info:
            line += "\n" + "".join(traceback.format_exception(*record.exc_info)).rstrip()
        return line


def setup_logging(settings: Settings) -> None:
    """
    Инициализирует корневой логгер: ротируемые ``app.log`` и ``errors.log``, опционально консоль.

    Вызывать один раз при старте процесса (Telegram/VK). Повторный вызов сбрасывает handlers.
    """
    log_root = Path(__file__).resolve().parent.parent / settings.log_dir
    log_root.mkdir(parents=True, exist_ok=True)

    _fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    _datefmt = "%Y-%m-%d %H:%M:%S"
    fmt_common = logging.Formatter(_fmt, datefmt=_datefmt)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)

    app_file = RotatingFileHandler(
        log_root / "app.log",
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    app_file.setLevel(logging.INFO)
    app_file.setFormatter(fmt_common)

    err_file = RotatingFileHandler(
        log_root / "errors.log",
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    err_file.setLevel(logging.ERROR)
    err_file.setFormatter(_ErrorsLogFormatter(_fmt, datefmt=_datefmt))

    root.addHandler(app_file)
    root.addHandler(err_file)

    if settings.log_console:
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.INFO)
        console.setFormatter(fmt_common)
        root.addHandler(console)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.INFO)
