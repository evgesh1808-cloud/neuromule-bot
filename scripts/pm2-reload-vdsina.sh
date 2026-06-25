#!/usr/bin/env bash
# Быстрое восстановление бота на VDSina после деплоя или «бот молчит».
set -euo pipefail
cd "$(dirname "$0")/.."
exec bash scripts/vdsina-update.sh
