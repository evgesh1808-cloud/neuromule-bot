"""Context-Caching обёртка над OpenRouter (NeuroMule 🐎⚡️ Маржа +95%).

Фишка 1 мегаспеки: тяжёлые текстовые модели (``google/gemini-*``,
``anthropic/claude-*``, ``openai/o*``) поддерживают **нативный Context Caching**
на стороне провайдера. Кэш срабатывает только если запрос приходит со
**стабильной**, **неизменной** структурой ``messages`` (без timestamps, без
динамических меток роли, без перестановок). Тогда OpenRouter переиспользует
вычисленный префикс — input-токены тарифицируются по льготной ставке
(до −85% от обычной цены).

Этот модуль НЕ дублирует ``services/ai_text.py`` — он лишь:

1. Декларирует список **кэш-дружественных моделей** (см. :data:`CACHE_FRIENDLY_MODELS`).
2. Строит каноническую структуру ``messages`` через
   :func:`build_cache_friendly_messages`:

   * ``system`` всегда первая, неизменная между ходами.
   * ``persistent_memory`` — отдельным ``system``-сообщением сразу после, чтобы
     кэш не ломался при росте истории.
   * История диалога — только ``user``/``assistant`` без мета и таймстемпов.
   * Запрос пользователя — последним.

3. Предоставляет :func:`ask_cached_chat` — тонкий passthrough в
   ``ask_ai_messages`` без модификации payload.

Принципиально не добавляем в сообщения никаких ``timestamp``, ``message_id``,
``temperature`` flavours — это убило бы cache hit-ratio.
"""

from __future__ import annotations

import logging
from typing import Final, Iterable

from config import Settings
from services.ai_text import ask_ai_messages, ChatCompletionResult

logger = logging.getLogger(__name__)


CACHE_FRIENDLY_MODELS: Final[tuple[str, ...]] = (
    "google/gemini",
    "anthropic/claude",
    "openai/o1",
    "openai/o4",
    "openai/gpt-4",
)

_VALID_ROLES: Final = frozenset({"user", "assistant"})


def is_cache_friendly_model(model_id: str) -> bool:
    """True, если модель поддерживает Context Caching на OpenRouter.

    Сравнение по префиксу — все sub-revision этих семейств наследуют
    политику кэширования провайдера.
    """

    lower = (model_id or "").strip().lower()
    if not lower:
        return False
    return any(lower.startswith(prefix) for prefix in CACHE_FRIENDLY_MODELS)


def _clean_text(text: str | None) -> str:
    return (text or "").strip()


def build_cache_friendly_messages(
    system_prompt: str,
    persistent_memory: str | None,
    history: Iterable[dict[str, str]] | None,
    user_query: str,
) -> list[dict[str, str]]:
    """Канонический builder ``messages`` для тяжёлых моделей с Context Caching.

    Args:
        system_prompt: стартовый системный промпт (не должен меняться между
            ходами одного и того же сеанса роли — это первый блок кэша).
        persistent_memory: блок ИИ-Памяти (``[Данные о пользователе…]``).
            Стабилен между ходами, тоже относится к кэш-префиксу.
        history: предыдущие сообщения ``role`` + ``content``. Любые посторонние
            поля игнорируются, пустые сообщения отбрасываются.
        user_query: финальный запрос пользователя (всегда меняется → суффикс).

    Returns:
        ``list[dict[str, str]]`` с детерминированным порядком, готовый
        для прямой передачи в ``ask_ai_messages``.
    """

    messages: list[dict[str, str]] = []

    system_text = _clean_text(system_prompt)
    if system_text:
        messages.append({"role": "system", "content": system_text})

    memory_text = _clean_text(persistent_memory)
    if memory_text:
        messages.append(
            {
                "role": "system",
                "content": (
                    f"[Данные о пользователе, учитывай при ответе: {memory_text}]"
                ),
            }
        )

    if history:
        for item in history:
            if not isinstance(item, dict):
                continue
            role = (item.get("role") or "").strip().lower()
            content = _clean_text(item.get("content"))
            if role not in _VALID_ROLES or not content:
                continue
            messages.append({"role": role, "content": content})

    user_text = _clean_text(user_query)
    if user_text:
        messages.append({"role": "user", "content": user_text})

    return messages


async def ask_cached_chat(
    settings: Settings,
    messages: list[dict[str, str]],
    *,
    models: list[str],
    timeout: float | None = None,
) -> ChatCompletionResult:
    """Тонкий passthrough в ``ask_ai_messages``, без модификации payload.

    Намеренно ничего не добавляет / не переставляет — иначе мы порушим
    кэш-префикс OpenRouter. Если ``models`` пустой, эта функция всё равно
    делегирует выбор моделей нижнему слою (``settings.free_models``).
    """

    if any(is_cache_friendly_model(m) for m in models):
        logger.debug(
            "openrouter cache: dispatching cache-friendly chain models=%s len=%s",
            models,
            len(messages),
        )

    return await ask_ai_messages(
        settings,
        messages,
        timeout=timeout,
        models=list(models) if models else None,
    )


__all__ = (
    "CACHE_FRIENDLY_MODELS",
    "is_cache_friendly_model",
    "build_cache_friendly_messages",
    "ask_cached_chat",
)
