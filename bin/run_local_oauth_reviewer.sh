#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${OAUTH_REVIEWER_HOST:-127.0.0.1}"
PORT="${OAUTH_REVIEWER_PORT:-18910}"
SITE_PACKAGES_DIR="${ROOT}/local_only/oauth_reviewer_site"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Использование:
  bash bin/run_local_oauth_reviewer.sh

Перед запуском ожидаются env-переменные из:
  oauth_reviewer/.env.example

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

exec python3 -m uvicorn oauth_reviewer.app:app --host "$HOST" --port "$PORT"
