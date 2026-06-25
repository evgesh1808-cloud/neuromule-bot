"""FSM и callback выбора площадки для финансового аудита."""

from __future__ import annotations

import logging

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State

from platforms.telegram_states import (
    OneCAuditingStates,
    OzonAuditingStates,
    WBAuditingStates,
    YandexAuditingStates,
)
from services.marketplace_platform import MarketplacePlatformId, normalize_marketplace_platform

logger = logging.getLogger(__name__)

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


async def dismiss_fsm_chat_message(
    state: FSMContext,
    *,
    chat_id: int,
    data_key: str = "audit_upload_prompt_message_id",
) -> None:
    """Удаляет сохранённое в FSM сервисное сообщение (инструкция «загрузите файл»)."""
    from platforms.handlers import deps

    data = await state.get_data()
    raw_id = data.get(data_key)
    if not raw_id:
        return
    try:
        await deps.bot().delete_message(chat_id, int(raw_id))
    except Exception:
        logger.debug("dismiss_fsm_chat_message failed key=%s", data_key, exc_info=True)
    await state.update_data(**{data_key: None})


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
    """Только FSM ожидания файла после выбора площадки (не общий Нейротекст)."""
    if not state_key:
        return False
    return state_key in AUDIT_FILE_WAITING_STATE_KEYS


def is_marketplace_audit_context(
    state_key: str | None,
    data: dict[str, object] | None,
) -> bool:
    """Финансовый аудит площадки: явный audit_platform или FSM wait_for_xlsx."""
    if is_audit_file_waiting_state(state_key):
        return True
    if not data:
        return False
    return bool(data.get("audit_platform"))
