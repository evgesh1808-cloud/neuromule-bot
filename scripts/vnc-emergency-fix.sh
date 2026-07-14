#!/usr/bin/env bash
# Аварийное восстановление бота из VNC/консоли VDSina (без GitHub Actions).
# Запуск на сервере под root:
#   curl -fsSL https://raw.githubusercontent.com/evgesh1808-cloud/neuromule-bot/main/scripts/vnc-emergency-fix.sh | bash
# или:
#   cd /root/neuromule-bot && bash scripts/vnc-emergency-fix.sh
#
# Если GitHub Actions падает на SSH — сначала проверьте secret SSH_HOST:
# он должен быть IPv4 вашего VDSina (обычно 109.234.x.x), а не чужой хост.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/evgesh1808-cloud/neuromule-bot.git}"
DEPLOY_DIR="${DEPLOY_DIR:-}"

if [ -z "${DEPLOY_DIR}" ]; then
  for candidate in /root/neuromule-bot /opt/neuromule-bot /opt/neuromule; do
    if [ -d "${candidate}/.git" ] || [ -f "${candidate}/main.py" ]; then
      DEPLOY_DIR="${candidate}"
      break
    fi
  done
fi
if [ -z "${DEPLOY_DIR}" ]; then
  DEPLOY_DIR="/root/neuromule-bot"
fi

echo "==> $(hostname) $(date -Is)"
echo "==> DEPLOY_DIR=${DEPLOY_DIR}"

mkdir -p "${DEPLOY_DIR}"
cd "${DEPLOY_DIR}"

if [ ! -d ".git" ]; then
  echo "==> Fresh clone"
  git clone "${REPO_URL}" .
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  git remote add origin "${REPO_URL}"
fi

fetch_ok=0
for attempt in 1 2 3; do
  if git fetch --prune origin main; then
    fetch_ok=1
    break
  fi
  echo "WARN: git fetch retry ${attempt}/3"
  sleep 3
done
if [ "${fetch_ok}" -ne 1 ]; then
  echo "ERROR: git fetch origin main failed"
  exit 1
fi
git reset --hard origin/main

# Conflict killers
for svc in neuromule-bot neuromule_bot; do
  systemctl stop "${svc}" 2>/dev/null || true
  systemctl disable "${svc}" 2>/dev/null || true
done
pm2 delete neuromule 2>/dev/null || true
pkill -f '[n]euromule-bot.*main.py' 2>/dev/null || true
pkill -f '[N]EUROMULE_PLATFORM=telegram' 2>/dev/null || true
rm -f data/telegram_bot.lock 2>/dev/null || true

bash scripts/vdsina-update.sh

echo "==> pm2 list"
pm2 list
echo "==> recent logs"
pm2 logs neuromule-tg --lines 40 --nostream || true
echo "==> DONE. Напишите боту /version — ожидается rev=$(git rev-parse --short HEAD)"
