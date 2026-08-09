#!/usr/bin/env bash
# Диагностика RAM/swap/PM2 на VDSina (~1GB). Запуск в VNC-консоли:
#   bash scripts/vdsina-memory-check.sh
set -euo pipefail

echo "==> $(hostname) $(date -Is)"
echo
echo "=== RAM + SWAP ==="
free -h
echo
swapon --show 2>/dev/null || echo "(no swap active)"
echo
echo "=== TOP MEMORY (RSS MB) ==="
ps aux --sort=-%mem 2>/dev/null | head -12 || ps -eo pid,rss,comm --sort=-rss | head -12
echo
echo "=== PM2 ==="
if command -v pm2 >/dev/null 2>&1; then
  pm2 list 2>/dev/null || true
  echo
  pm2 jlist 2>/dev/null | python3 - <<'PY' 2>/dev/null || true
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for x in data:
    name = x.get("name", "?")
    env = x.get("pm2_env") or {}
    mon = x.get("monit") or {}
    mem_mb = round((mon.get("memory") or 0) / 1024 / 1024, 1)
    restarts = env.get("restart_time", 0)
    status = env.get("status", "?")
    print(f"  {name}: status={status} mem={mem_mb}MB restarts={restarts}")
PY
else
  echo "pm2 not installed"
fi
echo
echo "=== GIT (neuromule-bot) ==="
for d in /root/neuromule-bot /opt/neuromule-bot /opt/neuromule; do
  if [ -d "${d}/.git" ]; then
    echo "dir=${d} rev=$(git -C "${d}" rev-parse --short HEAD 2>/dev/null) $(git -C "${d}" log -1 --oneline 2>/dev/null)"
  fi
done
echo
echo "=== OOM killer (last 5) ==="
dmesg -T 2>/dev/null | grep -i 'out of memory\|killed process' | tail -5 || journalctl -k --no-pager 2>/dev/null | grep -i 'out of memory\|killed process' | tail -5 || echo "(no OOM lines or need root)"
echo
echo "=== RECOMMENDATIONS ==="
total_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')
swap_kb=$(grep SwapTotal /proc/meminfo | awk '{print $2}')
total_mb=$((total_kb / 1024))
swap_mb=$((swap_kb / 1024))
echo "RAM ~${total_mb}MB, swap ~${swap_mb}MB"
if [ "${swap_mb}" -lt 512 ]; then
  echo "WARN: swap < 512MB — на 1GB VDS neuromule-tg+api+wb-worker часто ловят OOM."
  echo "      Запустите: bash scripts/vdsina-update.sh  (создаст 2G /swapfile)"
fi
if command -v pm2 >/dev/null 2>&1; then
  if pm2 list 2>/dev/null | grep -F 'neuromule-tg' | grep -Fq 'errored\|stopped'; then
    echo "WARN: neuromule-tg не online — pm2 logs neuromule-tg --lines 50 --nostream"
  fi
  if pm2 list 2>/dev/null | grep -Fq 'neuromule '; then
    echo "WARN: legacy процесс «neuromule» — Telegram Conflict. pm2 delete neuromule"
  fi
fi
echo "Expected deploy rev: 4fc961e (or newer from origin/main)"
