#!/usr/bin/env bash
set -euo pipefail
studio_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$studio_dir"
mode="${1:-foreground}"
unit=gosha-motion-studio.service
case "$mode" in
  foreground|--background|--status|--stop) ;;
  *) echo 'Использование: bash start.sh [--background|--status|--stop]' >&2; exit 2 ;;
esac
if [[ $# -gt 1 ]]; then
  echo 'Ожидается не больше одного параметра.' >&2
  exit 2
fi
if [[ "$mode" != foreground ]]; then
  command -v systemctl >/dev/null && command -v systemd-run >/dev/null || {
    echo 'Фоновый режим требует Linux с пользовательским systemd.' >&2; exit 1;
  }
  service_dir="$(systemctl --user show "$unit" -p WorkingDirectory --value)"
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
if ! command -v npm >/dev/null 2>&1; then
  echo 'Нужны Node.js 22.12+ и npm.' >&2
  exit 1
fi
if [[ ! -f local_only/public/models/gosha.glb ]]; then
  echo 'Сначала подготовьте модель командой из README.md.' >&2
  exit 1
fi
if [[ ! -d node_modules ]]; then npm ci --no-audit --no-fund; fi
npm run build
if [[ "$mode" == --background ]]; then
  node_path="$(command -v node)"
  "$node_path" --input-type=module <<'JS'
import net from 'node:net';
const probe = net.createServer();
probe.once('error', () => {
  console.error('Порт 5176 уже занят или недоступен. Остановите прежний сервер редактора; фоновая служба не создана.');
  process.exitCode = 1;
});
probe.listen(5176, '127.0.0.1', () => probe.close());
JS
  exec systemd-run --user --collect --unit="$unit" \
    --description='Gosha Motion Studio local editor' \
    --property="WorkingDirectory=$studio_dir" \
    --property=Restart=on-failure --property=RestartSec=3 \
    "$node_path" "$studio_dir/node_modules/vite/bin/vite.js" preview \
    --host 127.0.0.1 --port 5176 --strictPort
fi
exec npm run preview
