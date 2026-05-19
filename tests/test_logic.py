"""
Проверки качества production-логики: списание энергии за чат, сброс истории (аналог /reset).

База — изолированная SQLite (фикстура ``repo_module``).
"""

from __future__ import annotations

import httpx
import pytest

from config import Settings
from services.repository import clear_user_dialog_and_memory
from services.use_cases.chat_turn import ChatTurnOutcome, run_chat_turn


@pytest.mark.asyncio
async def test_chat_consumes_energy_on_successful_completion(repo_module):
    """После успешного ответа модели списывается 1 ⚡ (стандартный чат)."""
    uid = 88001
    await repo_module.ensure_user(uid)
    s = Settings().model_copy(
        update={
            "free_models": ["stub-model"],
            "openrouter_key": "dummy",
            "cost_text_pro": 1,
            "telegram_chat_streaming": False,
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Короткий ответ."}}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        before = (await repo_module.get_user_row(uid)).energy
        result = await run_chat_turn(s, uid, "вопрос", http_client=client)
        after = (await repo_module.get_user_row(uid)).energy

    assert result.outcome is ChatTurnOutcome.SUCCESS
    assert after == before - 1


@pytest.mark.asyncio
async def test_reset_clears_dialog_and_persistent_memory(repo_module):
    """Сброс диалога и памяти: таблица сообщений пуста, ``persistent_memory`` сброшена в NULL."""
    uid = 88002
    await repo_module.ensure_user(uid)
    await repo_module.set_persistent_memory(uid, "тестовая память")
    await repo_module.dialog_append(uid, "user", "сообщение")
    await repo_module.dialog_append(uid, "assistant", "ответ")

    assert await repo_module.dialog_total_messages(uid) == 2
    assert await repo_module.get_persistent_memory(uid) == "тестовая память"

    await clear_user_dialog_and_memory(uid)

    assert await repo_module.dialog_total_messages(uid) == 0
    assert await repo_module.get_persistent_memory(uid) is None
