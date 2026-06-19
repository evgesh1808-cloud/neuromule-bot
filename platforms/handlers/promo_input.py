"""Ввод подарочного промокода (FSM ``waiting_promo_code``)."""

from __future__ import annotations

from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from content import messages as msg
from services.use_cases.promo_turn import PromoOutcome, PromoResult, run_promo_redeem


def _format_gift_payload(pr: PromoResult) -> str:
    """Собирает строку «+X ⚡ и +Y 💎» для удачного промокода."""
    parts: list[str] = []
    if pr.bonus_energy > 0:
        parts.append(f"+{pr.bonus_energy} ⚡")
    if pr.bonus_crystals > 0:
        parts.append(f"+{pr.bonus_crystals} 💎")
    return " и ".join(parts) if parts else "—"


async def reply_promo_result(message: Message, pr: PromoResult) -> None:
    if pr.outcome is PromoOutcome.REDEEMED:
        await message.answer(
            msg.TXT_PROMO_GIFT_REDEEMED.format(payload=_format_gift_payload(pr)),
            parse_mode=ParseMode.HTML,
        )
        return
    if pr.outcome is PromoOutcome.TARIFF_BLOCKED:
        await message.answer(msg.TXT_PROMO_TARIFF_BLOCKED, parse_mode=ParseMode.HTML)
        return
    if pr.outcome is PromoOutcome.USED:
        await message.answer(msg.TXT_PROMO_USED)
        return
    if pr.outcome is PromoOutcome.EXHAUSTED:
        await message.answer(msg.TXT_PROMO_EXHAUSTED)
        return
    await message.answer(msg.TXT_PROMO_UNKNOWN)


async def handle_promo_code_message(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.startswith("/"):
        await state.clear()
        return
    pr = await run_promo_redeem(message.from_user.id, raw)
    await state.clear()
    await reply_promo_result(message, pr)
