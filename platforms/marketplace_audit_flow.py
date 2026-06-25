"""FSM и callback выбора площадки для финансового аудита."""

from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State

from platforms.telegram_states import (
    OneCAuditingStates,
    OzonAuditingStates,
    UserFlow,
    WBAuditingStates,
    YandexAuditingStates,
)
from services.marketplace_platform import MarketplacePlatformId, normalize_marketplace_platform

_AUDIT_PLATFORM_STATES: dict[MarketplacePlatformId, State] = {
    "wildberries": WBAuditingStates.wait_for_xlsx,
    "ozon": OzonAuditingStates.wait_for_xlsx,
    "yandex": YandexAuditingStates.wait_for_xlsx,
    "1c": OneCAuditingStates.wait_for_xlsx,
}

AUDIT_FILE_WAITING_STATE_KEYS: frozenset[str] = frozenset(
    st.state for st in _AUDIT_PLATFORM_STATES.values()
)


def audit_state_for_platform(platform: str | None) -> State:
    pid = normalize_marketplace_platform(platform)
    return _AUDIT_PLATFORM_STATES[pid]


async def activate_marketplace_audit(
    state: FSMContext,
    *,
    platform: str,
) -> MarketplacePlatformId:
    """Сохраняет площадку в FSM и переводит в ожидание .xlsx/.csv."""
    pid = normalize_marketplace_platform(platform)
    await state.update_data(
        text_role="table_generator",
        table_subrole="wb_ozon_finance",
        audit_platform=pid,
    )
    await state.set_state(audit_state_for_platform(pid))
    return pid


def is_audit_file_waiting_state(state_key: str | None) -> bool:
    if not state_key:
        return False
    if state_key in AUDIT_FILE_WAITING_STATE_KEYS:
        return True
    return state_key in (
        UserFlow.waiting_for_text_prompt.state,
        str(UserFlow.waiting_for_text_prompt),
    )
