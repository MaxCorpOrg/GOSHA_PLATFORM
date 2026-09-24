#!/usr/bin/env bash
set -euo pipefail

studio_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
venv_dir="${GOSHA_MOTION_STUDIO_BRIDGE_VENV:-$studio_dir/local_only/usb_bridge_venv}"
requirements="$studio_dir/requirements-bridge.txt"
python_bin="$venv_dir/bin/python"
stamp="$venv_dir/.requirements-bridge.stamp"

if [[ ! -x "$python_bin" ]]; then
  python3 -m venv "$venv_dir"
fi

if ! "$python_bin" -m pip --version >/dev/null 2>&1; then
  if "$python_bin" -m ensurepip --upgrade >&2; then
    :
  elif python3 -m pip --version >/dev/null 2>&1; then
    python3 -m pip --python "$python_bin" install --upgrade pip >&2
  else
    echo 'В project venv нет pip, а локальный python3 -m pip недоступен.' >&2
    exit 1
  fi
fi

if [[ ! -f "$stamp" || "$requirements" -nt "$stamp" ]]; then
  "$python_bin" -m pip install --upgrade pip >&2
  "$python_bin" -m pip install -r "$requirements" >&2
  touch "$stamp"
fi

printf '%s\n' "$python_bin"
