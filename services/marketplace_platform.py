"""Площадки финансового аудита: колонки и формулы юнит-экономики."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MarketplacePlatformId = Literal["wildberries", "ozon", "yandex", "1c"]

DEFAULT_MARKETPLACE_PLATFORM: MarketplacePlatformId = "wildberries"

VALID_MARKETPLACE_PLATFORMS: frozenset[str] = frozenset(
    {"wildberries", "ozon", "yandex", "1c", "wb", "yandex_market", "moysklad"}
)

_PLATFORM_ALIASES: dict[str, MarketplacePlatformId] = {
    "wb": "wildberries",
    "wildberries": "wildberries",
    "ozon": "ozon",
    "yandex": "yandex",
    "yandex_market": "yandex",
    "1c": "1c",
    "moysklad": "1c",
}

PLATFORM_DISPLAY_NAMES: dict[MarketplacePlatformId, str] = {
    "wildberries": "Wildberries",
    "ozon": "Ozon",
    "yandex": "Яндекс.Маркет",
    "1c": "1С / МойСклад",
}


@dataclass(frozen=True)
class MarketplacePlatformProfile:
    """Профиль парсинга и P&L для одной площадки."""

    id: MarketplacePlatformId
    revenue_hints: tuple[str, ...]
    commission_hints: tuple[str, ...]
    logistics_hints: tuple[str, ...]
    ad_hints: tuple[str, ...]
    sales_hints: tuple[str, ...]
    delivery_hints: tuple[str, ...]
    return_hints: tuple[str, ...]
    stock_hints: tuple[str, ...]
    # Доп. удержания: буст Я.Маркета, себестоимость 1С, эквайринг Ozon и т.д.
    extra_deduction_hints: tuple[str, ...]
    simulate_wb_hidden_logistics: bool = False


_PROFILES: dict[MarketplacePlatformId, MarketplacePlatformProfile] = {
    "wildberries": MarketplacePlatformProfile(
        id="wildberries",
        revenue_hints=("перечислению", "выруч", "реализован"),
        commission_hints=("вознагражден", "комисс"),
        logistics_hints=("логистик", "доставк", "хранен", "обратн"),
        ad_hints=("продвижен", "реклам", "удержан"),
        sales_hints=("выкупили", "реализован", "продаж"),
        delivery_hints=("доставк", "к клиенту"),
        return_hints=("возврат",),
        stock_hints=("остаток", "склад", "stock"),
        extra_deduction_hints=("штраф", "компенсац"),
        simulate_wb_hidden_logistics=True,
    ),
    "ozon": MarketplacePlatformProfile(
        id="ozon",
        revenue_hints=("начислен", "перечислен", "выруч", "продаж"),
        commission_hints=("комисс", "вознагражден", "услуг"),
        logistics_hints=("логистик", "доставк", "fbo", "fbs", "последн"),
        ad_hints=("реклам", "продвижен", "трафарет", "спецразмещ"),
        sales_hints=("доставлен", "выкуп", "реализован", "продаж"),
        delivery_hints=("отправлен", "доставк"),
        return_hints=("возврат", "отмен"),
        stock_hints=("остаток", "склад"),
        extra_deduction_hints=("эквайринг", "агент", "обработк", "приём"),
    ),
    "yandex": MarketplacePlatformProfile(
        id="yandex",
        revenue_hints=("начислен", "перечислен", "выруч", "продаж"),
        commission_hints=("комисс", "вознагражден"),
        logistics_hints=("логистик", "доставк", "хранен"),
        ad_hints=("реклам", "продвижен"),
        sales_hints=("доставлен", "выкуп", "продаж", "заказ"),
        delivery_hints=("доставк", "отгруз"),
        return_hints=("возврат",),
        stock_hints=("остаток", "склад"),
        extra_deduction_hints=("буст", "boost", "показы", "клики", "размещен"),
    ),
    "1c": MarketplacePlatformProfile(
        id="1c",
        revenue_hints=("выруч", "реализац", "продаж", "доход"),
        commission_hints=("комисс", "расход"),
        logistics_hints=("логистик", "доставк"),
        ad_hints=("реклам", "маркетинг"),
        sales_hints=("кол-во", "количество", "продаж", "реализ"),
        delivery_hints=("отгруз", "доставк"),
        return_hints=("возврат",),
        stock_hints=("остаток", "склад", "налич"),
        extra_deduction_hints=("себестоим", "cost", "закуп", "поступлен"),
    ),
}


def normalize_marketplace_platform(raw: str | None) -> MarketplacePlatformId:
    key = (raw or "").strip().lower().replace(" ", "_")
    return _PLATFORM_ALIASES.get(key, DEFAULT_MARKETPLACE_PLATFORM)


def get_marketplace_profile(platform: str | None) -> MarketplacePlatformProfile:
    pid = normalize_marketplace_platform(platform)
    return _PROFILES[pid]


def platform_display_name(platform: str | None) -> str:
    pid = normalize_marketplace_platform(platform)
    return PLATFORM_DISPLAY_NAMES[pid]
