#!/usr/bin/env bash
# Ручное обновление на VDSina (если GitHub Actions не дошёл).
# Запуск на сервере: bash scripts/vdsina-update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
DEPLOY_DIR="$(pwd)"

echo "==> $(hostname) $(date -Is)"
echo "==> Dir: ${DEPLOY_DIR}"
echo "==> Remote: $(git remote get-url origin 2>/dev/null || echo 'no remote')"
echo "==> Before: $(git log -1 --oneline 2>/dev/null || echo 'no git')"

DEPLOY_SHA="${DEPLOY_SHA:-}"
LOCAL_SHA="$(git rev-parse HEAD)"
if [ -n "${DEPLOY_SHA}" ]; then
  echo "==> Deploy SHA (from workflow): ${DEPLOY_SHA:0:7}"
  if [ "${LOCAL_SHA}" != "${DEPLOY_SHA}" ]; then
    fetch_ok=0
    for attempt in 1 2 3; do
      if git fetch --prune origin "${DEPLOY_SHA}" main; then
        fetch_ok=1
        break
      fi
      echo "git fetch retry ${attempt}/3"
      sleep 3
    done
    if [ "${fetch_ok}" -ne 1 ]; then
      echo "ERROR: git fetch origin failed after 3 attempts"
      exit 1
    fi
    if ! git cat-file -e "${DEPLOY_SHA}^{commit}" 2>/dev/null; then
      echo "ERROR: commit ${DEPLOY_SHA} not found after fetch"
      exit 1
    fi
    git reset --hard "${DEPLOY_SHA}"
    LOCAL_SHA="$(git rev-parse HEAD)"
  fi
else
  REMOTE_SHA="$(git ls-remote origin refs/heads/main 2>/dev/null | awk '{print $1}')"
  if [ -z "${REMOTE_SHA}" ]; then
    echo "ERROR: git ls-remote origin main — пустой ответ (сеть или remote)"
    exit 1
  fi
  echo "==> GitHub main: ${REMOTE_SHA:0:7}"

  fetch_ok=0
  for attempt in 1 2 3; do
    if git fetch origin main; then
      fetch_ok=1
      break
    fi
    echo "git fetch retry ${attempt}/3"
    sleep 3
  done
  if [ "${fetch_ok}" -ne 1 ]; then
    echo "ERROR: git fetch origin main failed after 3 attempts"
    exit 1
  fi
  git reset --hard origin/main

  LOCAL_SHA="$(git rev-parse HEAD)"
  if [ "${LOCAL_SHA}" != "${REMOTE_SHA}" ]; then
    echo "ERROR: HEAD (${LOCAL_SHA:0:7}) != GitHub main (${REMOTE_SHA:0:7}) после fetch/reset"
    echo "       Попробуйте: git fetch origin main && git reset --hard ${REMOTE_SHA}"
    exit 1
  fi
fi

for svc in neuromule-bot neuromule_bot; do
  systemctl stop "${svc}" 2>/dev/null || true
done
# Legacy PM2-имя «neuromule» (до ecosystem.config.cjs) держит тот же TG_TOKEN
# → Telegram Conflict / «бот не отвечает», пока жив второй процесс.
pm2 delete neuromule 2>/dev/null || true
pkill -f '[n]euromule-bot.*main.py' 2>/dev/null || true
pkill -f '[N]EUROMULE_PLATFORM=telegram' 2>/dev/null || true
rm -f data/telegram_bot.lock 2>/dev/null || true

if ! grep -q reply_build_version platforms/build_info.py; then
  echo "ERROR: нет /version в коде — проверьте git remote и push в main"
  exit 1
fi

find "${DEPLOY_DIR}" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

# VDS ~1GB без swap: три main.py ловят OOM на старте neuromule-tg.
if [ ! -f /swapfile ]; then
  echo "==> Creating 2G /swapfile (OOM guard)"
  if fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none; then
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  else
    echo "WARN: could not create /swapfile"
  fi
elif ! swapon --show 2>/dev/null | grep -q '/swapfile'; then
  swapon /swapfile 2>/dev/null || true
fi

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

CFO_BUILD="$(grep -m1 '_FINANCE_REPORT_BUILD' services/table_wb_finance_ai.py 2>/dev/null | sed -n 's/.*= *"\([^"]*\)".*/\1/p' || true)"
echo "==> After: $(git log -1 --oneline)"
echo "==> CFO build: ${CFO_BUILD:-unknown}"
echo "==> Напишите боту /version — ожидается rev=$(git rev-parse --short HEAD) и ${CFO_BUILD:-cfo-?}"
