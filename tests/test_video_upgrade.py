"""Тесты апгрейда видео-конвейера: Prompt Enhancer, кеш последнего видео,
клавиатура и сообщения авто-рефанда."""

from __future__ import annotations

import pytest

from content import messages as msg
from content.video_menu import (
    CB_VIDEO_EXTEND,
    CB_VIDEO_REGENERATE,
    CB_VIDEO_UPSCALE,
    VIDEO_REGENERATE_COST,
    VIDEO_UPSCALE_COST,
    result_video_keyboard_pro,
)
from services import last_video_request
from services.billing import translator as tr


def test_cinematic_keywords_present() -> None:
    assert "cinematic" in tr.CINEMATIC_KEYWORDS
    assert "8k" in tr.CINEMATIC_KEYWORDS
    assert "photorealistic" in tr.CINEMATIC_KEYWORDS


def test_append_cinematic_adds_keywords_once() -> None:
    base = "A cat on a windowsill"
    out = tr._append_cinematic(base)
    assert tr._looks_already_enhanced(out)
    # Идемпотентность: повторный вызов не дублирует
    out2 = tr._append_cinematic(out)
    assert out2 == out


def test_looks_already_enhanced_detects_keywords() -> None:
    assert tr._looks_already_enhanced(
        "Wide shot, cinematic lighting, hyper-realistic, 8k, photorealistic"
    )
    assert not tr._looks_already_enhanced("Просто кот на подоконнике")


@pytest.mark.asyncio
async def test_enhance_video_prompt_uses_fallback_without_openrouter(
    monkeypatch,
) -> None:
    """Без OpenRouter — Prompt Enhancer возвращает текст + cinematic ключи."""

    class _DummySettings:
        openrouter_key = ""
        openrouter_timeout_sec = 30

    out = await tr.enhance_video_prompt_for_replicate(
        _DummySettings(), "Кот на подоконнике"
    )
    assert tr._looks_already_enhanced(out)


@pytest.mark.asyncio
async def test_enhance_video_prompt_empty_passthrough() -> None:
    class _DummySettings:
        openrouter_key = ""
        openrouter_timeout_sec = 30

    assert await tr.enhance_video_prompt_for_replicate(_DummySettings(), "") == ""
    assert await tr.enhance_video_prompt_for_replicate(_DummySettings(), "   ") == ""


@pytest.mark.asyncio
async def test_enhance_video_prompt_skips_already_enhanced() -> None:
    class _DummySettings:
        openrouter_key = "sk-test"
        openrouter_timeout_sec = 30

    already = "Wide shot, cinematic lighting, hyper-realistic, 8k, photorealistic"
    out = await tr.enhance_video_prompt_for_replicate(_DummySettings(), already)
    assert out == already


def test_result_video_keyboard_pro_has_three_upsell_buttons() -> None:
    kb = result_video_keyboard_pro()
    cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert CB_VIDEO_EXTEND in cbs
    assert CB_VIDEO_UPSCALE in cbs
    assert CB_VIDEO_REGENERATE in cbs
    # Цены проступают в подписях для прозрачности
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any(f"{VIDEO_UPSCALE_COST} 💎" in t for t in labels)
    assert any(f"{VIDEO_REGENERATE_COST} 💎" in t for t in labels)


def test_last_video_request_remember_and_get() -> None:
    uid = 7777001
    last_video_request.clear(uid)
    assert last_video_request.get(uid) is None
    last_video_request.remember(
        uid, scenario_id="video_pro_5sec", prompt="Кот в кино", file_id=" "
    )
    rec = last_video_request.get(uid)
    assert rec is not None
    assert rec.scenario_id == "video_pro_5sec"
    assert rec.prompt == "Кот в кино"
    assert rec.file_id is None  # пустой строкой не сохраняем


def test_last_video_request_ignores_empty_scenario() -> None:
    uid = 7777002
    last_video_request.clear(uid)
    last_video_request.remember(uid, scenario_id="")
    assert last_video_request.get(uid) is None


def test_messages_for_video_upgrades_exist() -> None:
    assert "<b>" in msg.TXT_VIDEO_REPLICATE_FAILED  # HTML-ready
    assert "вернули списанные" in msg.TXT_VIDEO_REPLICATE_FAILED
    assert "NeuroMule" in msg.TXT_VIDEO_UPSCALE_SOON
    assert msg.TXT_VIDEO_REGENERATE_NO_HISTORY
    assert msg.TXT_VIDEO_REGENERATE_FAILED
