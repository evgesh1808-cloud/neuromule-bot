#!/usr/bin/env bash
# Генерация deploy-ключа для GitHub Actions → VDSina.
# Запуск на ЛОКАЛЬНОЙ машине (не на сервере):
#   bash scripts/generate-deploy-key.sh
#
# 1) Публичный ключ — вставить на VDSina в /root/.ssh/authorized_keys
# 2) Приватный ключ — в GitHub Secret SSH_KEY
set -euo pipefail

KEY_PATH="${1:-$HOME/.ssh/neuromule_vdsina_deploy}"

if [ -f "${KEY_PATH}" ]; then
  echo "ERROR: ${KEY_PATH} уже существует. Удалите вручную или укажите другой путь."
  exit 1
fi

ssh-keygen -t ed25519 -f "${KEY_PATH}" -N "" -C "neuromule-github-actions-deploy"
chmod 600 "${KEY_PATH}"
chmod 644 "${KEY_PATH}.pub"

echo ""
echo "=== Готово ==="
echo "Приватный ключ (GitHub → Settings → Secrets → SSH_KEY):"
echo "  cat ${KEY_PATH}"
echo ""
echo "Публичный ключ (на VDSina в /root/.ssh/authorized_keys):"
echo "  cat ${KEY_PATH}.pub"
echo ""
echo "На VDSina (VNC):"
echo "  mkdir -p /root/.ssh && chmod 700 /root/.ssh"
echo "  nano /root/.ssh/authorized_keys   # вставьте строку из .pub"
echo "  chmod 600 /root/.ssh/authorized_keys"
echo ""
echo "GitHub Secrets:"
echo "  SSH_HOST = IPv4 из панели VDSina (hosted-by-vdsina.ru → 109.234.33.9)"
echo "  SSH_USER = root"
echo "  SSH_KEY  = содержимое ${KEY_PATH}"
