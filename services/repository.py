"""SQLite через aiosqlite: пользователи, рефералы, лимиты, промокоды, диалог, платежи."""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import aiosqlite

from services.db_timing import TimedQuery
from services.dialog_platform import DEFAULT_DIALOG_PLATFORM, normalize_dialog_platform


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
DAILY_ENERGY_LIMIT = 30


async def _migrate_users(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA table_info(users)") as cur:
        rows = await cur.fetchall()
    cols = {row[1] for row in rows}
    added: set[str] = set()
    alters = [
        ("tariff", "ALTER TABLE users ADD COLUMN tariff TEXT DEFAULT 'Free'"),
        ("referred_by", "ALTER TABLE users ADD COLUMN referred_by INTEGER"),
        ("photo_daily_date", "ALTER TABLE users ADD COLUMN photo_daily_date TEXT"),
        ("photo_daily_count", "ALTER TABLE users ADD COLUMN photo_daily_count INTEGER DEFAULT 0"),
        ("balance", "ALTER TABLE users ADD COLUMN balance INTEGER DEFAULT 0"),
        ("crystals", "ALTER TABLE users ADD COLUMN crystals INTEGER DEFAULT 0"),
        ("balance_crystals", "ALTER TABLE users ADD COLUMN balance_crystals INTEGER DEFAULT 0"),
        ("balance_energy", "ALTER TABLE users ADD COLUMN balance_energy INTEGER DEFAULT 30"),
        # NOTE: last_free_date — историческое название поля. Хранит дату последнего успешного
        # получения «Совета дня». К ежедневной энергии (⚡) отношения не имеет. Сброс лимита
        # контролируется автономно через сравнение дат.
        ("last_free_date", "ALTER TABLE users ADD COLUMN last_free_date TEXT"),
        ("last_reset_date", "ALTER TABLE users ADD COLUMN last_reset_date TEXT"),
        ("hd_report_json", "ALTER TABLE users ADD COLUMN hd_report_json TEXT"),
        ("hd_type", "ALTER TABLE users ADD COLUMN hd_type TEXT"),
        ("hd_birth_data", "ALTER TABLE users ADD COLUMN hd_birth_data TEXT"),
        ("match_partner_data", "ALTER TABLE users ADD COLUMN match_partner_data TEXT"),
        ("username", "ALTER TABLE users ADD COLUMN username TEXT"),
        ("persistent_memory", "ALTER TABLE users ADD COLUMN persistent_memory TEXT"),
        ("text_daily_date", "ALTER TABLE users ADD COLUMN text_daily_date TEXT"),
        ("text_daily_count", "ALTER TABLE users ADD COLUMN text_daily_count INTEGER DEFAULT 0"),
        ("has_paid", "ALTER TABLE users ADD COLUMN has_paid INTEGER DEFAULT 0"),
        ("has_pro_analysis", "ALTER TABLE users ADD COLUMN has_pro_analysis INTEGER DEFAULT 0"),
        ("advice_birth_data", "ALTER TABLE users ADD COLUMN advice_birth_data TEXT"),
        ("advice_user_role", "ALTER TABLE users ADD COLUMN advice_user_role TEXT"),
        ("advice_pending_at", "ALTER TABLE users ADD COLUMN advice_pending_at REAL"),
        ("accepted_terms", "ALTER TABLE users ADD COLUMN accepted_terms INTEGER DEFAULT 0"),
        ("blogger_face_file_id", "ALTER TABLE users ADD COLUMN blogger_face_file_id TEXT"),
    ]
    for name, ddl in alters:
        if name not in cols:
            await db.execute(ddl)
            added.add(name)
    if "accepted_terms" in added:
        await db.execute("UPDATE users SET accepted_terms = 1")
    if "balance" in added:
        await db.execute("UPDATE users SET balance = 0")
    if "crystals" in added and "balance" in cols:
        await db.execute("UPDATE users SET crystals = COALESCE(balance, 0)")
    if "balance_crystals" in added:
        if "crystals" in cols:
            await db.execute("UPDATE users SET balance_crystals = COALESCE(crystals, 0)")
        elif "balance" in cols:
            await db.execute("UPDATE users SET balance_crystals = COALESCE(balance, 0)")
    if "balance_energy" in added:
        if "energy" in cols:
            await db.execute("UPDATE users SET balance_energy = COALESCE(energy, 30)")
        else:
            await db.execute("UPDATE users SET balance_energy = 30")


async def _migrate_drop_users_crystals(db: aiosqlite.Connection) -> None:
    """Legacy no-op: ``crystals`` is now a persistent paid balance and must stay."""
    return


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


async def _migrate_blogger_post_drafts(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS blogger_post_drafts (
            post_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            raw_text TEXT NOT NULL,
            hashtags_applied INTEGER NOT NULL DEFAULT 0,
            chat_id INTEGER,
            message_id INTEGER,
            display_text TEXT,
            updated_at REAL NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_blogger_post_drafts_user_updated "
        "ON blogger_post_drafts (user_id, updated_at DESC)"
    )


async def _migrate_identity_map(db: aiosqlite.Connection) -> None:
    """
    Identity Map: сквозной ``account_id`` отдельно от нативных ID платформ.

    ``accounts`` — единый профиль (тариф, баланс в переходный период дублируется в ``users``).
    ``user_platform_links`` — связь (telegram|vk) + external_id → account_id.
    """
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tariff TEXT NOT NULL DEFAULT 'Free',
            balance_energy INTEGER NOT NULL DEFAULT 30,
            balance_crystals INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_platform_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            external_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES accounts(id),
            UNIQUE(platform, external_id)
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_platform_links_account "
        "ON user_platform_links (account_id)"
    )

    async with db.execute("PRAGMA table_info(users)") as cur:
        user_cols = {row[1] for row in await cur.fetchall()}
    if "account_id" not in user_cols:
        await db.execute("ALTER TABLE users ADD COLUMN account_id INTEGER")

    # Backfill: существующие ``users.id`` трактуем как Telegram external_id.
    has_balance_energy = "balance_energy" in user_cols
    has_balance_crystals = "balance_crystals" in user_cols
    if has_balance_energy and has_balance_crystals:
        user_select = "SELECT id, tariff, balance_energy, balance_crystals FROM users"
    else:
        user_select = "SELECT id, tariff FROM users"
    async with db.execute(user_select) as cur:
        legacy_users = await cur.fetchall()
    now = datetime.now(timezone.utc).isoformat()
    for row in legacy_users:
        legacy_id = int(row[0])
        tariff = str(row[1] or "Free")
        balance_energy = int(row[2]) if has_balance_energy and len(row) > 2 and row[2] is not None else 30
        balance_crystals = (
            int(row[3]) if has_balance_crystals and len(row) > 3 and row[3] is not None else 0
        )

        async with db.execute(
            """
            SELECT account_id FROM user_platform_links
            WHERE platform = 'telegram' AND external_id = ?
            """,
            (legacy_id,),
        ) as link_cur:
            link_row = await link_cur.fetchone()

        if link_row:
            account_id = int(link_row[0])
        else:
            cursor = await db.execute(
                """
                INSERT INTO accounts (tariff, balance_energy, balance_crystals, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (tariff, balance_energy, balance_crystals, now),
            )
            account_id = int(cursor.lastrowid)
            await db.execute(
                """
                INSERT OR IGNORE INTO user_platform_links
                    (account_id, platform, external_id, created_at)
                VALUES (?, 'telegram', ?, ?)
                """,
                (account_id, legacy_id, now),
            )

        await db.execute(
            "UPDATE users SET account_id = ? WHERE id = ? AND (account_id IS NULL OR account_id = 0)",
            (account_id, legacy_id),
        )


async def _migrate_table_reports(db: aiosqlite.Connection) -> None:
    """Таблицы table_generator для Mini App API (``/api/v1/reports/{id}``)."""
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS table_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            table_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_table_reports_user_created "
        "ON table_reports (user_id, created_at)"
    )


async def _migrate_wb_api(db: aiosqlite.Connection) -> None:
    """Токены WB API и очередь утренних уведомлений (09:00)."""
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS wb_api_tokens (
            user_id INTEGER PRIMARY KEY,
            api_token TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS wb_api_morning_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            report_id INTEGER NOT NULL,
            scheduled_for TEXT NOT NULL,
            sent_at TEXT,
            net_profit REAL NOT NULL DEFAULT 0,
            group_a_leader TEXT NOT NULL DEFAULT '',
            oos_product TEXT,
            oos_days INTEGER,
            fomo_rub REAL NOT NULL DEFAULT 0,
            morning_insight TEXT NOT NULL DEFAULT '',
            digest_line TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_wb_morning_scheduled "
        "ON wb_api_morning_notifications (scheduled_for, sent_at)"
    )


async def _migrate_dialog_messages(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS dialog_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            platform TEXT NOT NULL DEFAULT 'tg',
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    async with db.execute("PRAGMA table_info(dialog_messages)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "platform" not in cols:
        await db.execute(
            "ALTER TABLE dialog_messages ADD COLUMN platform TEXT NOT NULL DEFAULT 'tg'"
        )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_dialog_messages_user_created "
        "ON dialog_messages (user_id, created_at)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_dialog_messages_user_platform_created "
        "ON dialog_messages (user_id, platform, created_at)"
    )


async def _migrate_promo_codes(db: aiosqlite.Connection) -> None:
    """Промокоды теперь подарочные: ⚡ и/или 💎 — без скидок на тариф."""
    async with db.execute("PRAGMA table_info(promo_codes)") as cur:
        rows = await cur.fetchall()
    cols = {row[1] for row in rows}
    if "allowed_tariffs" not in cols:
        await db.execute(
            "ALTER TABLE promo_codes ADD COLUMN allowed_tariffs TEXT "
            "DEFAULT 'FREE,MINI,SMART,ULTRA'"
        )
    if "crystal_bonus" not in cols:
        await db.execute(
            "ALTER TABLE promo_codes ADD COLUMN crystal_bonus INTEGER NOT NULL DEFAULT 0"
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
                energy INTEGER DEFAULT 30,
                crystals INTEGER DEFAULT 0,
                balance INTEGER DEFAULT 0,
                balance_crystals INTEGER DEFAULT 0,
                balance_energy INTEGER DEFAULT 30,
                -- NOTE: last_free_date — дата последнего «Совета дня» (не связано с ⚡/last_reset_date)
                last_free_date TEXT,
                last_reset_date TEXT,
                hd_report_json TEXT,
                hd_type TEXT,
                hd_birth_data TEXT,
                match_partner_data TEXT,
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
        await _migrate_table_reports(db)
        await _migrate_wb_api(db)
        await _migrate_identity_map(db)
        await _migrate_rate_limit_hits(db)
        await _migrate_blogger_post_drafts(db)
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
        await _migrate_promo_codes(db)
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
        from services.billing.crystals_balance import migrate_crystal_split_columns
        from services.billing.store import _migrate_billing_columns
        from services.db_indexes import ensure_pr_o_indexes

        await _migrate_billing_columns(db)
        await migrate_crystal_split_columns(db)
        # PR-O: индексы под hot queries (referrals/payment_events и т.п.).
        # CREATE INDEX IF NOT EXISTS — идемпотентно, повторный запуск
        # пропускает уже существующие.
        await ensure_pr_o_indexes(db)
        await db.commit()


@dataclass(frozen=True)
class UserRow:
    id: int
    energy: int
    crystals: int
    sub_crystals: int
    buy_crystals: int
    balance_energy: int
    balance_crystals: int
    balance: int
    tariff: str
    subscription_ends_at: str | None
    referred_by: int | None
    photo_daily_date: str | None
    photo_daily_count: int
    text_daily_date: str | None
    text_daily_count: int
    has_paid: bool
    has_pro_analysis: bool
    hd_type: str | None
    hd_birth_data: str | None
    # NOTE: Историческое название поля. Хранит дату последнего успешного получения "Совета дня".
    # К ежедневной энергии (⚡) отношения не имеет. Сброс лимита контролируется автономно через сравнение дат.
    last_free_date: str | None
    last_reset_date: str | None

    @property
    def crystals_balance(self) -> int:
        return self.sub_crystals + self.buy_crystals


async def get_or_create_account(platform: str, external_id: int) -> int:
    """
    Находит или создаёт сквозной ``account_id`` по паре (platform, external_id).

    Платформы: ``telegram``, ``vk`` (и др. — lowercase).

    Переходный период (Telegram):
        - ``users.id`` по-прежнему равен ``external_id`` Telegram для биллинга/диалога;
        - ``users.account_id`` связывает legacy-строку с ``accounts.id``.

    Пример для Use-Case слоя::

        account_id = await get_or_create_account("telegram", callback.from_user.id)
        # Биллинг и dialog_messages пока на legacy users.id:
        legacy_uid = await legacy_user_id_for_account(account_id, platform="telegram")
        result = await run_chat_turn(settings, legacy_uid, text, ...)
        # Новый код может оперировать account_id в метриках и кросс-платформенных фичах.

    VK user 123 и Telegram user 123 получают **разные** ``account_id``.
    """
    platform_key = (platform or "").strip().lower()
    if platform_key not in ("telegram", "vk", "max"):
        raise ValueError(f"unsupported platform: {platform!r}")
    native_id = int(external_id)
    if native_id <= 0:
        raise ValueError("external_id must be positive")

    if platform_key == "telegram":
        await ensure_user(native_id)

    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT account_id FROM user_platform_links
            WHERE platform = ? AND external_id = ?
            """,
            (platform_key, native_id),
        ) as cur:
            row = await cur.fetchone()
        if row:
            return int(row[0])

        cursor = await db.execute(
            """
            INSERT INTO accounts (tariff, balance_energy, balance_crystals, created_at)
            VALUES ('Free', 30, 0, ?)
            """,
            (now,),
        )
        account_id = int(cursor.lastrowid)
        await db.execute(
            """
            INSERT INTO user_platform_links (account_id, platform, external_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (account_id, platform_key, native_id, now),
        )

        if platform_key == "telegram":
            await db.execute(
                "UPDATE users SET account_id = ? WHERE id = ?",
                (account_id, native_id),
            )

        await db.commit()
        return account_id


async def legacy_user_id_for_account(account_id: int, *, platform: str = "telegram") -> int | None:
    """
    Возвращает нативный ``external_id`` платформы для legacy-таблиц (``users``, ``dialog_messages``).

    Для Telegram это по-прежнему ``users.id`` == ``telegram_user_id``.
    """
    platform_key = (platform or "").strip().lower()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT external_id FROM user_platform_links
            WHERE account_id = ? AND platform = ?
            """,
            (int(account_id), platform_key),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return int(row[0])


async def get_account_id_for_platform(platform: str, external_id: int) -> int | None:
    """Возвращает ``account_id`` без создания новой записи."""
    platform_key = (platform or "").strip().lower()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT account_id FROM user_platform_links
            WHERE platform = ? AND external_id = ?
            """,
            (platform_key, int(external_id)),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else None


async def ensure_user(user_id: int, username: str | None = None) -> None:
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, last_reset_date FROM users WHERE id = ?", (user_id,)) as cur:
            exists = await cur.fetchone()
        if not exists:
            await db.execute(
                """
                INSERT INTO users (
                    id,
                    energy,
                    crystals,
                    balance_energy,
                    balance_crystals,
                    balance,
                    energy_free,
                    energy_paid,
                    last_reset_date,
                    tariff,
                    referred_by,
                    photo_daily_date,
                    photo_daily_count,
                    accepted_terms
                )
                VALUES (?, 30, 0, 30, 0, 0, 30, 0, ?, 'Free', NULL, NULL, 0, 0)
                """,
                (user_id, today),
            )
        elif (exists[1] or "") != today:
            from services.billing.store import _migrate_billing_columns, _apply_daily_reset_if_needed

            await _migrate_billing_columns(db)
            await _apply_daily_reset_if_needed(db, user_id, today)
        if username is not None:
            u = username.strip()[:255] if username else ""
            await db.execute("UPDATE users SET username = ? WHERE id = ?", (u or None, user_id))
        await db.commit()


async def user_has_accepted_terms(user_id: int) -> bool:
    """Принял ли пользователь оферту и политику (``accepted_terms = 1``)."""
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT accepted_terms FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return False
    return bool(row[0])


async def set_user_accepted_terms(user_id: int, *, accepted: bool = True) -> None:
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET accepted_terms = ? WHERE id = ?",
            (1 if accepted else 0, user_id),
        )
        await db.commit()


async def get_user_row(user_id: int) -> UserRow:
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT
                id,
                energy,
                crystals,
                COALESCE(sub_crystals, 0),
                COALESCE(buy_crystals, 0),
                balance_energy,
                balance_crystals,
                balance,
                tariff,
                subscription_ends_at,
                referred_by,
                photo_daily_date,
                photo_daily_count,
                text_daily_date,
                text_daily_count,
                has_paid,
                COALESCE(has_pro_analysis, 0),
                hd_type,
                hd_birth_data,
                last_free_date,
                last_reset_date
            FROM users WHERE id = ?
            """,
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    sub = int(row[3] or 0)
    buy = int(row[4] or 0)
    return UserRow(
        id=int(row[0]),
        energy=int(row[1] or 0),
        crystals=sub + buy if (row[3] is not None or row[4] is not None) else int(row[2] or 0),
        sub_crystals=sub,
        buy_crystals=buy,
        balance_energy=int(row[5] or 0),
        balance_crystals=int(row[6] or 0),
        balance=int(row[7] or 0),
        tariff=str(row[8] or "Free"),
        subscription_ends_at=row[9],
        referred_by=int(row[10]) if row[10] is not None else None,
        photo_daily_date=row[11],
        photo_daily_count=int(row[12] or 0),
        text_daily_date=row[13],
        text_daily_count=int(row[14] or 0),
        has_paid=bool(row[15] or 0),
        has_pro_analysis=bool(row[16] or 0),
        hd_type=row[17],
        hd_birth_data=row[18],
        last_free_date=row[19],
        last_reset_date=row[20],
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
    async with TimedQuery("referrals_count"), aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM referrals WHERE inviter_id = ?", (inviter_id,)) as cur:
            row = await cur.fetchone()
    return int(row[0])


async def update_balance(user_id: int, field: str, delta: int) -> None:
    if field not in {"energy", "crystals", "balance", "balance_energy", "balance_crystals"}:
        raise ValueError("Invalid field")
    await ensure_user(user_id)
    if field in {"crystals", "balance", "balance_crystals"}:
        from services.billing.crystals_balance import add_buy_crystals

        await add_buy_crystals(user_id, delta)
        return
    if delta == 0:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE users SET
                energy_paid = COALESCE(energy_paid, 0) + ?,
                energy = COALESCE(energy_free, 0) + COALESCE(energy_paid, 0) + ?,
                balance_energy = COALESCE(energy_free, 0) + COALESCE(energy_paid, 0) + ?
            WHERE id = ?
            """,
            (delta, delta, delta, user_id),
        )
        await db.commit()


async def try_consume_energy(user_id: int, amount: int) -> bool:
    if amount <= 0:
        return True
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE users
            SET energy = energy - ?,
                balance_energy = energy - ?
            WHERE id = ? AND energy >= ?
            """,
            (amount, amount, user_id, amount),
        )
        await db.commit()
        return cur.rowcount == 1


async def try_consume_crystals(user_id: int, amount: int) -> bool:
    from services.billing.crystals_balance import spend_crystals_split
    from services.god_mode import billing_bypass

    if billing_bypass(user_id):
        return True
    return await spend_crystals_split(user_id, amount)


async def check_and_spend(user_id: int, amount: int) -> bool:
    """Проверить и списать 💎 с баланса пользователя."""
    return await try_consume_crystals(user_id, amount)


async def try_consume_chat_credit(user_id: int) -> str | None:
    """Spend 1 free energy first, then 1 paid crystal. Returns spent column name."""
    from services.god_mode import billing_bypass

    if billing_bypass(user_id):
        return "god_mode"
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE users
            SET energy = energy - 1,
                balance_energy = energy - 1
            WHERE id = ? AND energy >= 1
            """,
            (user_id,),
        )
        if cur.rowcount == 1:
            await db.commit()
            return "energy"
        cur = await db.execute(
            """
            UPDATE users
            SET crystals = crystals - 1,
                balance = crystals - 1,
                balance_crystals = crystals - 1
            WHERE id = ? AND crystals >= 1
            """,
            (user_id,),
        )
        await db.commit()
        if cur.rowcount == 1:
            return "crystals"
    return None


async def reset_daily_energy(limit: int = DAILY_ENERGY_LIMIT) -> None:
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET energy = ?, balance_energy = ?, last_reset_date = ?", (limit, limit, today))
        await db.commit()


async def try_consume_daily_photo_slot(user_id: int, daily_limit: int) -> tuple[bool, int]:
    """Атомарно резервирует слот free-фото (BEGIN IMMEDIATE — защита от гонок)."""
    await ensure_user(user_id)
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT photo_daily_date, photo_daily_count FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        d, c = row[0], row[1]
        count = int(c or 0)
        if d != today:
            count = 0
        if count >= daily_limit:
            await db.commit()
            return False, count
        new_count = count + 1
        await db.execute(
            """
            UPDATE users
            SET photo_daily_date = ?, photo_daily_count = ?
            WHERE id = ?
            """,
            (today, new_count, user_id),
        )
        await db.commit()
        return True, new_count


_ADVICE_PENDING_STALE_SEC = 900.0


async def try_begin_daily_advice(user_id: int) -> bool:
    """
    Резервирует генерацию совета дня (без фиксации last_free_date).

    Блокирует повторный запуск, пока другой запрос в процессе; зависший lock
  сбрасывается через ``_ADVICE_PENDING_STALE_SEC`` секунд.
    """
    from services.god_mode import billing_bypass

    if billing_bypass(user_id):
        return True
    await ensure_user(user_id)
    today = date.today().isoformat()
    now = time.time()
    stale_before = now - _ADVICE_PENDING_STALE_SEC
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT last_free_date, advice_pending_at FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        last_free = (row[0] or "") if row else ""
        pending_at = float(row[1] or 0) if row and row[1] is not None else 0.0
        if last_free == today:
            await db.commit()
            return False
        if pending_at and pending_at > stale_before:
            await db.commit()
            return False
        await db.execute(
            "UPDATE users SET advice_pending_at = ? WHERE id = ?",
            (now, user_id),
        )
        await db.commit()
        return True


async def commit_daily_advice(user_id: int) -> None:
    """Фиксирует успешный совет дня (суточный лимит)."""
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE users
            SET last_free_date = ?, advice_pending_at = NULL
            WHERE id = ?
            """,
            (today, user_id),
        )
        await db.commit()


async def rollback_daily_advice(user_id: int) -> None:
    """Снимает резерв при ошибке Gemini (лимит не тратится)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET advice_pending_at = NULL WHERE id = ?",
            (user_id,),
        )
        await db.commit()


async def reset_admin_daily_advice_test_state(user_id: int) -> None:
    """Сброс лимита и профиля совета дня для админ-тестов (/reset_me)."""
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE users SET
                last_free_date = NULL,
                advice_pending_at = NULL,
                hd_type = NULL,
                advice_birth_data = NULL
            WHERE id = ?
            """,
            (user_id,),
        )
        await db.commit()


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


