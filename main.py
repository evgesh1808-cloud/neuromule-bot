"""
Точка входа NeuroMule.

NEUROMULE_PLATFORM:
  telegram (по умолчанию) — aiogram, полный UI
  vk — vkbottle (заглушка, общий сервисный слой подключается позже)
  max — maxgram (заглушка до реализации polling/webhook)
  api — FastAPI для Mini App backend
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys

# Держим сокет открытым, пока жив процесс — второй запуск main.py на том же ПК сразу выйдет (нет Conflict в Telegram).
_telegram_single_instance_sock: socket.socket | None = None


def _acquire_telegram_single_instance() -> None:
    global _telegram_single_instance_sock
    port = int(os.getenv("NEUROMULE_TELEGRAM_LOCK_PORT", "45678"))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        print(
            "NeuroMule (Telegram): уже запущен экземпляр бота на этом компьютере "
            f"(порт блокировки {port}). Закройте лишнее окно/процесс python main.py "
            "или задайте другой NEUROMULE_TELEGRAM_LOCK_PORT в .env.",
            file=sys.stderr,
        )
        sys.exit(1)
    _telegram_single_instance_sock = sock


def main() -> None:
    mode = os.getenv("NEUROMULE_PLATFORM", "telegram").strip().lower()

    if mode == "telegram":
        _acquire_telegram_single_instance()
        from platforms.telegram_bot import run_telegram

        asyncio.run(run_telegram())
    elif mode == "vk":
        from platforms.vk_bot import run_vk

        run_vk()
    elif mode == "max":
        from platforms.max_bot import run_max

        run_max()
    elif mode in ("api", "miniapp", "fastapi"):
        import uvicorn

        port = int(os.getenv("API_PORT", "8000"))
        uvicorn.run("api.mini_app:app", host="0.0.0.0", port=port, reload=False)
    else:
        print(f"Неизвестный NEUROMULE_PLATFORM={mode!r}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
