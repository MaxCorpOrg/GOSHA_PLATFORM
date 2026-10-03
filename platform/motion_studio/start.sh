#!/usr/bin/env bash
set -euo pipefail
studio_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$studio_dir"
mode="${1:-foreground}"
unit=gosha-motion-studio.service
panel_port=5176
bridge_port=5177
bridge_origin="http://127.0.0.1:${panel_port}"
venv_dir="${GOSHA_MOTION_STUDIO_BRIDGE_VENV:-$studio_dir/local_only/usb_bridge_venv}"
case "$mode" in
  foreground|--background|--status|--stop|--serve) ;;
  *) echo 'Использование: bash start.sh [--background|--status|--stop]' >&2; exit 2 ;;
esac
if [[ $# -gt 1 ]]; then
  echo 'Ожидается не больше одного параметра.' >&2
  exit 2
fi
if [[ "$mode" != foreground && "$mode" != --serve ]]; then
  command -v systemctl >/dev/null && command -v systemd-run >/dev/null || {
    echo 'Фоновый режим требует Linux с пользовательским systemd.' >&2; exit 1;
  }
  service_dir="$(systemctl --user show "$unit" -p WorkingDirectory --value 2>/dev/null || true)"
  if [[ -n "$service_dir" && "$service_dir" != "$studio_dir" ]]; then
    echo 'Служба редактора принадлежит другой рабочей папке; она не изменена.' >&2
    exit 1
  fi
  if [[ "$mode" == --status ]]; then
    exec systemctl --user status "$unit" --no-pager
  elif [[ "$mode" == --stop ]]; then
    exec systemctl --user stop "$unit"
  elif systemctl --user is-active --quiet "$unit"; then
    echo 'Редактор уже работает: http://127.0.0.1:5176/'
    exit 0
  fi
  if systemctl --user is-failed --quiet "$unit"; then
    systemctl --user reset-failed "$unit"
  fi
fi
if [[ "$mode" != --serve ]] && ! command -v npm >/dev/null 2>&1; then
  echo 'Нужны Node.js 22.12+ и npm.' >&2
  exit 1
fi
if [[ ! -s "$studio_dir/local_only/public/models/gosha.glb" ]]; then
  echo 'Полная модель Гоши отсутствует. Подготовьте её из исходных STL перед запуском:' >&2
  echo 'python3 scripts/prepare_model.py --source-dir "<папка Robot>" --output local_only/public/models/gosha.glb' >&2
  exit 1
fi
if [[ "$mode" == --serve ]]; then
  if [[ ! -s dist/index.html || ! -s dist/models/gosha.glb || ! -x "$venv_dir/bin/python" ]]; then
    echo 'Служба не подготовлена. Сначала запустите bash start.sh или bash start.sh --background.' >&2
    exit 1
  fi
  python_bin="$venv_dir/bin/python"
else
  if [[ ! -d node_modules ]]; then npm ci --no-audit --no-fund; fi
  npm run build
  python_bin="$(bash "$studio_dir/scripts/ensure_bridge_env.sh")"
fi
probe_ports() {
  "$python_bin" - "$@" <<'PY'
import socket
import sys
for value in sys.argv[1:]:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(('127.0.0.1', int(value)))
        except OSError:
            sys.exit(f'Порт {value} уже занят или недоступен. Остановите прежний сервер редактора; служба не создана.')
PY
}
run_servers() {
  PYTHONPATH="$studio_dir" "$python_bin" -m bridge.usb_bridge \
    --host 127.0.0.1 --port "$bridge_port" --origin "$bridge_origin" &
  bridge_pid=$!
  PYTHONPATH="$studio_dir" "$python_bin" -m bridge.studio_server \
    --dist "$studio_dir/dist" --port "$panel_port" &
  preview_pid=$!
  cleanup() {
    kill "$preview_pid" "$bridge_pid" 2>/dev/null || true
    wait "$preview_pid" "$bridge_pid" 2>/dev/null || true
  }
  trap cleanup EXIT INT TERM
  set +e
  wait -n "$preview_pid" "$bridge_pid"
  status=$?
  set -e
  cleanup
  exit "$status"
}
if [[ "$mode" == --background ]]; then
  probe_ports "$panel_port" "$bridge_port"
  exec systemd-run --user --collect --unit="$unit" \
    --description='Gosha Motion Studio local editor' \
    --property="WorkingDirectory=$studio_dir" \
    --property=Restart=on-failure --property=RestartSec=3 \
    bash "$studio_dir/start.sh" --serve
fi
probe_ports "$panel_port" "$bridge_port"
run_servers
