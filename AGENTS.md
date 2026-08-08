# AGENTS.md

## Cursor Cloud specific instructions

NeuroMule is a Python 3.11+ aiogram Telegram bot (AI chat/image/video/music,
Wildberries analytics, payments) plus a few companion services selected via the
`NEUROMULE_PLATFORM` env var in `main.py`. Standard setup/run/test commands live
in `README.md`; only non-obvious cloud caveats are captured here.

### Environment
- Dependencies live in a virtualenv at `/workspace/venv`. Use `./venv/bin/python`
  (or activate it) rather than the system `python3`.
- The update script keeps `venv` in sync with `requirements.txt`. System build
  deps (`build-essential`, `python3.12-dev`) are required only to compile
  `pyswisseph` from source and are baked into the VM; they are intentionally NOT
  in the update script.

### Tests / lint
- Run tests with `./venv/bin/python -m pytest -q` (config in `pytest.ini`).
- No linter/formatter is configured; the project's quality gate is `pytest`.
- Known: `tests/test_try_consume_energy.py::test_try_consume_energy_success_and_balance`
  and `tests/test_profile_view.py::test_profile_includes_blogger_constructor_block`
  fail against the current code because they expect a starting energy of 30 while
  `config.daily_free_energy` defaults to 10. This is a pre-existing test/config
  mismatch, unrelated to environment setup. Everything else passes (~943 pass,
  18 skipped).
- Integration tests (`@pytest.mark.integration`, `tests/integration/`) auto-skip
  unless `POSTGRES_TEST_DSN` is set. To run them, start the throwaway PG with
  `docker compose -f docker-compose.test.yml up -d` and follow `tests/conftest.py`.

### Running services
- Core product — Telegram bot: `NEUROMULE_PLATFORM=telegram ./venv/bin/python main.py`.
  Requires a real `TG_TOKEN` (BotFather) and `OPENROUTER_API_KEY`; it hard-fails
  on startup without a valid `TG_TOKEN`. Outbound HTTPS to `api.telegram.org`
  works from this VM (a dummy token returns a normal Telegram `Unauthorized`).
- Mini App API (runs locally with no real secrets):
  `NEUROMULE_PLATFORM=api ./venv/bin/python main.py` → uvicorn on port 8000
  (`API_PORT`). `GET /health` and `/docs` are open; data endpoints
  (`/api/v1/reports/...`, `/api/v1/wb/...`) require a Telegram-signed `initData`
  header (HMAC of `TG_TOKEN`). `api.auth.sign_init_data_for_tests` can mint a
  valid header for local testing.
- Summarizer API: `NEUROMULE_PLATFORM=summarizer_api ./venv/bin/python main.py`
  → uvicorn on port 8010 (`SUMMARIZER_API_PORT`); `POST /api/v1/summarize` needs
  `OPENAI_API_KEY`.

### Gotchas
- `main.py` reads `NEUROMULE_PLATFORM` from the real process environment BEFORE
  `.env` is loaded. Setting `NEUROMULE_PLATFORM` inside `.env` does NOT switch
  modes — pass it inline (e.g. `NEUROMULE_PLATFORM=api ./venv/bin/python main.py`).
  All other settings (`TG_TOKEN`, keys, etc.) ARE read from `.env` via
  `config.Settings` (a frozen `pydantic-settings` model).
- `.env` is gitignored; seed it from `.env.example`.
- Telegram mode takes a single-instance lock (port `NEUROMULE_TELEGRAM_LOCK_PORT`,
  default 45678, plus `data/telegram_bot.lock`); a second telegram `main.py` on
  the same VM exits immediately.
- SQLite DB path defaults to `neuromule_base.db` at the repo root; override with
  `DB_PATH`.
