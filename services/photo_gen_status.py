"""Статус-сообщение photo-генерации: один msg, до 2 edit, delete на успех.

Масштабирование: без глобального state — только asyncio.Task на активный job
(у нас один photo-worker; максимум 2 edit / job / ~90 с → пренебрежимо для Telegram API).
"""

from __future__ import annotations

import asyncio
import html
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError

from services.billing.image_pipeline import FREE_PHOTO_MODEL_KEY, normalize_image_model
from services.photo_aspect_ratio import normalize_photo_aspect_ratio

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message

logger = logging.getLogger(__name__)

# Не чаще: 2 edit за job — безопасно при 100k юзеров (активных job << DAU).
PHOTO_STATUS_PROGRESS_DELAYS_SEC: tuple[int, ...] = (45, 90)


def photo_gen_eta_hint(*, model_id: str, used_daily_slot: bool = False) -> str:
    """Оценка времени в UI (не SLA)."""
    key = normalize_image_model(model_id)
    if used_daily_slot or key in (FREE_PHOTO_MODEL_KEY, "flux_schnell"):
        return "1–3 мин"
    return "30–90 сек"


def format_photo_gen_status_html(
    *,
    model_label: str,
    aspect_ratio: str,
    phase: int = 0,
    eta_hint: str = "1–3 мин",
) -> str:
    """Фаза 0 — принято; 1 — рисую; 2 — финал."""
    model = html.escape((model_label or "модель").strip())
    aspect = html.escape(normalize_photo_aspect_ratio(aspect_ratio))
    eta = html.escape(eta_hint)
    if phase <= 0:
        body = f"⏱ ~{eta} · можно закрыть чат"
    elif phase == 1:
        body = "⏱ Ещё рисую… почти готово"
    else:
        body = "⏱ Финальные штрихи · скоро пришлю фото"
    return (
        f"🎨 <b>Генерирую</b> · {model} · {aspect}\n"
        f"{body}"
    )


async def send_photo_gen_status_message(
    bot: Bot,
    chat_id: int,
    *,
    model_label: str,
    aspect_ratio: str,
    model_id: str = "",
    used_daily_slot: bool = False,
) -> Message | None:
    """Отправить единственное статус-сообщение; ``message_id`` → ``fire_photo_job``."""
    eta = photo_gen_eta_hint(model_id=model_id, used_daily_slot=used_daily_slot)
    text = format_photo_gen_status_html(
        model_label=model_label,
        aspect_ratio=aspect_ratio,
        phase=0,
        eta_hint=eta,
    )
    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )
    except (TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError) as exc:
        logger.warning("photo status send failed chat_id=%s: %s", chat_id, exc)
        return None
    except Exception:
        logger.error("photo status send unexpected chat_id=%s", chat_id, exc_info=True)
        return None


async def _photo_status_progress_loop(
    bot: Bot,
    chat_id: int,
    message_id: int,
    *,
    model_label: str,
    aspect_ratio: str,
    eta_hint: str,
    stop: asyncio.Event,
) -> None:
    for phase, delay in enumerate(PHOTO_STATUS_PROGRESS_DELAYS_SEC, start=1):
        try:
            await asyncio.wait_for(stop.wait(), timeout=float(delay))
            return
        except TimeoutError:
            pass
        text = format_photo_gen_status_html(
            model_label=model_label,
            aspect_ratio=aspect_ratio,
            phase=phase,
            eta_hint=eta_hint,
        )
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest as exc:
            desc = (str(getattr(exc, "message", "")) or str(exc)).lower()
            if "message is not modified" in desc:
                continue
            return
        except (TelegramForbiddenError, TelegramNetworkError) as exc:
            logger.debug("photo status progress edit skipped: %s", exc)
            return
        except Exception:
            logger.warning(
                "photo status progress edit failed chat_id=%s msg_id=%s",
                chat_id,
                message_id,
                exc_info=True,
            )
            return


@asynccontextmanager
async def photo_status_progress_scope(
    bot: Bot | None,
    chat_id: int,
    status_message_id: int | None,
    *,
    model_label: str,
    aspect_ratio: str,
    model_id: str = "",
    used_daily_slot: bool = False,
) -> AsyncIterator[None]:
    """Фоновые edit статуса; отмена при выходе (успех / ошибка / delete)."""
    if bot is None or status_message_id is None:
        yield
        return

    stop = asyncio.Event()
    eta = photo_gen_eta_hint(model_id=model_id, used_daily_slot=used_daily_slot)
    task = asyncio.create_task(
        _photo_status_progress_loop(
            bot,
            chat_id,
            int(status_message_id),
            model_label=model_label,
            aspect_ratio=aspect_ratio,
            eta_hint=eta,
            stop=stop,
        ),
        name=f"photo_status_progress:{status_message_id}",
    )
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
