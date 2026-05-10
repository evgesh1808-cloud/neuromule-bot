"""Сброс диалога и persistent_memory (аналог /reset без Telegram)."""

from __future__ import annotations


async def test_clear_user_dialog_and_memory(repo_module):
    uid = 73001

    await repo_module.ensure_user(uid)
    await repo_module.set_persistent_memory(uid, "запомнить это")
    await repo_module.dialog_append(uid, "user", "привет")
    await repo_module.dialog_append(uid, "assistant", "здравствуй")

    assert await repo_module.dialog_total_messages(uid) == 2
    assert await repo_module.get_persistent_memory(uid) == "запомнить это"

    await repo_module.clear_user_dialog_and_memory(uid)

    assert await repo_module.dialog_total_messages(uid) == 0
    assert await repo_module.get_persistent_memory(uid) is None
