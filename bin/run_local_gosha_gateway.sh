#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/max/GOSHA_PLATFORM"
APP_ROOT="$ROOT/local_only/runtime_lab/app_root"

export APP_ROOT
export GOSHA_AGENT_GATEWAY_HOST="127.0.0.1"
export GOSHA_AGENT_GATEWAY_PORT="18110"
export GOSHA_AGENT_GATEWAY_TIMEOUT_SECONDS="45"

bash "$ROOT/bin/init_local_lab.sh"
cd "$ROOT/platform"
python3 gosha_agent_gateway.py
