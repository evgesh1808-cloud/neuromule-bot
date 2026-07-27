"""FREE chat lock: запрет параллельных запросов."""

from __future__ import annotations

import pytest

from services import repository as repo


@pytest.mark.asyncio
async def test_chat_lock_acquire_blocks_parallel(repo_module) -> None:
    uid = 991001
    assert await repo.chat_lock_acquire(uid, ttl_sec=12) is True
    assert await repo.chat_lock_acquire(uid, ttl_sec=12) is False
    await repo.chat_lock_release(uid)
    assert await repo.chat_lock_acquire(uid, ttl_sec=12) is True
    await repo.chat_lock_release(uid)


@pytest.mark.asyncio
async def test_free_chat_lock_context_manager(repo_module) -> None:
    from config import Settings
    from services.rate_limit_service import free_chat_lock

    s = Settings()
    uid = 991002
    async with free_chat_lock(s, uid, enabled=True, ttl_sec=12) as ok:
        assert ok is True
        async with free_chat_lock(s, uid, enabled=True, ttl_sec=12) as ok2:
            assert ok2 is False
    async with free_chat_lock(s, uid, enabled=True, ttl_sec=12) as ok3:
        assert ok3 is True
    async with free_chat_lock(s, uid, enabled=False, ttl_sec=12) as ok4:
        assert ok4 is True


@pytest.mark.asyncio
async def test_claim_chat_busy_notice_cooldown(repo_module) -> None:
    from unittest.mock import MagicMock

    from services import rate_limit_service as rls

    s = MagicMock()
    s.redis_url = ""
    uid = 991003
    rls._BUSY_NOTICE_UNTIL.pop(uid, None)

    assert await rls.claim_chat_busy_notice(s, uid, cooldown_sec=2) is True
    assert await rls.claim_chat_busy_notice(s, uid, cooldown_sec=2) is False
    assert await rls.claim_chat_busy_notice(s, uid, cooldown_sec=2) is False

    # Истёкший кулдаун снова разрешает уведомление.
    rls._BUSY_NOTICE_UNTIL[uid] = 0.0
    assert await rls.claim_chat_busy_notice(s, uid, cooldown_sec=2) is True


@pytest.mark.asyncio
async def test_remember_and_pop_chat_busy_message_id(repo_module) -> None:
    from unittest.mock import MagicMock

    from services import rate_limit_service as rls

    s = MagicMock()
    s.redis_url = ""
    uid = 991004
    rls._BUSY_NOTICE_MSG_ID.pop(uid, None)

    await rls.remember_chat_busy_message_id(s, uid, 4242)
    assert rls._BUSY_NOTICE_MSG_ID.get(uid) == 4242
    assert await rls.pop_chat_busy_message_id(s, uid) == 4242
    assert await rls.pop_chat_busy_message_id(s, uid) is None


@pytest.mark.asyncio
async def test_chat_turn_success_pops_busy_notice_id(repo_module) -> None:
    """После SUCCESS run_chat_turn отдаёт busy_notice_message_id для delete_message."""
    from unittest.mock import AsyncMock, patch

    from config import Settings
    from services import rate_limit_service as rls
    from services.billing.types import ChatRoutePlan, CurrencyKind, TextChatBillingResult, TariffTier
    from services.use_cases.chat_turn import ChatTurnOutcome, run_chat_turn

    uid = 991005
    rls._BUSY_NOTICE_MSG_ID[uid] = 7777

    plan = ChatRoutePlan(
        model_id="google/gemini-2.5-flash",
        price_type=CurrencyKind.ENERGY,
        energy_cost=1,
        crystal_cost=1,
        is_expert_role=False,
        max_tokens=2000,
        use_premium_prompt=False,
        fallback_model_ids=(),
        blocked=False,
        tariff=TariffTier.MINI,
    )
    billing = TextChatBillingResult(
        effective_role_id="standard",
        plan=plan,
        charge_id="c1",
        notice=None,
    )
    completion = {
        "content": "Ответ модели.",
        "prompt_tokens": 10,
        "completion_tokens": 5,
    }

    with (
        patch("services.use_cases.chat_turn.allow_request", AsyncMock(return_value=True)),
        patch(
            "services.use_cases.chat_turn.billing.resolve_and_charge_text_chat",
            AsyncMock(return_value=billing),
        ),
        patch(
            "services.use_cases.chat_turn.conv.build_openrouter_messages",
            AsyncMock(return_value=[{"role": "user", "content": "hi"}]),
        ),
        patch(
            "services.use_cases.chat_turn.prepare_openrouter_chat_messages",
            return_value=[{"role": "user", "content": "hi"}],
        ),
        patch(
            "services.use_cases.chat_turn.prune_context_messages",
            return_value=([{"role": "user", "content": "hi"}], True),
        ),
        patch(
            "services.use_cases.chat_turn.ask_ai_messages",
            AsyncMock(return_value=completion),
        ),
        patch("services.use_cases.chat_turn.commit_assistant_turn_queued", AsyncMock()),
        patch("services.use_cases.chat_turn.conv.schedule_memory_refresh"),
        patch("services.use_cases.chat_turn.dialog_append", AsyncMock()),
        patch(
            "services.repository.get_show_suggested_replies",
            AsyncMock(return_value=False),
        ),
    ):
        result = await run_chat_turn(
            Settings(tg_token="t", openrouter_key="k"),
            uid,
            "привет",
            text_role="standard",
        )

    assert result.outcome is ChatTurnOutcome.SUCCESS
    assert result.busy_notice_message_id == 7777
    assert uid not in rls._BUSY_NOTICE_MSG_ID