async def try_redeem_promo(user_id: int, raw_code: str) -> tuple[bool, str, int, int]:
    """
    Активация подарочного промокода.

    Промокоды дают единоразовое начисление ресурсов: энергия (paid) и/или
    вечные кристаллы (``buy_crystals``). На стоимость подписок влияния НЕТ.

    Возвращает ``(ok, key, energy_bonus, crystal_bonus)``.
    ``key``: unknown | used | exhausted | tariff_blocked | redeemed.
    """
    from services.promo_discount import is_tariff_allowed

    await ensure_user(user_id)
    code = (raw_code or "").strip().upper()
    if not code:
        return False, "unknown", 0, 0
    async with aiosqlite.connect(DB_PATH) as db:
        await _migrate_promo_codes(db)
        async with db.execute(
            """
            SELECT energy_bonus, max_uses, uses_count, allowed_tariffs, crystal_bonus
            FROM promo_codes WHERE code = ?
            """,
            (code,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return False, "unknown", 0, 0
        energy_bonus = int(row[0] or 0)
        max_uses = int(row[1])
        uses = int(row[2])
        allowed_tariffs = row[3]
        crystal_bonus = int(row[4] or 0)

        async with db.execute(
            "SELECT 1 FROM promo_redemptions WHERE user_id = ? AND code = ?",
            (user_id, code),
        ) as cur:
            if await cur.fetchone():
                return False, "used", 0, 0
        if uses >= max_uses:
            return False, "exhausted", 0, 0

        async with db.execute("SELECT tariff FROM users WHERE id = ?", (user_id,)) as cur:
            user_row = await cur.fetchone()
        user_tariff = str(user_row[0] if user_row else "FREE")
        if not is_tariff_allowed(user_tariff, allowed_tariffs):
            return False, "tariff_blocked", 0, 0

        redeemed_at = date.today().isoformat()
        await db.execute(
            "INSERT INTO promo_redemptions (user_id, code, redeemed_at) VALUES (?, ?, ?)",
            (user_id, code, redeemed_at),
        )
        await db.execute(
            "UPDATE promo_codes SET uses_count = uses_count + 1 WHERE code = ?",
            (code,),
        )

        if energy_bonus > 0:
            await db.execute(
                """
                UPDATE users SET
                    energy_paid = COALESCE(energy_paid, 0) + ?,
                    energy = COALESCE(energy_free, 0) + COALESCE(energy_paid, 0) + ?,
                    balance_energy = COALESCE(energy_free, 0) + COALESCE(energy_paid, 0) + ?
                WHERE id = ?
                """,
                (energy_bonus, energy_bonus, energy_bonus, user_id),
            )
        if crystal_bonus > 0:
            await db.execute(
                """
                UPDATE users SET
                    buy_crystals = COALESCE(buy_crystals, 0) + ?,
                    crystals = COALESCE(sub_crystals, 0) + COALESCE(buy_crystals, 0) + ?,
                    balance = COALESCE(sub_crystals, 0) + COALESCE(buy_crystals, 0) + ?,
                    balance_crystals = COALESCE(sub_crystals, 0) + COALESCE(buy_crystals, 0) + ?
                WHERE id = ?
                """,
                (crystal_bonus, crystal_bonus, crystal_bonus, crystal_bonus, user_id),
            )
        await db.commit()

    return True, "redeemed", energy_bonus, crystal_bonus


async def dialog_append(
    user_id: int,
    role: str,
    content: str,
    *,
    platform: str = DEFAULT_DIALOG_PLATFORM,
) -> None:
    if role not in ("user", "assistant"):
        raise ValueError("role must be 'user' or 'assistant'")
    platform_key = normalize_dialog_platform(platform)
    await ensure_user(user_id)
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO dialog_messages (user_id, platform, role, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, platform_key, role, content, ts),
        )
        await db.commit()


async def dialog_fetch_last(
    user_id: int,
    limit: int,
    *,
    platform: str = DEFAULT_DIALOG_PLATFORM,
) -> list[tuple[str, str]]:
    if limit <= 0:
        return []
    platform_key = normalize_dialog_platform(platform)
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT role, content FROM dialog_messages
            WHERE user_id = ? AND platform = ?
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            (user_id, platform_key, limit),
        ) as cur:
            rows = list(await cur.fetchall())
    rows.reverse()
    return [(str(r[0]), str(r[1])) for r in rows]


