#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[1/4] Проверка shell-скриптов"
mapfile -t SHELL_FILES < <(find bin ops platform -maxdepth 1 -type f -name '*.sh' | sort)
if ((${#SHELL_FILES[@]} > 0)); then
  for file in "${SHELL_FILES[@]}"; do
    bash -n "$file"
  done
fi

echo "[2/4] Проверка Python-синтаксиса"
mapfile -t PYTHON_FILES < <((
  find platform ops -maxdepth 1 -type f -name '*.py'
  find oauth_shared -type f -name '*.py'
  find oauth_reviewer -type f -name '*.py'
  find oauth_executor -type f -name '*.py'
) | sort)
if ((${#PYTHON_FILES[@]} > 0)); then
  python3 -m py_compile "${PYTHON_FILES[@]}"
fi

echo "[3/4] Проверка базовой структуры репозитория"
python3 - <<'PY'
from pathlib import Path
import sys

required_paths = [
    "AGENTS.md",
    "README_RU.md",
    "START_HERE_FOR_NEW_CHAT.md",
    "docs/PROJECT_STATUS_RU.md",
    "docs/AGENT_CHECKPOINT_RU.md",
    "docs/NEW_CHAT_CHECKPOINT_RU.md",
    "docs/GOSHA_OAUTH_REVIEWER_RUNBOOK_RU.md",
    "docs/GOSHA_OAUTH_EXECUTOR_RUNBOOK_RU.md",
    "platform/gui_panel.py",
    "platform/panel_index.html",
    "oauth_reviewer/app.py",
    "oauth_reviewer/static/index.html",
    "oauth_executor/app.py",
    "oauth_executor/static/index.html",
]
missing = [path for path in required_paths if not Path(path).exists()]
if missing:
    print("Не хватает обязательных файлов:", ", ".join(missing), file=sys.stderr)
    raise SystemExit(1)

panel_html = Path("platform/panel_index.html").read_text(encoding="utf-8", errors="ignore").lower()
if "<!doctype html>" not in panel_html:
    print("В platform/panel_index.html пропал doctype HTML.", file=sys.stderr)
    raise SystemExit(1)
PY

echo "[4/4] Проверка вспомогательных CLI"
python3 platform/check_gosha_mobile_contract.py --help >/dev/null
bash bin/start_codex_pr_branch.sh --help >/dev/null
bash bin/run_local_oauth_reviewer.sh --help >/dev/null 2>&1 || true
bash bin/run_local_oauth_executor.sh --help >/dev/null 2>&1 || true
bash bin/run_oauth_executor_reverse_tunnel.sh --help >/dev/null 2>&1 || true

echo "Проверка репозитория завершена успешно."
