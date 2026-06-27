"""
Пакетный ночной воркер автоподгрузки Wildberries по API.

Локально: ABC-анализ, OOS/FOMO, кассовый разрыв (0 ₽).
OpenRouter: только короткий утренний инсайт из сжатой строки.
Уведомления в Telegram — ровно в 09:00 МСК через очередь SQLite.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from config import Settings, settings
from services import repository as repo
from services.wb_api.analytics import (
    build_compact_digest,
    compute_product_margins,
    run_abc_analysis,
    run_out_of_stock_forecasts,
)
from services.wb_api.client import WbApiClient
from services.wb_api.morning_ai import generate_morning_insight
from services.wb_api.notifier import NotifierPort, TelegramNotifierPort
from services.wb_api.report_builder import build_extended_report_json
from services.wb_api.types import WbBatchDigest

logger = logging.getLogger(__name__)

BATCH_SIZE = 5
BATCH_PAUSE_SEC = 2.0
_MSK_TZ = timezone(timedelta(hours=3))


def next_morning_scheduled_iso(
    *,
    hour: int = 9,
    minute: int = 0,
    tz: timezone = _MSK_TZ,
) -> str:
    """Ближайшее ``hour:minute`` в ``tz``, в ISO UTC для SQLite."""
    now = datetime.now(tz)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return target.astimezone(timezone.utc).isoformat()


async def process_wb_user(
    user_id: int,
    api_token: str,
    *,
    wb_client: WbApiClient,
    app_settings: Settings,
    report_date: date | None = None,
) -> int | None:
    """
    ETL одного пользователя: WB API → ABC/OOS → отчёт → очередь на 09:00.

    Ошибки логируются; возвращает ``report_id`` или ``None``.
    """
    try:
        raw_rows = await wb_client.fetch_product_rows(api_token)
        products = compute_product_margins(raw_rows)
        abc = run_abc_analysis(products)
        oos = run_out_of_stock_forecasts(products)
        digest = build_compact_digest(products, abc, oos)
        insight = await generate_morning_insight(app_settings, digest.compact_line)
        digest = WbBatchDigest(
            compact_line=digest.compact_line,
            net_profit=digest.net_profit,
            group_a_leader=digest.group_a_leader,
            oos_product=digest.oos_product,
            oos_days=digest.oos_days,
            fomo_rub=digest.fomo_rub,
            morning_insight=insight,
        )
        table_json = build_extended_report_json(
            products=products,
            abc=abc,
            oos_forecasts=oos,
            digest=digest,
            report_date=report_date,
        )
        report_id = await repo.insert_table_report(user_id, table_json)
        scheduled_for = next_morning_scheduled_iso(
            hour=app_settings.wb_api_morning_hour,
            minute=app_settings.wb_api_morning_minute,
        )
        await repo.insert_wb_morning_notification(
            user_id=user_id,
            report_id=report_id,
            scheduled_for=scheduled_for,
            digest_line=digest.compact_line,
            net_profit=digest.net_profit,
            group_a_leader=digest.group_a_leader,
            oos_product=digest.oos_product,
            oos_days=digest.oos_days,
            fomo_rub=digest.fomo_rub,
            morning_insight=digest.morning_insight,
        )
        logger.info(
            "wb_api_worker: user=%s report_id=%s scheduled=%s",
            user_id,
            report_id,
            scheduled_for,
        )
        return report_id
    except Exception:
        logger.exception("wb_api_worker: failed user_id=%s", user_id)
        return None


async def run_nightly_batch(
    *,
    wb_client: WbApiClient | None = None,
    app_settings: Settings | None = None,
) -> tuple[int, int]:
    """
    Обрабатывает всех пользователей с ``wb_api_tokens.enabled = 1``.

    Батчи по ``BATCH_SIZE`` с паузой ``BATCH_PAUSE_SEC`` между батчами.
    Возвращает ``(ok_count, fail_count)``.
    """
    cfg = app_settings or settings

    from services.wb_tariffs_cache import update_global_tariffs_db

    await update_global_tariffs_db()

    client = wb_client or WbApiClient(
        base_url=cfg.wb_api_base_url,
        timeout_sec=cfg.wb_api_timeout_sec,
    )
    users = await repo.list_wb_api_enabled_users()
    if not users:
        logger.info("wb_api_worker: no enabled users")
        return 0, 0

    ok = 0
    fail = 0
    today = datetime.now(_MSK_TZ).date()
    for offset in range(0, len(users), BATCH_SIZE):
        batch = users[offset : offset + BATCH_SIZE]
        results = await asyncio.gather(
            *[
                process_wb_user(uid, token, wb_client=client, app_settings=cfg, report_date=today)
                for uid, token in batch
            ]
        )
        for report_id in results:
            if report_id is not None:
                ok += 1
            else:
                fail += 1
        if offset + BATCH_SIZE < len(users):
            await asyncio.sleep(BATCH_PAUSE_SEC)

    logger.info("wb_api_worker: batch done ok=%s fail=%s total=%s", ok, fail, len(users))
    return ok, fail


async def deliver_morning_notifications(
    notifier: NotifierPort | None = None,
) -> int:
    """Отправляет все просроченные утренние уведомления (``scheduled_for <= now``)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = await repo.list_due_wb_morning_notifications(now_iso)
    if not rows:
        return 0

    port = notifier or TelegramNotifierPort()
    sent = 0
    for row in rows:
        try:
            digest = WbBatchDigest(
                compact_line=str(row.get("digest_line") or ""),
                net_profit=float(row.get("net_profit") or 0),
                group_a_leader=str(row.get("group_a_leader") or "—"),
                oos_product=row.get("oos_product"),
                oos_days=row.get("oos_days"),
                fomo_rub=float(row.get("fomo_rub") or 0),
                morning_insight=str(row.get("morning_insight") or ""),
            )
            await port.send_morning_analytics(
                int(row["user_id"]),
                digest=digest,
                report_id=int(row["report_id"]),
            )
            await repo.mark_wb_morning_notification_sent(int(row["id"]))
            sent += 1
        except Exception:
            logger.exception(
                "wb_api_worker: morning notify failed notification_id=%s user_id=%s",
                row.get("id"),
                row.get("user_id"),
            )
    logger.info("wb_api_worker: morning notifications sent=%s", sent)
    return sent


