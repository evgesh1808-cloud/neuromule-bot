"""Адаптация тела поста блогера под площадки СНГ (отдельные LLM-запросы)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import Settings
from content import messages as msg
from content.chat_prompt import (
    SYSTEM_ADAPT_TG_MAX,
    SYSTEM_ADAPT_VC,
    SYSTEM_ADAPT_VIDEO,
    SYSTEM_ADAPT_VK,
)
from services.ai_text import ask_ai_messages
from services.billing.pricing import FREE_CHAT_MODEL
from services.blogger_post_parser import repair_blogger_telegram_html
from services.telegram_safe_text import prepare_telegram_html_text
from services.use_cases.chat_turn import strip_redacted_thinking

logger = logging.getLogger(__name__)

_ADAPT_TARGETS: frozenset[str] = frozenset({"video", "vc", "vk", "tg_max"})

_ADAPT_PREAMBLE_RE = re.compile(
    r"^(?:"
    r"here(?:'s| is) (?:your )?(?:post|article|script|text)|"
    r"output:|final (?:post|text|script):|"
    r"вот (?:ваш )?(?:пост|статья|сценарий|текст)|"
    r"готово[!,.]?|ответ:|адаптированн\w+ (?:пост|текст)"
    r")\s*:?\s*",
    re.IGNORECASE,
)

_FENCED_CODE_RE = re.compile(r"```[\w]*\n?([\s\S]*?)```", re.MULTILINE)


@dataclass(frozen=True)
class BloggerAdaptRoute:
    """Маршрут адаптации: промпт, модель(и), температура и лейбл для UX."""

    key: str
    label: str
    callback_data: str
    button_text: str
    system_prompt: str
    models: tuple[str, ...]
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class BloggerAdaptBillingResult:
    ok: bool
    content: str | None = None
    error: str = ""


BLOGGER_ADAPT_ROUTES: tuple[BloggerAdaptRoute, ...] = (
    BloggerAdaptRoute(
        key="video",
        label="Видео (Reels/TikTok/Shorts/Likee)",
        callback_data=msg.CB_ADAPT_TARGET_VIDEO,
        button_text=msg.BTN_BLOGGER_ADAPT_VIDEO,
        system_prompt=SYSTEM_ADAPT_VIDEO,
        models=(FREE_CHAT_MODEL,),
        temperature=0.4,
        max_tokens=1600,
    ),
    BloggerAdaptRoute(
        key="vc",
        label="Статья (VC.ru / Дзен)",
        callback_data=msg.CB_ADAPT_TARGET_VC,
        button_text=msg.BTN_BLOGGER_ADAPT_VC,
        system_prompt=SYSTEM_ADAPT_VC,
        models=(FREE_CHAT_MODEL,),
        temperature=0.3,
        max_tokens=2800,
    ),
    BloggerAdaptRoute(
        key="vk",
        label="Пост (ВКонтакте / VK)",
        callback_data=msg.CB_ADAPT_TARGET_VK,
        button_text=msg.BTN_BLOGGER_ADAPT_VK,
        system_prompt=SYSTEM_ADAPT_VK,
        models=(FREE_CHAT_MODEL,),
        temperature=0.4,
        max_tokens=1600,
    ),
    BloggerAdaptRoute(
        key="tg_max",
        label="Канал (Telegram / суперапп МАКС)",
        callback_data=msg.CB_ADAPT_TARGET_TG_MAX,
        button_text=msg.BTN_BLOGGER_ADAPT_TG_MAX,
        system_prompt=SYSTEM_ADAPT_TG_MAX,
        models=(FREE_CHAT_MODEL,),
        temperature=0.3,
        max_tokens=800,
    ),
)

_ROUTE_BY_KEY: dict[str, BloggerAdaptRoute] = {route.key: route for route in BLOGGER_ADAPT_ROUTES}


def get_blogger_adapt_keyboard(post_id: str) -> InlineKeyboardMarkup:
    """Подменю «🔄 Адаптировать»: 4 площадки СНГ (3 💎).

    ``adapt_target:*`` не содержит ``post_id`` — черновик резолвится по привязке
    ``(chat_id, message_id)`` в ``blogger_post_cache``.
    """
    builder = InlineKeyboardBuilder()
    for route in BLOGGER_ADAPT_ROUTES:
        builder.row(
            InlineKeyboardButton(
                text=route.button_text,
                callback_data=route.callback_data,
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Вернуться назад",
            callback_data=f"{msg.CB_BLOG_BACK_PREFIX}{post_id}",
        )
    )
    return builder.as_markup()


def parse_adapt_target(data: str) -> str | None:
    """Из ``adapt_target:video`` возвращает ключ площадки или ``None``."""
    prefix = msg.CB_ADAPT_TARGET_PREFIX
    if not (data or "").startswith(prefix):
        return None
    target = data[len(prefix) :].strip().lower()
    if target not in _ADAPT_TARGETS:
        return None
    return target


def adapt_platform_label(platform: str) -> str:
    route = _ROUTE_BY_KEY.get((platform or "").strip().lower())
    return route.label if route else platform.upper()


def is_valid_adapt_platform(platform: str) -> bool:
    return (platform or "").strip().lower() in _ADAPT_TARGETS


def _strip_adapt_preamble(text: str) -> str:
    result = (text or "").strip()
    while True:
        cleaned = _ADAPT_PREAMBLE_RE.sub("", result, count=1).strip()
        if cleaned == result:
            break
        result = cleaned
    return result.strip().strip('"').strip("'").strip()


def sanitize_adapt_model_output(text: str) -> str:
    """Убирает thinking-блоки, code fence и вводные фразы модели."""
    raw = strip_redacted_thinking(text or "").strip()
    if not raw:
        return ""

    fenced = _FENCED_CODE_RE.search(raw)
    if fenced and fenced.group(1).strip():
        raw = fenced.group(1).strip()

    raw = _strip_adapt_preamble(raw)
    raw = re.sub(r"^#{1,6}\s+", "", raw, flags=re.MULTILINE)
    return raw.strip()


def prepare_adapted_telegram_html(text: str) -> str:
    """Markdown → HTML, дозакрытие ``<b>``, безопасная подготовка для Telegram."""
    cleaned = sanitize_adapt_model_output(text)
    repaired = repair_blogger_telegram_html(cleaned)
    return prepare_telegram_html_text(repaired)


async def adapt_blogger_post_body(
    settings: Settings,
    *,
    source_body: str,
    platform: str,
) -> str | None:
    """Один запрос OpenRouter: system-промпт + только ``===ТЕЛО ПОСТА===`` из кэша."""
    body = (source_body or "").strip()
    if not body:
        return None

    route = _ROUTE_BY_KEY.get(platform.strip().lower())
    if route is None:
        return None

    messages = [
        {"role": "system", "content": route.system_prompt},
        {"role": "user", "content": body},
    ]
    try:
        result = await ask_ai_messages(
            settings,
            messages,
            models=list(route.models),
            max_tokens=route.max_tokens,
            temperature=route.temperature,
        )
    except Exception:
        logger.exception("blogger adapt ask_ai_messages failed platform=%s", platform)
        return None

    content = sanitize_adapt_model_output(result.content or "")
    return content or None


async def adapt_blogger_post_with_billing(
    settings: Settings,
    *,
    source_body: str,
    platform: str,
    user_id: int,
) -> BloggerAdaptBillingResult:
    """Проверка 3💎 → списание → OpenRouter; при сбое API — возврат кристаллов."""
    from services.billing import refund_charge
    from services.billing.blogger_pipeline import can_afford_blogger_adapt, spend_blogger_adapt
    from services.god_mode import billing_bypass

    body = (source_body or "").strip()
    if not body:
        return BloggerAdaptBillingResult(ok=False, error="empty_body")

    if not is_valid_adapt_platform(platform):
        return BloggerAdaptBillingResult(ok=False, error="invalid_platform")

    if not billing_bypass(user_id) and not await can_afford_blogger_adapt(user_id):
        return BloggerAdaptBillingResult(ok=False, error="insufficient_crystals")

    charge_id: str | None = None
    if not billing_bypass(user_id):
        spend = await spend_blogger_adapt(user_id)
        if not spend.ok:
            return BloggerAdaptBillingResult(ok=False, error="insufficient_crystals")
        charge_id = spend.charge.charge_id if spend.charge else None

    content = await adapt_blogger_post_body(
        settings,
        source_body=body,
        platform=platform,
    )
    if not content:
        if charge_id:
            await refund_charge(charge_id)
        return BloggerAdaptBillingResult(ok=False, error="api_failed")

    return BloggerAdaptBillingResult(ok=True, content=content)
