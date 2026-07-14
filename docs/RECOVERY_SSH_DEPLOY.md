# Восстановление SSH-деплоя на VDSina (бот молчит / Actions падает)

## Диагноз (проверено 2026-07-14)

GitHub Actions ходит **не на ваш VDSina**, а на чужой хост из secret:

| Secret | Фактическое значение | Проблема |
|---|---|---|
| `SSH_HOST` | `72.56.236.252` | Это **Timeweb** (AS9123), не VDSina |
| `SSH_USER` | `root` | ок |
| `SSH_KEY` | laptop-ключ `mulen@LAPTOP…` | на целевом сервере не принят |

Сети VDSina — диапазоны вроде `109.234.x.x` (AS216071 SERVERS TECH FZCO / vdsina.com).
Пока `SSH_HOST` указывает на Timeweb, workflow **Deploy to VDSina** физически
не может обновить ваш бот.

Код с фиксом пустых ответов уже в `main`, но на ваш VDSina не попал.

## Что сделать в панели VDSina (2 минуты)

### 1. Узнать IP вашего VPS

VDSina → ваш сервер → скопируйте **IPv4** (обычно `109.234.…`).

### 2. Поднять бота из консоли VDSina (VNC / «Консоль»)

```bash
curl -fsSL https://raw.githubusercontent.com/evgesh1808-cloud/neuromule-bot/main/scripts/vnc-emergency-fix.sh | bash
```

Либо вручную:

```bash
cd /root/neuromule-bot || cd /opt/neuromule-bot || cd /opt/neuromule
pm2 delete neuromule 2>/dev/null || true
git fetch origin main
git reset --hard origin/main
bash scripts/vdsina-update.sh
pm2 list
pm2 logs neuromule-tg --lines 40 --nostream
```

В Telegram: `/version` — должен ответить актуальным `rev=…`.

### 3. Починить GitHub Secrets (чтобы деплой снова работал)

GitHub → Settings → Secrets and variables → Actions:

| Secret | Значение |
|---|---|
| `SSH_HOST` | **ваш реальный IPv4 VDSina** (не `72.56.236.252`) |
| `SSH_USER` | `root` (или ваш SSH-пользователь) |
| `SSH_KEY` | private key, чей **pubkey** лежит в `/root/.ssh/authorized_keys` на VDSina |

Проверка ключа на сервере:

```bash
mkdir -p /root/.ssh && chmod 700 /root/.ssh
# вставьте строку pubkey:
nano /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
```

Затем: Actions → **Deploy to VDSina** → Run workflow.

## Проверка «два процесса = Conflict»

```bash
pm2 list
# Должны быть: neuromule-tg, neuromule-api, neuromule-wb-worker
# НЕ должно быть отдельного процесса с именем neuromule
```

Если `neuromule` ещё есть: `pm2 delete neuromule && pm2 save`.

## После восстановления

- Не храните private key в git.
- Secret `SSH_HOST` должен всегда совпадать с IP в панели VDSina.
