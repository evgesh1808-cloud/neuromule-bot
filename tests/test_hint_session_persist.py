"""HintSession L1 RAM + L2 SQLite: переживает «рестарт» (очистку RAM)."""

from __future__ import annotations

import pytest

from services.standard_suggested_replies import (
    _HINT_SESSIONS,
    bind_hint_session_message_persisted,
    clear_hint_sessions_persisted_for_tests,
    clear_suggested_replies_for_tests,
    create_hint_session_persisted,
    get_hint_session,
    resolve_hint_session,
)


@pytest.mark.asyncio
async def test_hint_session_survives_ram_clear_via_sqlite(repo_module) -> None:
    """Симуляция pm2 restart: L1 пуст → hydrate из SQLite → снова в RAM."""
    _ = repo_module  # гарантирует init_db / таблицу hint_sessions
    await clear_hint_sessions_persisted_for_tests()

    action_uuid = await create_hint_session_persisted(
        42,
        body="1. Разминка 10 минут.\n2. Стойки.",
        labels=["Про разминку?", "Про стойки?"],
        root_user_prompt="Как начать тхэквондо?",
        message_id=None,
    )
    await bind_hint_session_message_persisted(action_uuid, 9001)

    # «Рестарт процесса»: только L1 очищен, SQLite цел.
    clear_suggested_replies_for_tests()
    assert action_uuid not in _HINT_SESSIONS
    assert get_hint_session(action_uuid, user_id=42) is None

    restored = await resolve_hint_session(action_uuid, user_id=42)
    assert restored is not None
    assert restored.user_id == 42
    assert restored.message_id == 9001
    assert restored.root_user_prompt == "Как начать тхэквондо?"
    assert "Разминка" in restored.body
    assert "Про разминку?" in restored.labels

    # Hydrate: повторный get уже из RAM.
    assert get_hint_session(action_uuid, user_id=42) is restored
    assert await resolve_hint_session(action_uuid, user_id=99) is None

    await clear_hint_sessions_persisted_for_tests()


@pytest.mark.asyncio
async def test_resolve_miss_when_never_persisted(repo_module) -> None:
    _ = repo_module
    await clear_hint_sessions_persisted_for_tests()
    assert await resolve_hint_session("deadbeefdeadbeef", user_id=1) is None
