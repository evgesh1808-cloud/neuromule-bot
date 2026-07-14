# Восстановление SSH-деплоя (бот молчит / Actions падает)

## Диагноз (проверено 2026-07-14)

- Хост из secret `SSH_HOST`: Timeweb VPS `72.56.236.252` (AS9123), user `root`, порт `22`.
- TCP/SSH доходит, но ключ из GitHub secret `SSH_KEY` **отклоняется**:
  `ssh: unable to authenticate (publickey)`.
- Fingerprint текущего secret:
  `SHA256:ecGwUF2s2CaMR3GGrfr3AWwf317gfQo7viEaAAwODGM` (`mulen@LAPTOP-SNMQLID0`).
- Этот pubkey **нет** в `/root/.ssh/authorized_keys` на сервере.
- Из‑за этого с ~28 июня workflow **Deploy to VDSina** не обновляет прод.
- Код с фиксом пустых ответов уже в `main`, но на сервер не попал.

## Что сделать в панели Timeweb (1 раз, 2 минуты)

1. Откройте [Timeweb Cloud](https://timeweb.cloud/) → ваш VPS → **VNC / Консоль**.
2. Войдите как `root` (пароль из панели, если ключ не пускает).
3. Выполните:

```bash
mkdir -p /root/.ssh
chmod 700 /root/.ssh

# Вставьте ОДНУ строку pubkey (из GitHub → Settings → Secrets заново
# сгенерированного ключа, либо ту, что выдал агент):
# ssh-ed25519 AAAA... neuromule-github-actions-deploy
nano /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

# Сразу поднимите бота (даже до починки CI):
cd /root/neuromule-bot || cd /opt/neuromule-bot || cd /opt/neuromule
pm2 delete neuromule 2>/dev/null || true
pm2 delete all 2>/dev/null || true
git fetch origin main
git reset --hard origin/main
bash scripts/vdsina-update.sh
pm2 list
pm2 logs neuromule-tg --lines 30 --nostream
```

4. В GitHub → Settings → Secrets and variables → Actions обновите:

| Secret | Значение |
|---|---|
| `SSH_HOST` | `72.56.236.252` |
| `SSH_USER` | `root` |
| `SSH_KEY` | **весь** private key (`-----BEGIN OPENSSH PRIVATE KEY-----` …) |

5. Actions → **Deploy to VDSina** → Run workflow (или push в `main`).
6. В Telegram боту: `/version` — должен ответить актуальным `rev=…`.

## Проверка «два процесса = Conflict»

```bash
pm2 list
# Должны быть: neuromule-tg, neuromule-api, neuromule-wb-worker
# НЕ должно быть отдельного процесса с именем neuromule
```

Если `neuromule` ещё есть: `pm2 delete neuromule && pm2 save`.

## После восстановления

- Не храните private key в git.
- Старый laptop-ключ (`mulen@LAPTOP…`) лучше убрать из `authorized_keys`, если больше не нужен.
