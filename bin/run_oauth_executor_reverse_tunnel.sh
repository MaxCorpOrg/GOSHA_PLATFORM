#!/usr/bin/env bash
set -euo pipefail

SSH_HOST="${OAUTH_EXECUTOR_REVERSE_SSH_HOST:-maxcorp-server}"
REMOTE_BIND_HOST="${OAUTH_EXECUTOR_REVERSE_BIND_HOST:-127.0.0.1}"
REMOTE_PORT="${OAUTH_EXECUTOR_REVERSE_REMOTE_PORT:-18919}"
LOCAL_HOST="${OAUTH_EXECUTOR_REVERSE_LOCAL_HOST:-127.0.0.1}"
LOCAL_PORT="${OAUTH_EXECUTOR_REVERSE_LOCAL_PORT:-${OAUTH_EXECUTOR_PORT:-18912}}"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Использование:
  bash bin/run_oauth_executor_reverse_tunnel.sh

Скрипт поднимает постоянный обратный SSH-туннель:
  ${REMOTE_BIND_HOST}:${REMOTE_PORT} на сервере -> ${LOCAL_HOST}:${LOCAL_PORT} на этой машине

По умолчанию используется SSH-host:
  ${SSH_HOST}
EOF
  exit 0
fi

exec ssh \
  -N \
  -T \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -R "${REMOTE_BIND_HOST}:${REMOTE_PORT}:${LOCAL_HOST}:${LOCAL_PORT}" \
  "${SSH_HOST}"
