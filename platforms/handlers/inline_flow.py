"""Inline Mode NeuroMule 🐎⚡️ — вирусный конвейер ``@NeuroMule_bot <запрос>``.

Юзер пишет ``@NeuroMule_bot ...`` в любом чате Telegram. Бот:

1. Жёстко режет FREE-юзеров (без ULTRA-семьи) до списания, отдавая статью
   «🔒 Доступ ограничен» с deep-link на активацию тарифа.
2. Платных билит через ``billing.handle_text_chat(role="standard")`` —
   ровно 1 ⚡ или 1 💎 по приоритету ``sub → buy`` (см. ``store.atomic_spend``).
3. Гонит запрос в самую быструю mini-модель ``settings.free_text_model``
   с SLA ≤ 4 секунд (``asyncio.wait_for``). При сбое OpenRouter — рефанд
   через ``store.refund_charge``.
4. К каждому ответу пришивает вирусную HTML-подпись и одну inline-кнопку
   с deep-link на бота → ОПФ-paywall (подписка на канал) уже сам сработает
   внутри ``run_start_turn``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from config import settings as app_settings
from content import messages as msg
from services import last_share_media
from services.ai_text import ask_ai_messages
from services.billing import billing
from services.billing.store import load_user_billing, refund_charge
from services.billing.types import TariffTier
from services.family_sharing import resolve_duo_owner

logger = logging.getLogger(__name__)

router = Router(name="inline_flow")

INLINE_AI_TIMEOUT_SEC: float = 4.0
INLINE_MAX_QUERY_LEN: int = 400
INLINE_CACHE_TIME: int = 0  # каждый запрос свежий, без публичного кэша


# ─── helpers ───────────────────────────────────────────────────────────────


def _bot_deep_link(payload: str = "inline_ref") -> str:
    username = (app_settings.telegram_bot_username or "NeuroMule_bot").lstrip("@")
    return f"https://t.me/{username}?start={payload}"


def _inline_referral_keyboard() -> InlineKeyboardMarkup:
    """Одна вирусная кнопка под inline-ответом → активация ОПФ-paywall."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_INLINE_RESULT_BTN,
                    url=_bot_deep_link("inline_ref"),
                )
            ]
        ]
    )


def _result_id(user_id: int, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    return f"nm:{user_id}:{digest}"


def _build_article(
    *,
    result_id: str,
    title: str,
    description: str,
    message_text: str,
    with_referral_keyboard: bool = True,
) -> InlineQueryResultArticle:
    return InlineQueryResultArticle(
        id=result_id,
        title=title,
        description=description,
        input_message_content=InputTextMessageContent(
            message_text=message_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        ),
        reply_markup=_inline_referral_keyboard() if with_referral_keyboard else None,
    )


async def _is_duo_partner(uid: int) -> bool:
    try:
        owner_id = await resolve_duo_owner(uid)
    except Exception:  # pragma: no cover
        logger.warning("inline: resolve_duo_owner failed uid=%s", uid, exc_info=True)
        return False
    return owner_id != uid


async def _generate_inline_answer(query_text: str) -> str:
    """Запрос в самую быструю mini-модель из ``settings.free_text_model``.

    Жёсткий тайм-аут ``INLINE_AI_TIMEOUT_SEC`` (Telegram режет inline_query
    ответ на ~10 секундах, нам нужно прислать значительно быстрее).
    """

    fast_model = (app_settings.free_text_model or "").strip()
    models = [fast_model] if fast_model else None

    payload: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "Ты — NeuroMule 🐎⚡️, краткий и точный AI-ассистент в "
                "inline-режиме Telegram. Отвечай по делу, без лишней воды, "
                "максимум 700 символов. Используй простой HTML (<b>, <i>, "
                "<code>) только при необходимости, без <script>, без <pre> "
                "длиннее 30 строк. Никаких ссылок и скрытых символов."
            ),
        },
        {"role": "user", "content": query_text},
    ]

    return await asyncio.wait_for(
        ask_ai_messages(
            app_settings,
            payload,
            timeout=INLINE_AI_TIMEOUT_SEC,
            models=models,
        ),
        timeout=INLINE_AI_TIMEOUT_SEC + 0.5,
    )


# ─── stubs (заглушки в результатах inline) ─────────────────────────────────


def _stub_empty(user_id: int) -> list[InlineQueryResultArticle]:
    return [
        _build_article(
            result_id=f"nm:{user_id}:empty",
            title=msg.TXT_INLINE_EMPTY_TITLE,
            description=msg.TXT_INLINE_EMPTY_DESCRIPTION,
            message_text=(
                "💡 <b>Inline-режим NeuroMule 🐎⚡️</b>\n\n"
                "Напиши запрос после <code>@NeuroMule_bot</code> — и ИИ "
                "ответит прямо в этот чат."
            ),
        )
    ]


def _stub_free_lock(user_id: int) -> list[InlineQueryResultArticle]:
    return [
        _build_article(
            result_id=f"nm:{user_id}:lock",
            title=msg.TXT_INLINE_FREE_LOCK_TITLE,
            description=msg.TXT_INLINE_FREE_LOCK_DESCRIPTION,
            message_text=msg.TXT_INLINE_FREE_LOCK_MESSAGE,
        )
    ]


def _stub_insufficient(user_id: int) -> list[InlineQueryResultArticle]:
    return [
        _build_article(
            result_id=f"nm:{user_id}:nores",
            title=msg.TXT_INLINE_INSUFFICIENT_TITLE,
            description=msg.TXT_INLINE_INSUFFICIENT_DESCRIPTION,
            message_text=msg.TXT_INLINE_INSUFFICIENT_MESSAGE,
        )
    ]


