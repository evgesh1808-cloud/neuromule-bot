"""PostgreSQL data-access layer для NeuroMule 🐎⚡️.

Состоит из двух слоёв:

* :mod:`services.database.connection` — пул соединений и
  ``db_transaction`` async-context-manager.
* :mod:`services.database.repositories` — типизированные репозитории
  (``UserRepository``, ``PaymentRepository``), принимающие
  ``asyncpg.Connection`` в ``__init__``.

Использование (см. ``platforms/handlers/payment_demo.py``):

    async with db_transaction(pool) as conn:
        repo = PaymentRepository(conn)
        is_new = await repo.claim_payment_charge(...)
"""
from __future__ import annotations

from .connection import db_transaction, init_postgres_pool
from .repositories import PaymentRepository, UserRepository

__all__ = (
    "PaymentRepository",
    "UserRepository",
    "db_transaction",
    "init_postgres_pool",
)
