#!/usr/bin/env bash
# Ручное обновление на VDSina (если GitHub Actions не дошёл).
# Запуск на сервере: bash scripts/vdsina-update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
DEPLOY_DIR="$(pwd)"

echo "==> $(hostname) $(date -Is)"
echo "==> Before: $(git log -1 --oneline 2>/dev/null || echo 'no git')"

for svc in neuromule-bot neuromule_bot; do
  systemctl stop "${svc}" 2>/dev/null || true
done
pkill -f '[n]euromule-bot.*main.py' 2>/dev/null || true
rm -f data/telegram_bot.lock 2>/dev/null || true

git fetch origin main
git reset --hard origin/main

if ! grep -q reply_build_version platforms/build_info.py; then
  echo "ERROR: нет /version в коде — проверьте git remote и push в main"
  exit 1
fi

find "${DEPLOY_DIR}" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
# shellcheck disable=SC1091
source venv/bin/activate
pip install -q -r requirements.txt

pm2 stop all 2>/dev/null || true
pm2 delete all 2>/dev/null || true
sleep 2
pm2 start ecosystem.config.cjs --update-env
pm2 save
sleep 4
pm2 status
echo "==> After: $(git log -1 --oneline)"
echo "==> Напишите боту /version — должен ответить rev=$(git rev-parse --short HEAD)"
