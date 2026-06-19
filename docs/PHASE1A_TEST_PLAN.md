# PR-P · Phase 1a · Тестовый прогон на реальном PostgreSQL

Этот документ — чек-лист для проверки, что новый data-access слой
(`services/database/*`) корректно работает на живом PG: ON CONFLICT
держит идемпотентность под гонкой, таймауты убивают runaway-запросы,
ROLLBACK откатывает данные, пул не голодает под нагрузкой.

## 0. TL;DR — один прогон от и до

```bash
# 1. Поднять тестовый PG (фоном):
docker compose -f docker-compose.test.yml up -d
docker compose -f docker-compose.test.yml ps        # healthy?

# 2. DSN в окружение:
export POSTGRES_TEST_DSN="postgresql://test:test@127.0.0.1:55432/neuromule_test"

# 3. Накатить схему:
POSTGRES_DSN="$POSTGRES_TEST_DSN" python tools/init_postgres.py

# 4. Integration-тесты + latency-отчёты (≈ 30-90 сек, `-s` обязателен
#    чтобы видеть p50/p95/p99 в выводе):
python -m pytest tests/integration -v -s -m integration

# 5. Нагрузочный smoke (1000 уникальных claim'ов, 20 в параллель):
python tools/loadtest_pg.py --total 1000 --concurrency 20

# 6. Гонка (все 500 клиентов на один charge_id):
python tools/loadtest_pg.py --total 500 --concurrency 50 --race

# 7. Снести тестовый PG (вместе с данными):
docker compose -f docker-compose.test.yml down -v
```

Ожидаемый результат:

* шаг 4 → **12 passed** в integration suite, 0 failed.
* шаг 5 → throughput ≥ 200 claims/s на ноутбуке, p99 ≤ 0.05 s.
* шаг 6 → ровно **1 winner**, 499 losers, БД содержит ровно 1 строку.

---

## 1. Pre-flight (один раз)

### 1.1 Установлен Docker
```bash
docker --version          # >= 24
docker compose version    # plugin v2, не legacy docker-compose
```

### 1.2 Установлен asyncpg (в venv проекта)
```bash
pip install "asyncpg>=0.30"
```

### 1.3 Свободен порт 127.0.0.1:55432
```bash
# Linux/macOS:
ss -tln | grep 55432 || echo "free"
# Windows PowerShell:
netstat -an | findstr 55432
```

Если занят — поменяйте маппинг в `docker-compose.test.yml`
(`"127.0.0.1:55432:5432"`).

---

## 2. Старт тестового PG

### 2.1 Поднять контейнер
```bash
docker compose -f docker-compose.test.yml up -d
```

`healthcheck` ждёт `pg_isready` каждые 2 сек. Дождаться `healthy`:

```bash
docker compose -f docker-compose.test.yml ps
# STATUS: Up 8s (healthy)
```

Если статус `unhealthy` — лог:

```bash
docker compose -f docker-compose.test.yml logs postgres-test
```

### 2.2 Применить DDL
```bash
export POSTGRES_TEST_DSN="postgresql://test:test@127.0.0.1:55432/neuromule_test"
POSTGRES_DSN="$POSTGRES_TEST_DSN" python tools/init_postgres.py
```

Ожидаем:

```
postgres pool …                            (не показывается — connect разовый)
[1/7] CREATE TABLE IF NOT EXISTS users (   …
[2/7] CREATE INDEX IF NOT EXISTS idx_users_referred_by …
…
DDL applied. Public tables present: ['payment_charges','payment_events','users']
```

Скрипт идемпотентен — можно гонять повторно при изменениях схемы.

### 2.3 Контрольная сверка через psql (опционально)
```bash
docker exec -it neuromule_pg_test psql -U test -d neuromule_test -c "\d+ payment_charges"
```

Должно быть:
* PK = `telegram_payment_charge_id` (TEXT);
* `user_id BIGINT REFERENCES users(id)`;
* индекс `idx_payment_charges_user_id`.

---

## 3. Integration-тесты (pytest)

### 3.1 Запуск
```bash
# Базовый прогон (без latency-отчётов в stdout):
python -m pytest tests/integration -v -m integration

# С latency-отчётами в выводе (рекомендую — это даёт «глаза» на p99):
python -m pytest tests/integration -v -s -m integration
```

Параметр `-m integration` страхует от случайного запуска без env —
без `POSTGRES_TEST_DSN` все тесты skip'аются.

Флаг `-s` (`--capture=no`) нужен, чтобы pytest не глотал `print()`
из `LatencyTracker.report(...)`. Без него отчёты появятся только
в diagnostics упавшего теста.

### 3.2 Что проверяется

