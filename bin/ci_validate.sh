#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail() {
  printf 'Ошибка: %s\n' "$*" >&2
  exit 1
}

require_file() {
  local path="$1"
  [[ -f "$path" ]] || fail "не найден обязательный файл: $path"
}

echo "[1/5] Проверка ключевых файлов"
required_paths=(
  ".github/workflows/repo-validation.yml"
  "bin/ci_validate.sh"
  "AGENTS.md"
  "README_RU.md"
  "START_HERE_FOR_NEW_CHAT.md"
  "docs/GOSHA_PROJECT_MAP_RU.md"
  "docs/PROJECT_STATUS_RU.md"
  "docs/AGENT_CHECKPOINT_RU.md"
  "docs/NEW_CHAT_CHECKPOINT_RU.md"
  "platform/gui_panel.py"
  "platform/panel_index.html"
  "platform/test_edge_hub_transport_diagnostics.py"
  "platform/check_gosha_mobile_contract.py"
  "ops/install_server.sh"
)
for path in "${required_paths[@]}"; do
  require_file "$path"
done

python3 - <<'PY'
from pathlib import Path
import sys

panel_html = Path("platform/panel_index.html").read_text(encoding="utf-8", errors="ignore").lower()
if "<!doctype html>" not in panel_html:
    print("Ошибка: в platform/panel_index.html не найден doctype HTML.", file=sys.stderr)
    raise SystemExit(1)
PY

echo "[2/5] Проверка синтаксиса shell-файлов"
mapfile -d '' tracked_files < <(git ls-files -z -- bin ops platform | sort -z)
shell_files=()
python_files=()
for path in "${tracked_files[@]}"; do
  case "$path" in
    *.sh)
      shell_files+=("$path")
      ;;
    platform/*.py|platform/**/*.py|ops/*.py|ops/**/*.py)
      python_files+=("$path")
      ;;
  esac
done

if ((${#shell_files[@]} == 0)); then
  fail "не найдены отслеживаемые shell-файлы в bin/ops/platform"
fi

for path in "${shell_files[@]}"; do
  bash -n "$path"
done

echo "[3/5] Проверка Python AST для platform/ops"
if ((${#python_files[@]} == 0)); then
  fail "не найдены отслеживаемые Python-файлы в platform/ops"
fi

PYTHONDONTWRITEBYTECODE=1 python3 -B - "${python_files[@]}" <<'PY'
import ast
from pathlib import Path
import sys

failed = False
for filename in sys.argv[1:]:
    path = Path(filename)
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=filename)
    except SyntaxError as exc:
        failed = True
        print(f"{filename}:{exc.lineno}:{exc.offset}: {exc.msg}", file=sys.stderr)
    except UnicodeDecodeError as exc:
        failed = True
        print(f"{filename}: ошибка чтения UTF-8: {exc}", file=sys.stderr)

if failed:
    raise SystemExit(1)
PY

echo "[4/5] Проверка диагностики edge-hub"
PYTHONDONTWRITEBYTECODE=1 python3 -B platform/test_edge_hub_transport_diagnostics.py

echo "[5/5] Проверка CLI контракта mobile API"
python3 platform/check_gosha_mobile_contract.py --help >/dev/null

echo "Проверка репозитория завершена успешно."
