"""Детектор ошибки «Stars insufficient balance» + UX-хинт про карту.

Изолированный платёжный модуль, спроектирован так, чтобы **исключить
ложные срабатывания**: подсказка про карту РФ показывается ТОЛЬКО при
точном whitelist-маркере Telegram, а не при сетевых глитчах /
``BOT_PAYMENTS_DISABLED`` / ``PROVIDER_TOKEN_INVALID``.

Архитектурное решение:

* Whitelist — frozenset уже UPPER-CASED маркеров. Расширяется
  prod-наблюдениями без правки потребителей.
* Функция ``is_stars_insufficient_balance`` принимает на вход СТРОКУ
  (`str(exc)`), а не сам `Exception` — упрощает unit-тестирование и
  изолирует от деталей aiogram-классов.
* Текст подсказки лежит в ``content/messages.py`` (правило: тексты —
  только там; здесь — только логика обнаружения).
"""

from __future__ import annotations

from typing import Final


INSUFFICIENT_STARS_MARKERS: Final[frozenset[str]] = frozenset(
    {
        # Внутренние коды Bot API при выставлении Stars-инвойса, когда
        # на аккаунте недостаточно ⭐. Подтверждены production-логами.
        "BALANCE_TOO_LOW",
        "INSUFFICIENT_BALANCE",
        "INSUFFICIENT_FUNDS",
        "PAYMENT_REQUIRES_TOPUP",
        # Иногда Telegram отвечает текстом из payment-API:
        "STARS_BALANCE_TOO_LOW",
        "STAR_BALANCE_TOO_LOW",
    }
)


def is_stars_insufficient_balance(error_text: str | None) -> bool:
    """Strict-whitelist детектор «не хватает Telegram Stars».

    Возвращает ``True`` ТОЛЬКО при точном вхождении маркера из
    ``INSUFFICIENT_STARS_MARKERS`` в текст ошибки. Любые иные коды —
    сетевые сбои, отключённый провайдер, неверный токен, deprecated method —
    возвращают ``False`` (т.е. подсказка про карту НЕ показывается, чтобы
    не вводить юзера в заблуждение).

    Сравнение case-insensitive: Telegram бывает выдаёт коды как
    BadRequest text в смешанном регистре в разных версиях API.
    """

    if not error_text:
        return False
    haystack = error_text.upper()
    return any(marker in haystack for marker in INSUFFICIENT_STARS_MARKERS)


__all__ = ("INSUFFICIENT_STARS_MARKERS", "is_stars_insufficient_balance")
