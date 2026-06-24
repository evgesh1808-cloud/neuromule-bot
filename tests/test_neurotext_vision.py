"""Тесты multimodal Нейротекста (фото → OpenRouter)."""

from __future__ import annotations

import base64
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services.ai_text import _messages_contain_image, ask_ai_messages
from services.neurotext_media import build_openrouter_user_content
from services.use_cases.chat_turn import run_chat_turn, ChatTurnOutcome
from tests.conftest import TEST_ADMIN_IDS


def test_build_openrouter_user_content_text_only() -> None:
    assert build_openrouter_user_content("Привет") == "Привет"


def test_build_openrouter_user_content_multimodal() -> None:
    content = build_openrouter_user_content(
        "Что на фото?",
        image_data_url="data:image/jpeg;base64,abc",
    )
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "Что на фото?"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,abc"


def test_messages_contain_image() -> None:
    assert _messages_contain_image(
        [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]
    )
    assert not _messages_contain_image([{"role": "user", "content": "текст"}])


@pytest.mark.asyncio
async def test_telegram_photo_to_data_url() -> None:
    from services.neurotext_media import telegram_photo_to_data_url

    bot = AsyncMock()
    bot.get_file.return_value = SimpleNamespace(file_path="photos/file.jpg")
    payload = b"\xff\xd8\xff fake jpeg"

    async def _download(_path, destination):
        destination.write(payload)

    bot.download_file.side_effect = _download
    photo = SimpleNamespace(file_id="ph1", file_size=len(payload))
    url = await telegram_photo_to_data_url(bot, photo)
    assert url.startswith("data:image/jpeg;base64,")
    decoded = base64.standard_b64decode(url.split(",", 1)[1])
    assert decoded == payload


@pytest.mark.asyncio
async def test_ask_ai_messages_multimodal_payload(monkeypatch) -> None:
    from config import Settings

    s = Settings(tg_token="x", openrouter_key="y", gemini_api_key="z")
    captured: dict = {}

    async def _fake_post(client, settings, model, messages, *, timeout, max_tokens=None, response_format=None):
        captured["messages"] = messages
        captured["model"] = model
        return {"content": "vision ok", "prompt_tokens": 0, "completion_tokens": 0}

    with patch("services.ai_text._post_chat_completion", new=AsyncMock(side_effect=_fake_post)):
        messages = [
            {"role": "system", "content": "sys"},
            {
                "role": "user",
                "content": build_openrouter_user_content(
                    "Опиши",
                    image_data_url="data:image/png;base64,QQ==",
                ),
            },
        ]
        out = await ask_ai_messages(s, messages, models=["google/gemini-2.5-flash"])
        assert out["content"] == "vision ok"
        user_content = captured["messages"][1]["content"]
        assert isinstance(user_content, list)
        assert user_content[1]["type"] == "image_url"


@pytest.mark.asyncio
async def test_run_chat_turn_with_photo_billing_bypass(repo_module, monkeypatch) -> None:
    from config import Settings, settings as app_settings

    uid = TEST_ADMIN_IDS[0]
    await repo_module.ensure_user(uid)
    object.__setattr__(app_settings, "god_mode_enabled", True)

    fake_plan = SimpleNamespace(
        blocked=False,
        block_reason="",
        model_id="google/gemini-2.5-flash",
        max_tokens=640,
        use_premium_prompt=True,
    )
    billing_result = SimpleNamespace(
        plan=fake_plan,
        charge_id="god_mode_skip",
        effective_role_id="psychologist",
        notice=None,
    )
    captured_messages: list = []

    async def _fake_ask(_settings, messages, **kwargs):
        captured_messages.extend(messages)
        return {"content": "Ответ по фото", "prompt_tokens": 0, "completion_tokens": 0}

    with patch(
        "services.use_cases.chat_turn.allow_request",
        new=AsyncMock(return_value=True),
    ), patch(
        "services.use_cases.chat_turn.billing.resolve_and_charge_text_chat",
        new=AsyncMock(return_value=billing_result),
    ), patch(
        "services.use_cases.chat_turn.dialog_append",
        new=AsyncMock(),
    ), patch(
        "services.use_cases.chat_turn.conv.build_openrouter_messages",
        new=AsyncMock(
            return_value=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "placeholder"},
            ]
        ),
    ), patch(
        "services.use_cases.chat_turn.ask_ai_messages",
        new=AsyncMock(side_effect=_fake_ask),
    ), patch(
        "services.use_cases.chat_turn.commit_assistant_turn_queued",
        new=AsyncMock(),
    ), patch(
        "services.use_cases.chat_turn.conv.schedule_memory_refresh",
    ):
        s = Settings(tg_token="x", openrouter_key="y", gemini_api_key="z")
        result = await run_chat_turn(
            s,
            uid,
            "Что на фото?",
            dialog_user_text="[📷 Фото]",
            user_image_data_url="data:image/jpeg;base64,ZmFrZQ==",
            text_role="psychologist",
        )
        assert result.outcome is ChatTurnOutcome.SUCCESS
        last_user = captured_messages[-1]["content"]
        assert isinstance(last_user, list)
        assert any(p.get("type") == "image_url" for p in last_user)
