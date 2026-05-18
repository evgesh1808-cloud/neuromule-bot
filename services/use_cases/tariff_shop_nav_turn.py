"""
Навигация по inline-магазину тарифов: разбор ``callback_data`` и какой экран показать.

Нужен чтобы ``pay_pick_package`` не содержал ветвления по ``parsed == "back"`` и индексам пакета.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from services import payments_catalog as paycat
from services.use_cases.payment_shop_turn import build_payment_choose_method_caption, build_payment_shop_intro_text


class TariffShopNavOutcome(str, Enum):
    """Итог разбора callback магазина пакетов."""

    INVALID = "invalid"
    SHOP_INTRO = "shop_intro"
    CHOOSE_METHOD = "choose_method"


@dataclass(frozen=True)
class TariffShopNavView:
    """Данные для ответа на callback: текст и (при выборе метода) индекс пакета под клавиатуру ``pay_method_keyboard``."""

    outcome: TariffShopNavOutcome
    text: str = ""
    pkg_index: int | None = None


def resolve_tariff_shop_callback(callback_data: str) -> TariffShopNavView:
    """
    По ``callback_data`` (префикс ``pk:`` из ``payments_catalog``) определяет следующий экран.

    Возвращает:
        ``TariffShopNavView`` — при ``INVALID`` текст пустой; при ``CHOOSE_METHOD`` задан ``pkg_index``.
    """
    parsed = paycat.parse_pkg_callback(callback_data or "")
    if parsed is None:
        return TariffShopNavView(outcome=TariffShopNavOutcome.INVALID)
    if parsed == "back":
        return TariffShopNavView(
            outcome=TariffShopNavOutcome.SHOP_INTRO,
            text=build_payment_shop_intro_text(),
        )
    if isinstance(parsed, int):
        return TariffShopNavView(
            outcome=TariffShopNavOutcome.CHOOSE_METHOD,
            text=build_payment_choose_method_caption(),
            pkg_index=parsed,
        )
    return TariffShopNavView(outcome=TariffShopNavOutcome.INVALID)
