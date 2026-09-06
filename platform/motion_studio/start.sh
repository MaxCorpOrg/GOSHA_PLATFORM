#!/usr/bin/env bash
set -euo pipefail
studio_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$studio_dir"
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
exec npm run preview
