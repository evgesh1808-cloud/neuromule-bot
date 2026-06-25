"""Use-case: премиум-меню «Нейротекст» — статус, баланс, ёмкость, доступность ролей."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from content.messages import (
    FREE_TARIFF_ALLOWED_ROLES,
    SMART_TARIFF_REQUIRED_ROLES,
    TEXT_ROLES,
)
from services.billing import billing
from services.billing.chat_pipeline import (
    is_expert_role,
    plan_text_chat,
    role_allowed_for_tariff,
    role_costs,
)
from services.billing.types import CurrencyKind, TariffTier, UserBillingState


class NeurotextRoleOutcome(str, Enum):
    OK = "ok"
    OK_VIA_CRYSTALS = "ok_via_crystals"
    UNKNOWN_ROLE = "unknown_role"
    PREMIUM_LOCKED = "premium_locked"
    SMART_REQUIRED = "smart_required"


@dataclass(frozen=True)
class NeurotextRolePickResult:
    outcome: NeurotextRoleOutcome
    role_id: str = ""
    role_label: str = ""
    crystal_cost: int = 0


@dataclass(frozen=True)
class RoleAvailability:
    role_id: str
    label: str
    locked: bool
    locked_reason: str
    via_crystals: bool


TEXT_ROLE_ALIASES: dict[str, str] = {
    "psychologist": "psychologist_coach",
    "blogger": "blogger_content",
    "academic": "summary",
    "speaker": "podcast_doc",
    "analyst": "summary",
    "storyteller": "standard",
}

_LEGACY_ROLE_LABELS: dict[str, str] = {
    "academic": "🎓 Академик",
    "psychologist": "🎭 Психолог",
    "speaker": "🗣️ Спикер (TED)",
    "blogger": "📱 Блогер",
    "analyst": "📉 Аналитик",
    "storyteller": "🧙 Сказочник",
}


def normalize_text_role_id(role_id: str) -> str:
    rid = (role_id or "standard").strip().lower()
    return TEXT_ROLE_ALIASES.get(rid, rid)


def text_role_label(role_id: str) -> str | None:
    rid = normalize_text_role_id(role_id)
    for label, role in TEXT_ROLES:
        if role == rid:
            return label
    return _LEGACY_ROLE_LABELS.get((role_id or "").strip().lower())


def _role_availability(role_id: str, user: UserBillingState) -> RoleAvailability:
    rid = role_id.strip().lower()
    label = text_role_label(rid) or rid
    energy, crystals = role_costs(rid)
    tariff = user.current_tariff

    if rid in SMART_TARIFF_REQUIRED_ROLES and tariff not in (TariffTier.SMART, TariffTier.ULTRA):
        return RoleAvailability(rid, label, locked=True, locked_reason="smart", via_crystals=False)

    if tariff is TariffTier.FREE and rid not in FREE_TARIFF_ALLOWED_ROLES:
        if user.crystals >= crystals:
            return RoleAvailability(rid, label, locked=False, locked_reason="", via_crystals=True)
        return RoleAvailability(rid, label, locked=True, locked_reason="free", via_crystals=False)

    return RoleAvailability(rid, label, locked=False, locked_reason="", via_crystals=False)


async def get_role_availability_map(user_id: int) -> dict[str, RoleAvailability]:
    user = await billing.load_user(user_id)
    return {rid: _role_availability(rid, user) for _, rid in TEXT_ROLES}


def _capacity(user: UserBillingState, role_id: str) -> tuple[int, bool]:
    """Сколько запросов в выбранной роли потянет текущий баланс. (кол-во, hasEnough)."""
    energy, crystals = role_costs(role_id)
    if user.current_tariff is TariffTier.FREE and role_id.strip().lower() not in FREE_TARIFF_ALLOWED_ROLES:
        cap = user.crystals // crystals if crystals else 0
    else:
        from_energy = user.total_energy // energy if energy else 0
        from_crystals = user.crystals // crystals if crystals else 0
        cap = from_energy + from_crystals
    return cap, cap > 0


async def build_neurotext_intro(user_id: int, active_role_id: str = "standard") -> str:
    """Премиум-карточка экрана выбора роли (HTML)."""
    from services.repository import get_user_row

    user = await billing.load_user(user_id)
    row = await get_user_row(user_id)
    rid = (active_role_id or "standard").strip().lower()
    role_label = text_role_label(rid) or "Стандарт"
    energy, crystals = role_costs(rid)
    cap, has_enough = _capacity(user, rid)

    if user.current_tariff is TariffTier.FREE:
        header = "✨ <b>NeuroMule AI • Базовая версия 🐎</b>"
    else:
        header = f"👑 <b>NeuroMule AI • Премиум [{user.current_tariff.value}] 🐎⚡️</b>"

    if user.current_tariff is TariffTier.FREE and rid not in FREE_TARIFF_ALLOWED_ROLES:
        cost_line = f"{crystals} 💎 (за Кристаллы)"
    else:
        cost_line = f"{energy} ⚡ / {crystals} 💎"

    photo_used = int(row.photo_daily_count or 0)

    from business_catalog import catalog

    free_text_limit = catalog.daily_free_energy
    photo_limit = catalog.free_imagen_daily_limit

    from content.messages import TXT_AI_ASSISTANT_CARD_TITLE

    lines = [
        header,
        TXT_AI_ASSISTANT_CARD_TITLE,
        f"🎭 Режим: <b>[ {role_label} ]</b> ➔ {cost_line}",
        "",
    ]
    if user.current_tariff is TariffTier.FREE:
        lines.append(
            f"⚡ Энергия: <b>{user.total_energy} / {free_text_limit}</b>   "
            f"🖼️ Imagen: <b>{photo_used} / {photo_limit}</b>   "
            f"💎 Кристаллы: <b>{user.crystals}</b>"
        )
    else:
        lines.append(
            f"⚡ Энергия: <b>{user.total_energy}</b>   "
            f"🖼️ Imagen сегодня: <b>{photo_used}</b>   "
            f"💎 Кристаллы: <b>{user.crystals}</b>"
        )
    lines.append("")
    if has_enough:
        lines.append(f"🎯 Этого баланса вам хватит на <b>{cap}</b> таких запросов.")
    else:
        lines.append("⚠️ <b>Недостаточно ресурсов на этот режим.</b>")
    return "\n".join(lines)


async def validate_text_role_pick(user_id: int, role_id: str) -> NeurotextRolePickResult:
    """Проверка роли перед FSM. FREE может включить роль за 💎, если хватает."""
    canonical = normalize_text_role_id(role_id)
    label = text_role_label(canonical) or text_role_label(role_id)
    if not label:
        return NeurotextRolePickResult(outcome=NeurotextRoleOutcome.UNKNOWN_ROLE)

    user = await billing.load_user(user_id)
    plan = plan_text_chat(user, canonical)
    energy, crystals = role_costs(canonical)

    if plan.blocked and plan.block_reason == "role_requires_smart_tariff":
        return NeurotextRolePickResult(
            outcome=NeurotextRoleOutcome.SMART_REQUIRED,
            role_id=canonical,
            role_label=label,
            crystal_cost=crystals,
        )
    if plan.blocked and plan.block_reason == "expert_role_requires_paid_tariff":
        return NeurotextRolePickResult(
            outcome=NeurotextRoleOutcome.PREMIUM_LOCKED,
            role_id=canonical,
            role_label=label,
            crystal_cost=crystals,
        )

    via_crystals = plan.price_type is CurrencyKind.CRYSTALS and user.current_tariff is TariffTier.FREE
    return NeurotextRolePickResult(
        outcome=NeurotextRoleOutcome.OK_VIA_CRYSTALS if via_crystals else NeurotextRoleOutcome.OK,
        role_id=canonical,
        role_label=label,
        crystal_cost=crystals,
    )


def is_expert_text_role(role_id: str) -> bool:
    return is_expert_role(role_id)
