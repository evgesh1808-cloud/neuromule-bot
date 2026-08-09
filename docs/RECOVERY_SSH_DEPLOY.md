# Восстановление деплоя на VDSina (миграция с Timeweb)

## Контекст: переезд Timeweb → VDSina

Раньше бот жил на **Timeweb** (`72.56.x.x`). После переезда на **VDSina**
(`hosted-by-vdsina.ru` → **`109.234.33.9`**) GitHub Actions **продолжает слать
деплой на старый IP**, если в Secrets не обновили `SSH_HOST`.

| | Timeweb (старый) | VDSina (текущий) |
|---|---|---|
| IP | `72.56.236.252` и др. `72.56.x.x` | **`109.234.33.9`** |
| Панель | timeweb.cloud | vdsina.com |
| Деплой | **отключить** — иначе Conflict по TG_TOKEN | **единственный prod** |

Пока `SSH_HOST` = Timeweb, workflow **Deploy to VDSina** обновляет **не тот
сервер** (или падает на SSH). Фиксы в `main` на VDSina **не попадают** → бот
«молчит» на старом/сломанном процессе.

---

## Шаг 1. Обновить GitHub Secrets (обязательно после переезда)

GitHub → репозиторий **neuromule-bot** → **Settings** → **Secrets and variables**
→ **Actions** → отредактировать:

| Secret | Было (Timeweb) | Должно быть (VDSina) |
|---|---|---|
| `SSH_HOST` | `72.56.236.252` | **`109.234.33.9`** |
| `SSH_USER` | `root` | `root` |
| `SSH_KEY` | старый ключ | private key, принятый на **VDSina** |

Проверка IP в панели VDSina: **Серверы** → ваш VPS → **IPv4** (не hostname из
`.env` — там может быть URL Mini App, это не SSH).

### SSH-ключ для деплоя

На ноутбуке:

```bash
bash scripts/generate-deploy-key.sh
```

На **VDSina** (консоль VNC):

```bash
mkdir -p /root/.ssh && chmod 700 /root/.ssh
nano /root/.ssh/authorized_keys   # вставить строку из *.pub
chmod 600 /root/.ssh/authorized_keys
```

Содержимое **private** key (`neuromule_vdsina_deploy` без `.pub`) → secret
`SSH_KEY` в GitHub.

Затем: **Actions** → **Deploy to VDSina** → **Run workflow**.

Workflow **отклонит** деплой, если `SSH_HOST` всё ещё Timeweb (`72.56.x`,
`5.35.x`, `85.193.x`) — это защита от случайного push на старый хост.

---

## Шаг 2. Поднять бота на VDSina (консоль VNC)

SSH с домашнего ПК на VDSina часто **закрыт** (timeout на :22). Используйте
**консоль в панели VDSina** (VNC / «Терминал»):

```bash
curl -fsSL https://raw.githubusercontent.com/evgesh1808-cloud/neuromule-bot/main/scripts/vnc-emergency-fix.sh | bash
```

Скрипт: `git pull`, 2G swap (защита OOM на 1GB RAM), `pm2` restart.

Вручную:

```bash
cd /root/neuromule-bot || cd /opt/neuromule-bot
pm2 delete neuromule 2>/dev/null || true
git fetch origin main && git reset --hard origin/main
bash scripts/vdsina-update.sh
pm2 list
pm2 logs neuromule-tg --lines 40 --nostream
bash scripts/vdsina-memory-check.sh
```

В Telegram: **`/ping`** → `🏓 pong`, **`/version`** → актуальный `rev=…`.

---

## Шаг 3. Остановить Timeweb (если ещё жив)

Если на Timeweb остался старый процесс с **тем же `TG_TOKEN`**:

```bash
# на старом Timeweb VPS (если доступен)
pm2 stop all && pm2 delete all
```

Иначе Telegram **Conflict** — два polling на одном токене, оба «молчат».

---

## Проверки после миграции

```bash
pm2 list
# OK: neuromule-tg, neuromule-api, neuromule-wb-worker — status online
# BAD: процесс «neuromule» (legacy) → pm2 delete neuromule

free -h && swapon --show
# OK: swap ≥ 2G на 1GB VDS

git log -1 --oneline
# OK: совпадает с последним push в main (например 4fc961e)
```

---

## Если Actions всё ещё падает

1. `SSH_HOST` = IPv4 из панели VDSina (не Timeweb).
2. Pubkey из `SSH_KEY` есть в `/root/.ssh/authorized_keys` на VDSina.
3. На VDSina открыт SSH **для GitHub Actions** (или деплой только через VNC +
   ручной `git pull`).
4. Логи: Actions → последний **Deploy to VDSina** → шаг «Deploy via SSH».

---

## После восстановления

- `SSH_HOST` всегда = IPv4 **текущего** VDS в панели VDSina.
- Private key **не** коммитить в git.
- Timeweb VPS можно выключить после проверки `/version` на VDSina.
