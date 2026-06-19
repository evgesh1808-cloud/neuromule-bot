# SQLite → PostgreSQL: фазированная миграция

**Статус:** Phase 0 завершён — data-access слой готов, не активен в production.
**Цель:** уйти от `database is locked` под нагрузкой, получить честные
ACID-транзакции на уровне БД, открыть путь к multi-instance бот-флоту.

## Контекст

Текущее состояние — `services/repository.py` (aiosqlite) + ряд расширений
(`services/billing/store.py`, `services/db_indexes.py`). SQLite в WAL-mode
выдерживает ~80 RPS на запись на одну ноду, но при росте MAU выше ~5K
активных в час начинаются `database is locked` на параллельных `INSERT`-ах
в `payment_events` / `dialog_messages`.

PostgreSQL устраняет это полностью (MVCC + row-level locks).
**Платим переходом** — нужно изолировать SQL за абстракциями
(Repository Pattern), затем поэтапно мигрировать.

---

## Phase 0 · Data-access слой (готово)

Канонический слой живёт в **`services/database/`** (см. `.cursorrules` §1.11).

**Готово:**

- [x] `services/database/connection.py`:
  - `init_postgres_pool(dsn) -> asyncpg.Pool` со строгими таймаутами
    (`command_timeout=5.0`, `timeout=5.0`, `min_size=10`, `max_size=50`,
    `statement_timeout=5000ms`, `jit=off`, UTC, `application_name`);
  - `db_transaction(pool)` — async-context-manager, ROLLBACK с
    `logger.exception(...)` на любом исключении, COMMIT на успехе.
- [x] `services/database/repositories.py`:
  - `UserRepository(conn)` — `is_tos_accepted`, `accept_tos`
    (`INSERT ... ON CONFLICT (id) DO UPDATE`);
  - `PaymentRepository(conn)` — `claim_payment_charge(charge_id,
    user_id, pack_index) -> bool` через `INSERT ... ON CONFLICT
    (telegram_payment_charge_id) DO NOTHING RETURNING ...`.
- [x] `platforms/handlers/payment_demo.py` — образец `successful_payment`-
      хэндлера с DI пула через `dp.workflow_data["pg_pool"]`.
- [x] `requirements.txt`: `asyncpg>=0.30`.
- [x] `config.py` + `.env.example`: `POSTGRES_DSN`,
      `POSTGRES_POOL_MIN_SIZE`, `POSTGRES_POOL_MAX_SIZE`,
      `POSTGRES_COMMAND_TIMEOUT_SEC=5.0`.
- [x] 10 unit-тестов: `tests/test_database_layer.py` (без реального PG).

**Production-эффект:** нулевой. `POSTGRES_DSN=""` → старый
SQLite-флоу работает как раньше. `payment_demo.router` НЕ
включён в `build_dispatcher()`. Тесты: 366 passed без регрессий.

**НЕ готово (phase 0):**

- PG-схема (DDL) — пока в TODO ниже, будет в `tools/init_postgres.py`.
- DI-контейнер в `run_telegram()` — поднятие пула и инжект в
  `dp.workflow_data["pg_pool"]`.
- Интеграционные тесты против реального PG (docker-compose).

---

## Phase 1 · Dual-write (тестовый прод)

**Подготовка:**

1. Поднять PG (docker-compose или managed: Yandex MDB / Supabase / etc).
2. Накатить схему — `python tools/init_postgres.py` (будет создан в начале фазы).
3. Прописать `POSTGRES_DSN` в `.env` тестовой ноды.

**Изменения в коде:**

1. В `platforms/telegram_bot.py::run_telegram()`:

   ```python
   from services.database import init_postgres_pool

   pg_pool = None
   if settings.postgres_dsn:
       pg_pool = await init_postgres_pool(settings.postgres_dsn)
       dp.workflow_data["pg_pool"] = pg_pool

   try:
       await dp.start_polling(...)
   finally:
       if pg_pool is not None:
           await pg_pool.close()
   ```

2. Включить `payment_demo.router` параллельно с легаси:

   ```python
   from platforms.handlers.payment_demo import router as payment_demo_router

   if settings.postgres_dsn:
       dp.include_router(payment_demo_router)
   ```

3. Легаси `successful_payment_handler` остаётся **source of truth**.
   Демо-handler ПО ИДЕНТИЧНОМУ событию сделает claim в PG (через
   `payment_demo.on_successful_payment`) и засчитает дубль (если v1
   успел) — это нормально для dual-write.

4. Алёрт `MigrationMismatch` (рассогласование счётчиков
   `payment.success` между v1 и v2 за 10 мин) — повод остановить
   миграцию и разобраться.

**Длительность:** 7–14 дней реального трафика, пока счётчики
`payment.success` обеих веток не сойдутся ≤ 0.1% за 48 часов.

