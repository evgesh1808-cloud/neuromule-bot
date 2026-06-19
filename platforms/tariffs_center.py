"""Экран «Тарифы»: тексты, клавиатуры, editMessageText."""

from __future__ import annotations

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from config import settings
from content import messages as msg
from services import payments_catalog as paycat
from services.billing.pricing import SHOP_PACKS
from services.billing.types import TariffTier

_HTML_PREVIEW = LinkPreviewOptions(is_disabled=True)

_ULTRA_PACK_IDS: tuple[str, ...] = ("ULTRA_3DAYS", "ULTRA_1WEEK", "ULTRA_1MONTH")


def _pack_catalog_index(pack_id: str) -> int | None:
    idx = paycat.PACK_CATALOG_ORDER.index(pack_id) if pack_id in paycat.PACK_CATALOG_ORDER else -1
    return idx if idx >= 0 else None


def _pack_button_label(pack_id: str, spec: dict) -> str:
    name = str(spec.get("name") or pack_id)
    return name.removeprefix("Пакет ").strip()


def _shop_tariff_pack_rows(pack_ids: tuple[str, ...]) -> list[list[InlineKeyboardButton]]:
    """Строки оплаты из ``SHOP_PACKS`` (цены из config → business_catalog)."""
    rows: list[list[InlineKeyboardButton]] = []
    for pack_id in pack_ids:
        spec = SHOP_PACKS.get(pack_id)
        if not spec or spec.get("tariff") is None:
            continue
        idx = _pack_catalog_index(pack_id)
        if idx is None:
            continue
        label = _pack_button_label(pack_id, spec)
        rub = int(spec["price_rub"])
        stars = int(spec["price_stars"])
        rows.append(_bundle_pay_row(label, idx, rub, stars))
    return rows


def tariffs_main_keyboard() -> InlineKeyboardMarkup:
    ultra_rows = _shop_tariff_pack_rows(_ULTRA_PACK_IDS)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_TARIFFS_BTN_BUNDLE,
                    callback_data=msg.CB_BUY_BUNDLE_MENU,
                ),
            ],
            [
                InlineKeyboardButton(
                    text=msg.TXT_TARIFFS_BTN_CRYSTALS,
                    callback_data=msg.CB_BUY_CRYSTALS_ONLY_MENU,
                ),
            ],
            *ultra_rows,
            [
                InlineKeyboardButton(
                    text=msg.TXT_TARIFFS_BTN_TERMS,
                    url=settings.subscription_terms_url,
                ),
            ],
            [
                InlineKeyboardButton(
                    text=msg.TXT_TARIFFS_BTN_CLOSE,
                    callback_data=msg.CB_CLOSE_TARIFFS,
                ),
            ],
        ]
    )


def _bundle_pay_row(tariff_label: str, pkg_index: int, rub: int, stars: int) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            text=f"🔥 {tariff_label} картой ({rub} ₽) — Выгодно",
            callback_data=f"{paycat.CB_PAY_METHOD_PREFIX}{pkg_index}:r",
        ),
        InlineKeyboardButton(
            text=f"📱 {tariff_label} в Stars ({stars} ⭐)",
            callback_data=f"{paycat.CB_PAY_METHOD_PREFIX}{pkg_index}:x",
        ),
    ]


