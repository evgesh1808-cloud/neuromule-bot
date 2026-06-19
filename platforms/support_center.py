"""Двухуровневый центр поддержки: тексты, клавиатуры, editMessageText."""

from __future__ import annotations

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from config import settings
from content import messages as msg


def support_main_text() -> str:
    return msg.format_support_text(settings)


def support_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_SUPPORT_BTN_FAQ,
                    callback_data=msg.CB_SUPP_FAQ,
                ),
            ],
            [
                InlineKeyboardButton(
                    text=msg.TXT_SUPPORT_BTN_GUIDES,
                    callback_data=msg.CB_SUPP_GUIDES,
                ),
                InlineKeyboardButton(
                    text=msg.TXT_SUPPORT_BTN_PAYMENT,
                    callback_data=msg.CB_SUPP_PAYMENT_ISSUE,
                ),
            ],
            [
                InlineKeyboardButton(
                    text=msg.TXT_SUPPORT_BTN_SUBSCRIPTION,
                    callback_data=msg.CB_MANAGE_SUBSCRIPTION,
                ),
            ],
            [
                InlineKeyboardButton(
                    text=msg.TXT_SUPPORT_BTN_WRITE,
                    callback_data=msg.CB_WRITE_TO_MANAGER,
                ),
            ],
            [
                InlineKeyboardButton(
                    text=msg.TXT_SUPPORT_BTN_CLOSE,
                    callback_data=msg.CB_CLOSE_SUPPORT,
                ),
            ],
        ]
    )


def support_faq_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        (msg.TXT_FAQ_BTN_ENERGY, msg.CB_FAQ_ENERGY),
        (msg.TXT_FAQ_BTN_HD_DIFF, msg.CB_FAQ_HD_DIFF),
        (msg.TXT_FAQ_BTN_SLOW_GEN, msg.CB_FAQ_SLOW_GEN),
        (msg.TXT_FAQ_BTN_PROMPTS, msg.CB_FAQ_PROMPTS),
        (msg.TXT_FAQ_BTN_PRIVACY, msg.CB_FAQ_PRIVACY),
        (msg.TXT_FAQ_BTN_CANCEL_SUB, msg.CB_FAQ_CANCEL_SUB),
        (msg.TXT_FAQ_BTN_HD_SOURCE, msg.CB_FAQ_HD_SOURCE),
        (msg.TXT_FAQ_BTN_REFUND, msg.CB_FAQ_REFUND_CRYSTALS),
        (msg.TXT_FAQ_BTN_STARS, msg.CB_FAQ_STARS_COST),
    ]
    keyboard = [[InlineKeyboardButton(text=t, callback_data=cb)] for t, cb in rows]
    keyboard.append(
        [
            InlineKeyboardButton(
                text=msg.TXT_SUPPORT_BTN_BACK_MAIN,
                callback_data=msg.CB_BACK_TO_SUPP_MAIN,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def support_faq_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_SUPPORT_BTN_BACK_FAQ,
                    callback_data=msg.CB_SUPP_FAQ,
                )
            ],
        ]
    )


def support_back_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_SUPPORT_BTN_BACK_MAIN,
                    callback_data=msg.CB_BACK_TO_SUPP_MAIN,
                )
            ],
        ]
    )


def support_payment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_SUPPORT_BTN_CHECK_PAYMENT,
                    callback_data=msg.CB_CHECK_PENDING_PAYMENT,
                )
            ],
            [
                InlineKeyboardButton(
                    text=msg.TXT_SUPPORT_BTN_BACK_MAIN,
                    callback_data=msg.CB_BACK_TO_SUPP_MAIN,
                )
            ],
        ]
    )


def support_guides_text() -> str:
    instruction_url = (
        getattr(settings, "support_instruction_url", "") or settings.service_offer_url
    )
    return msg.TXT_SUPPORT_GUIDES.format(
        instruction_url=instruction_url,
        channel_url=settings.channel_url,
    )


def support_manage_subscription_text() -> str:
    return msg.TXT_SUPPORT_MANAGE_SUBSCRIPTION.format(
        subscription_url=settings.subscription_terms_url,
    )


FAQ_ANSWER_BY_CALLBACK: dict[str, str] = {
    msg.CB_FAQ_ENERGY: msg.TXT_FAQ_ANSWER_ENERGY,
    msg.CB_FAQ_HD_DIFF: msg.TXT_FAQ_ANSWER_HD_DIFF,
    msg.CB_FAQ_SLOW_GEN: msg.TXT_FAQ_ANSWER_SLOW_GEN,
    msg.CB_FAQ_PROMPTS: msg.TXT_FAQ_ANSWER_PROMPTS,
    msg.CB_FAQ_PRIVACY: msg.TXT_FAQ_ANSWER_PRIVACY,
    msg.CB_FAQ_CANCEL_SUB: msg.TXT_FAQ_ANSWER_CANCEL_SUB,
    msg.CB_FAQ_HD_SOURCE: msg.TXT_FAQ_ANSWER_HD_SOURCE,
    msg.CB_FAQ_REFUND_CRYSTALS: msg.TXT_FAQ_ANSWER_REFUND_CRYSTALS,
    msg.CB_FAQ_STARS_COST: msg.TXT_FAQ_ANSWER_STARS_COST,
}


_HTML_PREVIEW = LinkPreviewOptions(is_disabled=True)


async def edit_support_screen(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    if not callback.message:
        await callback.answer()
        return
    try:
        await callback.message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
            link_preview_options=_HTML_PREVIEW,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
    await callback.answer()