---

## Phase 2 · Read-switch

Read-операции переключаются на PG модуль за модулем. Writes пока
по-прежнему dual.

**Порядок переключения (от менее рискованного к более):**

1. `is_tos_accepted` (in-memory cache + PG-fallback, SQLite-readonly).
2. Кабинет / профиль (read-only выборка).
3. `referrals_count`, `count_paid_this_month` (агрегаты).
4. `payment_events` history (admin-stats, BI).
5. `dialog_messages` (последний — самая нагруженная таблица, нужен
   нагрузочный тест PG).

Каждый переключатель — отдельная feature-flag в `config.py`:
`READ_FROM_PG_TOS=true`, `READ_FROM_PG_CABINET=true`, …

**Алёрт:** `ReadDriftDetected` — если PG-чтение даёт расхождение
с SQLite > 0.1% за час, откатываемся.

---

## Phase 3 · Cut-over (writes только в PG)

1. Все writes идут только через `services/database/repositories.py`.
2. Легаси `successful_payment_handler` снимается с регистрации,
   `payment_demo.router` становится production-router'ом
   (переименовать в `payment_handler.py`).
3. SQLite переходит в read-only — оставлен на 48ч как страховка.
4. `services/repository.py` помечается `# DEPRECATED — phase-3`.

**Откат:** возврат `POSTGRES_DSN=""` поднимает SQLite (writes
последние 48ч из dual-фазы там тоже есть).

---

## Phase 4 · Decommission

1. Удалить `services/repository.py` (и все импорты на него).
2. Удалить `data/main.db` после холодного бэкапа.
3. Снести feature-flag'и `READ_FROM_PG_*` из конфига.
4. Удалить `services/db_indexes.py`, `services/db_timing.py`
   (SQLite-only). PG-метрики query duration живут на стороне PG
   (`pg_stat_statements`).
5. Обновить `.cursorrules` (отметить PR-O как deprecated) и
   `docs/PRODUCTION_CHECKLIST.md` (runbook'и `slow-sql` → PG).

---

## PG-схема (DDL — будет в `tools/init_postgres.py` на phase 1)

Минимальные таблицы для phase 1, **строго совместимы** с SQL-кодом
из `services/database/repositories.py`:

```sql
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
);

CREATE INDEX IF NOT EXISTS idx_users_referred_by
    ON users (referred_by) WHERE referred_by IS NOT NULL;

CREATE TABLE IF NOT EXISTS payment_charges (
    telegram_payment_charge_id  TEXT        PRIMARY KEY,
    user_id                     BIGINT      NOT NULL REFERENCES users(id),
    pack_index                  INTEGER     NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payment_charges_user_id
    ON payment_charges (user_id);

CREATE TABLE IF NOT EXISTS payment_events (
    id          BIGSERIAL    PRIMARY KEY,
    user_id     BIGINT       NOT NULL REFERENCES users(id),
    tariff      TEXT         NOT NULL,
    method      TEXT         NOT NULL,                  -- 'r' | 'x'
    amount      INTEGER      NOT NULL,
    currency    TEXT         NOT NULL,                  -- 'RUB' | 'XTR'
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payment_events_user_created
    ON payment_events (user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_payment_events_created_at
    ON payment_events (created_at);
```

**Важно:** PK `payment_charges.telegram_payment_charge_id` — это и есть
тот UNIQUE-constraint, на который опирается
`PaymentRepository.claim_payment_charge`'s `ON CONFLICT`.

---

## Open questions (требуют решения до phase 1)

- **Initial data load:** для dual-write нужны users / referrals из
  существующей SQLite. Скрипт `tools/migrate_sqlite_to_postgres.py`
  блокирует Telegram-polling на время копирования (10–30 минут).
- **Multi-instance:** после phase 3 можно запускать N реплик бота за
  Telegram webhook'ом. Это **отдельный** large PR (требует Redis-locks
  для FSM, общий sticky-state).
- **TOS-cache:** `is_tos_accepted` hot — нужен in-memory TTL-cache
  (LRU 50K записей, TTL 5 мин). До phase 2 решить, нужен ли Redis или
  достаточно in-process.
- **`process_purchase`:** биллинг (`services/billing/store.py`) пока
  не parametrize'ован `conn`. Требует отдельной рефакторинг-фазы 1b
  (вынести `BillingService` с явным `conn`-параметром в той же
  PG-транзакции).

---

## Что не делаем сейчас

- Не запускаем `init_postgres_pool` в `run_telegram()` — DI-проводка
  выполняется в начале phase 1.
- Не пишем интеграционные тесты против PG (нет docker-compose).
- Не трогаем production-handler `successful_payment_handler` — он
  по-прежнему source of truth.
- Не пишем `tools/migrate_sqlite_to_postgres.py` — это начало phase 1.