async def dialog_total_messages(
    user_id: int,
    *,
    platform: str = DEFAULT_DIALOG_PLATFORM,
) -> int:
    platform_key = normalize_dialog_platform(platform)
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM dialog_messages WHERE user_id = ? AND platform = ?",
            (user_id, platform_key),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0])


async def dialog_pop_last_for_user(
    user_id: int,
    *,
    platform: str = DEFAULT_DIALOG_PLATFORM,
) -> None:
    platform_key = normalize_dialog_platform(platform)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            DELETE FROM dialog_messages
            WHERE id = (
                SELECT id FROM dialog_messages
                WHERE user_id = ? AND platform = ?
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT 1
            )
            """,
            (user_id, platform_key),
        )
        await db.commit()


async def dialog_prune_keep_last(
    user_id: int,
    keep: int,
    *,
    platform: str = DEFAULT_DIALOG_PLATFORM,
) -> None:
    if keep <= 0:
        return
    platform_key = normalize_dialog_platform(platform)
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT id FROM dialog_messages
            WHERE user_id = ? AND platform = ?
            ORDER BY datetime(created_at) ASC, id ASC
            """,
            (user_id, platform_key),
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


async def get_blogger_face_file_id(user_id: int) -> str | None:
    """Telegram ``file_id`` загруженного фото лица для AI-обложки блогера."""
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT blogger_face_file_id FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row or not row[0]:
        return None
    file_id = str(row[0]).strip()
    return file_id or None