def _stub_ai_failed(user_id: int, query_text: str) -> list[InlineQueryResultArticle]:
    return [
        _build_article(
            result_id=_result_id(user_id, "fail:" + query_text),
            title=msg.TXT_INLINE_AI_FAILED_TITLE,
            description=msg.TXT_INLINE_AI_FAILED_DESCRIPTION,
            message_text=msg.TXT_INLINE_AI_FAILED_MESSAGE,
        )
    ]


# ─── handler ───────────────────────────────────────────────────────────────


GET_MEDIA_PREFIX = "get_media_"


def _share_media_article(
    entry: last_share_media.ShareMediaEntry, user_id: int
) -> list[InlineQueryResultArticle]:
    """Карточка-инвойс для switch_inline_query=get_media_<task_id>.

    Открывается прямо в чате друга — без загрузки бинарных медиа (Telegram
    inline-результаты могут передавать только результат типа Article/Photo
    /Video с готовым URL, и нам важно не сливать file_id чужому чату).
    """

    username = (app_settings.telegram_bot_username or "NeuroMule_bot").lstrip("@")
    invite_url = f"https://t.me/{username}?start=share_{entry.task_id}"
    label = {
        "photo": "🎨 ИИ-фото",
        "video": "🎬 ИИ-видео",
        "animate": "✨ Оживление",
        "music": "🎸 ИИ-трек",
    }.get(entry.task_type, "🤖 Шедевр NeuroMule")
    prompt = entry.prompt[:200] or "Шедевр NeuroMule 🐎⚡️"
    body = (
        f"🚀 <b>Смотри, что я создал в NeuroMule 🐎⚡️</b>\n\n"
        f"{label}: <i>{prompt}</i>\n\n"
        f"⚡ Создай свой: {invite_url}"
    )
    return [
        _build_article(
            result_id=f"share:{entry.task_id}",
            title=f"{label} → отправить другу",
            description=prompt[:90],
            message_text=body,
            with_referral_keyboard=False,
        )
    ]


@router.inline_query(F.query.startswith(GET_MEDIA_PREFIX))
async def inline_share_media(query: InlineQuery) -> None:
    """Виральный share-в-ЛС: ``switch_inline_query=get_media_<task_id>``."""

    raw = (query.query or "").strip()
    suffix = raw[len(GET_MEDIA_PREFIX):]
    user_id = query.from_user.id

    entry = (
        last_share_media.get_by_task(suffix)
        if suffix and suffix != "last"
        else last_share_media.get_by_user(user_id)
    )
    if entry is None:
        await query.answer(
            results=_stub_empty(user_id),
            cache_time=INLINE_CACHE_TIME,
            is_personal=True,
        )
        return

    await query.answer(
        results=_share_media_article(entry, user_id),
        cache_time=INLINE_CACHE_TIME,
        is_personal=True,
    )


@router.inline_query()
async def inline_query_handler(query: InlineQuery) -> None:
    user_id = query.from_user.id
    text = (query.query or "").strip()[:INLINE_MAX_QUERY_LEN]

    if not text:
        await query.answer(
            results=_stub_empty(user_id),
            cache_time=INLINE_CACHE_TIME,
            is_personal=True,
        )
        return

    # 1. Гард тарифа: FREE без ULTRA-семьи → жёсткая заглушка.
    state = await load_user_billing(user_id)
    is_duo_partner = await _is_duo_partner(user_id)
    if state.current_tariff is TariffTier.FREE and not is_duo_partner:
        await query.answer(
            results=_stub_free_lock(user_id),
            cache_time=INLINE_CACHE_TIME,
            is_personal=True,
            switch_pm_text=msg.TXT_INLINE_FREE_LOCK_TITLE,
            switch_pm_parameter="inline_lock",
        )
        return

    # 2. Биллинг: 1 ⚡ → fallback 1 💎 (sub → buy внутри atomic_spend).
    plan, charge_id = await billing.handle_text_chat(user_id, role_type="standard")
    if plan.blocked or not charge_id:
        await query.answer(
            results=_stub_insufficient(user_id),
            cache_time=INLINE_CACHE_TIME,
            is_personal=True,
            switch_pm_text=msg.TXT_INLINE_INSUFFICIENT_TITLE,
            switch_pm_parameter="inline_topup",
        )
        return

    # 3. Быстрый ответ от mini-модели + рефанд при сбое.
    try:
        ai_answer = await _generate_inline_answer(text)
    except Exception as exc:
        logger.warning("inline: AI failed uid=%s err=%s", user_id, exc)
        try:
            await refund_charge(charge_id)
        except Exception:  # pragma: no cover
            logger.exception("inline: refund_charge failed charge_id=%s", charge_id)
        await query.answer(
            results=_stub_ai_failed(user_id, text),
            cache_time=INLINE_CACHE_TIME,
            is_personal=True,
        )
        return

    final_message = (ai_answer or "").strip()
    if not final_message:
        try:
            await refund_charge(charge_id)
        except Exception:  # pragma: no cover
            logger.exception("inline: refund_charge failed charge_id=%s", charge_id)
        await query.answer(
            results=_stub_ai_failed(user_id, text),
            cache_time=INLINE_CACHE_TIME,
            is_personal=True,
        )
        return

    final_message += msg.TXT_INLINE_VIRAL_FOOTER

    article = _build_article(
        result_id=_result_id(user_id, text),
        title="⚡ Ответ NeuroMule готов",
        description=text[:100],
        message_text=final_message,
    )
    await query.answer(
        results=[article],
        cache_time=INLINE_CACHE_TIME,
        is_personal=True,
    )


__all__ = (
    "router",
    "INLINE_AI_TIMEOUT_SEC",
    "INLINE_MAX_QUERY_LEN",
)
