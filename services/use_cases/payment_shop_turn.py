"""
Тексты магазина оплаты (шаг выбора пакета и шаг выбора способа оплаты).

Цены на тарифы и кристалл-пакеты — строго фиксированные (сетка из
``config.settings`` / ``SHOP_PACKS``). Подарочные коды не влияют на стоимость тарифов.
"""

from __future__ import annotations

from content import messages as msg
from config import Settings, settings


def _rub(kopecks: int) -> int:
    return kopecks // 100


def build_tariffs_main_text(cfg: Settings | None = None) -> str:
    """Главный продающий экран «Тарифы» (HTML) с фиксированными ценами."""
    s = cfg or settings
    return msg.TXT_TARIFFS_MAIN.format(
        mini_rub=_rub(s.mini_rub_kopecks),
        mini_stars=s.mini_stars,
        smart_rub=_rub(s.smart_rub_kopecks),
        smart_stars=s.smart_stars,
        ultra_rub=_rub(s.ultra_rub_kopecks),
        ultra_stars=s.ultra_stars,
        c10_rub=_rub(s.crystals_10_rub_kopecks),
        c10_stars=s.crystals_10_stars,
        c40_rub=_rub(s.crystals_40_rub_kopecks),
        c40_stars=s.crystals_40_stars,
        c100_rub=_rub(s.crystals_100_rub_kopecks),
        c100_stars=s.crystals_100_stars,
    )


def build_payment_shop_intro_text() -> str:
    """Экран списка тарифов (совпадает с главным экраном «Тарифы»)."""
    return build_tariffs_main_text()


def build_payment_choose_method_caption() -> str:
    """Возвращает подпись экрана выбора способа оплаты (карта ЮKassa или Telegram Stars)."""
    return msg.TXT_PAY_CHOOSE_METHOD


def build_tariffs_entry_text() -> str:
    """Текст по кнопке главного меню «Тарифы»."""
    return build_tariffs_main_text()
