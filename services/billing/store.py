"""Слой доступа к БД для биллинга (aiosqlite, атомарные транзакции)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import aiosqlite

from services.billing.crystals_balance import migrate_crystal_split_columns
from services.billing.pricing import DAILY_FREE_ENERGY
from services.billing.types import ChargeBreakdown, TariffTier, UserBillingState
from services import repository
from services.god_mode import god_mode_charge, is_god_mode_charge, billing_bypass
from services.repository import ensure_user


def _db_path() -> str:
    return repository.DB_PATH


async def _resolve_wallet_id(user_id: int) -> int:
    """DUO-роутер кошелька.

    Если ``user_id`` — приглашённый партнёр активной DUO-пары, возвращает
    ``owner_id`` владельца ULTRA (1 месяц). Иначе — сам ``user_id``.
    """
    try:
        from services.family_sharing import resolve_duo_owner

        return await resolve_duo_owner(user_id)
    except Exception:
        return user_id


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


async def _ensure_balance_packages_schema(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS balance_packages (
            package_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            package_type TEXT NOT NULL,
            paid_energy_left INTEGER NOT NULL DEFAULT 0,
            crystals_left INTEGER NOT NULL DEFAULT 0,
            expires_at TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_balance_packages_user "
        "ON balance_packages (user_id)"
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def grant_balance_package(
    user_id: int,
    kind: str,
    energy_amount: int,
    crystals_amount: int,
    expires_at: str | None,
) -> int:
    """
    Централизованное начисление: строка в ``balance_packages`` + синхронизация
    legacy-полей ``users`` (``energy_paid``, ``sub_crystals``, ``buy_crystals``).
    """
    await ensure_user(user_id)
    if energy_amount <= 0 and crystals_amount <= 0:
        return 0
    async with aiosqlite.connect(_db_path()) as db:
        await _ensure_balance_packages_schema(db)
        await migrate_crystal_split_columns(db)
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            """
            INSERT INTO balance_packages
                (user_id, package_type, paid_energy_left, crystals_left, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                kind,
                max(0, int(energy_amount)),
                max(0, int(crystals_amount)),
                expires_at,
                _now_iso(),
            ),
        )
        package_id = int(cur.lastrowid or 0)
        async with db.execute(
            """
            SELECT COALESCE(energy_free, energy, 0), COALESCE(energy_paid, 0),
                   COALESCE(sub_crystals, 0), COALESCE(buy_crystals, 0)
            FROM users WHERE id = ?
            """,
            (user_id,),
        ) as row_cur:
            row = await row_cur.fetchone()
        e_free = int(row[0] or 0) if row else 0
        e_paid = int(row[1] or 0) if row else 0
        sub_cr = int(row[2] or 0) if row else 0
        buy_cr = int(row[3] or 0) if row else 0
        if energy_amount > 0:
            e_paid += energy_amount
        if crystals_amount > 0:
            if expires_at is None:
                buy_cr += crystals_amount
            else:
                sub_cr += crystals_amount
        total_e = e_free + e_paid
        total_cr = sub_cr + buy_cr
        await db.execute(
            f"""
            UPDATE users SET
                energy_paid = ?,
                sub_crystals = ?,
                buy_crystals = ?,
                crystals = ?,
                balance = ?,
                balance_crystals = ?,
                {_set_energy_totals_sql()}
            WHERE id = ?
            """,
            (
                e_paid,
                sub_cr,
                buy_cr,
                total_cr,
                total_cr,
                total_cr,
                total_e,
                total_e,
                user_id,
            ),
        )
        await db.commit()
    return package_id


async def init_billing_schema() -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await _migrate_billing_columns(db)
        await _ensure_balance_packages_schema(db)
        await migrate_crystal_split_columns(db)
        await db.commit()


