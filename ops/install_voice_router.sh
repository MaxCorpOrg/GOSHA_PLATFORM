#!/usr/bin/env bash
set -euo pipefail

# Install/stage only. Promotion of the existing public voice port is a separate,
# reviewable runtime change described in docs/GOSHA_GPT_LIVE_RU.md.
if [[ "${1:-}" != "--stage" || $# != 1 ]]; then
  echo 'Usage: bash ops/install_voice_router.sh --stage' >&2
  exit 2
fi
if [[ $(id -u) != 0 ]]; then
  echo 'Запустите установку от root на сервере GOSHA.' >&2
  exit 1
fi
VOICE_APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$VOICE_APP_DIR" != /opt/gosha_platform/app ]]; then
  echo 'Сначала разместите проверенный исходный код в /opt/gosha_platform/app.' >&2
  exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
python3 -c 'import ctypes.util; assert ctypes.util.find_library("opus"), "Install libopus0 first"'
test -d /opt/gosha_platform/runtime/app_root
test -d /opt/gosha_platform/runtime/env
python3 -m venv /opt/gosha_platform/voice-venv
/opt/gosha_platform/voice-venv/bin/python -m pip install 'websockets>=11,<16'
if [[ ! -f /opt/gosha_platform/runtime/env/voice-router.env ]]; then
  install -m 600 "$VOICE_APP_DIR/backend/voice-router.env.example" /opt/gosha_platform/runtime/env/voice-router.env
fi
install -m 644 "$VOICE_APP_DIR/ops/systemd/gosha-voice-router.service" /etc/systemd/system/gosha-voice-router.service
systemctl daemon-reload
systemctl enable gosha-voice-router.service
systemctl restart gosha-voice-router.service
systemctl is-active --quiet gosha-voice-router.service
/opt/gosha_platform/voice-venv/bin/python - <<'PY'
import json, sys, time, urllib.request
from pathlib import Path
sys.path.insert(0, '/opt/gosha_platform/app/platform')
from selfhost_xiaozhi_common import load_env
config = load_env(Path('/opt/gosha_platform/runtime/env/voice-router.env'))
port = int(config.get('GOSHA_VOICE_PORT', '18084'))
for attempt in range(10):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=1) as response:
            result = json.load(response)
        if result.get('ok') and result.get('service') == 'gosha-voice-router':
            break
    except Exception:
        pass
    time.sleep(0.5)
else:
    raise SystemExit('Голосовая служба не прошла локальную проверку /healthz.')
PY
echo 'Голосовой сервер установлен, локальная проверка /healthz прошла. Перед переключением публичного маршрута проверьте OpenAI API.'
