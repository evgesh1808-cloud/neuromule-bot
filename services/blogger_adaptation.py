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
    SYSTEM_ADAPT_META,
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

PLATFORM_VIDEO = msg.PLATFORM_VIDEO
PLATFORM_VC = msg.PLATFORM_VC
PLATFORM_VK = msg.PLATFORM_VK
PLATFORM_TG_MAX = msg.PLATFORM_TG_MAX
PLATFORM_META = msg.PLATFORM_META
_ADAPT_TARGETS: frozenset[str] = frozenset(
    {PLATFORM_VIDEO, PLATFORM_VC, PLATFORM_VK, PLATFORM_TG_MAX, PLATFORM_META}
)
# Старые callback/legacy-ключи → канонический platform key
_ADAPT_PLATFORM_ALIASES: dict[str, str] = {
    "vc": PLATFORM_VC,
    "vk": PLATFORM_VK,
    "twitter": PLATFORM_VK,
    "tg_max": PLATFORM_TG_MAX,
    "tg": PLATFORM_TG_MAX,
    "meta": PLATFORM_META,
    "facebook": PLATFORM_META,
    "instagram": PLATFORM_META,
    "fb": PLATFORM_META,
    "ig": PLATFORM_META,
}

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
        key=PLATFORM_VIDEO,
        label=msg.PLATFORM_LABEL_VIDEO,
        callback_data=msg.CB_ADAPT_TARGET_VIDEO,
        button_text=msg.BTN_BLOGGER_ADAPT_VIDEO,
        system_prompt=SYSTEM_ADAPT_VIDEO,
        models=(FREE_CHAT_MODEL,),
        temperature=0.4,
        max_tokens=1600,
    ),
    BloggerAdaptRoute(
        key=PLATFORM_VC,
        label=msg.PLATFORM_LABEL_VC,
        callback_data=msg.CB_ADAPT_TARGET_VC,
        button_text=msg.BTN_BLOGGER_ADAPT_VC,
        system_prompt=SYSTEM_ADAPT_VC,
        models=(FREE_CHAT_MODEL,),
        temperature=0.3,
        max_tokens=2800,
    ),
    BloggerAdaptRoute(
        key=PLATFORM_VK,
        label=msg.PLATFORM_LABEL_VK,
        callback_data=msg.CB_ADAPT_TARGET_VK,
        button_text=msg.BTN_BLOGGER_ADAPT_VK,
        system_prompt=SYSTEM_ADAPT_VK,
        models=(FREE_CHAT_MODEL,),
        temperature=0.4,
        max_tokens=1600,
    ),
    BloggerAdaptRoute(
        key=PLATFORM_TG_MAX,
        label=msg.PLATFORM_LABEL_TG_MAX,
        callback_data=msg.CB_ADAPT_TARGET_TG_MAX,
        button_text=msg.BTN_BLOGGER_ADAPT_TG_MAX,
        system_prompt=SYSTEM_ADAPT_TG_MAX,
        models=(FREE_CHAT_MODEL,),
        temperature=0.3,
        max_tokens=800,
    ),
    BloggerAdaptRoute(
        key=PLATFORM_META,
        label=msg.PLATFORM_LABEL_META,
        callback_data=msg.CB_ADAPT_TARGET_META,
        button_text=msg.BTN_BLOGGER_ADAPT_META,
        system_prompt=SYSTEM_ADAPT_META,
        models=(FREE_CHAT_MODEL,),
        temperature=0.35,
        max_tokens=1600,
    ),
)

_ROUTE_BY_KEY: dict[str, BloggerAdaptRoute] = {route.key: route for route in BLOGGER_ADAPT_ROUTES}


def normalize_adapt_platform(platform: str) -> str:
    """Канонический ключ площадки адаптации (с учётом legacy-алиасов)."""
    key = (platform or "").strip().lower()
    return _ADAPT_PLATFORM_ALIASES.get(key, key)


def get_adaptation_prompt(target_platform: str) -> str:
    """System-промпт для площадки; fallback — Telegram / МАКС."""
    route = _ROUTE_BY_KEY.get(normalize_adapt_platform(target_platform))
    if route is not None:
        return route.system_prompt
    return SYSTEM_ADAPT_TG_MAX


def get_blogger_adapt_keyboard(post_id: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора площадки при нажатии «🔄 Адаптировать».

    Раскладка под смартфон: 1 + 2 + 2 + «Назад».
    Callback-контракт: ``adapt_target:<platform>:<post_id>``, назад — ``blog_back:``.
    """
    pid = (post_id or "").strip()
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=msg.BTN_BLOGGER_ADAPT_VIDEO,
            callback_data=build_adapt_target_callback(PLATFORM_VIDEO, pid),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=msg.BTN_BLOGGER_ADAPT_TG_MAX,
            callback_data=build_adapt_target_callback(PLATFORM_TG_MAX, pid),
        ),
        InlineKeyboardButton(
            text=msg.BTN_BLOGGER_ADAPT_VK,
            callback_data=build_adapt_target_callback(PLATFORM_VK, pid),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=msg.BTN_BLOGGER_ADAPT_META,
            callback_data=build_adapt_target_callback(PLATFORM_META, pid),
        ),
        InlineKeyboardButton(
            text=msg.BTN_BLOGGER_ADAPT_VC,
            callback_data=build_adapt_target_callback(PLATFORM_VC, pid),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=msg.BTN_BLOGGER_ADAPT_BACK,
            callback_data=f"{msg.CB_BLOG_BACK_PREFIX}{pid}",
        )
    )
    return builder.as_markup()


# Алиас имени из продуктового ТЗ
get_blogger_adaptation_keyboard = get_blogger_adapt_keyboard


def build_adapt_target_callback(platform: str, post_id: str) -> str:
    """``adapt_target:<platform>:<post_id>`` — post_id в callback для надёжного резолва."""
    return f"{msg.CB_ADAPT_TARGET_PREFIX}{platform.strip().lower()}:{post_id.strip()}"


def parse_adapt_target(data: str) -> tuple[str, str | None] | None:
    """Из ``adapt_target:video:<post_id>`` или legacy ``adapt_target:video``."""
    prefix = msg.CB_ADAPT_TARGET_PREFIX
    if not (data or "").startswith(prefix):
        return None
    rest = data[len(prefix) :].strip().lower()
    if ":" in rest:
        platform, post_id = rest.split(":", 1)
        platform = normalize_adapt_platform(platform.strip())
        post_id = post_id.strip()
        if platform in _ADAPT_TARGETS and post_id:
            return platform, post_id
    platform = normalize_adapt_platform(rest)
    if platform in _ADAPT_TARGETS:
        return platform, None
    return None


def adapt_platform_label(platform: str) -> str:
    route = _ROUTE_BY_KEY.get(normalize_adapt_platform(platform))
    return route.label if route else platform.upper()


def is_valid_adapt_platform(platform: str) -> bool:
    return normalize_adapt_platform(platform) in _ADAPT_TARGETS


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

    route = _ROUTE_BY_KEY.get(normalize_adapt_platform(platform))
    if route is None:
        return None

    messages = [
        {"role": "system", "content": get_adaptation_prompt(route.key)},
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

    content = sanitize_adapt_model_output(result.get("content", ""))
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