async def set_blogger_face_file_id(user_id: int, file_id: str) -> None:
    """Сохраняет или обновляет фото лица пользователя для обложек блогера."""
    await ensure_user(user_id)
    clean = (file_id or "").strip()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET blogger_face_file_id = ? WHERE id = ?",
            (clean or None, user_id),
        )
        await db.commit()


async def has_blogger_face_photo(user_id: int) -> bool:
    return (await get_blogger_face_file_id(user_id)) is not None


async def save_blogger_post_draft(
    *,
    post_id: str,
    user_id: int,
    raw_text: str,
    hashtags_applied: bool = False,
    chat_id: int | None = None,
    message_id: int | None = None,
    display_text: str | None = None,
) -> None:
    """Персистентный черновик поста блогера для inline-кнопок конструктора."""
    await ensure_user(user_id)
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO blogger_post_drafts (
                post_id, user_id, raw_text, hashtags_applied,
                chat_id, message_id, display_text, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(post_id) DO UPDATE SET
                raw_text = excluded.raw_text,
                hashtags_applied = excluded.hashtags_applied,
                chat_id = excluded.chat_id,
                message_id = excluded.message_id,
                display_text = excluded.display_text,
                updated_at = excluded.updated_at
            """,
            (
                post_id,
                user_id,
                raw_text,
                1 if hashtags_applied else 0,
                chat_id,
                message_id,
                display_text,
                now,
            ),
        )
        await db.commit()


async def load_blogger_post_draft(post_id: str, user_id: int) -> dict[str, object] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT post_id, user_id, raw_text, hashtags_applied,
                   chat_id, message_id, display_text
            FROM blogger_post_drafts
            WHERE post_id = ? AND user_id = ?
            """,
            (post_id, user_id),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return {
        "post_id": str(row[0]),
        "user_id": int(row[1]),
        "raw_text": str(row[2] or ""),
        "hashtags_applied": bool(row[3]),
        "chat_id": int(row[4]) if row[4] is not None else None,
        "message_id": int(row[5]) if row[5] is not None else None,
        "display_text": row[6],
    }


async def load_last_blogger_post_draft(user_id: int) -> dict[str, object] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT post_id, user_id, raw_text, hashtags_applied,
                   chat_id, message_id, display_text
            FROM blogger_post_drafts
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return {
        "post_id": str(row[0]),
        "user_id": int(row[1]),
        "raw_text": str(row[2] or ""),
        "hashtags_applied": bool(row[3]),
        "chat_id": int(row[4]) if row[4] is not None else None,
        "message_id": int(row[5]) if row[5] is not None else None,
        "display_text": row[6],
    }


async def clear_user_dialog_and_memory(user_id: int) -> None:
    """Полный reset: удаляет историю диалога и ИИ-Память.

    Используется только админ-сбросом или удалением аккаунта.
    Для пользовательской кнопки «🧹 Новый диалог» используй
    :func:`clear_user_dialog` — она НЕ стирает ИИ-Память (по ТЗ).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM dialog_messages WHERE user_id = ?", (user_id,))
        await db.execute("UPDATE users SET persistent_memory = NULL WHERE id = ?", (user_id,))
        await db.commit()


async def clear_user_dialog(
    user_id: int,
    *,
    platform: str = DEFAULT_DIALOG_PLATFORM,
) -> None:
    """Очищает историю диалога на одной платформе. ИИ-Память (persistent_memory) сохраняется."""
    platform_key = normalize_dialog_platform(platform)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM dialog_messages WHERE user_id = ? AND platform = ?",
            (user_id, platform_key),
        )
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
        async with TimedQuery("claim_payment_charge"):
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
    async with TimedQuery("insert_payment_event"):
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
    orders_total: int
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
    async with TimedQuery("get_sales_stats"), aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            users_total = int((await cur.fetchone())[0] or 0)

        async with db.execute("SELECT COUNT(*) FROM payment_events") as cur:
            orders_total = int((await cur.fetchone())[0] or 0)

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
        orders_total=orders_total,
        mini_today=today_map.get("MINI", 0),
        smart_today=today_map.get("SMART", 0),
        ultra_today=today_map.get("ULTRA", 0),
        mini_all=all_map.get("MINI", 0),
        smart_all=all_map.get("SMART", 0),
        ultra_all=all_map.get("ULTRA", 0),
        revenue_rub_total=rev_map.get("RUB", 0),
        revenue_xtr_total=rev_map.get("XTR", 0),
    )


def sales_stats_as_dict(stats: SalesStats) -> dict[str, int | float]:
    """Сводка для админ-панели (ключи как в шаблоне process_admin_stats)."""
    return {
        "total_users": stats.users_total,
        "total_orders": stats.orders_total,
        "today_mini": stats.mini_today,
        "today_smart": stats.smart_today,
        "today_ultra": stats.ultra_today,
        "all_mini": stats.mini_all,
        "all_smart": stats.smart_all,
        "all_ultra": stats.ultra_all,
        "revenue_rub": stats.revenue_rub_total,
        "revenue_stars": stats.revenue_xtr_total,
    }


async def add_promo_code(
    code: str,
    reward: int,
    uses: int,
    *,
    allowed_tariffs: str = "FREE,MINI,SMART,ULTRA",
    crystal_bonus: int = 0,
) -> bool:
    """Создаёт подарочный промокод (⚡ и/или 💎). Скидки не поддерживаются."""
    c = (code or "").strip().upper()
    if not c or uses <= 0:
        return False
    if reward <= 0 and crystal_bonus <= 0:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        await _migrate_promo_codes(db)
        await db.execute(
            """
            INSERT OR REPLACE INTO promo_codes (
                code, energy_bonus, max_uses, uses_count,
                allowed_tariffs, crystal_bonus
            )
            VALUES (?, ?, ?, 0, ?, ?)
            """,
            (c, max(0, reward), uses, allowed_tariffs.strip(), max(0, crystal_bonus)),
        )
        await db.commit()
    return True


async def list_all_user_ids() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM users") as cur:
            rows = await cur.fetchall()
    return [int(r[0]) for r in rows]


async def insert_table_report(user_id: int, table_json: str) -> int:
    """Сохраняет канонический JSON таблицы; возвращает ``report_id`` для Mini App."""
    await ensure_user(user_id)
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO table_reports (user_id, table_json, created_at)
            VALUES (?, ?, ?)
            """,
            (user_id, table_json, ts),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def fetch_table_report_rows_for_user(
    report_id: int,
    user_id: int,
) -> tuple[list[list[str]], str] | None:
    """
    Загружает строки отчёта (header + data) только для владельца.

    Возвращает ``(rows, title)`` или ``None``.
    """
    from services.table_json import parse_table_json_response

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, table_json FROM table_reports WHERE id = ?",
            (int(report_id),),
        ) as cur:
            row = await cur.fetchone()
    if not row or int(row[0]) != int(user_id):
        return None
    payload = parse_table_json_response(str(row[1]))
    if payload is None:
        return None
    return payload.to_rows_with_header(), payload.title