async def run_wb_api_worker_loop(
    *,
    app_settings: Settings | None = None,
    run_batch_on_start: bool | None = None,
) -> None:
    """
    Долгоживущий цикл: ночной батч (раз в сутки) + доставка уведомлений в 09:00.

    ``run_batch_on_start``: если ``True``, батч сразу при старте (для cron one-shot).
    """
    cfg = app_settings or settings
    await repo.init_db()
    client = WbApiClient(base_url=cfg.wb_api_base_url, timeout_sec=cfg.wb_api_timeout_sec)
    notifier = TelegramNotifierPort()
    last_batch_date: date | None = None
    do_batch_now = (
        run_batch_on_start
        if run_batch_on_start is not None
        else cfg.wb_api_run_batch_on_start
    )

    if do_batch_now:
        await run_nightly_batch(wb_client=client, app_settings=cfg)
        last_batch_date = datetime.now(_MSK_TZ).date()

    poll = max(15.0, float(cfg.wb_api_poll_interval_sec))
    batch_hour = int(cfg.wb_api_batch_hour)

    while True:
        now_msk = datetime.now(_MSK_TZ)
        if now_msk.hour == batch_hour and last_batch_date != now_msk.date():
            await run_nightly_batch(wb_client=client, app_settings=cfg)
            last_batch_date = now_msk.date()

        await deliver_morning_notifications(notifier)
        await asyncio.sleep(poll)


async def run_wb_api_worker() -> None:
    """Точка входа для ``NEUROMULE_PLATFORM=wb_worker``."""
    logging.basicConfig(level=logging.INFO)
    await run_wb_api_worker_loop()
