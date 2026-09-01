#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_ROOT="$ROOT/local_only/runtime_lab/app_root"

mkdir -p \
  "$APP_ROOT/share/legal" \
  "$APP_ROOT/share" \
  "$APP_ROOT/robots" \
  "$APP_ROOT/memory" \
  "$APP_ROOT/mobile" \
  "$APP_ROOT/shared/kb" \
  "$APP_ROOT/bin" \
  "$APP_ROOT/edge" \
  "$APP_ROOT/agents/profiles" \
  "$APP_ROOT/agents/bindings" \
  "$APP_ROOT/selfhost_xiaozhi/backend/data" \
  "$APP_ROOT/selfhost_xiaozhi/backend/models" \
  "$APP_ROOT/selfhost_xiaozhi/backend/mysql" \
  "$APP_ROOT/selfhost_xiaozhi/backend/redis" \
  "$APP_ROOT/selfhost_xiaozhi/backend/uploadfile"

if [[ ! -f "$APP_ROOT/share/legal/gosha-privacy-policy.html" ]]; then
  cat > "$APP_ROOT/share/legal/gosha-privacy-policy.html" <<'EOF'
<html><body><h1>Гоша</h1><p>Privacy policy</p><p>max.corp.org@yandex.ru</p></body></html>
EOF
fi

if [[ ! -f "$APP_ROOT/share/legal/gosha-terms-of-use.html" ]]; then
  cat > "$APP_ROOT/share/legal/gosha-terms-of-use.html" <<'EOF'
<html><body><h1>Условия пользования</h1><p>Гоша</p><p>max.corp.org@yandex.ru</p></body></html>
EOF
fi

if [[ ! -f "$APP_ROOT/share/maxcorp-connector-debug.apk" ]]; then
  dd if=/dev/zero of="$APP_ROOT/share/maxcorp-connector-debug.apk" bs=1024 count=128 status=none
fi

if [[ ! -f "$APP_ROOT/share/maxcorp-admin-connector-debug.apk" ]]; then
  dd if=/dev/zero of="$APP_ROOT/share/maxcorp-admin-connector-debug.apk" bs=1024 count=128 status=none
fi

if [[ ! -f "$APP_ROOT/mobile/onboarding_codes.json" ]]; then
  cat > "$APP_ROOT/mobile/onboarding_codes.json" <<'EOF'
{
  "codes": []
}
EOF
fi

if [[ ! -f "$APP_ROOT/mobile/panel_client_tokens.json" ]]; then
  cat > "$APP_ROOT/mobile/panel_client_tokens.json" <<'EOF'
{}
EOF
fi

cp "$ROOT/platform/add_robot.sh" "$APP_ROOT/bin/add_robot.sh"
chmod 755 "$APP_ROOT/bin/add_robot.sh"

if [[ ! -d "$APP_ROOT/robots/gosha-local" ]]; then
  APP_ROOT="$APP_ROOT" "$APP_ROOT/bin/add_robot.sh" "gosha-local"
fi

if [[ ! -d "$APP_ROOT/robots/gosha-main" ]]; then
  APP_ROOT="$APP_ROOT" "$APP_ROOT/bin/add_robot.sh" "gosha-main"
fi

normalize_selfhost_robot_env() {
  local robot_id="$1"
  local env_path="$APP_ROOT/robots/$robot_id/robot.env"
  [[ -f "$env_path" ]] || return 0
  python3 - "$env_path" "$robot_id" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
robot_id = sys.argv[2]
lines = env_path.read_text(encoding="utf-8", errors="ignore").splitlines()
updates = {
    "ROBOT_ID": robot_id,
    "ROBOT_NAME": robot_id,
    "ROBOT_RUNTIME_CLASS": "runtime",
    "ROBOT_BACKEND_MODE": "self_hosted_xiaozhi",
    "ROBOT_CONTROL_TRANSPORT": "cloud-mcp",
}
seen = set()
out = []
for raw in lines:
    stripped = raw.strip()
    if not stripped or stripped.startswith("#") or "=" not in raw:
        out.append(raw)
        continue
    key, _ = raw.split("=", 1)
    key = key.strip()
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(raw)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
env_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
PY
}

normalize_selfhost_robot_env "gosha-local"
normalize_selfhost_robot_env "gosha-main"

echo "Local lab ready: $APP_ROOT"
