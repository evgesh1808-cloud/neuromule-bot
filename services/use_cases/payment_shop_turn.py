"""
Тексты магазина оплаты (шаг выбора пакета и шаг выбора способа оплаты).

Нужен чтобы Telegram-хендлеры не дублировали склейку строк из ``content.messages``.
"""

from __future__ import annotations

from content import messages as msg


def build_payment_shop_intro_text() -> str:
    """Собирает текст экрана «Тарифы / пакеты энергии» с подставленным списком планов из конфигурации копирайта."""
    plans = "\n\n".join(msg.TARIFF_PLANS)
    return msg.TXT_PAY_SHOP_INTRO.format(plans=plans)


def build_payment_choose_method_caption() -> str:
    """Возвращает подпись экрана выбора способа оплаты (карта ЮKassa или Telegram Stars)."""
    return msg.TXT_PAY_CHOOSE_METHOD


def build_tariffs_entry_text() -> str:
    """Текст по кнопке главного меню «Тарифы» — тот же экран, что и список пакетов энергии."""
    return build_payment_shop_intro_text()
