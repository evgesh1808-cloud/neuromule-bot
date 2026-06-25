#!/usr/bin/env bash
# Быстрое восстановление бота на VDSina после деплоя или «бот молчит».
set -euo pipefail
cd "$(dirname "$0")/.."
DEPLOY_DIR="$(pwd)"

echo "==> $(git log -1 --oneline)"
find "${DEPLOY_DIR}" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
# shellcheck disable=SC1091
source venv/bin/activate
pip install -q -r requirements.txt

# Убрать дубликаты telegram-поллинга (Conflict getUpdates)
pm2 delete neuromule-tg 2>/dev/null || true
pm2 delete neuromule-api 2>/dev/null || true
pm2 delete neuromule-wb-worker 2>/dev/null || true

pm2 start ecosystem.config.cjs --update-env
pm2 save
pm2 status
echo "==> Logs (last 15 lines neuromule-tg):"
pm2 logs neuromule-tg --lines 15 --nostream || true
