#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${GOSHA_INSTALL_ROOT:-/opt/gosha_platform}"
APP_DIR="${INSTALL_ROOT}/app"
RUNTIME_ROOT="${INSTALL_ROOT}/runtime"
APP_ROOT="${RUNTIME_ROOT}/app_root"
ENV_ROOT="${RUNTIME_ROOT}/env"
REPORTS_ROOT="${RUNTIME_ROOT}/reports"
PUBLIC_HOST="${GOSHA_PUBLIC_HOST:-151.241.228.232}"
PANEL_PORT="${GOSHA_PANEL_PORT:-18876}"
WS_PORT="${GOSHA_WS_PORT:-18080}"
HTTP_PORT="${GOSHA_HTTP_PORT:-18083}"
WEB_PORT="${GOSHA_WEB_PORT:-18082}"
PANEL_URL="http://${PUBLIC_HOST}:${PANEL_PORT}"
WS_URL="ws://${PUBLIC_HOST}:${WS_PORT}/xiaozhi/v1/"
MCP_BASE="ws://${PUBLIC_HOST}:${WS_PORT}/mcp/"
PANEL_PASSWORD_FILE="${ENV_ROOT}/panel.password"
DB_PASSWORD_FILE="${ENV_ROOT}/selfhost-db.password"
BACKEND_STORAGE_ROOT="${APP_ROOT}/selfhost_xiaozhi/backend"

mkdir -p \
  "${INSTALL_ROOT}" \
  "${APP_ROOT}/robots" \
  "${APP_ROOT}/memory" \
  "${APP_ROOT}/mobile" \
  "${APP_ROOT}/edge" \
  "${APP_ROOT}/share/legal" \
  "${APP_ROOT}/shared/kb" \
  "${APP_ROOT}/bin" \
  "${BACKEND_STORAGE_ROOT}/data" \
  "${BACKEND_STORAGE_ROOT}/models" \
  "${BACKEND_STORAGE_ROOT}/mysql" \
  "${BACKEND_STORAGE_ROOT}/redis" \
  "${BACKEND_STORAGE_ROOT}/uploadfile" \
  "${ENV_ROOT}" \
  "${REPORTS_ROOT}"

if [[ "$(readlink -f "${REPO_ROOT}")" != "${APP_DIR}" ]]; then
  rm -rf "${APP_DIR}"
  mkdir -p "${APP_DIR}"
  (
    cd "${REPO_ROOT}"
    tar --exclude='./local_only' -cf - .
  ) | (
    cd "${APP_DIR}"
    tar -xf -
  )
fi

python3 - <<'PY' "${PANEL_PASSWORD_FILE}" "${DB_PASSWORD_FILE}"
import secrets
import sys
from pathlib import Path

for path_str, size in ((sys.argv[1], 20), (sys.argv[2], 24)):
    path = Path(path_str)
    if not path.exists():
        path.write_text(secrets.token_urlsafe(size) + "\n", encoding="utf-8")
        path.chmod(0o600)
PY

DB_PASSWORD="$(tr -d '\r\n' < "${DB_PASSWORD_FILE}")"

if [[ ! -f "${ENV_ROOT}/panel.env" ]]; then
  cat > "${ENV_ROOT}/panel.env" <<EOF
APP_ROOT=${APP_ROOT}
PANEL_HOST=0.0.0.0
PANEL_PORT=${PANEL_PORT}
PUBLIC_PANEL_URL=${PANEL_URL}
PUBLIC_EDGE_HUB_URL=ws://${PUBLIC_HOST}:18890
PANEL_OPERATOR_USER=operator
PANEL_OPERATOR_PASSWORD_FILE=${PANEL_PASSWORD_FILE}
PANEL_SESSION_TTL_SECONDS=43200
SELFHOST_XIAOZHI_PUBLIC_HTTP_BASE=${PANEL_URL}
SELFHOST_XIAOZHI_OTA_URL=${PANEL_URL}/xiaozhi/ota/
SELFHOST_XIAOZHI_ACTIVATE_URL=${PANEL_URL}/xiaozhi/ota/activate
SELFHOST_XIAOZHI_WS_URL=${WS_URL}
SELFHOST_XIAOZHI_MCP_ENDPOINT_BASE=${MCP_BASE}
APK_SHARE_PATH=${APP_ROOT}/share/maxcorp-connector-debug.apk
ADMIN_APK_SHARE_PATH=${APP_ROOT}/share/maxcorp-admin-connector-debug.apk
PRIVACY_POLICY_SHARE_PATH=${APP_ROOT}/share/legal/gosha-privacy-policy.html
TERMS_OF_USE_SHARE_PATH=${APP_ROOT}/share/legal/gosha-terms-of-use.html
EOF
fi

if [[ ! -f "${ENV_ROOT}/selfhost-backend.env" ]]; then
  cat > "${ENV_ROOT}/selfhost-backend.env" <<EOF
