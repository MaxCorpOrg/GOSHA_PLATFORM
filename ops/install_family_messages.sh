#!/usr/bin/env bash
set -euo pipefail
# Stage only. Never start a messenger worker or send a message from the installer.
if [[ "${1:-}" != "--stage" || $# != 1 ]]; then
  echo "Usage: bash ops/install_family_messages.sh --stage"
  exit 2
fi
if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run staging as root on the target server."
  exit 1
fi
family_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
family_app_dir=/opt/gosha_platform/app
for family_file in gosha_family_http.py gosha_family_store.py gosha_family_telegram.py family_index.html; do
  [[ -f "${family_app_dir}/platform/${family_file}" ]] || { echo "Deploy the reviewed family application files first."; exit 1; }
done
install -m 644 "${family_repo_root}/ops/systemd/gosha-family-telegram.service" /etc/systemd/system/gosha-family-telegram.service
systemctl daemon-reload
echo "Family Telegram service staged, not started. See docs/FAMILY_MESSAGING_RU.md."
