"""Сборка контекста для чата: system + память + последние N реплик по лимиту."""

from __future__ import annotations

from config import Settings
from services import conversation as conv


async def test_build_openrouter_messages_includes_memory_and_history_window(repo_module):
    uid = 72001
    settings = Settings().model_copy(update={"bot_name": "TestBot", "chat_history_limit": 2})

    await repo_module.ensure_user(uid)
    await repo_module.set_persistent_memory(uid, "Пользователь любит чай")

    await repo_module.dialog_append(uid, "user", "первое")
    await repo_module.dialog_append(uid, "assistant", "ответ1")
    await repo_module.dialog_append(uid, "user", "второе")
    await repo_module.dialog_append(uid, "assistant", "ответ2")
    await repo_module.dialog_append(uid, "user", "третье")

    messages = await conv.build_openrouter_messages(settings, uid)

    assert len(messages) == 1 + 2
    assert messages[0]["role"] == "system"
    assert "чай" in messages[0]["content"]

    assert messages[1] == {"role": "assistant", "content": "ответ2"}
    assert messages[2] == {"role": "user", "content": "третье"}


async def test_build_openrouter_messages_empty_dialog_only_system(repo_module):
    uid = 72002
    settings = Settings().model_copy(update={"chat_history_limit": 10})

    await repo_module.ensure_user(uid)
    messages = await conv.build_openrouter_messages(settings, uid)

    assert len(messages) == 1
    assert messages[0]["role"] == "system"
    assert "[USER_PERSISTENT_MEMORY]" not in messages[0]["content"]
