#!/usr/bin/env python3
"""Быстрая проверка доступности Telegram Bot API с этого ПК."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import settings
from platforms.telegram_bot import build_bot
from platforms.telegram_proxy import resolve_telegram_proxy_url


async def main() -> int:
    proxy = resolve_telegram_proxy_url(getattr(settings, "telegram_proxy_url", None))
    print(f"TG_TOKEN: {'задан' if settings.tg_token else 'ПУСТО'}")
    print(f"Прокси: {proxy or 'нет (прямое подключение)'}")
    if not settings.tg_token:
        print("Задайте TG_TOKEN в .env", file=sys.stderr)
        return 1
    bot = build_bot()
    try:
        me = await bot.get_me()
        print(f"OK: @{me.username} (id={me.id})")
        return 0
    except Exception as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        print(
            "\nЕсли api.telegram.org недоступен: включите VPN или TELEGRAM_PROXY_URL в .env",
            file=sys.stderr,
        )
        return 1
    finally:
        await bot.session.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
