"""FSM активации площадки аудита."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from platforms.marketplace_audit_flow import activate_marketplace_audit, audit_entry_state_for_platform
from platforms.telegram_states import WBAuditingStates


@pytest.mark.asyncio
async def test_activate_marketplace_audit_sets_state_and_data() -> None:
    state = AsyncMock()
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    pid = await activate_marketplace_audit(state, platform="wildberries")

    assert pid == "wildberries"
    state.update_data.assert_awaited_once()
    kwargs = state.update_data.await_args.kwargs
    assert kwargs["table_subrole"] == "wb_ozon_finance"
    assert kwargs["audit_platform"] == "wildberries"
    state.set_state.assert_awaited_once_with(WBAuditingStates.wait_for_tax)
    assert audit_entry_state_for_platform("wildberries") is WBAuditingStates.wait_for_tax
