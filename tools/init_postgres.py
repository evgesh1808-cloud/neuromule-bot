"""Однократный накат DDL на чистый PostgreSQL — старт Phase 1 миграции.

Использование:

    # либо передаём DSN явно:
    python tools/init_postgres.py "postgresql://user:pass@host:5432/db"

    # либо берём из .env (POSTGRES_DSN):
    python tools/init_postgres.py

Скрипт **идемпотентен** — все объекты создаются через
``IF NOT EXISTS``. Можно гонять повторно (например, после ручных
правок схемы или при апгрейде ноды).

Что создаётся:

* ``users``, ``payment_charges``, ``payment_events`` (см.
  ``docs/MIGRATION_POSTGRES.md`` — раздел PG-схема).
* Индексы под hot queries (синхронизированы с
  ``services/db_indexes.py`` для SQLite-флоу).
* UNIQUE-constraint ``payment_charges.telegram_payment_charge_id``
  (PRIMARY KEY) — на нём держится идемпотентность
  ``PaymentRepository.claim_payment_charge``.

Безопасность:

* Открываем connection с ``timeout=10`` и ``command_timeout=30`` —
  старт может занять время на сетевом PG.
* На любую ошибку — стандартный traceback, exit code 1. Скрипт
  предназначен для запуска человеком, а не из CI.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import asyncpg

# Импорт settings опционален: если .env нет — берём DSN из argv.
try:
    from config import settings
except Exception:  # pragma: no cover — для запуска вне репо
    settings = None  # type: ignore[assignment]


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("init_postgres")


_DDL_STATEMENTS: tuple[str, ...] = (
    # ── users ──────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS users (
        id                 BIGINT       PRIMARY KEY,
        energy             INTEGER      NOT NULL DEFAULT 30,
        crystals           INTEGER      NOT NULL DEFAULT 0,
        balance            INTEGER      NOT NULL DEFAULT 0,
        tariff             TEXT         NOT NULL DEFAULT 'Free',
        accepted_terms     BOOLEAN      NOT NULL DEFAULT FALSE,
        accepted_terms_at  TIMESTAMPTZ,
        referred_by        BIGINT,
        created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_users_referred_by
        ON users (referred_by) WHERE referred_by IS NOT NULL
    """,
    # ── payment_charges ────────────────────────────────────────────────
    # PRIMARY KEY = UNIQUE на telegram_payment_charge_id, на этом держится
    # `ON CONFLICT (telegram_payment_charge_id) DO NOTHING` в
    # `PaymentRepository.claim_payment_charge`.
    """
    CREATE TABLE IF NOT EXISTS payment_charges (
        telegram_payment_charge_id  TEXT        PRIMARY KEY,
        user_id                     BIGINT      NOT NULL REFERENCES users(id),
        pack_index                  INTEGER     NOT NULL,
        created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_payment_charges_user_id
        ON payment_charges (user_id)
    """,
    # ── payment_events ─────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS payment_events (
        id          BIGSERIAL    PRIMARY KEY,
        user_id     BIGINT       NOT NULL REFERENCES users(id),
        tariff      TEXT         NOT NULL,
        method      TEXT         NOT NULL,
        amount      INTEGER      NOT NULL,
        currency    TEXT         NOT NULL,
        created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_payment_events_user_created
        ON payment_events (user_id, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_payment_events_created_at
        ON payment_events (created_at)
    """,
)


def _resolve_dsn(argv: list[str]) -> str:
    """argv > POSTGRES_DSN из .env > ошибка."""
    if len(argv) >= 2 and argv[1].strip():
        return argv[1].strip()
    if settings is not None:
        dsn = getattr(settings, "postgres_dsn", "") or ""
        if dsn:
            return dsn
    raise SystemExit(
        "DSN не задан. Используйте:\n"
        "    python tools/init_postgres.py postgresql://user:pass@host/db\n"
        "или пропишите POSTGRES_DSN в .env"
    )


async def _apply_ddl(dsn: str) -> None:
    logger.info("connecting to %s …", _mask_dsn(dsn))
    conn = await asyncpg.connect(
        dsn=dsn,
        timeout=10.0,
        command_timeout=30.0,
    )
    try:
        for i, sql in enumerate(_DDL_STATEMENTS, 1):
            first_line = sql.strip().splitlines()[0]
            logger.info("[%d/%d] %s …", i, len(_DDL_STATEMENTS), first_line)
            await conn.execute(sql)
        # Контрольная выборка структуры — диагностический след для оператора.
        rows = await conn.fetch(
            """
            SELECT table_name
              FROM information_schema.tables
             WHERE table_schema = 'public'
               AND table_name IN ('users','payment_charges','payment_events')
             ORDER BY table_name
            """
        )
        logger.info(
            "DDL applied. Public tables present: %s",
            [r["table_name"] for r in rows],
        )
    finally:
        await conn.close()


def _mask_dsn(dsn: str) -> str:
    """Скрываем пароль из лога — выводим хост/базу для трейсинга."""
    try:
        from urllib.parse import urlparse

        u = urlparse(dsn)
        host = u.hostname or "?"
        port = u.port or 5432
        db = (u.path or "/").lstrip("/")
        user = u.username or "?"
        return f"postgresql://{user}:***@{host}:{port}/{db}"
    except Exception:
        return "postgresql://***"


def main() -> None:
    dsn = _resolve_dsn(sys.argv)
    try:
        asyncio.run(_apply_ddl(dsn))
    except asyncpg.PostgresError as exc:
        logger.error("PostgreSQL error: %s", exc)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    # Чтобы запуск из любого CWD работал: гарантируем, что корень
    # репо в sys.path (для импорта `config`).
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    main()