| Файл | Что покрыто |
|---|---|
| `tests/integration/test_pg_repositories.py` | `init_postgres_pool` (TZ/timeout/jit/app_name), `db_transaction` COMMIT/ROLLBACK, `UserRepository` UPSERT, `PaymentRepository.claim_payment_charge` happy + duplicate + FK violation, **`command_timeout=5.0`** на `pg_sleep(6)` |
| `tests/integration/test_pg_load.py` | 30 параллельных claim'ов на ОДИН charge_id → ровно 1 winner, 50 на разные → все winners, 200 claim'ов через семафор 20 → ни одного error. Каждый тест **печатает latency-отчёт**: samples / mean / p50 / p95 / p99 / max. |
| `tests/integration/test_pg_latency.py` | Целевой latency-бенчмарк с soft-gate'ами на p99 (50/100/150/100 ms): `SELECT 1` baseline, `is_tos_accepted` (hot path), `claim_payment_charge` cold и warm pool сценарии. |

### 3.3 Если тест упал
1. **`test_command_timeout_kills_runaway_query`** не получает `TimeoutError`
   → проверьте, что `command_timeout=5.0` в `services/database/connection.py`
   и переменная `STATEMENT_TIMEOUT_MS = 5000` не была переопределена.
2. **`test_concurrent_same_charge_id_only_one_wins`** даёт >1 winners
   → значит регрессия в SQL `claim_payment_charge`: проверьте, что
   `ON CONFLICT (telegram_payment_charge_id) DO NOTHING RETURNING …`
   действительно использует PRIMARY KEY (PK = `telegram_payment_charge_id`).
3. Любой тест получает `asyncpg.UndefinedTableError`
   → схема не накатана: `python tools/init_postgres.py` ещё раз.

---

## 4. Нагрузочный benchmark (вручную)

### 4.1 Уникальные charge_id (типичный прод-сценарий)
```bash
python tools/loadtest_pg.py --total 5000 --concurrency 50
```

**Бюджеты** (на ноутбуке dev-машины, локальный Docker-PG):

| Метрика | Норма | Тревога |
|---|---|---|
| throughput | ≥ 200 claims/s | < 100 |
| p50 | ≤ 0.005 s | > 0.020 |
| p99 | ≤ 0.050 s | > 0.200 |
| errors | 0 | любое > 0 |
| consistent | YES | NO — **критично**, разбираемся немедленно |

### 4.2 Гонка (`--race`) — 500 клиентов на один charge_id
```bash
python tools/loadtest_pg.py --total 500 --concurrency 50 --race
```

Ожидаем:

```
winners     : 1
losers      : 499
errors      : 0
DB rows     : 1  (expected 1)
consistent  : YES
```

Любое расхождение (winners > 1 или DB rows ≠ 1) = **критический баг**
в `claim_payment_charge` или в схеме. **Не двигаемся к Phase 1b**, пока
не исправим.

### 4.3 Стресс перед сдачей
Снести базу и прогнать оба сценария последовательно:

```bash
docker exec neuromule_pg_test psql -U test -d neuromule_test \
    -c "TRUNCATE payment_events, payment_charges, users RESTART IDENTITY CASCADE"

python tools/loadtest_pg.py --total 10000 --concurrency 100
python tools/loadtest_pg.py --total 1000 --concurrency 100 --race
```

10K последовательных + 1K гонкой — если оба зелёные с `consistent: YES`,
слой готов к подключению `payment_demo.router` в продакшен (Phase 1b).

---

## 5. Диагностика реального PG

### 5.1 Логи slow-query
В `docker-compose.test.yml` мы включили
`log_min_duration_statement=100` — любой запрос >100 ms попадёт в лог:

```bash
docker compose -f docker-compose.test.yml logs postgres-test | grep "duration:"
```

В норме: пусто или единичные строки на warmup'е.

### 5.2 Текущая активность
```bash
docker exec -it neuromule_pg_test psql -U test -d neuromule_test \
    -c "SELECT pid, application_name, state, query
          FROM pg_stat_activity
         WHERE application_name = 'neuromule_bot' AND state != 'idle'"
```

В норме во время benchmark'а — несколько `active`. Если видите
`idle in transaction` — где-то забыли выйти из `db_transaction`.

### 5.3 Размер таблиц после прогона
```bash
docker exec neuromule_pg_test psql -U test -d neuromule_test \
    -c "SELECT relname, n_live_tup
          FROM pg_stat_user_tables
         ORDER BY n_live_tup DESC"
```

После `--total 5000` ожидаем `payment_charges = 5000`, `users = 1`.

---

## 6. Снос

```bash
docker compose -f docker-compose.test.yml down -v
```

`-v` сносит volume `neuromule_pg_test_data` целиком — на следующий
старт получите чистую БД и заново накатите DDL.

---

## 7. Что делать с результатами

* **Все зелёное** → можно идти в Phase 1b: подключение dual-write
  observer'а в `successful_payment_handler` (см.
  `docs/MIGRATION_POSTGRES.md` раздел Phase 1).
* **Race-тест красный** → откат к коду до последнего изменения
  `services/database/repositories.py`, ревью SQL claim_payment_charge,
  повторный прогон.
* **Latency p99 > 200 ms** → проверьте, не запускается ли параллельно
  другой docker (или антивирус). Если воспроизводится в чистом
  окружении — копать в `pg_stat_statements`, проверять fsync/synchronous_commit.
