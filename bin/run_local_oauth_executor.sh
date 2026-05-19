#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${OAUTH_EXECUTOR_HOST:-127.0.0.1}"
PORT="${OAUTH_EXECUTOR_PORT:-18912}"
SITE_PACKAGES_DIR="${ROOT}/local_only/oauth_executor_site"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Использование:
  bash bin/run_local_oauth_executor.sh

Перед запуском ожидаются env-переменные из:
  oauth_executor/.env.example

По умолчанию сервис поднимается на:
  http://${HOST}:${PORT}
EOF
  exit 0
fi

cd "$ROOT"
if [[ -d "${SITE_PACKAGES_DIR}" ]]; then
  export PYTHONPATH="${SITE_PACKAGES_DIR}:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
else
  export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
fi

exec python3 -m uvicorn oauth_executor.app:app --host "$HOST" --port "$PORT"