async def fetch_table_report_json_for_user(report_id: int, user_id: int) -> dict | None:
    """
    Загружает JSON отчёта только если ``table_reports.user_id`` совпадает с владельцем.

    Сохраняет расширенные поля (``abc_analysis``, ``out_of_stock_forecast``) для Mini App.
    """
    import json

    from services.table_json import parse_table_json_response

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, table_json FROM table_reports WHERE id = ?",
            (int(report_id),),
        ) as cur:
            row = await cur.fetchone()
    if not row or int(row[0]) != int(user_id):
        return None
    raw_json = str(row[1])
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    payload = parse_table_json_response(raw_json)
    if payload is not None:
        data.setdefault("title", payload.title)
        data.setdefault("headers", payload.headers)
        data.setdefault("rows", payload.rows)
    return data


async def fetch_latest_table_report_json_for_user(
    user_id: int,
) -> tuple[int, dict] | None:
    """Последний отчёт пользователя (для Studio без report_id в URL)."""
    import json

    from services.table_json import parse_table_json_response

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT id, table_json FROM table_reports
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (int(user_id),),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    report_id = int(row[0])
    raw_json = str(row[1])
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    payload = parse_table_json_response(raw_json)
    if payload is not None:
        data.setdefault("title", payload.title)
        data.setdefault("headers", payload.headers)
        data.setdefault("rows", payload.rows)
    return report_id, data


