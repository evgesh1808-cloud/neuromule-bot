"""Маршрутизация текстового чата (OpenRouter)."""

from __future__ import annotations

from typing import Any

from content.messages import (
    FREE_TARIFF_ALLOWED_ROLES,
    PREMIUM_TEXT_ROLE_IDS,
    SMART_TARIFF_REQUIRED_ROLES,
    TEXT_ROLE_COSTS,
    TXT_CHAT_ROLE_FALLBACK_STANDARD,
)
from services.billing import store
from services.billing.pricing import (
    CHAT_EXPERT_CRYSTALS,
    CHAT_EXPERT_ENERGY,
    CHAT_STANDARD_CRYSTALS,
    CHAT_STANDARD_ENERGY,
    FREE_CHAT_MODEL,
    PAID_CHAT_MODEL,
)
from config import settings
from content.chat_prompt import USER_COMPLIANCE_TAIL_MARKER, build_user_compliance_tail
from services.god_mode import billing_bypass
from services.billing.types import (
    ChatRoutePlan,
    CurrencyKind,
    SpendFeature,
    TariffTier,
    TextChatBillingResult,
    UserBillingState,
)


def _unique_model_ids(*candidates: str) -> tuple[str, ...]:
    out: list[str] = []
    for mid in candidates:
        mid = str(mid).strip()
        if mid and mid not in out:
            out.append(mid)
    return tuple(out)


def _free_model_fallbacks() -> tuple[str, ...]:
    return _unique_model_ids(*settings.free_models)


def _paid_model_fallbacks() -> tuple[str, ...]:
    if settings.smart_models:
        return _unique_model_ids(*settings.smart_models)
    return _free_model_fallbacks()


def inject_compliance_rules_into_last_user_message(
    messages: list[dict[str, Any]],
    *,
    use_premium_prompt: bool,
) -> None:
    """
    Дублирует критичные правила роли в конец последнего ``user`` перед вызовом OpenRouter.

    Помогает free-моделям (Gemini) не «забывать» запрет робо-маркеров, правило одной точки
    и плоскую верстку шагов (без вложенной нумерации) в длинных диалогах, когда system-prompt
    далеко от текущего вопроса.
    """
    suffix = build_user_compliance_tail(premium=use_premium_prompt)
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if part.get("type") != "text":
                    continue
                text = (part.get("text") or "").strip()
                if USER_COMPLIANCE_TAIL_MARKER in text:
                    return
                part["text"] = f"{text}{suffix}" if text else suffix.lstrip()
                return
            msg["content"] = [*content, {"type": "text", "text": suffix.lstrip()}]
            return
        text_content = (content or "").strip() if isinstance(content, str) else str(content or "").strip()
        if USER_COMPLIANCE_TAIL_MARKER in text_content:
            return
        msg["content"] = f"{text_content}{suffix}" if text_content else suffix.lstrip()
        return


def prepare_openrouter_chat_messages(
    messages: list[dict[str, str]],
    *,
    use_premium_prompt: bool,
    text_role: str | None = None,
) -> list[dict[str, str]]:
    """Финальная подготовка payload чата непосредственно перед OpenRouter."""
    if (text_role or "").strip().lower() != "table_generator":
        inject_compliance_rules_into_last_user_message(
            messages,
            use_premium_prompt=use_premium_prompt,
        )
    return messages


def role_costs(role_id: str) -> tuple[int, int]:
    """Возвращает (energy, crystals) стоимости для роли (fallback: standard)."""
    return TEXT_ROLE_COSTS.get((role_id or "standard").strip().lower(), (CHAT_STANDARD_ENERGY, CHAT_STANDARD_CRYSTALS))


def is_expert_role(role_id: str) -> bool:
    return (role_id or "standard").strip().lower() in PREMIUM_TEXT_ROLE_IDS


def use_premium_system_prompt(tariff: TariffTier, *, is_expert_role: bool) -> bool:
    """Премиальный промпт по тарифу/роли, а не по совпадению model_id с FREE."""
    return tariff is not TariffTier.FREE or is_expert_role


def role_allowed_for_tariff(role_id: str, tariff: TariffTier) -> bool:
    """SMART-only роли блокированы для MINI и FREE."""
    rid = (role_id or "standard").strip().lower()
    if rid in SMART_TARIFF_REQUIRED_ROLES:
        return tariff in (TariffTier.SMART, TariffTier.ULTRA)
    return True


