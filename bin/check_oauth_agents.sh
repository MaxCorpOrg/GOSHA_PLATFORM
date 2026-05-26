#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REVIEWER_ENV="${ROOT}/local_only/oauth_reviewer.env"
EXECUTOR_ENV="${ROOT}/local_only/oauth_executor.env"

print_section() {
  printf '\n== %s ==\n' "$1"
}

print_section "Git"
git -C "${ROOT}" status --short --branch
upstream="$(git -C "${ROOT}" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
if [[ -n "${upstream}" ]]; then
  counts="$(git -C "${ROOT}" rev-list --left-right --count "${upstream}...HEAD" 2>/dev/null || true)"
  if [[ -n "${counts}" ]]; then
    behind="$(awk '{print $1}' <<<"${counts}")"
    ahead="$(awk '{print $2}' <<<"${counts}")"
    printf 'Отставание от upstream: behind=%s, ahead=%s\n' "${behind}" "${ahead}"
  fi
else
  printf 'Upstream для текущей ветки не настроен.\n'
fi

print_section "Службы"
for unit in gosha-oauth-reviewer.service gosha-oauth-executor.service gosha-oauth-executor-tunnel.service; do
  state="$(systemctl --user is-active "${unit}" 2>/dev/null || true)"
  enabled="$(systemctl --user is-enabled "${unit}" 2>/dev/null || true)"
  printf '%s | active=%s | enabled=%s\n' "${unit}" "${state:-unknown}" "${enabled:-unknown}"
done

print_section "Healthz"
for url in http://127.0.0.1:18910/healthz http://127.0.0.1:18912/healthz; do
  printf '%s\n' "${url}"
  curl -fsS "${url}" || printf '{"ok":false,"error":"endpoint unavailable"}'
  printf '\n'
done

print_section "Конфигурация Codex"
python3 - <<'PY' "${REVIEWER_ENV}" "${EXECUTOR_ENV}" "${HOME}/.codex/config.toml"
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import tomllib
except Exception:  # pragma: no cover
    tomllib = None


def parse_env(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        data[key.strip()] = value
    return data


def load_global_config(path: Path) -> dict[str, str]:
    if not path.exists() or tomllib is None:
        return {}
    payload = tomllib.loads(path.read_text(encoding="utf-8", errors="ignore"))
    return {
        "model": str(payload.get("model", "") or "").strip(),
        "model_reasoning_effort": str(payload.get("model_reasoning_effort", "") or "").strip(),
    }


reviewer_env = parse_env(Path(sys.argv[1]))
executor_env = parse_env(Path(sys.argv[2]))
global_config = load_global_config(Path(sys.argv[3]))

def line(prefix: str, env: dict[str, str], model_key: str, effort_key: str, profile_key: str, terminal_key: str) -> None:
    model = env.get(model_key) or global_config.get("model") or "не задано"
    effort = env.get(effort_key) or global_config.get("model_reasoning_effort") or "не задано"
    profile = env.get(profile_key) or "не задан"
    terminal = env.get(terminal_key) or "false"
    print(f"{prefix}: модель={model} | уровень={effort} | профиль={profile} | терминал={terminal}")


line("reviewer", reviewer_env, "OAUTH_REVIEWER_CODEX_MODEL", "OAUTH_REVIEWER_CODEX_REASONING_EFFORT", "OAUTH_REVIEWER_CODEX_PROFILE", "OAUTH_REVIEWER_OPEN_TERMINAL")
line("executor", executor_env, "OAUTH_EXECUTOR_CODEX_MODEL", "OAUTH_EXECUTOR_CODEX_REASONING_EFFORT", "OAUTH_EXECUTOR_CODEX_PROFILE", "OAUTH_EXECUTOR_OPEN_TERMINAL")
print(f"global ~/.codex/config.toml: модель={global_config.get('model') or 'не задано'} | уровень={global_config.get('model_reasoning_effort') or 'не задано'}")
PY

print_section "Локальные данные"
python3 - <<'PY' "${ROOT}/local_only"
from __future__ import annotations

import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
targets = [
    ("reviewer_sessions", root / "oauth_reviewer_sessions"),
    ("executor_sessions", root / "oauth_executor_sessions"),
    ("reviewer_terminals", root / "oauth_reviewer_terminals"),
    ("executor_terminals", root / "oauth_executor_terminals"),
]

for label, path in targets:
    if not path.exists():
        print(f"{label}: каталог отсутствует")
        continue
    file_count = sum(1 for _ in path.rglob("*") if _.is_file())
    dir_count = sum(1 for _ in path.iterdir() if _.is_dir())
    size_bytes = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    print(f"{label}: files={file_count} dirs={dir_count} size={size_bytes} bytes")
PY
