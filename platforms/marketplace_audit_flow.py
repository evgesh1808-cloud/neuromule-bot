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
from services.audit_tax import (
    AuditTaxPreset,
    default_wb_audit_tax_preset,
    preset_from_regime_rate,
    resolve_audit_tax_preset,
)
from services.marketplace_platform import MarketplacePlatformId, normalize_marketplace_platform

logger = logging.getLogger(__name__)

_AUDIT_FILE_STATES: dict[MarketplacePlatformId, State] = {
    "wildberries": WBAuditingStates.wait_for_xlsx,
    "ozon": OzonAuditingStates.wait_for_xlsx,
    "yandex": YandexAuditingStates.wait_for_xlsx,
    "1c": OneCAuditingStates.wait_for_xlsx,
}

_AUDIT_ENTRY_STATES: dict[MarketplacePlatformId, State] = {
    "wildberries": WBAuditingStates.wait_for_tax,
    "ozon": OzonAuditingStates.wait_for_xlsx,
    "yandex": YandexAuditingStates.wait_for_xlsx,
    "1c": OneCAuditingStates.wait_for_xlsx,
}

AUDIT_FILE_WAITING_STATE_KEYS: frozenset[str] = frozenset(
    st.state for st in _AUDIT_FILE_STATES.values()
)

AUDIT_TAX_WAITING_STATE_KEYS: frozenset[str] = frozenset(
    {
        WBAuditingStates.wait_for_tax.state,
        WBAuditingStates.wait_for_custom_tax.state,
    }
)


def audit_entry_state_for_platform(platform: str | None) -> State:
    pid = normalize_marketplace_platform(platform)
    return _AUDIT_ENTRY_STATES[pid]


def audit_file_state_for_platform(platform: str | None) -> State:
    pid = normalize_marketplace_platform(platform)
    return _AUDIT_FILE_STATES[pid]


def audit_state_for_platform(platform: str | None) -> State:
    """Обратная совместимость: FSM ожидания файла."""
    return audit_file_state_for_platform(platform)


def audit_tax_preset_from_data(data: dict[str, object] | None) -> AuditTaxPreset:
    if not data:
        return default_wb_audit_tax_preset()
    tax_type = data.get("user_tax_type")
    tax_rate = data.get("user_tax_rate")
    if tax_type is not None and tax_rate is not None:
        try:
            return preset_from_regime_rate(str(tax_type), float(tax_rate))
        except (TypeError, ValueError):
            pass
    raw = data.get("audit_tax_preset")
    return resolve_audit_tax_preset(str(raw) if raw is not None else None)


async def save_wb_user_tax_selection(
    state: FSMContext,
    *,
    tax_type: str,
    tax_rate: float,
) -> AuditTaxPreset:
    """Фиксирует налог WB в FSM и переводит в ожидание файла."""
    preset = preset_from_regime_rate(tax_type, tax_rate)
    await state.update_data(
        user_tax_type=preset.regime,
        user_tax_rate=preset.rate_percent,
        audit_tax_preset=preset.id,
        audit_usn_rate=preset.rate,
        audit_usn_base=preset.base,
    )
    await state.set_state(WBAuditingStates.wait_for_xlsx)
    return preset


async def activate_wb_audit_after_tax(
    state: FSMContext,
    *,
    tax_preset_id: str,
) -> None:
    """Legacy: preset id → user_tax_type / user_tax_rate."""
    preset = resolve_audit_tax_preset(tax_preset_id)
    await save_wb_user_tax_selection(
        state, tax_type=preset.regime, tax_rate=preset.rate_percent
    )


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
    """Сохраняет площадку в FSM и переводит в первый шаг аудита (налог WB или файл)."""
    pid = normalize_marketplace_platform(platform)
    payload: dict[str, object] = {
        "text_role": "table_generator",
        "table_subrole": "wb_ozon_finance",
        "audit_platform": pid,
    }
    if pid == "wildberries":
        payload["audit_tax_preset"] = default_wb_audit_tax_preset().id
    await state.update_data(**payload)
    await state.set_state(audit_entry_state_for_platform(pid))
    return pid


def is_audit_file_waiting_state(state_key: str | None) -> bool:
    """Только FSM ожидания файла после выбора площадки (не общий Нейротекст)."""
    if not state_key:
        return False
    return state_key in AUDIT_FILE_WAITING_STATE_KEYS


def is_audit_tax_waiting_state(state_key: str | None) -> bool:
    if not state_key:
        return False
    return state_key in AUDIT_TAX_WAITING_STATE_KEYS


def is_marketplace_audit_context(
    state_key: str | None,
    data: dict[str, object] | None,
) -> bool:
    """Финансовый аудит площадки: выбор налога WB, ожидание файла или audit_platform в data."""
    if is_audit_file_waiting_state(state_key):
        return True
    if not data:
        return False
    return bool(data.get("audit_platform"))
