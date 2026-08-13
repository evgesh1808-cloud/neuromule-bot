"""Ежедневный отчёт администратору о балансе OpenRouter."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiohttp
from aiogram import Bot
from aiogram.enums import ParseMode

from config import Settings, settings

logger = logging.getLogger(__name__)

_MSK = timezone(timedelta(hours=3))
_REPORT_HOUR_MSK = 9
_REPORT_MINUTE_MSK = 0

OPENROUTER_CREDITS_URL = "https://openrouter.ai/api/v1/credits"
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"

OPENROUTER_WARN_BELOW_USD = 3.0

_HTTP_TIMEOUT_SEC = 30.0


@dataclass(frozen=True)
class ProviderBalanceSnapshot:
    """Результат запроса баланса одного провайдера."""

    name: str
    balance_usd: float | None
    ok: bool
    detail: str = ""


def resolve_balance_report_admin_id(cfg: Settings | None = None) -> int:
    """Telegram user id для ежедневного отчёта: ADMIN_TELEGRAM_ID или первый ADMIN_IDS."""
    cfg = cfg or settings
    owner_id = int(cfg.admin_telegram_id or 0)
    if owner_id > 0:
        return owner_id
    for raw_id in cfg.admin_ids or []:
        admin_id = int(raw_id or 0)
        if admin_id > 0:
            return admin_id
    return 0


def _seconds_until_next_msk_clock(
    hour: int,
    minute: int = 0,
    *,
    now: datetime | None = None,
) -> float:
    moment = now or datetime.now(_MSK)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_MSK)
    else:
        moment = moment.astimezone(_MSK)
    target = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if moment >= target:
        target = target + timedelta(days=1)
    return max(5.0, (target - moment).total_seconds())


def _status_icon(balance_usd: float | None, *, warn_below: float) -> str:
    if balance_usd is None:
        return "❌"
    if balance_usd < warn_below:
        return "⚠️"
    return "✅"


def _format_usd(amount: float | None) -> str:
    if amount is None:
        return "н/д"
    return f"${amount:,.2f}".replace(",", " ")


def format_api_balance_report_markdown(
    *,
    openrouter: ProviderBalanceSnapshot,
    generated_at: datetime | None = None,
) -> str:
    """Markdown-отчёт для Telegram (legacy Markdown)."""
    moment = generated_at or datetime.now(_MSK)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_MSK)
    else:
        moment = moment.astimezone(_MSK)
    stamp = moment.strftime("%d.%m.%Y, %H:%M МСК")

    or_icon = _status_icon(openrouter.balance_usd, warn_below=OPENROUTER_WARN_BELOW_USD)

    lines = [
        "📊 *Ежедневный отчёт API-балансов*",
        f"🗓 {stamp}",
        "",
        f"{or_icon} *OpenRouter*: {_format_usd(openrouter.balance_usd)}",
        f"   _Порог: {_format_usd(OPENROUTER_WARN_BELOW_USD)}_",
    ]
    if openrouter.detail:
        lines.append(f"   _{openrouter.detail}_")

    lines.extend(["", "───────────────────"])
    if openrouter.balance_usd is not None and openrouter.balance_usd < OPENROUTER_WARN_BELOW_USD:
        lines.append("💡 Низкий баланс OpenRouter — пополните аккаунт.")
    elif openrouter.ok:
        lines.append("💚 Баланс OpenRouter в норме.")
    else:
        lines.append("❌ Не удалось получить баланс OpenRouter.")

    return "\n".join(lines)


async def fetch_openrouter_balance(
    session: aiohttp.ClientSession,
    *,
    api_key: str | None = None,
) -> ProviderBalanceSnapshot:
    """GET OpenRouter credits; fallback — /api/v1/key (limit_remaining)."""
    key = (
        api_key
        if api_key is not None
        else os.getenv("OPENROUTER_API_KEY") or ""
    ).strip()
    if not key:
        return ProviderBalanceSnapshot(
            name="OpenRouter",
            balance_usd=None,
            ok=False,
            detail="OPENROUTER_API_KEY не задан",
        )

    headers = {"Authorization": f"Bearer {key}"}

    try:
        async with session.get(
            OPENROUTER_CREDITS_URL,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=_HTTP_TIMEOUT_SEC),
        ) as resp:
            if resp.status == 200:
                payload = await resp.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, dict):
                    total_credits = data.get("total_credits")
                    total_usage = data.get("total_usage")
                    try:
                        credits = float(total_credits)
                        usage = float(total_usage)
                        remaining = max(0.0, credits - usage)
                        return ProviderBalanceSnapshot(
                            name="OpenRouter",
                            balance_usd=remaining,
                            ok=True,
                            detail="аккаунт: total_credits − total_usage",
                        )
                    except (TypeError, ValueError):
                        logger.error(
                            "openrouter credits: invalid numbers credits=%r usage=%r",
                            total_credits,
                            total_usage,
                        )
                else:
                    logger.error("openrouter credits: data block missing")
            else:
                body_text = await resp.text()
                logger.error(
                    "openrouter credits HTTP %s: %s",
                    resp.status,
                    body_text[:500],
                )

        async with session.get(
            OPENROUTER_KEY_URL,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=_HTTP_TIMEOUT_SEC),
        ) as resp:
            if resp.status != 200:
                body_text = await resp.text()
                logger.error(
                    "openrouter key HTTP %s: %s",
                    resp.status,
                    body_text[:500],
                )
                return ProviderBalanceSnapshot(
                    name="OpenRouter",
                    balance_usd=None,
                    ok=False,
                    detail=f"HTTP {resp.status}",
                )
            payload = await resp.json()
    except aiohttp.ClientError as exc:
        logger.exception("openrouter balance request failed")
        return ProviderBalanceSnapshot(
            name="OpenRouter",
            balance_usd=None,
            ok=False,
            detail=str(exc)[:120],
        )
    except Exception as exc:
        logger.exception("openrouter balance parse failed")
        return ProviderBalanceSnapshot(
            name="OpenRouter",
            balance_usd=None,
            ok=False,
            detail=str(exc)[:120],
        )

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        logger.error("openrouter key: data block missing")
        return ProviderBalanceSnapshot(
            name="OpenRouter",
            balance_usd=None,
            ok=False,
            detail="нет поля data",
        )

    limit_remaining = data.get("limit_remaining")
    if limit_remaining is None:
        return ProviderBalanceSnapshot(
            name="OpenRouter",
            balance_usd=None,
            ok=True,
            detail="ключ без лимита (limit_remaining=null)",
        )

    try:
        balance = float(limit_remaining)
    except (TypeError, ValueError):
        logger.error("openrouter key: invalid limit_remaining=%r", limit_remaining)
        return ProviderBalanceSnapshot(
            name="OpenRouter",
            balance_usd=None,
            ok=False,
            detail="некорректный limit_remaining",
        )

    return ProviderBalanceSnapshot(
        name="OpenRouter",
        balance_usd=balance,
        ok=True,
        detail="остаток по API-ключу",
    )


async def build_api_balance_report(
    session: aiohttp.ClientSession | None = None,
) -> str:
    """Собирает Markdown-отчёт по балансу OpenRouter."""
    owns_session = session is None
    if owns_session:
        session = aiohttp.ClientSession()
    assert session is not None
    try:
        openrouter = await fetch_openrouter_balance(session)
    finally:
        if owns_session:
            await session.close()

    return format_api_balance_report_markdown(openrouter=openrouter)


async def send_daily_api_balance_report(cfg: Settings | None = None) -> None:
    """Запрашивает баланс OpenRouter и отправляет отчёт администратору в Telegram."""
    cfg = cfg or settings
    admin_id = resolve_balance_report_admin_id(cfg)
    if admin_id <= 0:
        logger.warning("api_balance_monitor: ADMIN_TELEGRAM_ID / ADMIN_IDS не заданы")
        return

    token = (cfg.tg_token or "").strip()
    if not token:
        logger.warning("api_balance_monitor: TG_TOKEN не задан, отчёт не отправлен")
        return

    report = await build_api_balance_report()
    bot = Bot(token=token)
    try:
        await bot.send_message(
            chat_id=admin_id,
            text=report,
            parse_mode=ParseMode.MARKDOWN,
        )
        logger.info("api_balance_monitor: report sent admin_id=%s", admin_id)
    except Exception:
        logger.exception("api_balance_monitor: telegram send failed admin_id=%s", admin_id)
    finally:
        await bot.session.close()


async def api_balance_monitor_loop() -> None:
    """Фон: каждый день в 09:00 МСК — отчёт о балансе OpenRouter."""
    while True:
        delay = _seconds_until_next_msk_clock(_REPORT_HOUR_MSK, _REPORT_MINUTE_MSK)
        logger.info(
            "api_balance_monitor_loop sleep %.0fs until next %02d:%02d MSK",
            delay,
            _REPORT_HOUR_MSK,
            _REPORT_MINUTE_MSK,
        )
        await asyncio.sleep(delay)
        try:
            await send_daily_api_balance_report()
        except Exception:
            logger.exception("api_balance_monitor_loop tick failed")
        await asyncio.sleep(60.0)
