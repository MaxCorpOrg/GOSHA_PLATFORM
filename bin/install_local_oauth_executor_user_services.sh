#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl не найден." >&2
  exit 1
fi

mkdir -p "${USER_SYSTEMD_DIR}"
install -m 644 "${ROOT}/ops/user_systemd/gosha-oauth-executor.service" "${USER_SYSTEMD_DIR}/gosha-oauth-executor.service"
install -m 644 "${ROOT}/ops/user_systemd/gosha-oauth-executor-tunnel.service" "${USER_SYSTEMD_DIR}/gosha-oauth-executor-tunnel.service"

systemctl --user daemon-reload
systemctl --user enable --now gosha-oauth-executor.service gosha-oauth-executor-tunnel.service

cat <<'EOF'
Локальные user-службы установлены и запущены:
- gosha-oauth-executor.service
- gosha-oauth-executor-tunnel.service

Проверка:
  systemctl --user status gosha-oauth-executor.service
  systemctl --user status gosha-oauth-executor-tunnel.service
EOF
