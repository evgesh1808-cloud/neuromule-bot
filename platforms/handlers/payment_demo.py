"""Образец платёжного хэндлера на новом data-access слое.

Этот модуль — **референс**, как должен выглядеть production-handler
на ``asyncpg``-pool'е после миграции (PR-P Phase 1). Не подключён
в дефолтный router бота — для активации добавьте в
``platforms/telegram_bot.py::build_dispatcher`` строку:

    from platforms.handlers.payment_demo import router as payment_demo_router
    dp.include_router(payment_demo_router)

И в ``run_telegram()`` передайте pool через DI:

    pool = await init_postgres_pool(settings.postgres_dsn)
    dp.workflow_data["pg_pool"] = pool

Ключевые свойства образца:

1. **Атомарность.** Весь критический путь (claim → начисление) живёт
   в одной ``async with db_transaction(pool):`` — любой сбой =
   автоматический rollback. Manual saga compensation, описанная в
   PR-E для SQLite, здесь не нужна.

2. **Idempotency.** ``claim_payment_charge`` через ``ON CONFLICT
   DO NOTHING``. Повторный ``successful_payment`` от Telegram (после
   нашего таймаута на ответ) сразу даёт ``False`` без побочных эффектов.

3. **Observability (PR-H).** Метрики ``payment.success`` /
   ``payment.duplicate`` / ``payment.failed`` пишутся по той же
   таксономии, что и в legacy-флоу.

4. **Никаких ``except Exception``.** Generic-фолбэк живёт строго в
   ``db_transaction`` — здесь call-site пробрасывает наверх, чтобы
   middleware ``aiogram``-а корректно ответил юзеру через
   ``ErrorEvent``-обработчик (или ничего не сделал, если retry
   Telegram'а нам подходит).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.types import Message

from services import metrics
from services.database import (
    PaymentRepository,
    UserRepository,
    db_transaction,
)

if TYPE_CHECKING:
    from asyncpg import Pool

logger = logging.getLogger(__name__)

router = Router(name="payment_demo")


@router.message(F.successful_payment)
async def on_successful_payment(message: Message, pg_pool: "Pool") -> None:
    """Обработчик ``successful_payment`` поверх Postgres data-access слоя.

    Параметр ``pg_pool`` инжектится aiogram-Dispatcher'ом из
    ``workflow_data["pg_pool"]`` — единый pool на весь процесс бота.
    """

    sp = message.successful_payment
    if sp is None or message.from_user is None:
        return

    user_id = int(message.from_user.id)
    charge_id = (sp.telegram_payment_charge_id or "").strip()
    pack_index = _parse_pack_index(sp.invoice_payload)
    if not charge_id or pack_index < 0:
        metrics.incr("payment.invalid", {"reason": "bad_payload"})
        logger.warning(
            "payment_demo: bad payload user=%s charge=%s payload=%r",
            user_id,
            charge_id,
            sp.invoice_payload,
        )
        return

    async with db_transaction(pg_pool) as conn:
        payments = PaymentRepository(conn)
        is_new = await payments.claim_payment_charge(
            charge_id, user_id, pack_index
        )

        if not is_new:
            metrics.incr("payment.duplicate")
            logger.info(
                "payment_demo: duplicate charge_id=%s user=%s pack=%s",
                charge_id,
                user_id,
                pack_index,
            )
            return

        users = UserRepository(conn)
        # ensure юзер существует и TOS-флаг живой (для корректной
        # cross-сессии работы с другими репозиториями).
        if not await users.is_tos_accepted(user_id):
            await users.accept_tos(user_id)

        # Здесь дальнейшее начисление — заглушка для образца.
        # В production вызовется BillingService.apply_purchase(conn, ...)
        # с явным conn-параметром (Phase 2 миграции).
        await _credit_pack_placeholder(conn, user_id, pack_index)

        metrics.incr(
            "payment.success",
            {"pack": str(pack_index), "source": "pg_demo"},
        )
        logger.info(
            "payment_demo: credited user=%s pack=%s charge=%s",
            user_id,
            pack_index,
            charge_id,
        )


# ── Внутренние helper'ы (вынесены вне handler'а для тестируемости) ──────


def _parse_pack_index(invoice_payload: str | None) -> int:
    """Извлекает ``pack_index`` из payload вида ``"u<uid>:p<pkg>:m<method>"``.

    Возвращает ``-1`` если формат не распознан — call-site трактует
    это как ``payment.invalid{reason=bad_payload}``.
    """

    if not invoice_payload:
        return -1
    try:
        for part in invoice_payload.split(":"):
            if part.startswith("p"):
                return int(part[1:])
    except ValueError:
        return -1
    return -1


async def _credit_pack_placeholder(conn, user_id: int, pack_index: int) -> None:
    """Заглушка начисления — будет заменена на ``BillingService`` на Phase 2.

    На текущей фазе миграции реальный billing продолжает писать в
    SQLite (см. ``services/billing/store.py``). Чтобы атомарность
    претензии и начисления была честной, BillingService должен принимать
    тот же ``conn``-объект и работать через тот же data-access слой.
    """

    # placeholder — никаких записей в БД, только лог для трассировки.
    logger.debug(
        "payment_demo._credit_pack_placeholder user=%s pack=%s conn=%s",
        user_id,
        pack_index,
        id(conn),
    )


__all__ = ("router",)
