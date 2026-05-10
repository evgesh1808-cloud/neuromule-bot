"""MAX-интерфейс (maxgram). Заглушка до подключения Bot API MAX."""
from __future__ import annotations

from config import settings


def run_max() -> None:
    if not settings.max_token.strip():
        raise RuntimeError("Задайте MAX_TOKEN в .env для запуска MAX-бота.")
    try:
        import maxgram  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "Установите библиотеку для MAX (например maxgram) согласно актуальной документации Bot API."
        ) from exc
    raise RuntimeError(
        "MAX: добавьте обработчики maxgram (см. документацию) и вызовите polling/webhook здесь."
    )
