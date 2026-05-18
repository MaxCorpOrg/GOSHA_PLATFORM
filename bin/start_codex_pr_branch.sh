#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

usage() {
  cat <<'EOF'
Использование:
  bash bin/start_codex_pr_branch.sh <краткое-имя-задачи> [желаемая-базовая-ветка]

Пример:
  bash bin/start_codex_pr_branch.sh simplify-robot-page main

Скрипт:
  - проверяет чистоту рабочей копии;
  - пытается взять указанную базовую ветку;
  - если её нет на origin, берёт текущую HEAD-ветку origin;
  - создаёт локальную рабочую ветку вида codex/<задача>-YYYYMMDD-HHMMSS.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || $# -lt 1 ]]; then
  usage
  exit 0
fi

TASK_RAW="$1"
REQUESTED_BASE="${2:-main}"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Рабочая копия не чистая. Сначала закоммить или убери незавершённые изменения." >&2
  exit 1
fi

sanitize_slug() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//; s/-{2,}/-/g'
}

TASK_SLUG="$(sanitize_slug "$TASK_RAW")"
if [[ -z "$TASK_SLUG" ]]; then
  echo "Не удалось собрать безопасное короткое имя ветки из: $TASK_RAW" >&2
  exit 1
fi

has_remote_branch() {
  git ls-remote --exit-code --heads origin "$1" >/dev/null 2>&1
}

REMOTE_HEAD="$(git remote show origin | sed -n 's/.*HEAD branch: //p' | head -n 1)"
BASE_BRANCH="$REQUESTED_BASE"

if ! has_remote_branch "$BASE_BRANCH"; then
  if [[ -z "$REMOTE_HEAD" ]]; then
    echo "На origin не найдена ветка '$BASE_BRANCH', и не удалось определить HEAD-ветку origin." >&2
    exit 1
  fi
  BASE_BRANCH="$REMOTE_HEAD"
  echo "Ветка '$REQUESTED_BASE' на origin не найдена. Использую текущую базовую ветку origin: $BASE_BRANCH"
fi

git fetch origin "$BASE_BRANCH" >/dev/null

STAMP="$(date +%Y%m%d-%H%M%S)"
NEW_BRANCH="codex/${TASK_SLUG}-${STAMP}"

if git show-ref --verify --quiet "refs/heads/$NEW_BRANCH"; then
  echo "Локальная ветка уже существует: $NEW_BRANCH" >&2
  exit 1
fi

git checkout -b "$NEW_BRANCH" "origin/$BASE_BRANCH" >/dev/null

cat <<EOF
Создана рабочая ветка:
  $NEW_BRANCH

Базовая ветка:
  $BASE_BRANCH

Дальше обычно так:
  git push -u origin $NEW_BRANCH
  открыть Draft PR в $BASE_BRANCH
  дождаться проверки repo-validation
  если автоматический review Codex не включён, оставить комментарий: @codex review
  если включён PR-Agent, дождаться его замечаний и ответить правками
EOF
