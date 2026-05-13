#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/max/GOSHA_PLATFORM"
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

if [[ ! -f "$APP_ROOT/bin/add_robot.sh" ]]; then
  cp "$ROOT/platform/add_robot.sh" "$APP_ROOT/bin/add_robot.sh"
  chmod 755 "$APP_ROOT/bin/add_robot.sh"
fi

if [[ ! -d "$APP_ROOT/robots/gosha-local" ]]; then
  APP_ROOT="$APP_ROOT" "$APP_ROOT/bin/add_robot.sh" "gosha-local"
fi

echo "Local lab ready: $APP_ROOT"
