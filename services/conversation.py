"""
Сборка payload для чат-комплишена и фоновое обновление persistent_memory.

Работает поверх ``services.repository`` (aiosqlite).

Выбор модели для прямого ответа пользователю в чате — в ``services/billing/chat_pipeline.py``
(``plan_text_chat``) и ``services/use_cases/chat_turn.py``; этот модуль модель по тарифу не маршрутизирует.
"""

from __future__ import annotations

import asyncio
import logging

from config import Settings
from content.chat_prompt import build_memory_update_prompt, build_system_prompt
from services import repository as repo
from services.dialog_platform import DEFAULT_DIALOG_PLATFORM
from services.dialog_sanitize import sanitize_dialog_content_for_chat

logger = logging.getLogger(__name__)

# Фоновое сжатие persistent_memory — только бесплатный шлюз OpenRouter, без VIP-линии.
_PERSISTENT_MEMORY_MODEL_CHAIN: tuple[str, ...] = (
    "google/gemini-2.5-flash:free",
    "google/gemini-2.5-flash-lite:free",
)

_PERSISTENT_MEMORY_FORMAT_RULE = (
    "Всегда структурируй и разделяй итоговый текст памяти строго по трем блокам через эмодзи, "
    "если данные присутствуют:\n"
    "💼 Проекты (сфера деятельности, задачи);\n"
    "📐 Стиль (предпочтения по ответам);\n"
    "⚙️ Контекст (серверы, стек технологий).\n"
    "Текст внутри блоков пиши ультра-кратко, тезисно, без воды."
)

_MEMORY_COMPRESSION_SYSTEM = (
    "Ты — служебный компрессор долгосрочной памяти пользователя. "
    "Сжимай факты из диалога в краткие тезисы на русском. Не отвечай пользователю напрямую."
)


async def build_openrouter_messages(
    settings: Settings,
    user_id: int,
    text_role: str = "standard",
    *,
    premium: bool = False,
    platform: str = DEFAULT_DIALOG_PLATFORM,
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
    rows = await repo.dialog_fetch_last(user_id, settings.chat_history_limit, platform=platform)
    out: list[dict[str, str]] = [{"role": "system", "content": system}]
    for role, content in rows:
        if role in ("user", "assistant"):
            out.append({"role": role, "content": sanitize_dialog_content_for_chat(content)})
    return out


async def _dialog_transcript_for_memory(
    user_id: int,
    max_pairs: int = 5,
    *,
    platform: str = DEFAULT_DIALOG_PLATFORM,
) -> str:
    """
    Собирает текст последних реплик для служебного запроса «сжать память».

    Возвращает многострочную строку или пустую, если истории нет.
    """
    rows = await repo.dialog_fetch_last(user_id, max_pairs * 2, platform=platform)
    lines: list[str] = []
    for role, content in rows:
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


async def _ask_memory_compression(settings: Settings, prompt: str) -> str:
    """Служебный вызов OpenRouter только через бесплатные модели (:free)."""
    from services.ai_text import ask_ai_messages

    try:
        completion = await ask_ai_messages(
            settings,
            [
                {"role": "system", "content": _MEMORY_COMPRESSION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            timeout=25.0,
            max_context_chars=50_000,
            max_context_tokens=16_000,
            models=list(_PERSISTENT_MEMORY_MODEL_CHAIN),
            max_tokens=512,
        )
        return (completion.get("content") or "").strip()
    except RuntimeError as e:
        if str(e) in ("context_too_long", "context_too_long_tokens"):
            return ""
        logger.error("memory compression: all free models failed or unavailable")
        return ""
    except Exception:
        logger.exception("memory compression unexpected error")
        return ""


async def maybe_refresh_persistent_memory(
    settings: Settings,
    user_id: int,
    *,
    platform: str = DEFAULT_DIALOG_PLATFORM,
) -> None:
    """
    Периодически обновляет поле persistent_memory в БД через отдельный вызов модели.

    Вызывается в ``asyncio.create_task`` после ответа пользователю, ошибки глушатся в лог.

    Модель жёстко зафиксирована на бесплатном шлюзе ``google/gemini-2.5-flash:free``
    (резерв — ``google/gemini-2.5-flash-lite:free``) на всех тарифах; тариф пользователя
    не учитывается. Платная VIP-линия не расходуется.

    Вход:
        settings — конфиг.
        user_id — пользователь.

    Возвращает: ничего.
    """
    try:
        n = await repo.dialog_total_messages(user_id, platform=platform)
        if n == 0 or n % 10 != 0:
            return
        transcript = await _dialog_transcript_for_memory(user_id, platform=platform)
        if len(transcript) < 40:
            return
        prompt = (
            f"{build_memory_update_prompt(transcript[:12000])}\n\n"
            f"{_PERSISTENT_MEMORY_FORMAT_RULE}"
        )
        cleaned = await _ask_memory_compression(settings, prompt)
        if not cleaned or "недоступен" in cleaned or "слишком длинным" in cleaned:
            return
        if len(cleaned) > 2000:
            cleaned = cleaned[:2000]
        await repo.set_persistent_memory(user_id, cleaned)
    except Exception:
        logger.exception("persistent_memory refresh failed user_id=%s", user_id)


def schedule_memory_refresh(
    settings: Settings,
    user_id: int,
    *,
    platform: str = DEFAULT_DIALOG_PLATFORM,
) -> None:
    """
    Планирует фоновое обновление памяти (не блокирует хендлер).

    Вход: settings, user_id.
    Возвращает: ничего.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(maybe_refresh_persistent_memory(settings, user_id, platform=platform))