def plan_text_chat(user: UserBillingState, role_type: str) -> ChatRoutePlan:
    """Рассчитать модель, лимит ``max_tokens`` и стоимость без списания."""
    role_id = (role_type or "standard").strip().lower()
    energy_cost, crystal_cost = role_costs(role_id)
    expert = is_expert_role(role_id)
    tariff = user.current_tariff
    free_max = settings.openrouter_max_output_tokens
    premium_max = settings.openrouter_premium_max_output_tokens
    table_max = settings.openrouter_table_max_output_tokens

    def _max_tokens_for_role() -> int:
        if role_id == "table_generator":
            return table_max
        return premium_max if expert or tariff is not TariffTier.FREE else free_max

    if not role_allowed_for_tariff(role_id, tariff):
        return ChatRoutePlan(
            model_id=PAID_CHAT_MODEL,
            price_type=CurrencyKind.NONE,
            energy_cost=energy_cost,
            crystal_cost=crystal_cost,
            is_expert_role=expert,
            max_tokens=_max_tokens_for_role(),
            use_premium_prompt=use_premium_system_prompt(tariff, is_expert_role=expert),
            fallback_model_ids=_paid_model_fallbacks(),
            blocked=True,
            block_reason="role_requires_smart_tariff",
        )

    if tariff is TariffTier.FREE:
        if role_id in FREE_TARIFF_ALLOWED_ROLES:
            return ChatRoutePlan(
                model_id=FREE_CHAT_MODEL,
                price_type=CurrencyKind.ENERGY,
                energy_cost=energy_cost,
                crystal_cost=crystal_cost,
                is_expert_role=False,
                max_tokens=free_max,
                use_premium_prompt=False,
                fallback_model_ids=_free_model_fallbacks(),
            )
        if user.crystals >= crystal_cost:
            return ChatRoutePlan(
                model_id=PAID_CHAT_MODEL,
                price_type=CurrencyKind.CRYSTALS,
                energy_cost=energy_cost,
                crystal_cost=crystal_cost,
                is_expert_role=True,
                max_tokens=_max_tokens_for_role(),
                use_premium_prompt=True,
                fallback_model_ids=_paid_model_fallbacks(),
            )
        return ChatRoutePlan(
            model_id=FREE_CHAT_MODEL,
            price_type=CurrencyKind.NONE,
            energy_cost=energy_cost,
            crystal_cost=crystal_cost,
            is_expert_role=True,
            max_tokens=_max_tokens_for_role(),
            use_premium_prompt=True,
            fallback_model_ids=_free_model_fallbacks(),
            blocked=True,
            block_reason="expert_role_requires_paid_tariff",
        )

    if tariff is TariffTier.MINI:
        return ChatRoutePlan(
            model_id=FREE_CHAT_MODEL,
            price_type=CurrencyKind.ENERGY,
            energy_cost=energy_cost,
            crystal_cost=crystal_cost,
            is_expert_role=expert,
            max_tokens=_max_tokens_for_role(),
            use_premium_prompt=True,
            fallback_model_ids=_free_model_fallbacks(),
        )

    return ChatRoutePlan(
        model_id=PAID_CHAT_MODEL,
        price_type=CurrencyKind.ENERGY,
        energy_cost=energy_cost,
        crystal_cost=crystal_cost,
        is_expert_role=expert,
        max_tokens=_max_tokens_for_role(),
        use_premium_prompt=use_premium_system_prompt(tariff, is_expert_role=expert),
        fallback_model_ids=_paid_model_fallbacks(),
    )


def is_zero_chat_balance(user: UserBillingState) -> bool:
    """Нет ни энергии, ни кристаллов для любого текстового запроса."""
    return user.total_energy <= 0 and user.crystals <= 0


def can_afford_role_minimum(user: UserBillingState, role_id: str) -> bool:
    """
    Строгая проверка: хватает ли ⚡ по тарифу роли или 💎 как запасной валюты.

    standard → минимум 1 ⚡ или 1 💎; экспертные роли → 5 ⚡ или 3 💎 (из ``TEXT_ROLE_COSTS``).
    """
    if billing_bypass(user.user_id):
        return True
    energy_cost, crystal_cost = role_costs(role_id)
    if user.total_energy >= energy_cost:
        return True
    return user.crystals >= crystal_cost


def _blocked_plan(
    plan: ChatRoutePlan,
    *,
    block_reason: str,
) -> ChatRoutePlan:
    return ChatRoutePlan(
        model_id=plan.model_id,
        price_type=CurrencyKind.NONE,
        energy_cost=plan.energy_cost,
        crystal_cost=plan.crystal_cost,
        is_expert_role=plan.is_expert_role,
        max_tokens=plan.max_tokens,
        use_premium_prompt=plan.use_premium_prompt,
        fallback_model_ids=plan.fallback_model_ids,
        blocked=True,
        block_reason=block_reason,
    )