async def apply_tariff_period_renewal(
    user_id: int,
    *,
    tariff: str,
    energy_paid_grant: int,
    sub_crystals_grant: int,
    extend_days: int = 30,
) -> None:
    """
    Продление/покупка тарифа: сброс подписочной энергии и ``sub_crystals``, затем новый пакет.
    ``buy_crystals`` не трогаем.
    """
    await ensure_user(user_id)
    ends = (date.today() + timedelta(days=extend_days)).isoformat()
    async with aiosqlite.connect(_db_path()) as db:
        await migrate_crystal_split_columns(db)
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            f"""
            UPDATE users SET
                sub_crystals = 0,
                energy_paid = 0,
                energy_free = 0,
                energy = 0,
                balance_energy = 0
            WHERE id = ?
            """,
            (user_id,),
        )
        new_total = energy_paid_grant
        await db.execute(
            f"""
            UPDATE users SET
                tariff = ?,
                has_paid = 1,
                first_purchase_done = 1,
                energy_paid = ?,
                sub_crystals = ?,
                subscription_ends_at = ?,
                {_set_energy_totals_sql()}
            WHERE id = ?
            """,
            (
                tariff,
                energy_paid_grant,
                sub_crystals_grant,
                ends,
                new_total,
                new_total,
                user_id,
            ),
        )
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
    # Family Sharing: balance member'а ULTRA-семьи = balance кошелька owner.
    wallet_id = await _resolve_wallet_id(user_id)
    async with aiosqlite.connect(_db_path()) as db:
        await _apply_daily_reset_if_needed(db, wallet_id, today)
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
            (wallet_id,),
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
    buy_crystals_delta: int = 0,
) -> None:
    """Доп. начисление энергии/кристаллов без сброса тарифного периода (пакеты 💎, реф. бонус)."""
    async with aiosqlite.connect(_db_path()) as db:
        await migrate_crystal_split_columns(db)
        await db.execute("BEGIN IMMEDIATE")
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
        delta_buy = buy_crystals_delta or crystals_delta
        if delta_buy:
            await db.execute(
                """
                UPDATE users SET buy_crystals = COALESCE(buy_crystals, 0) + ? WHERE id = ?
                """,
                (delta_buy, user_id),
            )
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
    if billing_bypass(user_id):
        return god_mode_charge()
    charge_id = uuid.uuid4().hex[:16]
    today = date.today().isoformat()
    # Family Sharing: для members ULTRA-семьи кошелёк = owner_id, а user_id
    # остаётся индивидуальным для рефанда / логов / cooldowns верхнего уровня.
    wallet_id = await _resolve_wallet_id(user_id)
    async with aiosqlite.connect(_db_path()) as db:
        await _apply_daily_reset_if_needed(db, wallet_id, today)
        await db.execute("BEGIN IMMEDIATE")
        await migrate_crystal_split_columns(db)
        async with db.execute(
            """
            SELECT
                COALESCE(energy_free, energy, 0),
                COALESCE(energy_paid, 0),
                COALESCE(sub_crystals, 0),
                COALESCE(buy_crystals, 0),
                crystals,
                photo_daily_date,
                photo_daily_count,
                UPPER(COALESCE(tariff, 'FREE'))
            FROM users WHERE id = ?
            """,
            (wallet_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            await db.execute("ROLLBACK")
            return None
        e_free, e_paid = int(row[0]), int(row[1])
        sub_cr, buy_cr = int(row[2]), int(row[3])
        crystals = sub_cr + buy_cr if (row[2] is not None or row[3] is not None) else int(row[4] or 0)
        p_date, p_count, _tariff = row[5], int(row[6] or 0), row[7]
        used_slot = False
        spend_free = spend_paid = spend_crystals = 0

        if reserve_photo_slot:
            if p_date != today:
                p_count = 0
            if p_count < photo_daily_limit:
                used_slot = True
                new_count = p_count + 1
                await db.execute(
                    "UPDATE users SET photo_daily_date = ?, photo_daily_count = ? WHERE id = ?",
                    (today, new_count, wallet_id),
                )
            elif crystal_need < 1:
                # Слоты Imagen 4 исчерпаны и нет оплаты кристаллами — отказ.
                await db.execute("ROLLBACK")
                return None

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
            if sub_cr + buy_cr < spend_crystals:
                await db.execute("ROLLBACK")
                return None
            from_sub = min(sub_cr, spend_crystals)
            from_buy = spend_crystals - from_sub
            new_sub = sub_cr - from_sub
            new_buy = buy_cr - from_buy
            new_total = new_sub + new_buy
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
                (new_sub, new_buy, new_total, new_total, new_total, wallet_id),
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
                (new_free, new_paid, new_total, new_total, wallet_id, spend_free, spend_paid),
            )
            if cur.rowcount != 1:
                await db.execute("ROLLBACK")
                return None

        # billing_charges.user_id ставим = wallet_id, чтобы refund_charge
        # вернул ресурсы на тот же кошелёк (owner для members ULTRA-семьи).
        await db.execute(
            """
            INSERT INTO billing_charges
                (charge_id, user_id, feature, energy_free, energy_paid, crystals, photo_slot, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'charged', ?)
            """,
            (
                charge_id,
                wallet_id,
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
    if is_god_mode_charge(charge_id):
        return True
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
                buy_crystals = COALESCE(buy_crystals, 0) + ?,
                {_set_energy_totals_sql()}
            WHERE id = ?
            """,
            (new_free, new_paid, cr, new_total, new_total, uid),
        )
        await db.execute(
            """
            UPDATE users SET
                crystals = COALESCE(sub_crystals, 0) + COALESCE(buy_crystals, 0),
                balance = COALESCE(sub_crystals, 0) + COALESCE(buy_crystals, 0),
                balance_crystals = COALESCE(sub_crystals, 0) + COALESCE(buy_crystals, 0)
            WHERE id = ?
            """,
            (uid,),
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
