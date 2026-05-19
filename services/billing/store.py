"""Слой доступа к БД для биллинга (aiosqlite, атомарные транзакции)."""

from __future__ import annotations

import uuid
from datetime import date

import aiosqlite

from services.billing.pricing import DAILY_FREE_ENERGY
from services.billing.types import ChargeBreakdown, TariffTier, UserBillingState
from services import repository
from services.repository import ensure_user


def _db_path() -> str:
    return repository.DB_PATH


async def _migrate_billing_columns(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA table_info(users)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    alters = [
        ("energy_free", "ALTER TABLE users ADD COLUMN energy_free INTEGER DEFAULT 30"),
        ("energy_paid", "ALTER TABLE users ADD COLUMN energy_paid INTEGER DEFAULT 0"),
        ("first_purchase_done", "ALTER TABLE users ADD COLUMN first_purchase_done INTEGER DEFAULT 0"),
    ]
    for name, ddl in alters:
        if name not in cols:
            await db.execute(ddl)
    if "energy_free" not in cols and "energy" in cols:
        await db.execute(
            """
            UPDATE users SET
                energy_free = CASE
                    WHEN UPPER(COALESCE(tariff, 'FREE')) IN ('FREE', '') THEN COALESCE(energy, 30)
                    ELSE 0
                END,
                energy_paid = CASE
                    WHEN UPPER(COALESCE(tariff, 'FREE')) IN ('FREE', '') THEN 0
                    ELSE COALESCE(energy, 0)
                END
            """
        )
    if "first_purchase_done" not in cols and "has_paid" in cols:
        await db.execute(
            "UPDATE users SET first_purchase_done = COALESCE(has_paid, 0) WHERE first_purchase_done IS NULL"
        )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS billing_charges (
            charge_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            feature TEXT NOT NULL,
            energy_free INTEGER NOT NULL DEFAULT 0,
            energy_paid INTEGER NOT NULL DEFAULT 0,
            crystals INTEGER NOT NULL DEFAULT 0,
            photo_slot INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'charged',
            created_at TEXT NOT NULL
        )
        """
    )


async def init_billing_schema() -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await _migrate_billing_columns(db)
        await db.commit()


async def ensure_daily_reset_for_user(user_id: int) -> None:
    """Вызывается из ``repository.ensure_user`` — сброс free-энергии по правилам тарифа."""
    today = date.today().isoformat()
    async with aiosqlite.connect(_db_path()) as db:
        await _migrate_billing_columns(db)
        await _apply_daily_reset_if_needed(db, user_id, today)
        await db.commit()


def _set_energy_totals_sql() -> str:
    """Явные плейсхолдеры: SQLite в одном UPDATE не видит новые energy_free/energy_paid."""
    return "energy = ?, balance_energy = ?"


async def load_user_billing(user_id: int) -> UserBillingState:
    await ensure_user(user_id)
    await init_billing_schema()
    today = date.today().isoformat()
    async with aiosqlite.connect(_db_path()) as db:
        await _apply_daily_reset_if_needed(db, user_id, today)
        async with db.execute(
            """
            SELECT
                id, tariff,
                COALESCE(energy_free, energy, 30),
                COALESCE(energy_paid, 0),
                crystals,
                last_reset_date,
                referred_by,
                COALESCE(first_purchase_done, 0),
                photo_daily_date,
                photo_daily_count
            FROM users WHERE id = ?
            """,
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise LookupError(f"user {user_id}")
    return UserBillingState(
        user_id=int(row[0]),
        current_tariff=TariffTier.from_db(row[1]),
        energy_free=int(row[2] or 0),
        energy_paid=int(row[3] or 0),
        crystals=int(row[4] or 0),
        last_energy_reset=row[5],
        invited_by_id=int(row[6]) if row[6] is not None else None,
        first_purchase_done=bool(row[7]),
        photo_daily_date=row[8],
        photo_daily_count=int(row[9] or 0),
    )


async def _apply_daily_reset_if_needed(db: aiosqlite.Connection, user_id: int, today: str) -> None:
    async with db.execute(
        "SELECT last_reset_date, UPPER(COALESCE(tariff, 'FREE')) FROM users WHERE id = ?",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row or (row[0] or "") == today:
        return
    tariff = row[1] or "FREE"
    if tariff == "FREE":
        energy_free = DAILY_FREE_ENERGY
    else:
        energy_free = 0
    total = energy_free + 0
    await db.execute(
        f"""
        UPDATE users SET
            energy_free = ?,
            energy_paid = COALESCE(energy_paid, 0),
            {_set_energy_totals_sql()},
            last_reset_date = ?
        WHERE id = ?
        """,
        (energy_free, total, total, today, user_id),
    )


async def reset_daily_free_energy() -> None:
    """Массовый сброс в 00:00 (cron): FREE -> 30 ⚡ free, платные -> 0 free."""
    today = date.today().isoformat()
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            f"""
            UPDATE users SET
                energy_free = CASE
                    WHEN UPPER(COALESCE(tariff, 'FREE')) = 'FREE' THEN ?
                    ELSE 0
                END,
                energy_paid = COALESCE(energy_paid, 0),
                energy = CASE
                    WHEN UPPER(COALESCE(tariff, 'FREE')) = 'FREE' THEN ?
                    ELSE COALESCE(energy_paid, 0)
                END,
                balance_energy = CASE
                    WHEN UPPER(COALESCE(tariff, 'FREE')) = 'FREE' THEN ?
                    ELSE COALESCE(energy_paid, 0)
                END,
                last_reset_date = ?
            """,
            (DAILY_FREE_ENERGY, DAILY_FREE_ENERGY, DAILY_FREE_ENERGY, today),
        )
        await db.commit()


async def apply_purchase_credits(
    user_id: int,
    *,
    tariff: str | None,
    energy_paid_delta: int,
    crystals_delta: int,
) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("BEGIN IMMEDIATE")
        if tariff:
            await db.execute(
                "UPDATE users SET tariff = ?, has_paid = 1, first_purchase_done = 1 WHERE id = ?",
                (tariff, user_id),
            )
        if energy_paid_delta:
            async with db.execute(
                "SELECT COALESCE(energy_free, energy, 0), COALESCE(energy_paid, 0) FROM users WHERE id = ?",
                (user_id,),
            ) as cur:
                er = await cur.fetchone()
            new_paid = int(er[1]) + energy_paid_delta
            new_total = int(er[0]) + new_paid
            await db.execute(
                f"""
                UPDATE users SET
                    energy_paid = ?,
                    {_set_energy_totals_sql()}
                WHERE id = ?
                """,
                (new_paid, new_total, new_total, user_id),
            )
        if crystals_delta:
            await db.execute(
                """
                UPDATE users SET
                    crystals = crystals + ?,
                    balance = crystals + ?,
                    balance_crystals = crystals + ?
                WHERE id = ?
                """,
                (crystals_delta, crystals_delta, crystals_delta, user_id),
            )
        await db.commit()


async def mark_first_purchase_done(user_id: int) -> int | None:
    """Возвращает inviter_id если это первая покупка."""
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT COALESCE(first_purchase_done, has_paid, 0), referred_by FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            await db.commit()
            return None
        if bool(row[0]):
            await db.commit()
            return None
        inviter = int(row[1]) if row[1] is not None else None
        await db.execute(
            "UPDATE users SET first_purchase_done = 1, has_paid = 1 WHERE id = ?",
            (user_id,),
        )
        await db.commit()
        return inviter


async def atomic_spend(
    user_id: int,
    feature: str,
    *,
    energy_need: int,
    crystal_need: int,
    crystals_only: bool,
    reserve_photo_slot: bool,
    photo_daily_limit: int,
) -> ChargeBreakdown | None:
    charge_id = uuid.uuid4().hex[:16]
    today = date.today().isoformat()
    async with aiosqlite.connect(_db_path()) as db:
        await _apply_daily_reset_if_needed(db, user_id, today)
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            """
            SELECT
                COALESCE(energy_free, energy, 0),
                COALESCE(energy_paid, 0),
                crystals,
                photo_daily_date,
                photo_daily_count,
                UPPER(COALESCE(tariff, 'FREE'))
            FROM users WHERE id = ?
            """,
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            await db.execute("ROLLBACK")
            return None
        e_free, e_paid, crystals = int(row[0]), int(row[1]), int(row[2])
        p_date, p_count, _tariff = row[3], int(row[4] or 0), row[5]
        used_slot = False
        spend_free = spend_paid = spend_crystals = 0

        if reserve_photo_slot:
            if p_date != today:
                p_count = 0
            if p_count >= photo_daily_limit:
                await db.execute("ROLLBACK")
                return None
            used_slot = True
            new_count = p_count + 1
            await db.execute(
                "UPDATE users SET photo_daily_date = ?, photo_daily_count = ? WHERE id = ?",
                (today, new_count, user_id),
            )

        if crystal_need > 0:
            if crystals < crystal_need:
                await db.execute("ROLLBACK")
                return None
            spend_crystals = crystal_need
        elif energy_need > 0 and not crystals_only:
            total_e = e_free + e_paid
            if total_e < energy_need:
                await db.execute("ROLLBACK")
                return None
            take = energy_need
            from_free = min(e_free, take)
            take -= from_free
            from_paid = take
            spend_free, spend_paid = from_free, from_paid
        elif energy_need > 0 and crystals_only:
            await db.execute("ROLLBACK")
            return None

        if spend_crystals:
            await db.execute(
                """
                UPDATE users SET
                    crystals = crystals - ?,
                    balance = crystals - ?,
                    balance_crystals = crystals - ?
                WHERE id = ? AND crystals >= ?
                """,
                (spend_crystals, spend_crystals, spend_crystals, user_id, spend_crystals),
            )
        if spend_free or spend_paid:
            new_free = e_free - spend_free
            new_paid = e_paid - spend_paid
            new_total = new_free + new_paid
            cur = await db.execute(
                f"""
                UPDATE users SET
                    energy_free = ?,
                    energy_paid = ?,
                    {_set_energy_totals_sql()}
                WHERE id = ?
                  AND COALESCE(energy_free, energy, 0) >= ?
                  AND COALESCE(energy_paid, 0) >= ?
                """,
                (new_free, new_paid, new_total, new_total, user_id, spend_free, spend_paid),
            )
            if cur.rowcount != 1:
                await db.execute("ROLLBACK")
                return None

        await db.execute(
            """
            INSERT INTO billing_charges
                (charge_id, user_id, feature, energy_free, energy_paid, crystals, photo_slot, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'charged', ?)
            """,
            (
                charge_id,
                user_id,
                feature,
                spend_free,
                spend_paid,
                spend_crystals,
                1 if used_slot else 0,
                today,
            ),
        )
        await db.commit()
    return ChargeBreakdown(
        charge_id=charge_id,
        energy_free=spend_free,
        energy_paid=spend_paid,
        crystals=spend_crystals,
        used_photo_free_slot=used_slot,
    )


async def refund_charge(charge_id: str) -> bool:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            """
            SELECT user_id, energy_free, energy_paid, crystals, photo_slot, status
            FROM billing_charges WHERE charge_id = ?
            """,
            (charge_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row or row[5] != "charged":
            await db.execute("ROLLBACK")
            return False
        uid, ef, ep, cr, slot, _ = row
        async with db.execute(
            "SELECT COALESCE(energy_free, energy, 0), COALESCE(energy_paid, 0) FROM users WHERE id = ?",
            (uid,),
        ) as cur:
            er = await cur.fetchone()
        new_free = int(er[0]) + ef
        new_paid = int(er[1]) + ep
        new_total = new_free + new_paid
        await db.execute(
            f"""
            UPDATE users SET
                energy_free = ?,
                energy_paid = ?,
                crystals = crystals + ?,
                balance = crystals + ?,
                balance_crystals = crystals + ?,
                {_set_energy_totals_sql()}
            WHERE id = ?
            """,
            (new_free, new_paid, cr, cr, cr, new_total, new_total, uid),
        )
        if slot:
            await db.execute(
                """
                UPDATE users SET photo_daily_count = CASE
                    WHEN photo_daily_count > 0 THEN photo_daily_count - 1 ELSE 0
                END WHERE id = ?
                """,
                (uid,),
            )
        await db.execute(
            "UPDATE billing_charges SET status = 'refunded' WHERE charge_id = ?",
            (charge_id,),
        )
        await db.commit()
    return True
