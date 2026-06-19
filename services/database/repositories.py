"""Типизированные репозитории data-access слоя.

Дизайн:

* Каждый репозиторий принимает ``asyncpg.Connection`` в ``__init__``
  и хранит его. Один connection = одна транзакция (Unit-of-Work).
* Репозитории НЕ открывают/коммитят/роллбэкают транзакции — это
  делает ``db_transaction`` в ``services.database.connection``.
* Методы возвращают plain Python-типы (``bool``, ``int``, dataclass'ы),
  без ``asyncpg.Record`` за пределами модуля.
* Никаких generic ``except Exception``: ошибки драйвера пробрасываются
  в ``db_transaction`` и там логируются перед rollback'ом.

Используется:

    async with db_transaction(pool) as conn:
        users = UserRepository(conn)
        if not await users.is_tos_accepted(user_id):
            await users.accept_tos(user_id)

        payments = PaymentRepository(conn)
        is_new = await payments.claim_payment_charge(charge_id, user_id, pkg)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from asyncpg import Connection


class UserRepository:
    """Операции над таблицей ``users``."""

    __slots__ = ("_conn",)

    def __init__(self, conn: "Connection") -> None:
        self._conn = conn

    async def is_tos_accepted(self, user_id: int) -> bool:
        """Проверка принятия пользовательского соглашения.

        Hot-path: вызывается из ``TosGateMiddleware`` на каждом
        update'е. Запрос идёт по PK ``users.id`` — O(log N) с
        btree-индексом, который PG строит автоматически на PRIMARY KEY.
        """

        value = await self._conn.fetchval(
            "SELECT accepted_terms FROM users WHERE id = $1",
            user_id,
        )
        return bool(value)

    async def accept_tos(self, user_id: int) -> None:
        """Фиксирует факт принятия TOS + timestamp ``accepted_terms_at``.

        Если строки юзера ещё нет — создаёт её с дефолтами и сразу
        выставляет ``accepted_terms = TRUE``. Это убирает гонку
        ``ensure_user → accept_tos`` в один INSERT...ON CONFLICT.
        """

        await self._conn.execute(
            """
            INSERT INTO users (id, accepted_terms, accepted_terms_at, created_at)
            VALUES ($1, TRUE, NOW(), NOW())
            ON CONFLICT (id) DO UPDATE
                SET accepted_terms = TRUE,
                    accepted_terms_at = NOW()
            """,
            user_id,
        )


class PaymentRepository:
    """Операции над платёжными таблицами."""

    __slots__ = ("_conn",)

    def __init__(self, conn: "Connection") -> None:
        self._conn = conn

    async def claim_payment_charge(
        self,
        charge_id: str,
        user_id: int,
        pack_index: int,
    ) -> bool:
        """Идемпотентный first-write-wins claim Telegram-чарджа.

        Гонка двух одновременных вызовов с одним ``charge_id``
        разрешается на уровне Postgres'а через UNIQUE-constraint на
        ``telegram_payment_charge_id``: один INSERT'ит, другой видит
        conflict и получает ``None`` из ``RETURNING``.

        Args:
            charge_id: ``successful_payment.telegram_payment_charge_id``.
            user_id: Telegram ID плательщика.
            pack_index: индекс пакета в ``payments_catalog.PACKAGES``.

        Returns:
            ``True``  — запись создана впервые → call-site выполняет начисление;
            ``False`` — ``charge_id`` уже claim'нут → штатный DUPLICATE,
                       call-site молча возвращает (Telegram повторит — мы
                       снова вернём DUPLICATE, юзер видит один факт оплаты).
        """

        if not charge_id:
            return False

        row = await self._conn.fetchrow(
            """
            INSERT INTO payment_charges
                (telegram_payment_charge_id, user_id, pack_index, created_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (telegram_payment_charge_id) DO NOTHING
            RETURNING telegram_payment_charge_id
            """,
            charge_id,
            user_id,
            pack_index,
        )
        return row is not None


__all__ = ("PaymentRepository", "UserRepository")
