"""
Сборка payload для чат-комплишена и фоновое обновление persistent_memory.

Работает поверх ``services.repository`` (aiosqlite).
"""

from __future__ import annotations

import asyncio
import logging

from config import Settings
from content.chat_prompt import build_memory_update_prompt, build_system_prompt
from services import repository as repo
from services.dialog_sanitize import sanitize_dialog_content_for_chat

logger = logging.getLogger(__name__)


async def build_openrouter_messages(
    settings: Settings,
    user_id: int,
    text_role: str = "standard",
    *,
    premium: bool = False,
) -> list[dict[str, str]]:
    """
    Формирует список сообщений в формате OpenAI Chat для OpenRouter.

    Порядок:
        1) system — базовая роль + защита от injection + блок persistent_memory;
        2) последние ``settings.chat_history_limit`` реплик user/assistant из БД (хронологически).

    Вход:
        settings — конфиг.
        user_id — id пользователя Telegram (= primary key в users).

    Возвращает:
        Список словарей ``{"role": "...", "content": "..."}``.
    """
    mem = await repo.get_persistent_memory(user_id)
    system = build_system_prompt(settings, mem, text_role, premium=premium)
    rows = await repo.dialog_fetch_last(user_id, settings.chat_history_limit)
    out: list[dict[str, str]] = [{"role": "system", "content": system}]
    for role, content in rows:
        if role in ("user", "assistant"):
            out.append({"role": role, "content": sanitize_dialog_content_for_chat(content)})
    return out


async def _dialog_transcript_for_memory(user_id: int, max_pairs: int = 5) -> str:
    """
    Собирает текст последних реплик для служебного запроса «сжать память».

    Возвращает многострочную строку или пустую, если истории нет.
    """
    rows = await repo.dialog_fetch_last(user_id, max_pairs * 2)
    lines: list[str] = []
    for role, content in rows:
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


async def maybe_refresh_persistent_memory(settings: Settings, user_id: int) -> None:
    """
    Периодически обновляет поле persistent_memory в БД через отдельный вызов модели.

    Вызывается в ``asyncio.create_task`` после ответа пользователю, ошибки глушатся в лог.

    Вход:
        settings — конфиг.
        user_id — пользователь.

    Возвращает: ничего.
    """
    try:
        n = await repo.dialog_total_messages(user_id)
        if n == 0 or n % 10 != 0:
            return
        transcript = await _dialog_transcript_for_memory(user_id)
        if len(transcript) < 40:
            return
        from services.ai_text import ask_ai_text

        prompt = build_memory_update_prompt(transcript[:12000])
        raw = await ask_ai_text(settings, prompt, timeout_override=25.0)
        cleaned = (raw or "").strip()
        if not cleaned or "недоступен" in cleaned or "слишком длинным" in cleaned:
            return
        if len(cleaned) > 2000:
            cleaned = cleaned[:2000]
        await repo.set_persistent_memory(user_id, cleaned)
    except Exception:
        logger.exception("persistent_memory refresh failed user_id=%s", user_id)


def schedule_memory_refresh(settings: Settings, user_id: int) -> None:
    """
    Планирует фоновое обновление памяти (не блокирует хендлер).

    Вход: settings, user_id.
    Возвращает: ничего.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(maybe_refresh_persistent_memory(settings, user_id))