async def fetch_table_report_json(report_id: int) -> dict | None:
    """
    Загружает отчёт по ``table_reports.id``.

    Возвращает распарсенный объект ``{title, headers, rows}`` или ``None``.
    """
    import json

    from services.table_json import parse_table_json_response

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT table_json FROM table_reports WHERE id = ?",
            (int(report_id),),
        ) as cur:
            row = await cur.fetchone()
    if not row or not row[0]:
        return None
    payload = parse_table_json_response(str(row[0]))
    if payload is None:
        try:
            data = json.loads(str(row[0]))
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return data
    return json.loads(payload.raw_json)


# ── WB API nightly worker ───────────────────────────────────────────────────


async def upsert_wb_api_token(user_id: int, api_token: str, *, enabled: bool = True) -> None:
    await ensure_user(user_id)
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO wb_api_tokens (user_id, api_token, enabled, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                api_token = excluded.api_token,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (int(user_id), api_token.strip(), 1 if enabled else 0, ts),
        )
        await db.commit()


async def list_wb_api_enabled_users() -> list[tuple[int, str]]:
    """``(user_id, api_token)`` для ночного батча."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, api_token FROM wb_api_tokens WHERE enabled = 1"
        ) as cur:
            rows = await cur.fetchall()
    return [(int(r[0]), str(r[1])) for r in rows]


async def insert_wb_morning_notification(
    *,
    user_id: int,
    report_id: int,
    scheduled_for: str,
    digest_line: str,
    net_profit: float,
    group_a_leader: str,
    oos_product: str | None,
    oos_days: int | None,
    fomo_rub: float,
    morning_insight: str,
) -> int:
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO wb_api_morning_notifications (
                user_id, report_id, scheduled_for, sent_at,
                net_profit, group_a_leader, oos_product, oos_days, fomo_rub,
                morning_insight, digest_line, created_at
            ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                int(report_id),
                scheduled_for,
                float(net_profit),
                group_a_leader,
                oos_product,
                oos_days,
                float(fomo_rub),
                morning_insight,
                digest_line,
                ts,
            ),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def list_due_wb_morning_notifications(now_iso: str) -> list[dict]:
    """Неотправленные уведомления, у которых ``scheduled_for <= now``."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT id, user_id, report_id, net_profit, group_a_leader,
                   oos_product, oos_days, fomo_rub, morning_insight, digest_line
            FROM wb_api_morning_notifications
            WHERE sent_at IS NULL AND scheduled_for <= ?
            ORDER BY scheduled_for ASC
            """,
            (now_iso,),
        ) as cur:
            rows = await cur.fetchall()
    keys = (
        "id",
        "user_id",
        "report_id",
        "net_profit",
        "group_a_leader",
        "oos_product",
        "oos_days",
        "fomo_rub",
        "morning_insight",
        "digest_line",
    )
    return [dict(zip(keys, row, strict=True)) for row in rows]


async def mark_wb_morning_notification_sent(notification_id: int) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE wb_api_morning_notifications SET sent_at = ? WHERE id = ?",
            (ts, int(notification_id)),
        )
        await db.commit()


async def fetch_wb_api_settings(user_id: int) -> dict[str, object] | None:
    """Настройки автопилота WB API для Mini App (без полного токена)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT api_token, enabled, updated_at FROM wb_api_tokens WHERE user_id = ?",
            (int(user_id),),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    token = str(row[0] or "").strip()
    masked = ""
    if len(token) > 8:
        masked = f"{token[:4]}…{token[-4:]}"
    elif token:
        masked = "••••"
    return {
        "has_token": bool(token),
        "token_mask": masked,
        "enabled": bool(int(row[1])),
        "updated_at": str(row[2] or ""),
    }


async def set_wb_api_enabled(user_id: int, enabled: bool) -> bool:
    """Вкл/выкл ежедневный мониторинг без смены токена."""
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            UPDATE wb_api_tokens
            SET enabled = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (1 if enabled else 0, ts, int(user_id)),
        )
        await db.commit()
        return int(cursor.rowcount or 0) > 0

