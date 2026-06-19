"""
Use-case: «👤 Мой профиль» — единый сборщик HTML кабинета.

Содержит расчёт доступных генераций по ⚡/💎 и блок премиум-медиа (строго 💎).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from business_catalog import catalog
from config import Settings
from services.billing.types import TariffTier
from services.repository import (
    UserRow,
    get_user_row,
    referrals_count,
)

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class ProfileView:
    """Готовый HTML текст для отправки в Telegram."""

    text: str


# --- утилиты ---


def _bar(current: int, maximum: int, width: int = 10) -> str:
    if maximum <= 0:
        return "░" * width
    filled = max(0, min(width, round(current / maximum * width)))
    return "█" * filled + "░" * (width - filled)


def _format_subscription_end(raw: str | None) -> str:
    if not raw:
        return "—"
    try:
        return date.fromisoformat(str(raw)[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return str(raw)


def _hd_profile_label(birth_data: str | None) -> str:
    for line in (birth_data or "").splitlines():
        low = line.lower().strip()
        if low.startswith("профиль:"):
            return line.split(":", 1)[1].strip() or "—"
    return "—"


def _free_photos_left(row: UserRow, daily_limit: int) -> int:
    today = date.today().isoformat()
    if row.photo_daily_date != today:
        return daily_limit
    return max(0, daily_limit - int(row.photo_daily_count or 0))


# --- блоки текста ---


def _header_block(user_id: int) -> str:
    return (
        "👤 <b>Мой профиль NeuroMule 🐎⚡️</b>\n\n"
        f"🆔 <b>Твой ID:</b> <code>{user_id}</code>"
    )


def _crystals_block(tariff: TariffTier, sub: int, buy: int) -> str:
    total = sub + buy
    if tariff is TariffTier.FREE or sub <= 0:
        return f"<b>💎 Баланс:</b> <code>{total}</code> Кристаллов"
    return (
        f"<b>💎 Баланс:</b> <code>{total}</code> Кристаллов "
        f"<i>(из них {sub} по тарифу, {buy} вечные)</i>"
    )


def _tariff_block(tariff: TariffTier, subscription_ends_at: str | None) -> str:
    if tariff is TariffTier.FREE:
        return "<b>📊 Текущий тариф:</b> <code>FREE</code> 🎁"
    end = _format_subscription_end(subscription_ends_at)
    return (
        f"<b>📊 Текущий тариф:</b> <code>{tariff.value}</code> 🚀\n"
        f"<b>📅 Подписка активна до:</b> {end}"
    )


def _energy_block(energy: int, energy_max: int) -> str:
    return (
        f"• ⚡️ <b>Доступная Энергия:</b> {_bar(energy, energy_max)} "
        f"<b>{energy}</b> / {energy_max}"
    )


def _photos_block_free(photos_left: int, limit: int) -> str:
    return (
        f"• 🎨 <b>Базовые фото:</b> {_bar(photos_left, limit)} "
        f"<b>{photos_left}</b> / {limit} сегодня"
    )


def _photos_block_paid(sub_crystals: int, max_sub: int) -> str:
    return (
        f"• 💎 <b>Подписочные Кристаллы:</b> {_bar(sub_crystals, max_sub or 1)} "
        f"<b>{sub_crystals}</b> / {max_sub or '—'}"
    )


def _photo_capacity_block(energy: int, total_diamonds: int) -> str:
    imagen_e = energy // 10
    flux_e = energy // 30
    banana_pro_e = energy // 35
    imagen_d = total_diamonds // 2
    flux_d = total_diamonds // 3
    gpt_image_d = total_diamonds // 5
    if energy <= 0:
        primary = (
            f"• 🌠 <b>Imagen 4</b> ➔ <code>{imagen_d}</code> шт. (за 💎)\n"
            f"• 🎨 <b>Flux Schnell PRO</b> ➔ <code>{flux_d}</code> шт. (за 💎)"
        )
    else:
        primary = (
            f"• 🌠 <b>Imagen 4</b> ➔ <code>{imagen_e}</code> шт. (⚡) / "
            f"<code>{imagen_d}</code> шт. (💎)\n"
            f"• 🎨 <b>Flux Schnell PRO</b> ➔ <code>{flux_e}</code> шт. (⚡) / "
            f"<code>{flux_d}</code> шт. (💎)\n"
            f"• 🍌 <b>Nano Banana Pro</b> ➔ <code>{banana_pro_e}</code> шт. (⚡)"
        )
    return (
        "🎯 <b>Доступно генераций фото прямо сейчас:</b>\n"
        f"{primary}\n"
        f"• 🎨 <b>GPT Image 2 (DALL-E 3)</b> ➔ <code>{gpt_image_d}</code> шт. <i>(строго 💎)</i>"
    )


def _premium_media_block(total_diamonds: int) -> str:
    video_anim = total_diamonds // 20
    music = total_diamonds // 15
    if total_diamonds >= 70:
        hd_full = f"доступно <b>{total_diamonds // 70}</b> шт."
    else:
        hd_full = f"нужно ещё <b>{70 - total_diamonds} 💎</b>"
    if total_diamonds >= 50:
        hd_match = f"доступно <b>{total_diamonds // 50}</b> шт."
    else:
        hd_match = f"нужно ещё <b>{50 - total_diamonds} 💎</b>"
    return (
        "💎 <b>Премиум-медиа</b> <i>(строго за Кристаллы)</i>\n"
        f"• 🎬 <b>Видео / ✨ Оживление</b> ➔ <code>{video_anim}</code> шт.\n"
        f"• 🎸 <b>Музыка Suno</b> ➔ <code>{music}</code> шт.\n"
        f"• 🧬 <b>Полный разбор HD:</b> {hd_full}\n"
        f"• 💞 <b>Совместимость HD:</b> {hd_match}"
    )


def _hd_block(has_pro: bool, hd_type: str | None, hd_birth_data: str | None) -> str:
    if not has_pro or not (hd_type or "").strip():
        return (
            "🧬 <b>Дизайн Человека:</b>\n"
            "<i>Карта ещё не рассчитана. Запусти «Полный разбор HD» в меню «🎨 Создать» ➔ "
            "«🧬 Дизайн человека» — открой свой Тип, Стратегию и Авторитет навсегда.</i>"
        )
    hd = (hd_type or "").strip()
    profile = _hd_profile_label(hd_birth_data)
    return f"🧬 <b>Дизайн Человека:</b>\n✅ Твой тип: <b>{hd} ({profile})</b>"


def _limits_footer(tariff: TariffTier) -> str:
    if tariff is TariffTier.FREE:
        return (
            "🔄 <i>Бесплатные лимиты обновляются в 00:00 по МСК. "
            "Неиспользованные остатки сгорают.</i>"
        )
    return (
        "🔄 <i>Лимиты тарифа обновляются каждые 30 дней. Неиспользованные "
        "Кристаллы подписки сгорают, купленные отдельно — хранятся навсегда.</i>"
    )


def _referral_block(bot_username: str, user_id: int, invites: int) -> str:
    ref_link = f"https://t.me/{bot_username}?start=ref{user_id}"
    return (
        "🤝 <b>Реферальная программа Мула</b>\n"
        "Приглашай друзей и получай <b>+2 💎</b> за каждого, кто подпишется на канал.\n\n"
        f"🔗 <b>Твоя ссылка:</b>\n<code>{ref_link}</code>\n\n"
        f"👥 <i>Уже приглашено: {invites} чел.</i>"
    )


# --- main builder ---


async def build_user_profile_html(settings: Settings, user_id: int) -> str:
    """Собирает финальный HTML-текст экрана «👤 Мой профиль»."""
    row = await get_user_row(user_id)
    invites = await referrals_count(user_id)

    tariff = TariffTier.from_db(row.tariff)
    sub = int(row.sub_crystals or 0)
    buy = int(row.buy_crystals or 0)
    total_diamonds = sub + buy
    energy = int(row.energy or 0)

    photo_limit = settings.free_daily_photo_limit
    energy_max = (
        catalog.daily_free_energy if tariff is TariffTier.FREE else max(1, energy or settings.mini_energy)
    )

    parts: list[str] = [
        _header_block(user_id),
        _crystals_block(tariff, sub, buy),
    ]
    parts.append("\n———————————————")
    parts.append(_tariff_block(tariff, row.subscription_ends_at))
    parts.append(_energy_block(energy, catalog.daily_free_energy if tariff is TariffTier.FREE else energy_max))
    if tariff is TariffTier.FREE:
        parts.append(_photos_block_free(_free_photos_left(row, photo_limit), photo_limit))
    else:
        max_sub_pack = _tariff_pack_crystals(tariff)
        parts.append(_photos_block_paid(sub, max_sub_pack))

    parts.append("")
    parts.append(_photo_capacity_block(energy, total_diamonds))
    parts.append("")
    parts.append(_premium_media_block(total_diamonds))
    parts.append("")
    parts.append(_hd_block(bool(row.has_pro_analysis), row.hd_type, row.hd_birth_data))
    parts.append("")
    parts.append(_limits_footer(tariff))
    parts.append("\n———————————————")
    bot_username = settings.telegram_bot_username.lstrip("@")
    parts.append(_referral_block(bot_username, user_id, invites))
    return "\n".join(parts)


def _tariff_pack_crystals(tariff: TariffTier) -> int:
    """Максимум sub_crystals по тарифу — для шкалы прогресса в платном кабинете."""
    pack = catalog.shop_packs.get(tariff.value)
    if not pack:
        return 0
    return int(pack.get("crystals", 0) or 0)


# --- обратная совместимость: старый CabinetView ---


@dataclass(frozen=True)
class CabinetView:
    text: str


async def build_cabinet_view(settings: Settings, user_id: int) -> CabinetView:
    """Тонкая обёртка для существующих хендлеров (`menu_support.refresh_profile`)."""
    text = await build_user_profile_html(settings, user_id)
    return CabinetView(text=text)
