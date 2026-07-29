"""HintSession L1 RAM + L2 SQLite: переживает «рестарт» (очистку RAM)."""

from __future__ import annotations

import time

import aiosqlite
import pytest

from services import metrics
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
    """Симуляция pm2 restart: L1 пуст → hydrate из SQLite → hint_session_db_hit += 1."""
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

    # «Рестарт процесса»: явно чистим только L1, SQLite цел.
    _HINT_SESSIONS.clear()
    assert action_uuid not in _HINT_SESSIONS
    assert get_hint_session(action_uuid, user_id=42) is None

    hits_before = int(metrics.snapshot()["counters"].get("hint_session_db_hit", 0))
    restored = await resolve_hint_session(action_uuid, user_id=42)
    hits_after = int(metrics.snapshot()["counters"].get("hint_session_db_hit", 0))

    assert restored is not None
    assert restored.user_id == 42
    assert restored.message_id == 9001
    assert restored.root_user_prompt == "Как начать тхэквондо?"
    assert "Разминка" in restored.body
    # labels_json → tuple[str, ...]
    assert isinstance(restored.labels, tuple)
    assert restored.labels == (
        "Что мне учесть про разминку?",
        "Что мне учесть про стойки?",
    )
    assert hits_after == hits_before + 1

    # Hydrate: повторный get уже из RAM (db_hit не должен снова вырасти от get).
    assert get_hint_session(action_uuid, user_id=42) is restored
    assert await resolve_hint_session(action_uuid, user_id=99) is None

    await clear_hint_sessions_persisted_for_tests()


@pytest.mark.asyncio
async def test_resolve_miss_when_never_persisted(repo_module) -> None:
    _ = repo_module
    await clear_hint_sessions_persisted_for_tests()
    assert await resolve_hint_session("deadbeefdeadbeef", user_id=1) is None


@pytest.mark.asyncio
async def test_clear_expired_hint_sessions_deletes_old_rows(repo_module) -> None:
    from services import hint_session_store as store

    _ = repo_module
    await clear_hint_sessions_persisted_for_tests()
    action_uuid = await create_hint_session_persisted(
        7,
        body="тело",
        labels=["Один?"],
        root_user_prompt="вопрос",
    )
    # Протухло вчера.
    async with aiosqlite.connect(repo_module.DB_PATH) as db:
        await db.execute(
            "UPDATE hint_sessions SET expires_at = ? WHERE action_uuid = ?",
            (time.time() - 10, action_uuid),
        )
        await db.commit()

    deleted = await store.clear_expired_hint_sessions()
    assert deleted >= 1
    clear_suggested_replies_for_tests()  # L1 мог ещё держать копию
    assert await resolve_hint_session(action_uuid, user_id=7) is None
