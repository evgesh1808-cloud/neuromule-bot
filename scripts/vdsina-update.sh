#!/usr/bin/env bash
# Ручное обновление на VDSina (если GitHub Actions не дошёл).
# Запуск на сервере: bash scripts/vdsina-update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
DEPLOY_DIR="$(pwd)"

echo "==> $(hostname) $(date -Is)"
echo "==> Dir: ${DEPLOY_DIR}"
echo "==> Before: $(git log -1 --oneline 2>/dev/null || echo 'no git')"

for svc in neuromule-bot neuromule_bot; do
  systemctl stop "${svc}" 2>/dev/null || true
done
pkill -f '[n]euromule-bot.*main.py' 2>/dev/null || true
rm -f data/telegram_bot.lock 2>/dev/null || true

for attempt in 1 2 3; do
  git fetch origin main && break
  echo "git fetch retry ${attempt}/3"
  sleep 3
done
git reset --hard origin/main

if ! grep -q reply_build_version platforms/build_info.py; then
  echo "ERROR: нет /version в коде — проверьте git remote и push в main"
  exit 1
fi

find "${DEPLOY_DIR}" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

if [ ! -d "venv" ]; then
  python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
pip install -q -r requirements.txt

pm2 stop all 2>/dev/null || true
pm2 delete all 2>/dev/null || true
sleep 2
pm2 start ecosystem.config.cjs --update-env
pm2 save || true

online=0
for attempt in 1 2 3 4 5 6; do
  sleep 5
  if pm2 list 2>/dev/null | grep -F 'neuromule-tg' | grep -Fq 'online'; then
    online=1
    break
  fi
  echo "==> wait neuromule-tg online (${attempt}/6)..."
done

pm2 status
if [ "${online}" -ne 1 ]; then
  echo "ERROR: neuromule-tg не в статусе online"
  pm2 logs neuromule-tg --lines 50 --nostream || true
  exit 1
fi

echo "==> After: $(git log -1 --oneline)"
echo "==> Напишите боту /version — должен ответить rev=$(git rev-parse --short HEAD)"
