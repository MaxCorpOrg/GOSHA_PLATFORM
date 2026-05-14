#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/max/GOSHA_PLATFORM"
APP_ROOT="$ROOT/local_only/runtime_lab/app_root"

export APP_ROOT
export PANEL_HOST="127.0.0.1"
export PANEL_PORT="18876"
export PUBLIC_PANEL_URL="http://127.0.0.1:18876"
export PUBLIC_EDGE_HUB_URL="ws://127.0.0.1:18890"
export GOSHA_AGENT_GATEWAY_URL="http://127.0.0.1:18110"
export GOSHA_AGENT_GATEWAY_TIMEOUT_SECONDS="5"
export SELFHOST_XIAOZHI_PUBLIC_HTTP_BASE="http://127.0.0.1:18876"
export SELFHOST_XIAOZHI_OTA_URL="http://127.0.0.1:18876/xiaozhi/ota/"
export SELFHOST_XIAOZHI_ACTIVATE_URL="http://127.0.0.1:18876/xiaozhi/ota/activate"
export SELFHOST_XIAOZHI_WS_URL="ws://127.0.0.1:18876/xiaozhi/v1/"
export SELFHOST_XIAOZHI_MCP_ENDPOINT_BASE="ws://127.0.0.1:18876/mcp/"
export APK_SHARE_PATH="$APP_ROOT/share/maxcorp-connector-debug.apk"
export ADMIN_APK_SHARE_PATH="$APP_ROOT/share/maxcorp-admin-connector-debug.apk"
export PRIVACY_POLICY_SHARE_PATH="$APP_ROOT/share/legal/gosha-privacy-policy.html"
export TERMS_OF_USE_SHARE_PATH="$APP_ROOT/share/legal/gosha-terms-of-use.html"

bash "$ROOT/bin/init_local_lab.sh"
cd "$ROOT/platform"
python3 gui_panel.py
