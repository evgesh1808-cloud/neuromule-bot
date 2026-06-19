"""Дополнительные индексы для hot queries (PR-O).

Содержит только ``CREATE INDEX IF NOT EXISTS`` — это безопасные
идемпотентные миграции, которые можно повторно запускать без вреда.
Вызывается из ``init_db()`` после всех ``CREATE TABLE``.

Какие запросы прикрыты:

* ``SELECT COUNT(*) FROM referrals WHERE inviter_id = ?``
  → ``idx_referrals_inviter_id`` (composite-PK `(invited_id)` не помогал).
* ``SELECT ... FROM payment_events WHERE user_id = ? ORDER BY created_at``
  → ``idx_payment_events_user_created`` (composite, покрывает оба
  предиката одним index seek'ом).
* ``SELECT tariff, COUNT(*) FROM payment_events WHERE created_at = ? GROUP BY tariff``
  → ``idx_payment_events_created_at``.
* ``SELECT * FROM payment_charges WHERE user_id = ?`` (audit/backoffice)
  → ``idx_payment_charges_user_id``.
* ``SELECT id FROM users WHERE referred_by = ?`` (backref-аналитика)
  → partial ``idx_users_referred_by`` (NULL-skewed, partial-index
  пропускает NULL-значения и в 5-10x меньше full).

Каждый ``CREATE INDEX`` — отдельный SQL statement. Если миграция
прервётся (например, по power-loss посередине), при следующем
запуске она просто продолжится с того же места.
"""
from __future__ import annotations

import logging

import aiosqlite

logger = logging.getLogger(__name__)


# Список (имя, SQL). Имена начинаются с ``idx_pro_`` (PR-O) — чтобы не
# конфликтовать с уже существующими ``idx_*`` индексами.
_PR_O_INDEXES: list[tuple[str, str]] = [
    (
        "idx_pro_referrals_inviter_id",
        "CREATE INDEX IF NOT EXISTS idx_pro_referrals_inviter_id "
        "ON referrals (inviter_id)",
    ),
    (
        "idx_pro_payment_events_user_created",
        "CREATE INDEX IF NOT EXISTS idx_pro_payment_events_user_created "
        "ON payment_events (user_id, created_at)",
    ),
    (
        "idx_pro_payment_events_created_at",
        "CREATE INDEX IF NOT EXISTS idx_pro_payment_events_created_at "
        "ON payment_events (created_at)",
    ),
    (
        "idx_pro_payment_charges_user_id",
        "CREATE INDEX IF NOT EXISTS idx_pro_payment_charges_user_id "
        "ON payment_charges (user_id)",
    ),
    (
        # Partial index — пропускает строки с NULL (а это большинство
        # пользователей без реферера). Размер индекса = ~10% от full,
        # запросы «найти приглашённых юзером X» отрабатывают за O(log N).
        "idx_pro_users_referred_by",
        "CREATE INDEX IF NOT EXISTS idx_pro_users_referred_by "
        "ON users (referred_by) WHERE referred_by IS NOT NULL",
    ),
]


async def ensure_pr_o_indexes(db: aiosqlite.Connection) -> None:
    """Идемпотентно создаёт все индексы PR-O в открытом соединении.

    Не делает ``commit`` — это ответственность вызывающего кода
    (обычно ``init_db``, который коммитит всё разом в конце).
    """

    for name, sql in _PR_O_INDEXES:
        await db.execute(sql)
        logger.debug("db_indexes: ensured %s", name)

    logger.info(
        "db_indexes: PR-O indexes ensured (count=%s)", len(_PR_O_INDEXES)
    )


__all__ = ("ensure_pr_o_indexes",)
