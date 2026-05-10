# NeuroMule

Russian version: [README.md](README.md)

Telegram bot with OpenRouter chat, image/video/music flows, tariffs, promo codes, and optional channel subscription gate.

## Requirements

- Python **3.11+** (Docker image: `python:3.11-slim`)
- Bot token from [@BotFather](https://t.me/BotFather)
- [OpenRouter](https://openrouter.ai/) API key for AI features

## Quick start

1. Clone the repo and enter the project directory.

2. Create a virtual environment and install dependencies:

   ```bash
   python -m venv venv
   ```

   Windows: `venv\Scripts\activate`  
   Linux/macOS: `source venv/bin/activate`

   ```bash
   pip install -r requirements.txt
   ```

3. Copy env template and fill secrets:

   ```bash
   cp .env.example .env   # Windows: copy .env.example .env
   ```

   Minimum: `TG_TOKEN`, `OPENROUTER_API_KEY`, `CHANNEL_ID`, `CHANNEL_URL`, `ADMIN_IDS`. See `.env.example` for all variables.

4. Run the Telegram bot (default):

   ```bash
   python main.py
   ```

   Set `NEUROMULE_PLATFORM` in `.env` (`telegram`, `vk`, `max`, or `api` / `miniapp` / `fastapi`) — see `main.py`.

### Telegram API connectivity

If you see errors connecting to `api.telegram.org`, check firewall/VPN: HTTPS to Telegram must work.

### Single instance per machine

A second `python main.py` on the same host exits if the lock port is taken (`NEUROMULE_TELEGRAM_LOCK_PORT`, default `45678`).

## Docker

Place a filled `.env` next to `docker-compose.yml` (secrets are not baked into the image).

```bash
docker compose up -d --build
```

DB and logs use volumes (`docker-compose.yml`). In the container, DB path is `/app/data/neuromule_base.db`.

### VPS deployment (Timeweb and similar)

Use a **VPS with Docker**. Typical flow:

1. Install Git and Docker (+ Compose) on the server.
2. Upload code (`git clone` or SFTP/SCP).
3. Create `.env` on the server (never commit it).
4. Run `docker compose up -d --build` and check logs with `docker compose logs -f`.

Optional host paths for volumes: `NM_DATA_VOL` / `NM_LOGS_VOL` in `.env` (see `docker-compose.yml`).

Confirm outbound HTTPS to `api.telegram.org:443` from the server.

## Admin (in Telegram)

For user IDs listed in `ADMIN_IDS`:

- `/admin` — stats and broadcast  
- `/give_energy <user_id> <amount>`  
- `/add_promo <code> <reward> <max_uses>`  

`ADMIN_USERNAME` (no `@`) adds a support menu link only; it does not grant admin rights.

## Tests

```bash
pytest
```

## Layout

| Path | Role |
|------|------|
| `main.py` | Entry, platform switch |
| `platforms/` | Messenger adapters (Telegram, etc.) |
| `services/` | Domain logic, DB, integrations |
| `content/` | User-facing copy |
| `api/` | FastAPI for Mini App mode |
| `tests/` | Automated tests |

## Security

Do not commit `.env` or publish tokens. Keep only `.env.example` in the repo.

## License

Add a `LICENSE` file if you open-source the project.
