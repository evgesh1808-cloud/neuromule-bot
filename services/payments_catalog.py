"""Пакеты магазина для Telegram Payments (ЮKassa / Stars)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice

from config import settings


@dataclass(frozen=True)
class EnergyPack:
    index: int
    tariff: str
    energy: int
    crystals: int
    rub_kopecks: int
    stars: int
    is_tariff: bool = True

    @property
    def button_label(self) -> str:
        rub = self.rub_kopecks // 100
        if self.energy > 0:
            return f"{self.tariff} — {self.energy} ⚡️ + {self.crystals} 💎 • {rub}₽ / {self.stars} ⭐"
        return f"{self.tariff} — {self.crystals} 💎 • {rub}₽ / {self.stars} ⭐"


def load_energy_packages() -> tuple[EnergyPack, ...]:
    return (
        EnergyPack(
            0,
            "MINI",
            settings.mini_energy,
            settings.mini_crystals,
            settings.mini_rub_kopecks,
            settings.mini_stars,
        ),
        EnergyPack(
            1,
            "SMART",
            settings.smart_energy,
            settings.smart_crystals,
            settings.smart_rub_kopecks,
            settings.smart_stars,
        ),
        EnergyPack(
            2,
            "ULTRA",
            settings.ultra_energy,
            settings.ultra_crystals,
            settings.ultra_rub_kopecks,
            settings.ultra_stars,
        ),
        EnergyPack(
            3,
            "10 💎",
            0,
            settings.crystals_10_amount,
            settings.crystals_10_rub_kopecks,
            settings.crystals_10_stars,
            False,
        ),
        EnergyPack(
            4,
            "40 💎",
            0,
            settings.crystals_40_amount,
            settings.crystals_40_rub_kopecks,
            settings.crystals_40_stars,
            False,
        ),
        EnergyPack(
            5,
            "100 💎",
            0,
            settings.crystals_100_amount,
            settings.crystals_100_rub_kopecks,
            settings.crystals_100_stars,
            False,
        ),
    )


PACKAGES = load_energy_packages()

CB_PAY_PKG_PREFIX = "pk:"
CB_PAY_METHOD_PREFIX = "pm:"

_RE_PKG = re.compile(r"^pk:(\d+|back)$")
_RE_METHOD = re.compile(r"^pm:(\d+):([rx])$")


def shop_packages_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=p.button_label, callback_data=f"{CB_PAY_PKG_PREFIX}{p.index}")]
        for p in PACKAGES
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pay_method_keyboard(pkg_index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="ЮKassa 💳", callback_data=f"{CB_PAY_METHOD_PREFIX}{pkg_index}:r"),
                InlineKeyboardButton(text="Stars ⭐", callback_data=f"{CB_PAY_METHOD_PREFIX}{pkg_index}:x"),
            ],
            [InlineKeyboardButton(text="⬅️ К пакетам", callback_data=f"{CB_PAY_PKG_PREFIX}back")],
        ]
    )


def parse_pkg_callback(data: str) -> int | str | None:
    m = _RE_PKG.match(data or "")
    if not m:
        return None
    g = m.group(1)
    if g == "back":
        return "back"
    idx = int(g)
    if idx < 0 or idx >= len(PACKAGES):
        return None
    return idx


def parse_method_callback(data: str) -> tuple[int, str] | None:
    m = _RE_METHOD.match(data or "")
    if not m:
        return None
    idx = int(m.group(1))
    if idx < 0 or idx >= len(PACKAGES):
        return None
    return idx, m.group(2)


def build_invoice_payload(user_id: int, pkg_index: int, method: str) -> str:
    """Компактный payload для сверки в successful_payment (≤128 байт)."""
    return f"nm:{user_id}:{pkg_index}:{method}"


def parse_invoice_payload(payload: str) -> tuple[int, int, str] | None:
    if not payload or len(payload) > 128:
        return None
    parts = payload.split(":")
    if len(parts) != 4 or parts[0] != "nm":
        return None
    try:
        uid = int(parts[1])
        pkg = int(parts[2])
        method = parts[3]
    except ValueError:
        return None
    if method not in ("r", "x") or pkg < 0 or pkg >= len(PACKAGES):
        return None
    return uid, pkg, method


def labeled_prices_for(pack: EnergyPack, method: str) -> list[LabeledPrice]:
    label = f"{pack.energy} ⚡️ + {pack.crystals} 💎" if pack.energy > 0 else f"{pack.crystals} 💎 кристаллов"
    if method == "r":
        return [LabeledPrice(label=label, amount=pack.rub_kopecks)]
    return [LabeledPrice(label=label, amount=pack.stars)]


def invoice_currency(method: str) -> str:
    return "RUB" if method == "r" else "XTR"


def provider_token_for(method: str, yookassa_token: str) -> str:
    return yookassa_token if method == "r" else ""
