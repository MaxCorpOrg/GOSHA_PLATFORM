#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/opt/gosha_platform/runtime/app_root}"
ROBOT_ID="${1:-}"

if [[ -z "$ROBOT_ID" ]]; then
  echo "Usage: $0 <robot-id>" >&2
  exit 1
fi

if [[ ! "$ROBOT_ID" =~ ^[a-zA-Z0-9._-]+$ ]]; then
  echo "Invalid robot-id. Allowed: a-z A-Z 0-9 . _ -" >&2
  exit 1
fi

ROBOT_DIR="$APP_ROOT/robots/$ROBOT_ID"
MEMORY_DIR="$APP_ROOT/memory/$ROBOT_ID/clients/default"
KB_DIR="$APP_ROOT/shared/kb/robots/$ROBOT_ID"

mkdir -p "$ROBOT_DIR" "$MEMORY_DIR" "$KB_DIR"

if [[ ! -f "$ROBOT_DIR/profile.md" ]]; then
  cat > "$ROBOT_DIR/profile.md" <<EOF
# Robot Profile: $ROBOT_ID

## Role
Self-hosted staging robot for GOSHA platform.

## Behavior
- Коротко и по делу.
- Не выдумывать факты.
- Если данных не хватает, сначала уточнить.
EOF
fi

if [[ ! -f "$ROBOT_DIR/robot.env" ]]; then
  cat > "$ROBOT_DIR/robot.env" <<EOF
ROBOT_ID=$ROBOT_ID
ROBOT_NAME=$ROBOT_ID
ROBOT_RUNTIME_CLASS=runtime
ROBOT_BACKEND_MODE=xiaozhi_cloud
ROBOT_CONTROL_TRANSPORT=cloud-mcp
ROBOT_DEVICE_WS_URL=
ROBOT_DEVICE_IP=
ROBOT_DEVICE_PORT=8080
ROBOT_DEVICE_WS_PATH=/ws
EOF
fi

if [[ ! -f "$ROBOT_DIR/mcp_endpoint.txt" ]]; then
  cat > "$ROBOT_DIR/mcp_endpoint.txt" <<'EOF'
ws://127.0.0.1:18080/mcp/?token=REPLACE_WITH_ROBOT_TOKEN
EOF
fi

if [[ ! -f "$ROBOT_DIR/mcp_config.json" ]]; then
  cat > "$ROBOT_DIR/mcp_config.json" <<'EOF'
{
  "mcpServers": {}
}
EOF
fi

if [[ ! -f "$MEMORY_DIR/events.jsonl" ]]; then
  : > "$MEMORY_DIR/events.jsonl"
fi
if [[ ! -f "$MEMORY_DIR/notes.md" ]]; then
  printf "# Client notes\n\n" > "$MEMORY_DIR/notes.md"
fi
if [[ ! -f "$MEMORY_DIR/prefs.json" ]]; then
  printf "{}\n" > "$MEMORY_DIR/prefs.json"
fi
if [[ ! -f "$MEMORY_DIR/contacts.json" ]]; then
  printf "[]\n" > "$MEMORY_DIR/contacts.json"
fi

chmod 600 "$ROBOT_DIR/robot.env" "$ROBOT_DIR/mcp_endpoint.txt" "$ROBOT_DIR/mcp_config.json"
echo "Robot created: $ROBOT_ID"

