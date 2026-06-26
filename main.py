"""
Точка входа NeuroMule.

NEUROMULE_PLATFORM:
  telegram (по умолчанию) — aiogram, полный UI; при старте вызывается
    ``platforms.telegram_studio.setup_studio_menu_button`` (кнопка «📱 Studio»).
  vk — vkbottle (заглушка, общий сервисный слой подключается позже)
  max — maxgram (заглушка до реализации polling/webhook)
  api — FastAPI для Mini App backend
  wb_worker — ночной батч WB API + утренние уведомления 09:00 МСК
"""
from __future__ import annotations

import asyncio
import atexit
import os
import socket
import sys
from pathlib import Path

# Держим сокет открытым, пока жив процесс — второй запуск main.py на том же ПК сразу выйдет (нет Conflict в Telegram).
_telegram_single_instance_sock: socket.socket | None = None
_LOCK_FILE = Path(
    os.getenv(
        "NEUROMULE_TELEGRAM_LOCK_FILE",
        str(Path(__file__).resolve().parent / "data" / "telegram_bot.lock"),
    )
)


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _release_lock_file() -> None:
    try:
        if _LOCK_FILE.exists() and _LOCK_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            _LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _acquire_telegram_single_instance() -> None:
    global _telegram_single_instance_sock
    _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    if _LOCK_FILE.exists():
        try:
            old_pid = int(_LOCK_FILE.read_text(encoding="utf-8").strip())
        except ValueError:
            old_pid = 0
        if _pid_is_running(old_pid):
            print(
                "NeuroMule (Telegram): уже запущен экземпляр бота "
                f"(PID {old_pid}, lock {_LOCK_FILE}). "
                "Остановите лишний процесс python main.py.",
                file=sys.stderr,
            )
            sys.exit(1)
        _LOCK_FILE.unlink(missing_ok=True)

    base_port = int(os.getenv("NEUROMULE_TELEGRAM_LOCK_PORT", "45678"))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":
        # После kill процесса Windows может держать порт — разрешаем быстрый перезапуск.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    bound_port: int | None = None
    for port in range(base_port, base_port + 12):
        try:
            sock.bind(("127.0.0.1", port))
            bound_port = port
            break
        except OSError:
            continue
    if bound_port is None:
        print(
            "NeuroMule (Telegram): не удалось занять порт блокировки "
            f"({base_port}–{base_port + 11}). Закройте лишние python main.py "
            "или задайте другой NEUROMULE_TELEGRAM_LOCK_PORT в .env.",
            file=sys.stderr,
        )
        sys.exit(1)
    if bound_port != base_port:
        print(
            f"NeuroMule (Telegram): lock-port {base_port} занят, использую {bound_port}.",
            file=sys.stderr,
        )
    _telegram_single_instance_sock = sock
    _LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(_release_lock_file)


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
    elif mode in ("wb_worker", "wb_api_worker"):
        from workers.wb_api_worker import run_wb_api_worker

        asyncio.run(run_wb_api_worker())
    else:
        print(f"Неизвестный NEUROMULE_PLATFORM={mode!r}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