def resolve_effective_text_role(
    user: UserBillingState,
    role_type: str,
) -> tuple[str, str | None, ChatRoutePlan | None]:
    """
    Выбирает роль с учётом баланса до списания.

    Возвращает ``(effective_role_id, notice, blocked_plan)``.
    Если ``blocked_plan`` не ``None`` — запрос в API не отправлять.
    """
    role_id = (role_type or "standard").strip().lower()
    notice: str | None = None
    probe_plan = plan_text_chat(user, role_id)

    if billing_bypass(user.user_id):
        if probe_plan.blocked:
            return role_id, None, probe_plan
        return role_id, notice, None

    if is_zero_chat_balance(user):
        return role_id, None, _blocked_plan(probe_plan, block_reason="zero_balance")

    if is_expert_role(role_id) and not can_afford_role_minimum(user, role_id):
        if can_afford_role_minimum(user, "standard"):
            return "standard", TXT_CHAT_ROLE_FALLBACK_STANDARD, None
        return role_id, None, _blocked_plan(probe_plan, block_reason="zero_balance")

    if not can_afford_role_minimum(user, role_id):
        return role_id, None, _blocked_plan(probe_plan, block_reason="zero_balance")

    if probe_plan.blocked:
        return role_id, None, probe_plan

    return role_id, notice, None


def can_afford_chat(user: UserBillingState, plan: ChatRoutePlan) -> bool:
    if plan.blocked:
        return False
    if user.total_energy >= plan.energy_cost:
        return True
    return user.crystals >= plan.crystal_cost


async def _charge_text_chat_for_role(
    user_id: int,
    user: UserBillingState,
    role_id: str,
) -> tuple[ChatRoutePlan, str | None]:
    """План + атомарное списание для уже проверенной роли."""
    plan = plan_text_chat(user, role_id)
    if plan.blocked:
        return plan, None
    if not billing_bypass(user_id) and not can_afford_role_minimum(user, role_id):
        return _blocked_plan(plan, block_reason="zero_balance"), None

    if plan.price_type is CurrencyKind.CRYSTALS:
        energy_need, crystal_need = 0, plan.crystal_cost
    elif user.total_energy >= plan.energy_cost:
        energy_need, crystal_need = plan.energy_cost, 0
    else:
        energy_need, crystal_need = 0, plan.crystal_cost

    charge = await store.atomic_spend(
        user_id,
        SpendFeature.CHAT.value,
        energy_need=energy_need,
        crystal_need=crystal_need,
        crystals_only=(plan.price_type is CurrencyKind.CRYSTALS),
        reserve_photo_slot=False,
        photo_daily_limit=0,
    )
    if not charge:
        return _blocked_plan(plan, block_reason="spend_failed"), None

    price_type = CurrencyKind.ENERGY if charge.energy_free or charge.energy_paid else CurrencyKind.CRYSTALS
    return ChatRoutePlan(
        model_id=plan.model_id,
        price_type=price_type,
        energy_cost=charge.energy_free + charge.energy_paid,
        crystal_cost=charge.crystals,
        is_expert_role=plan.is_expert_role,
        max_tokens=plan.max_tokens,
        use_premium_prompt=plan.use_premium_prompt,
        fallback_model_ids=plan.fallback_model_ids,
    ), charge.charge_id


async def resolve_and_charge_text_chat(
    user_id: int,
    role_type: str,
) -> TextChatBillingResult:
    """
    Строгая проверка баланса, возможный откат роли на standard, списание до OpenRouter.

    Приоритет списания: энергия → подписочные ``sub_crystals`` → ``buy_crystals``.
    """
    user = await store.load_user_billing(user_id)
    effective_role, notice, blocked = resolve_effective_text_role(user, role_type)
    if blocked is not None:
        return TextChatBillingResult(
            effective_role_id=effective_role,
            plan=blocked,
            charge_id=None,
            notice=notice,
        )

    plan, charge_id = await _charge_text_chat_for_role(user_id, user, effective_role)
    if plan.blocked:
        return TextChatBillingResult(
            effective_role_id=effective_role,
            plan=plan,
            charge_id=None,
            notice=notice,
        )
    return TextChatBillingResult(
        effective_role_id=effective_role,
        plan=plan,
        charge_id=charge_id,
        notice=notice,
    )


async def handle_text_chat(user_id: int, role_type: str) -> tuple[ChatRoutePlan, str | None]:
    """Обратная совместимость: план + charge_id без notice."""
    result = await resolve_and_charge_text_chat(user_id, role_type)
    return result.plan, result.charge_id
