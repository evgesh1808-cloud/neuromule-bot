"""Тесты адаптации поста блогера под площадки СНГ."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from content import messages as msg
from services import blogger_post_cache
from services.blogger_adaptation import (
    BLOGGER_ADAPT_ROUTES,
    adapt_blogger_post_body,
    parse_adapt_target,
    prepare_adapted_telegram_html,
    sanitize_adapt_model_output,
)
from services.billing.pricing import FREE_CHAT_MODEL


def test_parse_adapt_target_valid() -> None:
    assert parse_adapt_target(msg.CB_ADAPT_TARGET_VIDEO) == ("video", None)
    assert parse_adapt_target("adapt_target:vc:abc12345") == ("vc", "abc12345")
    assert parse_adapt_target(msg.CB_ADAPT_TARGET_VK) == ("vk", None)
    assert parse_adapt_target("adapt_target:vk:deadbeef") == ("vk", "deadbeef")
    assert parse_adapt_target(msg.CB_ADAPT_TARGET_TG_MAX) == ("tg_max", None)
    assert parse_adapt_target("adapt_target:unknown") is None


def test_blogger_adapt_routes_use_free_chat_model() -> None:
    for route in BLOGGER_ADAPT_ROUTES:
        assert route.models == (FREE_CHAT_MODEL,)


def test_sanitize_adapt_model_output_strips_preamble_and_fence() -> None:
    raw = 'Вот ваш пост:\n```\n<b>Заголовок</b>\nТекст поста\n```'
    cleaned = sanitize_adapt_model_output(raw)
    assert "Вот ваш пост" not in cleaned
    assert "<b>Заголовок</b>" in cleaned
    assert "```" not in cleaned


def test_prepare_adapted_telegram_html_repairs_markdown_and_closes_b() -> None:
    raw = "**жирный** тезис и <b>незакрытый"
    html = prepare_adapted_telegram_html(raw)
    assert "<b>жирный</b>" in html
    assert html.count("<b>") == html.lower().count("</b>")


@pytest.mark.asyncio
async def test_adapt_blogger_post_body_reads_dict_content() -> None:
    from services.blogger_adaptation import adapt_blogger_post_body

    mock_result = {"content": "Готовый пост для VK", "prompt_tokens": 0, "completion_tokens": 0}
    with patch(
        "services.blogger_adaptation.ask_ai_messages",
        AsyncMock(return_value=mock_result),
    ):
        out = await adapt_blogger_post_body(
            type("S", (), {})(),
            source_body="Исходный текст поста",
            platform="vk",
        )
    assert out == "Готовый пост для VK"


@pytest.mark.asyncio
async def test_cb_blogger_adapt_target_sends_result() -> None:
    from platforms.blogger_flow import cb_blogger_adapt_target

    sample = """===ТЕЛО ПОСТА===
Тестовое тело поста для адаптации."""
    post_id = blogger_post_cache.remember(701, sample)
    await blogger_post_cache.bind_telegram_message(post_id, 701, chat_id=1, message_id=20)

    callback = MagicMock()
    callback.from_user.id = 701
    callback.data = f"adapt_target:vk:{post_id}"
    callback.message.chat.id = 1
    callback.message.message_id = 20
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()

    adapt_result = type(
        "AdaptResult",
        (),
        {"ok": True, "content": "<b>VK пост</b> готов", "error": ""},
    )()

    with (
        patch("platforms.blogger_flow.billing_bypass", return_value=True),
        patch(
            "platforms.blogger_flow.adapt_blogger_post_with_billing",
            AsyncMock(return_value=adapt_result),
        ),
    ):
        await cb_blogger_adapt_target(callback)

    callback.answer.assert_awaited_once_with(msg.TXT_BLOGGER_ADAPT_QUEUED)
    callback.message.answer.assert_awaited_once()
    sent_text = callback.message.answer.await_args.args[0]
    assert "VK пост" in sent_text


@pytest.mark.asyncio
async def test_cb_blogger_adapt_target_display_html_cache() -> None:
    """Черновик из display-HTML (fallback кэша) тоже адаптируется."""
    from platforms.blogger_flow import cb_blogger_adapt_target
    from services.blogger_post_parser import format_blogger_display_html, normalize_blogger_raw_output

    raw = """===ТЕЛО ПОСТА===
Текст поста из display-кэша для VK."""
    display = format_blogger_display_html(normalize_blogger_raw_output(raw))
    post_id = blogger_post_cache.remember(702, display)
    await blogger_post_cache.bind_telegram_message(post_id, 702, chat_id=2, message_id=30)

    callback = MagicMock()
    callback.from_user.id = 702
    callback.data = f"adapt_target:video:{post_id}"
    callback.message.chat.id = 2
    callback.message.message_id = 30
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()

    adapt_result = type(
        "AdaptResult",
        (),
        {"ok": True, "content": "Reels сценарий готов", "error": ""},
    )()

    with (
        patch("platforms.blogger_flow.billing_bypass", return_value=True),
        patch(
            "platforms.blogger_flow.adapt_blogger_post_with_billing",
            AsyncMock(return_value=adapt_result),
        ),
    ):
        await cb_blogger_adapt_target(callback)

    callback.answer.assert_awaited_once_with(msg.TXT_BLOGGER_ADAPT_QUEUED)
    callback.message.answer.assert_awaited_once()

