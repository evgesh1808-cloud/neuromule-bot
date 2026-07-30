#!/usr/bin/env python3
"""Одноразовый перенос photo_daily_* → user_daily_quotas (free_banana_daily)."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.billing.daily_quotas import FREE_PHOTO_QUOTA_KEY
from services.repository import DB_PATH


def migrate(db_path: str | None = None) -> int:
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_daily_quotas (
            user_id     INTEGER NOT NULL,
            quota_key   TEXT    NOT NULL,
            quota_date  TEXT    NOT NULL,
            used_count  INTEGER NOT NULL DEFAULT 0,
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, quota_key, quota_date)
        )
        """
    )
    # Legacy free_photo_daily → free_banana_daily (rename rows if present).
    conn.execute(
        """
        UPDATE user_daily_quotas
        SET quota_key = ?
        WHERE quota_key = 'free_photo_daily'
        """,
        (FREE_PHOTO_QUOTA_KEY,),
    )
    cur = conn.execute(
        """
        INSERT INTO user_daily_quotas (user_id, quota_key, quota_date, used_count, updated_at)
        SELECT id, ?, photo_daily_date, photo_daily_count, datetime('now')
        FROM users
        WHERE photo_daily_date IS NOT NULL
          AND COALESCE(photo_daily_count, 0) > 0
        ON CONFLICT(user_id, quota_key, quota_date) DO UPDATE SET
            used_count = MAX(user_daily_quotas.used_count, excluded.used_count),
            updated_at = datetime('now')
        """,
        (FREE_PHOTO_QUOTA_KEY,),
    )
    conn.commit()
    n = int(cur.rowcount or 0)
    conn.close()
    return n


async def _main_async(db_path: str | None) -> None:
    n = migrate(db_path)
    print(f"migrated {n} row(s) → quota_key={FREE_PHOTO_QUOTA_KEY!r}")


def main() -> None:
    db = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(_main_async(db))


if __name__ == "__main__":
    main()
