#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REQ_FILE="${ROOT}/platform/requirements.txt"

if python3 - <<'PY' >/dev/null 2>&1
from websockets.sync.client import connect  # noqa: F401
PY
then
  exit 0
fi

if [[ ! -f "${REQ_FILE}" ]]; then
  echo "Не найден файл зависимостей панели: ${REQ_FILE}" >&2
  exit 1
fi

if [[ "$(id -u)" -eq 0 ]]; then
  python3 -m pip install --break-system-packages -r "${REQ_FILE}"
else
  python3 -m pip install --user --break-system-packages -r "${REQ_FILE}"
fi
