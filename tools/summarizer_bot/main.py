"""Обёртка запуска мультиплатформенного движка (без отдельного Telegram-polling)."""
from __future__ import annotations

import asyncio

from core.runner import run_summarizer_platforms

if __name__ == "__main__":
    asyncio.run(run_summarizer_platforms())
