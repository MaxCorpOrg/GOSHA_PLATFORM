#!/usr/bin/env bash
set -euo pipefail
studio_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
physics_python="${GOSHA_PHYSICS_PYTHON:-${studio_dir}/local_only/physics-venv/bin/python}"
if [[ ! -x "$physics_python" ]]; then
  echo 'Не найдено окружение физики. Установите requirements-physics.txt в отдельное окружение и задайте GOSHA_PHYSICS_PYTHON.' >&2
  exit 1
fi
if [[ ! -f "$studio_dir/dist/index.html" ]]; then
  echo 'Сначала выполните npm run build в Motion Studio.' >&2
  exit 1
fi
cd "$studio_dir"
exec "$physics_python" -m bridge.physics_server --simulation-only "$@"