def tariffs_bundle_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора платного пакета. Цены из ``SHOP_PACKS``."""
    tariff_ids = tuple(
        pid for pid in paycat.PACK_CATALOG_ORDER if SHOP_PACKS.get(pid, {}).get("tariff")
    )
    rows = _shop_tariff_pack_rows(tariff_ids)
    rows.append(
        [
            InlineKeyboardButton(
                text=msg.TXT_TARIFFS_BTN_BACK,
                callback_data=msg.CB_OPEN_TARIFFS,
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _crystal_pay_row(amount: int, pkg_index: int, rub: int, stars: int) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            text=f"🔥 {amount} 💎 за {rub} ₽ (Карта)",
            callback_data=f"{paycat.CB_PAY_METHOD_PREFIX}{pkg_index}:r",
        ),
        InlineKeyboardButton(
            text=f"📱 {amount} 💎 за {stars} ⭐",
            callback_data=f"{paycat.CB_PAY_METHOD_PREFIX}{pkg_index}:x",
        ),
    ]


def _crystal_packs_from_shop() -> list[tuple[int, int, int, int]]:
    """(crystals, index, rub, stars) для кристалл-пакетов из ``SHOP_PACKS``."""
    out: list[tuple[int, int, int, int]] = []
    for pack_id in paycat.PACK_CATALOG_ORDER:
        spec = SHOP_PACKS.get(pack_id)
        if not spec or spec.get("tariff") is not None:
            continue
        idx = _pack_catalog_index(pack_id)
        if idx is None:
            continue
        out.append(
            (
                int(spec["crystals"]),
                idx,
                int(spec["price_rub"]),
                int(spec["price_stars"]),
            )
        )
    return out


def tariffs_crystals_shop_keyboard() -> InlineKeyboardMarkup:
    rows = [
        _crystal_pay_row(crystals, idx, rub, stars)
        for crystals, idx, rub, stars in _crystal_packs_from_shop()
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            *rows,
            [
                InlineKeyboardButton(
                    text=msg.TXT_TARIFFS_BTN_BACK,
                    callback_data=msg.CB_OPEN_TARIFFS,
                ),
            ],
        ]
    )


def tariffs_crystals_blocked_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔙 Назад", callback_data=msg.CB_OPEN_TARIFFS),
                InlineKeyboardButton(
                    text=msg.TXT_TARIFFS_BTN_CLOSE,
                    callback_data=msg.CB_CLOSE_TARIFFS,
                ),
            ],
        ]
    )


def crystals_shop_inline_card_keyboard() -> InlineKeyboardMarkup:
    """Компактная карточка магазина 💎 для алертов о нехватке (40/100 💎 + назад к тарифам)."""
    crystal_packs = _crystal_packs_from_shop()
    p40 = next((p for p in crystal_packs if p[0] == 40), None)
    p100 = next((p for p in crystal_packs if p[0] == 100), None)
    rows: list[list[InlineKeyboardButton]] = []
    if p40:
        _, idx40, rub40, stars40 = p40
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🔥 40 💎 за {rub40} ₽",
                    callback_data=f"{paycat.CB_PAY_METHOD_PREFIX}{idx40}:r",
                ),
                InlineKeyboardButton(
                    text=f"📱 40 💎 за {stars40} ⭐",
                    callback_data=f"{paycat.CB_PAY_METHOD_PREFIX}{idx40}:x",
                ),
            ]
        )
    if p100:
        _, idx100, rub100, stars100 = p100
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🔥 100 💎 за {rub100} ₽",
                    callback_data=f"{paycat.CB_PAY_METHOD_PREFIX}{idx100}:r",
                ),
                InlineKeyboardButton(
                    text=f"📱 100 💎 за {stars100} ⭐",
                    callback_data=f"{paycat.CB_PAY_METHOD_PREFIX}{idx100}:x",
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🚀 Все тарифы и пакеты",
                callback_data=msg.CB_OPEN_TARIFFS,
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def crystals_screen_for_tariff(tariff: TariffTier) -> tuple[str, InlineKeyboardMarkup]:
    if tariff is TariffTier.FREE:
        return msg.TXT_TARIFFS_CRYSTALS_FREE_BLOCKED, tariffs_crystals_blocked_keyboard()
    return msg.TXT_TARIFFS_CRYSTALS_SHOP, tariffs_crystals_shop_keyboard()


async def edit_tariffs_screen(
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


async def send_tariffs_screen(message, text: str) -> None:
    await message.answer(
        text,
        reply_markup=tariffs_main_keyboard(),
        parse_mode=ParseMode.HTML,
        link_preview_options=_HTML_PREVIEW,
    )
