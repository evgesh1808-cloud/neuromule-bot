"""Раздельный учёт sub_crystals (тариф) и buy_crystals (покупки/рефералы)."""

from __future__ import annotations

import aiosqlite

from services import repository


def _db_path() -> str:
    return repository.DB_PATH


async def migrate_crystal_split_columns(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA table_info(users)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    added: set[str] = set()
    for name, ddl in (
        ("sub_crystals", "ALTER TABLE users ADD COLUMN sub_crystals INTEGER DEFAULT 0"),
        ("buy_crystals", "ALTER TABLE users ADD COLUMN buy_crystals INTEGER DEFAULT 0"),
        ("subscription_ends_at", "ALTER TABLE users ADD COLUMN subscription_ends_at TEXT"),
    ):
        if name not in cols:
            await db.execute(ddl)
            added.add(name)
    if "buy_crystals" in added or "sub_crystals" in added:
        await db.execute(
            """
            UPDATE users SET
                buy_crystals = COALESCE(crystals, 0),
                sub_crystals = 0
            WHERE buy_crystals IS NULL OR sub_crystals IS NULL
            """
        )
        await _sync_totals_all(db)
    async with db.execute("PRAGMA table_info(referrals)") as cur:
        ref_cols = {row[1] for row in await cur.fetchall()}
    if "channel_bonus_paid" not in ref_cols:
        await db.execute(
            "ALTER TABLE referrals ADD COLUMN channel_bonus_paid INTEGER DEFAULT 0"
        )


async def _sync_totals_all(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        UPDATE users SET
            crystals = COALESCE(sub_crystals, 0) + COALESCE(buy_crystals, 0),
            balance = COALESCE(sub_crystals, 0) + COALESCE(buy_crystals, 0),
            balance_crystals = COALESCE(sub_crystals, 0) + COALESCE(buy_crystals, 0)
        """
    )


async def sync_user_crystal_totals(user_id: int) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            UPDATE users SET
                crystals = COALESCE(sub_crystals, 0) + COALESCE(buy_crystals, 0),
                balance = COALESCE(sub_crystals, 0) + COALESCE(buy_crystals, 0),
                balance_crystals = COALESCE(sub_crystals, 0) + COALESCE(buy_crystals, 0)
            WHERE id = ?
            """,
            (user_id,),
        )
        await db.commit()


async def add_buy_crystals(user_id: int, amount: int) -> None:
    if amount <= 0:
        return
    await repository.ensure_user(user_id)
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "UPDATE users SET buy_crystals = COALESCE(buy_crystals, 0) + ? WHERE id = ?",
            (amount, user_id),
        )
        await sync_user_crystal_totals(user_id)


async def add_sub_crystals(user_id: int, amount: int) -> None:
    if amount <= 0:
        return
    await repository.ensure_user(user_id)
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "UPDATE users SET sub_crystals = COALESCE(sub_crystals, 0) + ? WHERE id = ?",
            (amount, user_id),
        )
        await sync_user_crystal_totals(user_id)


async def spend_crystals_split(user_id: int, amount: int) -> bool:
    """Списание: сначала sub_crystals, затем buy_crystals."""
    if amount <= 0:
        return True
    await repository.ensure_user(user_id)
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT COALESCE(sub_crystals, 0), COALESCE(buy_crystals, 0) FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            await db.execute("ROLLBACK")
            return False
        sub, buy = int(row[0]), int(row[1])
        if sub + buy < amount:
            await db.execute("ROLLBACK")
            return False
        from_sub = min(sub, amount)
        from_buy = amount - from_sub
        new_sub = sub - from_sub
        new_buy = buy - from_buy
        await db.execute(
            """
            UPDATE users SET
                sub_crystals = ?,
                buy_crystals = ?,
                crystals = ?,
                balance = ?,
                balance_crystals = ?
            WHERE id = ?
            """,
            (
                new_sub,
                new_buy,
                new_sub + new_buy,
                new_sub + new_buy,
                new_sub + new_buy,
                user_id,
            ),
        )
        await db.commit()
    return True


async def refund_crystals_to_buy(user_id: int, amount: int) -> None:
    if amount <= 0:
        return
    await add_buy_crystals(user_id, amount)
