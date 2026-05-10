"""SQLite через aiosqlite: пользователи, рефералы, лимиты, промокоды, диалог, платежи."""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import aiosqlite


def _resolve_db_path() -> str:
    project_root = Path(__file__).resolve().parent.parent
    try:
        from dotenv import load_dotenv

        load_dotenv(project_root / ".env")
    except ImportError:
        pass
    default_path = project_root / "neuromule_base.db"
    raw = os.getenv("DB_PATH", "").strip()
    db_path = Path(raw) if raw else default_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return str(db_path)


DB_PATH = _resolve_db_path()


async def _migrate_users(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA table_info(users)") as cur:
        rows = await cur.fetchall()
    cols = {row[1] for row in rows}
    alters = [
        ("tariff", "ALTER TABLE users ADD COLUMN tariff TEXT DEFAULT 'Free'"),
        ("referred_by", "ALTER TABLE users ADD COLUMN referred_by INTEGER"),
        ("photo_daily_date", "ALTER TABLE users ADD COLUMN photo_daily_date TEXT"),
        ("photo_daily_count", "ALTER TABLE users ADD COLUMN photo_daily_count INTEGER DEFAULT 0"),
        ("username", "ALTER TABLE users ADD COLUMN username TEXT"),
        ("persistent_memory", "ALTER TABLE users ADD COLUMN persistent_memory TEXT"),
        ("text_daily_date", "ALTER TABLE users ADD COLUMN text_daily_date TEXT"),
        ("text_daily_count", "ALTER TABLE users ADD COLUMN text_daily_count INTEGER DEFAULT 0"),
        ("has_paid", "ALTER TABLE users ADD COLUMN has_paid INTEGER DEFAULT 0"),
    ]
    for name, ddl in alters:
        if name not in cols:
            await db.execute(ddl)


async def _migrate_drop_users_crystals(db: aiosqlite.Connection) -> None:
    """Удаляет устаревшую колонку ``crystals`` из ``users`` (SQLite ≥ 3.35) или копирует таблицу без неё."""
    async with db.execute("PRAGMA table_info(users)") as cur:
        rows = await cur.fetchall()
    names = {str(r[1]) for r in rows}
    if "crystals" not in names:
        return
    try:
        await db.execute("ALTER TABLE users DROP COLUMN crystals")
        return
    except sqlite3.OperationalError:
        pass

    await db.execute(
        """
        CREATE TABLE users__no_crystals (
            id INTEGER PRIMARY KEY,
            energy INTEGER DEFAULT 20,
            tariff TEXT DEFAULT 'Free',
            referred_by INTEGER,
            photo_daily_date TEXT,
            photo_daily_count INTEGER DEFAULT 0,
            username TEXT,
            persistent_memory TEXT,
            text_daily_date TEXT,
            text_daily_count INTEGER DEFAULT 0,
            has_paid INTEGER DEFAULT 0
        )
        """
    )
    await db.execute(
        """
        INSERT INTO users__no_crystals (
            id,
            energy,
            tariff,
            referred_by,
            photo_daily_date,
            photo_daily_count,
            username,
            persistent_memory,
            text_daily_date,
            text_daily_count,
            has_paid
        )
        SELECT
            id,
            energy,
            COALESCE(tariff, 'Free'),
            referred_by,
            photo_daily_date,
            photo_daily_count,
            username,
            persistent_memory,
            text_daily_date,
            text_daily_count,
            COALESCE(has_paid, 0)
        FROM users
        """
    )
    await db.execute("DROP TABLE users")
    await db.execute("ALTER TABLE users__no_crystals RENAME TO users")


async def _migrate_rate_limit_hits(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS rate_limit_hits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ts REAL NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_rate_limit_hits_user_ts ON rate_limit_hits (user_id, ts)"
    )


async def _migrate_dialog_messages(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS dialog_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_dialog_messages_user_created "
        "ON dialog_messages (user_id, created_at)"
    )


async def _seed_promos(db: aiosqlite.Connection, promo_seeds: str) -> None:
    if not promo_seeds.strip():
        return
    for part in promo_seeds.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        bits = part.split(":")
        if len(bits) != 3:
            continue
        code, bonus_s, max_s = bits[0].strip().upper(), bits[1].strip(), bits[2].strip()
        try:
            bonus = int(bonus_s)
            max_u = int(max_s)
        except ValueError:
            continue
        await db.execute(
            "INSERT OR IGNORE INTO promo_codes (code, energy_bonus, max_uses, uses_count) VALUES (?, ?, ?, 0)",
            (code, bonus, max_u),
        )


async def init_db(promo_seeds: str = "") -> None:
    """Создаёт таблицы, миграции и сиды промокодов. Вызывать при старте приложения."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                energy INTEGER DEFAULT 20,
                tariff TEXT DEFAULT 'Free',
                referred_by INTEGER,
                photo_daily_date TEXT,
                photo_daily_count INTEGER DEFAULT 0
            )
            """
        )
        await _migrate_users(db)
        await _migrate_drop_users_crystals(db)
        await _migrate_dialog_messages(db)
        await _migrate_rate_limit_hits(db)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS referrals (
                invited_id INTEGER PRIMARY KEY,
                inviter_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                energy_bonus INTEGER NOT NULL,
                max_uses INTEGER NOT NULL,
                uses_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS promo_redemptions (
                user_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                redeemed_at TEXT NOT NULL,
                PRIMARY KEY (user_id, code)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_charges (
                charge_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                energy_added INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tariff TEXT NOT NULL,
                method TEXT NOT NULL,
                amount INTEGER NOT NULL,
                currency TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await _seed_promos(db, promo_seeds)
        await db.commit()


@dataclass(frozen=True)
class UserRow:
    id: int
    energy: int
    tariff: str
    referred_by: int | None
    photo_daily_date: str | None
    photo_daily_count: int
    text_daily_date: str | None
    text_daily_count: int
    has_paid: bool


async def ensure_user(user_id: int, username: str | None = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM users WHERE id = ?", (user_id,)) as cur:
            exists = await cur.fetchone()
        if not exists:
            await db.execute(
                """
                INSERT INTO users (id, energy, tariff, referred_by, photo_daily_date, photo_daily_count)
                VALUES (?, 20, 'Free', NULL, NULL, 0)
                """,
                (user_id,),
            )
        if username is not None:
            u = username.strip()[:255] if username else ""
            await db.execute("UPDATE users SET username = ? WHERE id = ?", (u or None, user_id))
        await db.commit()


async def get_user_row(user_id: int) -> UserRow:
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT
                id,
                energy,
                tariff,
                referred_by,
                photo_daily_date,
                photo_daily_count,
                text_daily_date,
                text_daily_count,
                has_paid
            FROM users WHERE id = ?
            """,
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    return UserRow(
        id=int(row[0]),
        energy=int(row[1]),
        tariff=str(row[2] or "Free"),
        referred_by=int(row[3]) if row[3] is not None else None,
        photo_daily_date=row[4],
        photo_daily_count=int(row[5] or 0),
        text_daily_date=row[6],
        text_daily_count=int(row[7] or 0),
        has_paid=bool(row[8] or 0),
    )


async def try_set_referrer(invited_id: int, inviter_id: int) -> bool:
    if invited_id == inviter_id:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT referred_by FROM users WHERE id = ?", (invited_id,)) as cur:
            r = await cur.fetchone()
        if not r or r[0] is not None:
            return False
        async with db.execute("SELECT 1 FROM referrals WHERE invited_id = ?", (invited_id,)) as cur:
            if await cur.fetchone():
                return False
        await db.execute("UPDATE users SET referred_by = ? WHERE id = ?", (inviter_id, invited_id))
        await db.execute(
            "INSERT INTO referrals (invited_id, inviter_id, created_at) VALUES (?, ?, ?)",
            (invited_id, inviter_id, date.today().isoformat()),
        )
        await db.commit()
    return True


async def referrals_count(inviter_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM referrals WHERE inviter_id = ?", (inviter_id,)) as cur:
            row = await cur.fetchone()
    return int(row[0])


async def update_balance(user_id: int, field: str, delta: int) -> None:
    if field != "energy":
        raise ValueError("Invalid field")
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {field} = {field} + ? WHERE id = ?", (delta, user_id))
        await db.commit()


async def try_consume_energy(user_id: int, amount: int) -> bool:
    if amount <= 0:
        return True
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE users SET energy = energy - ? WHERE id = ? AND energy >= ?",
            (amount, user_id, amount),
        )
        await db.commit()
        return cur.rowcount == 1


async def try_consume_daily_photo_slot(user_id: int, daily_limit: int) -> tuple[bool, int]:
    await ensure_user(user_id)
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT photo_daily_date, photo_daily_count FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        d, c = row[0], row[1]
        count = int(c or 0)
        if d != today:
            count = 0
            await db.execute(
                "UPDATE users SET photo_daily_date = ?, photo_daily_count = 0 WHERE id = ?",
                (today, user_id),
            )
        if count >= daily_limit:
            await db.commit()
            return False, count
        await db.execute(
            "UPDATE users SET photo_daily_date = ?, photo_daily_count = ? WHERE id = ?",
            (today, count + 1, user_id),
        )
        await db.commit()
        return True, count + 1


async def rollback_daily_photo_slot(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE users SET photo_daily_count = CASE WHEN photo_daily_count > 0 THEN photo_daily_count - 1 ELSE 0 END
            WHERE id = ?
            """,
            (user_id,),
        )
        await db.commit()


async def try_consume_daily_text_slot(user_id: int, daily_limit: int) -> tuple[bool, int]:
    await ensure_user(user_id)
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT text_daily_date, text_daily_count FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        d, c = row[0], row[1]
        count = int(c or 0)
        if d != today:
            count = 0
            await db.execute(
                "UPDATE users SET text_daily_date = ?, text_daily_count = 0 WHERE id = ?",
                (today, user_id),
            )
        if count >= daily_limit:
            await db.commit()
            return False, count
        await db.execute(
            "UPDATE users SET text_daily_date = ?, text_daily_count = ? WHERE id = ?",
            (today, count + 1, user_id),
        )
        await db.commit()
        return True, count + 1


async def set_user_tariff(user_id: int, tariff: str) -> None:
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET tariff = ? WHERE id = ?", (tariff, user_id))
        await db.commit()


async def mark_user_first_purchase_and_get_referrer(user_id: int) -> int | None:
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT has_paid, referred_by FROM users WHERE id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        already_paid = bool((row[0] if row else 0) or 0)
        referred_by = int(row[1]) if row and row[1] is not None else None
        if already_paid:
            return None
        await db.execute("UPDATE users SET has_paid = 1 WHERE id = ?", (user_id,))
        await db.commit()
        return referred_by


async def try_redeem_promo(user_id: int, raw_code: str) -> tuple[bool, str, int]:
    await ensure_user(user_id)
    code = (raw_code or "").strip().upper()
    if not code:
        return False, "unknown", 0
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT energy_bonus, max_uses, uses_count FROM promo_codes WHERE code = ?",
            (code,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return False, "unknown", 0
        bonus, max_uses, uses = int(row[0]), int(row[1]), int(row[2])
        async with db.execute(
            "SELECT 1 FROM promo_redemptions WHERE user_id = ? AND code = ?",
            (user_id, code),
        ) as cur:
            if await cur.fetchone():
                return False, "used", 0
        if uses >= max_uses:
            return False, "exhausted", 0
        await db.execute(
            "INSERT INTO promo_redemptions (user_id, code, redeemed_at) VALUES (?, ?, ?)",
            (user_id, code, date.today().isoformat()),
        )
        await db.execute("UPDATE promo_codes SET uses_count = uses_count + 1 WHERE code = ?", (code,))
        await db.execute("UPDATE users SET energy = energy + ? WHERE id = ?", (bonus, user_id))
        await db.commit()
    return True, "redeemed", bonus


async def dialog_append(user_id: int, role: str, content: str) -> None:
    if role not in ("user", "assistant"):
        raise ValueError("role must be 'user' or 'assistant'")
    await ensure_user(user_id)
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO dialog_messages (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (user_id, role, content, ts),
        )
        await db.commit()


async def dialog_fetch_last(user_id: int, limit: int) -> list[tuple[str, str]]:
    if limit <= 0:
        return []
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT role, content FROM dialog_messages
            WHERE user_id = ?
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ) as cur:
            rows = list(await cur.fetchall())
    rows.reverse()
    return [(str(r[0]), str(r[1])) for r in rows]


async def dialog_total_messages(user_id: int) -> int:
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM dialog_messages WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
    return int(row[0])


async def dialog_pop_last_for_user(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            DELETE FROM dialog_messages
            WHERE id = (
                SELECT id FROM dialog_messages
                WHERE user_id = ?
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT 1
            )
            """,
            (user_id,),
        )
        await db.commit()


async def dialog_prune_keep_last(user_id: int, keep: int) -> None:
    if keep <= 0:
        return
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM dialog_messages WHERE user_id = ? ORDER BY datetime(created_at) ASC, id ASC",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        ids = [int(r[0]) for r in rows]
        if len(ids) <= keep:
            return
        to_delete = ids[: len(ids) - keep]
        await db.executemany("DELETE FROM dialog_messages WHERE id = ?", [(i,) for i in to_delete])
        await db.commit()


async def get_persistent_memory(user_id: int) -> str | None:
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT persistent_memory FROM users WHERE id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
    if not row or row[0] is None:
        return None
    s = str(row[0]).strip()
    return s or None


async def set_persistent_memory(user_id: int, text: str | None) -> None:
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET persistent_memory = ? WHERE id = ?", (text, user_id))
        await db.commit()


async def clear_user_dialog_and_memory(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM dialog_messages WHERE user_id = ?", (user_id,))
        await db.execute("UPDATE users SET persistent_memory = NULL WHERE id = ?", (user_id,))
        await db.commit()


async def rate_limit_allow(user_id: int, max_per_minute: int) -> bool:
    """Скользящее окно ~60 с по ``time.time()``; записи старше окна удаляются."""
    import time as _time

    if max_per_minute <= 0:
        return True
    now = _time.time()
    cutoff = now - 60.0
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM rate_limit_hits WHERE ts < ?", (cutoff,))
        async with db.execute(
            "SELECT COUNT(*) FROM rate_limit_hits WHERE user_id = ? AND ts >= ?",
            (user_id, cutoff),
        ) as cur:
            row = await cur.fetchone()
        n = int(row[0]) if row else 0
        if n >= max_per_minute:
            await db.commit()
            return False
        await db.execute("INSERT INTO rate_limit_hits (user_id, ts) VALUES (?, ?)", (user_id, now))
        await db.commit()
    return True


async def rate_limit_rollback_last(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            DELETE FROM rate_limit_hits WHERE id = (
                SELECT id FROM rate_limit_hits WHERE user_id = ?
                ORDER BY id DESC LIMIT 1
            )
            """,
            (user_id,),
        )
        await db.commit()


async def claim_payment_charge(charge_id: str, user_id: int, energy_added: int) -> bool:
    if not charge_id:
        return False
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO payment_charges (charge_id, user_id, energy_added, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (charge_id, user_id, energy_added, date.today().isoformat()),
            )
            await db.commit()
        return True
    except sqlite3.IntegrityError:
        return False


async def insert_payment_event(
    user_id: int,
    tariff: str,
    method: str,
    amount: int,
    currency: str,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO payment_events (user_id, tariff, method, amount, currency, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, tariff.upper(), method, amount, currency.upper(), date.today().isoformat()),
        )
        await db.commit()


@dataclass(frozen=True)
class SalesStats:
    users_total: int
    mini_today: int
    smart_today: int
    ultra_today: int
    mini_all: int
    smart_all: int
    ultra_all: int
    revenue_rub_total: int
    revenue_xtr_total: int


async def get_sales_stats() -> SalesStats:
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            users_total = int((await cur.fetchone())[0] or 0)

        async with db.execute(
            "SELECT tariff, COUNT(*) FROM payment_events WHERE created_at = ? GROUP BY tariff",
            (today,),
        ) as cur:
            today_rows = await cur.fetchall()
        today_map = {str(r[0]).upper(): int(r[1]) for r in today_rows}

        async with db.execute("SELECT tariff, COUNT(*) FROM payment_events GROUP BY tariff") as cur:
            all_rows = await cur.fetchall()
        all_map = {str(r[0]).upper(): int(r[1]) for r in all_rows}

        async with db.execute(
            "SELECT currency, COALESCE(SUM(amount), 0) FROM payment_events GROUP BY currency"
        ) as cur:
            rev_rows = await cur.fetchall()
        rev_map = {str(r[0]).upper(): int(r[1]) for r in rev_rows}

    return SalesStats(
        users_total=users_total,
        mini_today=today_map.get("MINI", 0),
        smart_today=today_map.get("SMART", 0),
        ultra_today=today_map.get("ULTRA", 0),
        mini_all=all_map.get("MINI", 0),
        smart_all=all_map.get("SMART", 0),
        ultra_all=all_map.get("ULTRA", 0),
        revenue_rub_total=rev_map.get("RUB", 0),
        revenue_xtr_total=rev_map.get("XTR", 0),
    )


async def add_promo_code(code: str, reward: int, uses: int) -> bool:
    c = (code or "").strip().upper()
    if not c or reward <= 0 or uses <= 0:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO promo_codes (code, energy_bonus, max_uses, uses_count)
            VALUES (?, ?, ?, 0)
            """,
            (c, reward, uses),
        )
        await db.commit()
    return True


async def list_all_user_ids() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM users") as cur:
            rows = await cur.fetchall()
    return [int(r[0]) for r in rows]
