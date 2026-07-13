# NeuroMule 🐎⚡️ — Production Deployment Checklist

Целевая платформа: Linux (systemd) + Python 3.12+, опционально Docker.
Один процесс aiogram-polling на хост (lock-file `data/telegram_bot.lock` +
порт `NEUROMULE_TELEGRAM_LOCK_PORT`).

Этот документ — обязательный читать перед первым деплоем. После него
держите его открытым во время каждого следующего релиза.

---

## 0. TL;DR — стандартный релизный цикл

1. На staging: `git pull`, `pip install -r requirements.txt`, `python -m pytest -q`.
2. Прогон зелёный (baseline ≥ 346 passed) → `git push prod main`.
3. На прод-хосте: `systemctl restart neuromule-bot`.
4. Открыть `journalctl -u neuromule-bot -f` и убедиться: `polling started.`
5. Курлом дёрнуть `curl -s http://127.0.0.1:$METRICS_HTTP_PORT/health` → `{"ok":true}`.
6. Открыть Grafana / Prometheus — алёрты `BotMetricsDown` НЕ горят.

Если на каком-то шаге что-то не так — раздел [Инциденты](#5-инциденты).

---

## 1. Pre-deploy: подготовка окружения

### 1.1 Обязательные ENV (см. `.env.example`)

| Категория | Переменная | Что произойдёт без неё |
|---|---|---|
| Telegram | `TG_TOKEN` | Бот не стартует |
| Pay (RUB) | `UKASSA_PROVIDER_TOKEN` либо `PAYMENT_TOKEN` | RUB-оплата выключена (останутся только Stars) |
| AI text | `OPENROUTER_API_KEY` | RuntimeError при старте |
| AI proxy (VDSina) | `AI_PROXY` | Cloudflare блокирует IP датацентра → все AI-запросы падают в runtime; при старте бот упадёт на smoke-check OpenRouter |
| Telegram proxy | `TELEGRAM_PROXY_URL` | Только если `api.telegram.org` недоступен напрямую (независимо от `AI_PROXY`) |
| AI Imagen | `GEMINI_API_KEY` | Daily-advice без иллюстраций |
| Replicate | `REPLICATE_API_TOKEN` | Видео-генерация выключена |
| Suno | `SUNO_API_KEY` | Музыка выключена |
| Gallery mod | `GALLERY_MODERATION_CHAT_ID` | Премодерация деградирует в авто-публикацию + WARNING лог |
| Metrics | `METRICS_HTTP_PORT` | Эндпоинт `/metrics` не поднимается (всё работает, но Prometheus слепой) |
| Lock | `NEUROMULE_TELEGRAM_LOCK_PORT` | Если запустить два процесса на хосте — Telegram Conflict |

#### OpenRouter на VDSina (обход Cloudflare)

На ряде VDS/VPS (включая VDSina) IP датацентра блокируется Cloudflare на стороне
`openrouter.ai`. Telegram при этом часто доступен напрямую — прокси для AI и для
Telegram **настраиваются отдельно**.

1. Проверьте прямой доступ с хоста:
   ```bash
   curl -sI https://openrouter.ai/api/v1/models
   ```
   HTTP `403` или таймаут → задайте `AI_PROXY` в `.env`.

2. Примеры (см. также `.env.example`):
   ```env
   AI_PROXY=http://user:pass@proxy-host:8080
   # или SOCKS5 (нужен httpx[socks] из requirements.txt):
   AI_PROXY=socks5://127.0.0.1:1080
   ```

3. `TELEGRAM_PROXY_URL` и `AI_PROXY` независимы: один может быть пуст, второй — задан.

4. При старте бот выполняет probe прокси и smoke-check OpenRouter. Если прокси мёртв
   или API недоступен — процесс падает с явным `RuntimeError` в `journalctl` (не молчит
   до первого чата).

5. После деплоя:
   ```bash
   python tools/probe_openrouter_model_slugs.py
   journalctl -u neuromule-bot -n 50 | grep -i openrouter
   ```
   Ожидаемые строки: `OpenRouter proxy probe OK` или `прямое подключение`, затем
   `OpenRouter API OK`.

### 1.2 Файловая система

```bash
mkdir -p /var/lib/neuromule/data
chown neuromule:neuromule /var/lib/neuromule/data
chmod 750 /var/lib/neuromule/data
```

- `data/main.db` — SQLite БД (юзеры, балансы, платежи, рефералы).
  **Бэкап обязателен** (см. §2.3).
- `data/telegram_bot.lock` — PID-файл; автоматический cleanup в `atexit`.

### 1.3 Зависимости

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Проверка совместимости Python:

```bash
python --version   # требуется 3.12+
```

### 1.4 Smoke-tests

```bash
python -m pytest -q
```

Жёсткий gate перед деплоем: **346+ passed, 0 failed**. Если падает —
**не деплойте**. См. также `.cursorrules` §4 (Baseline).

### 1.5 Конфиг-валидация

```bash
python -c "from config import settings; print('TG_TOKEN ok:', bool(settings.tg_token)); print('OPENROUTER ok:', bool(settings.openrouter_key))"
```

Любая ошибка `pydantic_core.ValidationError` — фикс в `.env` ДО рестарта.

---

## 2. Deploy

### 2.1 systemd-юнит (рекомендованный)

`/etc/systemd/system/neuromule-bot.service`:

```ini
[Unit]
Description=NeuroMule Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=neuromule
Group=neuromule
WorkingDirectory=/opt/neuromule
EnvironmentFile=/etc/neuromule/.env
ExecStart=/opt/neuromule/.venv/bin/python -m main
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=120
StartLimitBurst=5

# Защита и ресурсные лимиты:
MemoryMax=2G
LimitNOFILE=4096
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/neuromule /var/log/neuromule
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now neuromule-bot
systemctl status neuromule-bot
```

### 2.2 Reverse-proxy для metrics (опционально, но рекомендуем)

`/etc/nginx/sites-enabled/neuromule-metrics`:

```nginx
server {
    listen 9090;
    server_name _;

    location = /metrics {
        auth_basic "neuromule metrics";
        auth_basic_user_file /etc/nginx/.htpasswd-metrics;
        proxy_pass http://127.0.0.1:9101/metrics;
        proxy_read_timeout 5s;
    }

    location = /health {
        proxy_pass http://127.0.0.1:9101/health;
        access_log off;
    }
}
```

Где `9101` = ваш `METRICS_HTTP_PORT`.
**Никогда не выставляйте `127.0.0.1:9101` наружу напрямую** — у эндпоинта нет аутентификации.

### 2.3 Бэкапы SQLite

В `crontab` пользователя `neuromule`:

```cron
# Каждый час: snapshot SQLite через .backup (атомарный)
0 * * * * /usr/bin/sqlite3 /var/lib/neuromule/data/main.db ".backup /var/backups/neuromule/main-$(date +\%Y\%m\%d-\%H).db" && find /var/backups/neuromule -name 'main-*.db' -mtime +7 -delete
```

`.backup` гарантирует, что снэпшот будет консистентным даже под нагрузкой.

### 2.4 Логи

`journalctl -u neuromule-bot` достаточно для дев-окружения. На проде —
ротация через `/etc/systemd/journald.conf` (`SystemMaxUse=2G`,
`MaxRetentionSec=14d`), либо отдельный sink в `loki` / `journald-export`.

---

## 3. Post-deploy: верификация

Сразу после рестарта проверьте по чек-листу:

- [ ] `systemctl status neuromule-bot` → **active (running)**;
- [ ] `journalctl -u neuromule-bot -n 50` → нет `ERROR` / `CRITICAL`;
- [ ] лог содержит `telegram: polling started.`;
- [ ] лог содержит `gc: optimized mode enabled (auto-collect=OFF, frozen=N)`;
- [ ] если `METRICS_HTTP_PORT > 0` — лог содержит
      `metrics_http: serving on http://127.0.0.1:N/metrics/json`;
- [ ] `curl -s http://127.0.0.1:$METRICS_HTTP_PORT/health` → `{"ok":true}`;
- [ ] `curl -s http://127.0.0.1:$METRICS_HTTP_PORT/metrics | head -20` —
      видны строки `# TYPE …`;
- [ ] из своего личного TG: `/start` → бот отвечает в <2 сек;
- [ ] в Grafana dashboard — счётчики `payment_success`, `notify_sent`
      растут со временем;
- [ ] алёрты `BotMetricsDown` / `PaymentPipelineFailed` / `NotifyUnexpectedError`
      НЕ горят 10+ минут после рестарта.

---

## 4. Регулярные операции

### 4.1 Ежедневно (автоматически)

- `controlled_gc_loop` каждые 10 минут (PR-D) — see §5 если slow GC.
- `clear_expired_cache_loop` чистит in-memory `last_share_media` старше 48ч.
- Бэкап SQLite (см. §2.3).

### 4.2 Еженедельно (вручную)

- Прогон `python -m pytest -q` на проде с актуальной БД (read-only).
- Просмотр алёртов Grafana за неделю — нет ли «тихих» warning'ов.
- Проверка размера БД: `du -h /var/lib/neuromule/data/main.db`. Расти
  должна линейно. Резкий скачок — потенциальная утечка.

### 4.3 По релизу

1. Бэкап: `sqlite3 main.db .backup main-pre-release-$(date +%F).db`.
2. `git pull`, `pip install -r requirements.txt`.
3. `python -m pytest -q` → зелёный.
4. `systemctl restart neuromule-bot`.
5. Post-deploy чек-лист (§3).
6. 30-минутный мониторинг алёртов; если ничего не горит → релиз
   считается успешным. Иначе — rollback.

### 4.4 Rollback

```bash
git reset --hard <previous-commit>
pip install -r requirements.txt
cp /var/backups/neuromule/main-pre-release-YYYY-MM-DD.db /var/lib/neuromule/data/main.db
systemctl restart neuromule-bot
```

После rollback'а — снова §3.

---

## 5. Инциденты (runbook'и)

Эти секции напрямую цитируются из алёртов в `monitoring/alerts.yml`.

### 5.1 `PaymentPipelineFailed` — payment-failed { #payment-failed }

**Симптомы:** `increase(payment_failed[5m]) > 0`.
В логе:

```
CRITICAL: Payment failed for user N charge_id=… pack=… — manual saga compensation required
```

**Что делать:**

1. Найти все CRITICAL за окно: `journalctl -u neuromule-bot --since '15 min ago' | grep CRITICAL`.
2. Для каждой записи извлечь `user_id`, `charge_id`, `pack`.
3. В Telegram-кабинете провайдера (ЮKassa / Stars) подтвердить, что
   платёж реально прошёл.
4. Решить вручную:
   - **Платёж есть, юзер без товара** → начислить кристаллы вручную через
     SQL: `UPDATE users SET sub_crystals = sub_crystals + N WHERE telegram_id = U`;
   - **Платёжа нет** (странный кейс) → ответить юзеру через support, что
     платёж не подтвердился; вернуть его deposit, если успел;
5. После ручного fix-а — записать в БД `INSERT INTO payment_events(...)`
   с пометкой `note='manual_saga_compensation YYYY-MM-DD'`.
6. Если CRITICAL продолжает расти после ручных исправлений — это
   bug в коде. Откройте инцидент-тикет, делайте rollback.

### 5.2 `BotMetricsDown` — бот не отвечает { #bot-down }

**Симптомы:** Prometheus `up == 0` 2+ минуты.

1. `systemctl status neuromule-bot` — running?
   - Нет → `systemctl restart neuromule-bot`, идти в §3.
2. Running, но `/metrics` 404? → `METRICS_HTTP_PORT` не задан в env.
   Это не critical, просто закройте алёрт.
3. Running, `/metrics` отвечает, но Prometheus говорит `up=0`?
   → проблема reverse-proxy (nginx), а не бота. Проверьте nginx.
4. `systemctl status` показывает `crash-loop` (`Restart=on-failure`,
   быстрые рестарты)?
   - `journalctl -u neuromule-bot -n 200` — найти Python traceback;
   - типичные причины: пропал `.env`, упал внешний API (Replicate)
     при ленивой инициализации, кончилось место на диске.

### 5.3 `GCSlowPhase` — медленная GC-фаза { #slow-gc }

**Симптомы:** `increase(gc_phase_slow[10m]) > 0`, в логе:

```
WARNING gc: gen=N collected=… elapsed=X.XXXXs
```

1. Проверьте размер in-memory кэшей:
   - `last_share_media._BY_USER` (TTL 48h) — норма ~10K записей;
   - `_LAST_CALL_AT` в throttling — норма ≤ DAU.
2. Если кэши большие — увеличьте `SHARE_CACHE_TTL_SEC` обратной
   стороной (агрессивнее чистка) или увеличьте `MemoryMax` в systemd.
3. Если ничего не помогает — добавьте дополнительный `gc.freeze()` после
   инициализации крупных объектов в `run_telegram()` (см. .cursorrules §2.4).

### 5.4 `NotifyUnexpectedError` — баг в notify-wrapper'е { #notify-unexpected }

**Симптомы:** `notify_unexpected` > 0.

Это всегда баг: специализированные exceptions из `aiogram.exceptions`
ловятся выше, а generic-фолбэк попадает в `ERROR` лог.

1. `journalctl -u neuromule-bot | grep 'telegram_notify: unexpected'`.
2. Скопируйте stacktrace в тикет.
3. Скорее всего нужно добавить новый specialized `except` в
   `platforms/telegram_notify.py`. Сделайте PR (см. правило PR-A).

### 5.5 `SlowDbQuery` — медленный SQL { #slow-sql }

**Симптомы:** `db_query_ms{quantile="1"} > 100` (т.е. ≥100 мс на отдельный запрос).

1. Подключитесь к проду и снимите план запроса:
   ```bash
   sqlite3 /var/lib/neuromule/data/main.db
   EXPLAIN QUERY PLAN SELECT ... FROM ...;
   ```
2. Если видите `SCAN TABLE` — нужен индекс. Добавьте в
   `services/db_indexes.py` и закоммитьте.
3. Если запрос реально по индексу, но всё равно медленный — проверьте
   `VACUUM` и `ANALYZE`:
   ```bash
   sqlite3 main.db "VACUUM; ANALYZE;"
   ```
4. Если БД >2 ГБ — задумайтесь о партиционировании старых
   `payment_events` / `dialog_messages` в отдельные архивные таблицы
   (или Postgres-миграция).

### 5.6 `DownloadTooBigBurst` — медиа сверх лимита { #too-big }

**Симптомы:** `download_too_big{source=…}` растёт.

1. Какой source? VK photo / MAX App video / etc.
2. Найти примеры в логе: `journalctl -u neuromule-bot | grep "exceeds limit source=X"`.
3. Размер реально подскочил (например, Replicate стал отдавать 4K вместо HD)?
   → апгрейдьте `DEFAULT_MAX_BYTES` в `services/streaming_download.py`,
   проверьте RAM-headroom.
4. Это атака (юзер скармливает гигантские URL'ы)? → нужно добавить
   валидацию URL'а в `media_url` source (PR в `last_share_media`).

### 5.7 `MigrationMismatch` — расхождение dual-write { #migration-mismatch }

**Симптомы:** `migration_mismatch{phase="1"} > 0`, в логе
`CRITICAL: dual-write mismatch v1=... v2=... payload=...`.

v1 (SQLite) — source of truth до конца phase-2, поэтому юзер ничего
не замечает. Но PG-копия рассинхронизирована — без её фикса phase-3
(cut-over на PG) делать **нельзя**.

1. Поднять последние CRITICAL-логи за интервал алёрта:
   ```
   journalctl -u neuromule-bot --since "10 min ago" | grep "dual-write mismatch"
   ```
2. Сравнить payload'ы: какие именно успешные платежи разошлись?
   Типичные причины:
   - PG-нода была недоступна → v2 упал, метрика всё равно засчитала
     (см. `MigrationV2Error` отдельно);
   - Конкурентный race с тестовым прогоном `tools/init_postgres.py` —
     схема пересоздавалась, claim'ы потерялись;
   - DDL и SQL в коде разошлись (например, переименовали колонку,
     забыли откатить миграцию).
3. Решение: остановить dual-write observer (закомментировать вызов
   в `successful_payment_handler`), обновить
   `POSTGRES_DSN=""` → перезапустить бот. Phase-1 на паузе.
4. Восстановить PG: либо ручной `INSERT` пропущенных чарджей, либо
   повторный накат `tools/migrate_sqlite_to_postgres.py` для затронутых
   user_id (когда будет написан).
5. Только после **48ч с `migration_mismatch == 0`** возобновлять dual-write.

### 5.8 `MigrationV2Error` — PG-ветка падает { #migration-v2-error }

**Симптомы:** `migration_v2_error > 5 / 10 мин`, в логе
`ERROR: dual-write v2 failed` (exc_info).

Юзеру это не вредит — v1 (SQLite) уже отработал. Но систематические
сбои PG-ветки = деградация PG-инстанса либо bad data path.

1. Какой тип exception? Поднять stacktrace последних 5–10 событий.
   - `asyncpg.PostgresConnectionError` / `TimeoutError` → проблема с
     сетью или с самим PG. Проверьте: `pg_stat_activity`,
     `pg_stat_replication`, нагрузку CPU/IO.
   - `asyncpg.UniqueViolationError` → дубль в `payment_charges` без
     `ON CONFLICT` — баг в SQL, нужен hotfix.
   - `asyncpg.UndefinedTableError` → DDL не накатан на этой ноде →
     `python tools/init_postgres.py` повторно.
2. Если pool исчерпан (`asyncpg.TooManyConnectionsError`) →
   в `services/database/connection.py` поднимите `max_size`, либо
   найдите утечку коннектов (handler забыл выйти из `db_transaction`).
3. Если >50% ошибок сетевые → временно выключите PG-ветку
   (`POSTGRES_DSN=""` + перезапуск), разберитесь с инфраструктурой,
   потом включайте обратно. Метрика `migration_v2_error` НЕ блокирует
   `migration_mismatch` алёрт — следите за обоими.

---

## 6. Ссылки

* `.cursorrules` — архитектурные инварианты;
* `monitoring/alerts.yml` — все Prometheus-правила;
* `monitoring/prometheus.example.yml` — пример scrape-конфига;
* `docs/ARCHITECTURE.md` — карта модулей;
* `docs/TECHNICAL_AUDIT.md` — отчёт по последнему audit'у.

---

**Baseline на момент последнего обновления:** 346 passed, 0 failed.
Любое отклонение требует обновления этого документа.