SELFHOST_XIAOZHI_TZ=Europe/Moscow
SELFHOST_XIAOZHI_WS_BIND=0.0.0.0
SELFHOST_XIAOZHI_WS_PORT=${WS_PORT}
SELFHOST_XIAOZHI_HTTP_BIND=127.0.0.1
SELFHOST_XIAOZHI_HTTP_PORT=${HTTP_PORT}
SELFHOST_XIAOZHI_WEB_BIND=127.0.0.1
SELFHOST_XIAOZHI_WEB_PORT=${WEB_PORT}
SELFHOST_XIAOZHI_DB_USER=root
SELFHOST_XIAOZHI_DB_PASSWORD=${DB_PASSWORD}
SELFHOST_XIAOZHI_REDIS_PASSWORD=
SELFHOST_XIAOZHI_STORAGE_ROOT=${BACKEND_STORAGE_ROOT}
EOF
fi

copy_if_missing() {
  local src="$1"
  local dst="$2"
  if [[ -f "${src}" && ! -f "${dst}" ]]; then
    mkdir -p "$(dirname "${dst}")"
    cp "${src}" "${dst}"
  fi
}

if [[ -d /opt/ai_robot/robots ]] && [[ -z "$(find "${APP_ROOT}/robots" -mindepth 1 -maxdepth 1 2>/dev/null | head -n 1)" ]]; then
  cp -a /opt/ai_robot/robots/. "${APP_ROOT}/robots/"
fi

copy_if_missing /opt/ai_robot/mobile/onboarding_codes.json "${APP_ROOT}/mobile/onboarding_codes.json"
copy_if_missing /opt/ai_robot/mobile/panel_client_tokens.json "${APP_ROOT}/mobile/panel_client_tokens.json"
copy_if_missing /opt/ai_robot/share/maxcorp-connector-debug.apk "${APP_ROOT}/share/maxcorp-connector-debug.apk"
copy_if_missing /opt/ai_robot/share/maxcorp-admin-connector-debug.apk "${APP_ROOT}/share/maxcorp-admin-connector-debug.apk"
copy_if_missing /opt/ai_robot/share/legal/gosha-privacy-policy.html "${APP_ROOT}/share/legal/gosha-privacy-policy.html"
copy_if_missing /opt/ai_robot/share/legal/gosha-terms-of-use.html "${APP_ROOT}/share/legal/gosha-terms-of-use.html"

if [[ ! -f "${APP_ROOT}/share/maxcorp-connector-debug.apk" ]]; then
  dd if=/dev/zero of="${APP_ROOT}/share/maxcorp-connector-debug.apk" bs=1024 count=128 status=none
fi
if [[ ! -f "${APP_ROOT}/share/maxcorp-admin-connector-debug.apk" ]]; then
  dd if=/dev/zero of="${APP_ROOT}/share/maxcorp-admin-connector-debug.apk" bs=1024 count=128 status=none
fi
if [[ ! -f "${APP_ROOT}/share/legal/gosha-privacy-policy.html" ]]; then
  cat > "${APP_ROOT}/share/legal/gosha-privacy-policy.html" <<'EOF'
<html><body><h1>Гоша</h1><p>Privacy policy</p><p>max.corp.org@yandex.ru</p></body></html>
EOF
fi
if [[ ! -f "${APP_ROOT}/share/legal/gosha-terms-of-use.html" ]]; then
  cat > "${APP_ROOT}/share/legal/gosha-terms-of-use.html" <<'EOF'
<html><body><h1>Условия пользования</h1><p>Гоша</p><p>max.corp.org@yandex.ru</p></body></html>
EOF
fi
if [[ ! -f "${APP_ROOT}/mobile/onboarding_codes.json" ]]; then
  printf '{\n  "codes": []\n}\n' > "${APP_ROOT}/mobile/onboarding_codes.json"
fi
if [[ ! -f "${APP_ROOT}/mobile/panel_client_tokens.json" ]]; then
  printf '{}\n' > "${APP_ROOT}/mobile/panel_client_tokens.json"
fi

install -m 755 "${APP_DIR}/platform/add_robot.sh" "${APP_ROOT}/bin/add_robot.sh"

install -m 644 "${APP_DIR}/ops/systemd/gosha-backend.service" /etc/systemd/system/gosha-backend.service
install -m 644 "${APP_DIR}/ops/systemd/gosha-panel.service" /etc/systemd/system/gosha-panel.service
install -m 644 "${APP_DIR}/ops/systemd/gosha-observer.service" /etc/systemd/system/gosha-observer.service
install -m 644 "${APP_DIR}/ops/systemd/gosha-observer.timer" /etc/systemd/system/gosha-observer.timer

systemctl daemon-reload
systemctl enable --now gosha-backend.service
systemctl enable --now gosha-panel.service
systemctl enable --now gosha-observer.timer
systemctl start gosha-observer.service || true

echo "GOSHA server install complete."
echo "Panel: ${PANEL_URL}"
echo "WebSocket backend: ${WS_URL}"
echo "Operator password file: ${PANEL_PASSWORD_FILE}"
