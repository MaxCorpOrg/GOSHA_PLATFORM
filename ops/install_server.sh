#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

PHASE="all"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --phase)
      shift
      [[ $# -gt 0 ]] || { echo "Missing value for --phase" >&2; exit 1; }
      PHASE="$1"
      ;;
    --phase=*)
      PHASE="${1#*=}"
      ;;
    -h|--help)
      cat <<'EOF'
Usage: bash ops/install_server.sh [--phase panel|backend|all]

  --phase panel    install and start panel, agent gateway, observer
  --phase backend  install and start only compatible backend
  --phase all      full install, start all services
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
  shift
done

case "${PHASE}" in
  panel|backend|all) ;;
  *)
    echo "Unsupported phase: ${PHASE}" >&2
    exit 1
    ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${GOSHA_INSTALL_ROOT:-/opt/gosha_platform}"
APP_DIR="${INSTALL_ROOT}/app"
RUNTIME_ROOT="${INSTALL_ROOT}/runtime"
APP_ROOT="${RUNTIME_ROOT}/app_root"
ENV_ROOT="${RUNTIME_ROOT}/env"
REPORTS_ROOT="${RUNTIME_ROOT}/reports"
PUBLIC_HOST="${GOSHA_PUBLIC_HOST:-}"
PANEL_PORT="${GOSHA_PANEL_PORT:-18876}"
WS_PORT="${GOSHA_WS_PORT:-18080}"
HTTP_PORT="${GOSHA_HTTP_PORT:-18083}"
WEB_PORT="${GOSHA_WEB_PORT:-18082}"
AGENT_GATEWAY_PORT="${GOSHA_AGENT_GATEWAY_PORT:-18110}"
strip_trailing_slash() {
  local value="$1"
  printf '%s' "${value%/}"
}
ensure_trailing_slash() {
  local value="$1"
  if [[ -z "${value}" || "${value}" == */ ]]; then
    printf '%s' "${value}"
  else
    printf '%s/' "${value}"
  fi
}
PANEL_URL="$(strip_trailing_slash "${GOSHA_PUBLIC_PANEL_URL:-${PUBLIC_PANEL_URL:-}}")"
if [[ -z "${PANEL_URL}" && -n "${PUBLIC_HOST}" ]]; then
  PANEL_URL="http://${PUBLIC_HOST}:${PANEL_PORT}"
fi
SELFHOST_PUBLIC_HTTP_BASE="$(strip_trailing_slash "${SELFHOST_XIAOZHI_PUBLIC_HTTP_BASE:-${PANEL_URL}}")"
GOSHA_OTA_URL="$(ensure_trailing_slash "${SELFHOST_GOSHA_OTA_URL:-${SELFHOST_XIAOZHI_OTA_URL:-}}")"
if [[ -z "${GOSHA_OTA_URL}" && -n "${SELFHOST_PUBLIC_HTTP_BASE}" ]]; then
  GOSHA_OTA_URL="${SELFHOST_PUBLIC_HTTP_BASE}/gosha/ota/"
fi
GOSHA_ACTIVATE_URL="$(strip_trailing_slash "${SELFHOST_GOSHA_ACTIVATE_URL:-${SELFHOST_XIAOZHI_ACTIVATE_URL:-}}")"
if [[ -z "${GOSHA_ACTIVATE_URL}" && -n "${GOSHA_OTA_URL}" ]]; then
  GOSHA_ACTIVATE_URL="$(strip_trailing_slash "${GOSHA_OTA_URL}")/activate"
fi
WS_URL="$(ensure_trailing_slash "${SELFHOST_GOSHA_WS_URL:-${SELFHOST_XIAOZHI_WS_URL:-}}")"
if [[ -z "${WS_URL}" && -n "${PUBLIC_HOST}" ]]; then
  WS_URL="ws://${PUBLIC_HOST}:${WS_PORT}/xiaozhi/v1/"
fi
MCP_BASE="$(ensure_trailing_slash "${SELFHOST_XIAOZHI_MCP_ENDPOINT_BASE:-}")"
if [[ -z "${MCP_BASE}" && -n "${PUBLIC_HOST}" ]]; then
  MCP_BASE="ws://${PUBLIC_HOST}:${WS_PORT}/mcp/"
fi
PUBLIC_EDGE_HUB_URL_VALUE="${GOSHA_PUBLIC_EDGE_HUB_URL:-${PUBLIC_EDGE_HUB_URL:-}}"
PANEL_PASSWORD_FILE="${ENV_ROOT}/panel.password"
DB_PASSWORD_FILE="${ENV_ROOT}/selfhost-db.password"
INTERNAL_PROXY_TOKEN_FILE="${ENV_ROOT}/internal-openai-proxy.token"
PROVIDERS_ENV_FILE="${ENV_ROOT}/providers.env"
BACKEND_STORAGE_ROOT="${APP_ROOT}/selfhost_xiaozhi/backend"
ASR_PROVIDER_KEY="${SELFHOST_XIAOZHI_ASR_PROVIDER:-VoskASR}"
case "$(printf '%s' "${ASR_PROVIDER_KEY}" | tr '[:upper:]' '[:lower:]')" in
  funasr)
    ASR_PROVIDER_KEY="FunASR"
    ;;
  vosk|voskasr|"")
    ASR_PROVIDER_KEY="VoskASR"
    ;;
  *)
    echo "Unsupported ASR provider: ${ASR_PROVIDER_KEY}" >&2
    exit 1
    ;;
esac
SENSEVOICE_MODEL_DIR="${BACKEND_STORAGE_ROOT}/models/SenseVoiceSmall"
SENSEVOICE_MODEL_FILE="${SENSEVOICE_MODEL_DIR}/model.pt"
SENSEVOICE_MODEL_URL="${SELFHOST_XIAOZHI_SENSEVOICE_URL:-https://modelscope.cn/models/iic/SenseVoiceSmall/resolve/master/model.pt}"
VOSK_MODEL_NAME="${SELFHOST_XIAOZHI_VOSK_MODEL_NAME:-vosk-model-small-ru-0.22}"
VOSK_MODELS_ROOT="${BACKEND_STORAGE_ROOT}/models/vosk"
VOSK_MODEL_DIR="${VOSK_MODELS_ROOT}/${VOSK_MODEL_NAME}"
VOSK_MODEL_ARCHIVE="${BACKEND_STORAGE_ROOT}/models/${VOSK_MODEL_NAME}.zip"
VOSK_MODEL_URL="${SELFHOST_XIAOZHI_VOSK_MODEL_URL:-https://alphacephei.com/vosk/models/${VOSK_MODEL_NAME}.zip}"
VOSK_CONTAINER_MODEL_PATH="/opt/xiaozhi-esp32-server/models/vosk/${VOSK_MODEL_NAME}"
AUDIO_SAMPLE_RATE="${SELFHOST_XIAOZHI_AUDIO_SAMPLE_RATE:-16000}"
SILERO_MODELS_ROOT="${BACKEND_STORAGE_ROOT}/models/silero"
SILERO_MODEL_ID="${SELFHOST_XIAOZHI_SILERO_MODEL_ID:-v5_5_ru}"
SILERO_DEFAULT_SPEAKER="${SELFHOST_XIAOZHI_SILERO_DEFAULT_SPEAKER:-xenia}"
SILERO_SAMPLE_RATE="${SELFHOST_XIAOZHI_SILERO_SAMPLE_RATE:-24000}"
SILERO_DEVICE="${SELFHOST_XIAOZHI_SILERO_DEVICE:-cpu}"
SILERO_CACHE_DIR="${SELFHOST_XIAOZHI_SILERO_CACHE_DIR:-/opt/xiaozhi-esp32-server/models/silero}"
BACKEND_CONFIG_FILE="${BACKEND_STORAGE_ROOT}/data/.config.yaml"

ensure_env_key() {
  local file="$1"
  local key="$2"
  local value="$3"
  [[ -f "${file}" ]] || touch "${file}"
  if grep -E "^${key}=" "${file}" >/dev/null 2>&1; then
    return 0
  fi
  printf '%s=%s\n' "${key}" "${value}" >> "${file}"
}

mkdir -p \
  "${INSTALL_ROOT}" \
  "${APP_ROOT}/robots" \
  "${APP_ROOT}/memory" \
  "${APP_ROOT}/mobile" \
  "${APP_ROOT}/edge" \
  "${APP_ROOT}/agents/profiles" \
  "${APP_ROOT}/agents/bindings" \
  "${APP_ROOT}/share/legal" \
  "${APP_ROOT}/shared/kb" \
  "${APP_ROOT}/bin" \
  "${BACKEND_STORAGE_ROOT}/data" \
  "${SENSEVOICE_MODEL_DIR}" \
  "${VOSK_MODELS_ROOT}" \
  "${SILERO_MODELS_ROOT}" \
  "${BACKEND_STORAGE_ROOT}/mysql" \
  "${BACKEND_STORAGE_ROOT}/redis" \
  "${BACKEND_STORAGE_ROOT}/uploadfile" \
  "${ENV_ROOT}" \
  "${REPORTS_ROOT}"

if [[ "$(readlink -f "${REPO_ROOT}")" != "${APP_DIR}" ]]; then
  rm -rf "${APP_DIR}"
  mkdir -p "${APP_DIR}"
  (
    cd "${REPO_ROOT}"
    tar --exclude='./local_only' -cf - .
  ) | (
    cd "${APP_DIR}"
    tar -xf -
  )
fi

python3 - <<'PY' "${PANEL_PASSWORD_FILE}" "${DB_PASSWORD_FILE}" "${INTERNAL_PROXY_TOKEN_FILE}"
import secrets
import sys
from pathlib import Path

for path_str, size in ((sys.argv[1], 20), (sys.argv[2], 24), (sys.argv[3], 28)):
    path = Path(path_str)
    if not path.exists():
        path.write_text(secrets.token_urlsafe(size) + "\n", encoding="utf-8")
        path.chmod(0o600)
PY

DB_PASSWORD="$(tr -d '\r\n' < "${DB_PASSWORD_FILE}")"
INTERNAL_PROXY_TOKEN="$(tr -d '\r\n' < "${INTERNAL_PROXY_TOKEN_FILE}")"

DEFAULT_PROVIDER_PROFILE_ID="$(
  python3 - <<'PY' "${APP_ROOT}"
import json
import sys
from pathlib import Path

app_root = Path(sys.argv[1])
profiles_dir = app_root / "agents" / "profiles"
bindings_dir = app_root / "agents" / "bindings"

def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

profiles = {}
for path in sorted(profiles_dir.glob("*.json")):
    data = read_json(path)
    profile_id = str(data.get("profile_id", "") or path.stem).strip()
    if profile_id:
        profiles[profile_id] = data

for path in sorted(bindings_dir.glob("*.json")):
    binding = read_json(path)
    for key in ("active_profile_id", "fallback_profile_id"):
        profile_id = str(binding.get(key, "") or "").strip()
        if profile_id and profile_id in profiles:
            print(profile_id)
            raise SystemExit(0)

for profile_id, data in profiles.items():
    if data.get("enabled", True):
        print(profile_id)
        raise SystemExit(0)

print("")
PY
)"

if [[ ! -f "${ENV_ROOT}/panel.env" ]]; then
  cat > "${ENV_ROOT}/panel.env" <<EOF
APP_ROOT=${APP_ROOT}
PANEL_HOST=0.0.0.0
PANEL_PORT=${PANEL_PORT}
PUBLIC_PANEL_URL=${PANEL_URL}
# Optional future operator edge hub. Keep empty during the presence-only triangle stage.
PUBLIC_EDGE_HUB_URL=${PUBLIC_EDGE_HUB_URL_VALUE}
PANEL_OPERATOR_USER=operator
PANEL_OPERATOR_PASSWORD_FILE=${PANEL_PASSWORD_FILE}
PANEL_SESSION_TTL_SECONDS=43200
GOSHA_AGENT_GATEWAY_URL=http://127.0.0.1:${AGENT_GATEWAY_PORT}
GOSHA_AGENT_GATEWAY_TIMEOUT_SECONDS=5
GOSHA_INTERNAL_OPENAI_PROXY_TOKEN_FILE=${INTERNAL_PROXY_TOKEN_FILE}
GOSHA_BACKEND_PROXY_PROFILE_ID=${DEFAULT_PROVIDER_PROFILE_ID}
SELFHOST_XIAOZHI_PUBLIC_HTTP_BASE=${SELFHOST_PUBLIC_HTTP_BASE}
SELFHOST_GOSHA_OTA_URL=${GOSHA_OTA_URL}
SELFHOST_GOSHA_ACTIVATE_URL=${GOSHA_ACTIVATE_URL}
SELFHOST_XIAOZHI_OTA_URL=${GOSHA_OTA_URL}
SELFHOST_XIAOZHI_ACTIVATE_URL=${GOSHA_ACTIVATE_URL}
SELFHOST_XIAOZHI_WS_URL=${WS_URL}
SELFHOST_XIAOZHI_MCP_ENDPOINT_BASE=${MCP_BASE}
APK_SHARE_PATH=${APP_ROOT}/share/maxcorp-connector-debug.apk
ADMIN_APK_SHARE_PATH=${APP_ROOT}/share/maxcorp-admin-connector-debug.apk
PRIVACY_POLICY_SHARE_PATH=${APP_ROOT}/share/legal/gosha-privacy-policy.html
TERMS_OF_USE_SHARE_PATH=${APP_ROOT}/share/legal/gosha-terms-of-use.html
EOF
fi

if [[ ! -f "${ENV_ROOT}/selfhost-backend.env" ]]; then
  cat > "${ENV_ROOT}/selfhost-backend.env" <<EOF
SELFHOST_XIAOZHI_TZ=Europe/Moscow
SELFHOST_XIAOZHI_WS_BIND=0.0.0.0
SELFHOST_XIAOZHI_WS_PORT=${WS_PORT}
SELFHOST_XIAOZHI_HTTP_BIND=127.0.0.1
SELFHOST_XIAOZHI_HTTP_PORT=${HTTP_PORT}
SELFHOST_XIAOZHI_WEB_BIND=127.0.0.1
SELFHOST_XIAOZHI_WEB_PORT=${WEB_PORT}
SELFHOST_XIAOZHI_DB_USER=root
SELFHOST_XIAOZHI_DB_PASSWORD=${DB_PASSWORD}
SELFHOST_XIAOZHI_REDIS_PASSWORD=
SELFHOST_XIAOZHI_ASR_PROVIDER=${ASR_PROVIDER_KEY}
SELFHOST_XIAOZHI_VOSK_MODEL_NAME=${VOSK_MODEL_NAME}
SELFHOST_XIAOZHI_AUDIO_SAMPLE_RATE=${AUDIO_SAMPLE_RATE}
SELFHOST_XIAOZHI_SILERO_MODEL_ID=${SILERO_MODEL_ID}
SELFHOST_XIAOZHI_SILERO_DEFAULT_SPEAKER=${SILERO_DEFAULT_SPEAKER}
SELFHOST_XIAOZHI_SILERO_SAMPLE_RATE=${SILERO_SAMPLE_RATE}
SELFHOST_XIAOZHI_SILERO_DEVICE=${SILERO_DEVICE}
SELFHOST_XIAOZHI_SILERO_CACHE_DIR=${SILERO_CACHE_DIR}
SELFHOST_XIAOZHI_STORAGE_ROOT=${BACKEND_STORAGE_ROOT}
EOF
fi

if [[ ! -f "${ENV_ROOT}/agent-gateway.env" ]]; then
  cat > "${ENV_ROOT}/agent-gateway.env" <<EOF
APP_ROOT=${APP_ROOT}
GOSHA_AGENT_GATEWAY_HOST=127.0.0.1
GOSHA_AGENT_GATEWAY_PORT=${AGENT_GATEWAY_PORT}
GOSHA_AGENT_GATEWAY_TIMEOUT_SECONDS=45
GOSHA_AGENT_GATEWAY_DEFAULT_PROFILE_ID=${DEFAULT_PROVIDER_PROFILE_ID}
EOF
fi

if [[ ! -f "${PROVIDERS_ENV_FILE}" ]]; then
  cat > "${PROVIDERS_ENV_FILE}" <<'EOF'
# Переменные окружения для профилей ИИ-провайдеров.
# Заполняйте значения справа от знака "=".
# OPENAI_API_KEY=
# DEEPSEEK_API_KEY=
EOF
fi

ensure_env_key "${ENV_ROOT}/panel.env" "APP_ROOT" "${APP_ROOT}"
ensure_env_key "${ENV_ROOT}/panel.env" "PANEL_HOST" "0.0.0.0"
ensure_env_key "${ENV_ROOT}/panel.env" "PANEL_PORT" "${PANEL_PORT}"
ensure_env_key "${ENV_ROOT}/panel.env" "PUBLIC_PANEL_URL" "${PANEL_URL}"
ensure_env_key "${ENV_ROOT}/panel.env" "PUBLIC_EDGE_HUB_URL" "${PUBLIC_EDGE_HUB_URL_VALUE}"
ensure_env_key "${ENV_ROOT}/panel.env" "PANEL_OPERATOR_USER" "operator"
ensure_env_key "${ENV_ROOT}/panel.env" "PANEL_OPERATOR_PASSWORD_FILE" "${PANEL_PASSWORD_FILE}"
ensure_env_key "${ENV_ROOT}/panel.env" "PANEL_SESSION_TTL_SECONDS" "43200"
ensure_env_key "${ENV_ROOT}/panel.env" "GOSHA_AGENT_GATEWAY_URL" "http://127.0.0.1:${AGENT_GATEWAY_PORT}"
ensure_env_key "${ENV_ROOT}/panel.env" "GOSHA_AGENT_GATEWAY_TIMEOUT_SECONDS" "5"
ensure_env_key "${ENV_ROOT}/panel.env" "GOSHA_INTERNAL_OPENAI_PROXY_TOKEN_FILE" "${INTERNAL_PROXY_TOKEN_FILE}"
ensure_env_key "${ENV_ROOT}/panel.env" "GOSHA_BACKEND_PROXY_PROFILE_ID" "${DEFAULT_PROVIDER_PROFILE_ID}"
ensure_env_key "${ENV_ROOT}/panel.env" "SELFHOST_XIAOZHI_PUBLIC_HTTP_BASE" "${SELFHOST_PUBLIC_HTTP_BASE}"
ensure_env_key "${ENV_ROOT}/panel.env" "SELFHOST_GOSHA_OTA_URL" "${GOSHA_OTA_URL}"
ensure_env_key "${ENV_ROOT}/panel.env" "SELFHOST_GOSHA_ACTIVATE_URL" "${GOSHA_ACTIVATE_URL}"
ensure_env_key "${ENV_ROOT}/panel.env" "SELFHOST_XIAOZHI_OTA_URL" "${GOSHA_OTA_URL}"
ensure_env_key "${ENV_ROOT}/panel.env" "SELFHOST_XIAOZHI_ACTIVATE_URL" "${GOSHA_ACTIVATE_URL}"
ensure_env_key "${ENV_ROOT}/panel.env" "SELFHOST_XIAOZHI_WS_URL" "${WS_URL}"
ensure_env_key "${ENV_ROOT}/panel.env" "SELFHOST_XIAOZHI_MCP_ENDPOINT_BASE" "${MCP_BASE}"
ensure_env_key "${ENV_ROOT}/panel.env" "APK_SHARE_PATH" "${APP_ROOT}/share/maxcorp-connector-debug.apk"
ensure_env_key "${ENV_ROOT}/panel.env" "ADMIN_APK_SHARE_PATH" "${APP_ROOT}/share/maxcorp-admin-connector-debug.apk"
ensure_env_key "${ENV_ROOT}/panel.env" "PRIVACY_POLICY_SHARE_PATH" "${APP_ROOT}/share/legal/gosha-privacy-policy.html"
ensure_env_key "${ENV_ROOT}/panel.env" "TERMS_OF_USE_SHARE_PATH" "${APP_ROOT}/share/legal/gosha-terms-of-use.html"

ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_TZ" "Europe/Moscow"
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_WS_BIND" "0.0.0.0"
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_WS_PORT" "${WS_PORT}"
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_HTTP_BIND" "127.0.0.1"
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_HTTP_PORT" "${HTTP_PORT}"
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_WEB_BIND" "127.0.0.1"
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_WEB_PORT" "${WEB_PORT}"
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_DB_USER" "root"
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_DB_PASSWORD" "${DB_PASSWORD}"
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_REDIS_PASSWORD" ""
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_ASR_PROVIDER" "${ASR_PROVIDER_KEY}"
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_ASR_LANGUAGE" "ru"
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_VOSK_MODEL_NAME" "${VOSK_MODEL_NAME}"
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_AUDIO_SAMPLE_RATE" "${AUDIO_SAMPLE_RATE}"
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_SILERO_MODEL_ID" "${SILERO_MODEL_ID}"
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_SILERO_DEFAULT_SPEAKER" "${SILERO_DEFAULT_SPEAKER}"
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_SILERO_SAMPLE_RATE" "${SILERO_SAMPLE_RATE}"
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_SILERO_DEVICE" "${SILERO_DEVICE}"
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_SILERO_CACHE_DIR" "${SILERO_CACHE_DIR}"
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_STORAGE_ROOT" "${BACKEND_STORAGE_ROOT}"

ensure_env_key "${ENV_ROOT}/agent-gateway.env" "APP_ROOT" "${APP_ROOT}"
ensure_env_key "${ENV_ROOT}/agent-gateway.env" "GOSHA_AGENT_GATEWAY_HOST" "127.0.0.1"
ensure_env_key "${ENV_ROOT}/agent-gateway.env" "GOSHA_AGENT_GATEWAY_PORT" "${AGENT_GATEWAY_PORT}"
ensure_env_key "${ENV_ROOT}/agent-gateway.env" "GOSHA_AGENT_GATEWAY_TIMEOUT_SECONDS" "45"
ensure_env_key "${ENV_ROOT}/agent-gateway.env" "GOSHA_AGENT_GATEWAY_DEFAULT_PROFILE_ID" "${DEFAULT_PROVIDER_PROFILE_ID}"

if [[ "${ASR_PROVIDER_KEY}" == "FunASR" && ! -s "${SENSEVOICE_MODEL_FILE}" ]]; then
  echo "Downloading SenseVoiceSmall model to ${SENSEVOICE_MODEL_FILE}"
  curl -fL --retry 3 --retry-delay 5 -C - -o "${SENSEVOICE_MODEL_FILE}" "${SENSEVOICE_MODEL_URL}"
fi

if [[ "${ASR_PROVIDER_KEY}" == "VoskASR" && ! -f "${VOSK_MODEL_DIR}/am/final.mdl" ]]; then
  echo "Downloading Vosk model ${VOSK_MODEL_NAME} to ${VOSK_MODEL_ARCHIVE}"
  curl -fL --retry 3 --retry-delay 5 -C - -o "${VOSK_MODEL_ARCHIVE}" "${VOSK_MODEL_URL}"
  python3 - <<'PY' "${VOSK_MODEL_ARCHIVE}" "${VOSK_MODELS_ROOT}" "${VOSK_MODEL_NAME}"
import sys
import zipfile
from pathlib import Path

archive_path = Path(sys.argv[1])
extract_root = Path(sys.argv[2])
model_name = sys.argv[3]
target_dir = extract_root / model_name

if target_dir.joinpath("am", "final.mdl").exists():
    raise SystemExit(0)

extract_root.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(archive_path) as zf:
    zf.extractall(extract_root)

if not target_dir.joinpath("am", "final.mdl").exists():
    raise SystemExit(f"Vosk model extraction failed: {target_dir}")
PY
fi

SILERO_MODEL_ID="${SILERO_MODEL_ID}" \
SILERO_DEFAULT_SPEAKER="${SILERO_DEFAULT_SPEAKER}" \
SILERO_SAMPLE_RATE="${SILERO_SAMPLE_RATE}" \
SILERO_DEVICE="${SILERO_DEVICE}" \
SILERO_CACHE_DIR="${SILERO_CACHE_DIR}" \
python3 - <<'PY' "${APP_ROOT}"
import json
import os
import sys
import time
from pathlib import Path

app_root = Path(sys.argv[1])
agents_root = app_root / "agents"
assistants_dir = agents_root / "assistants"
tts_engines_dir = agents_root / "tts_engines"
voices_dir = agents_root / "voices"
bindings_dir = agents_root / "bindings"
memory_dir = agents_root / "memory"
mcp_dir = agents_root / "mcp_bundles"
screens_dir = agents_root / "screens"
wake_dir = agents_root / "wake"

for path in (assistants_dir, tts_engines_dir, voices_dir, bindings_dir, memory_dir, mcp_dir, screens_dir, wake_dir):
    path.mkdir(parents=True, exist_ok=True)

now = int(time.time())
silero_model_id = os.environ.get("SILERO_MODEL_ID", "v5_5_ru")
silero_default_speaker = os.environ.get("SILERO_DEFAULT_SPEAKER", "xenia")
silero_sample_rate = int(os.environ.get("SILERO_SAMPLE_RATE", "24000") or "24000")
silero_device = os.environ.get("SILERO_DEVICE", "cpu")
silero_cache_dir = os.environ.get("SILERO_CACHE_DIR", "/opt/xiaozhi-esp32-server/models/silero")

def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def save_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

tts_engine_profiles = {
    "tts-engine-edge-default": {
        "profile_id": "tts-engine-edge-default",
        "display_name": "EdgeTTS основной",
        "engine_kind": "edge_tts",
        "module_name": "EdgeTTS",
        "provider_label": "Текущий живой контур Microsoft Edge TTS",
        "runtime_state": "ready",
        "supports_speech_rate": True,
        "supports_pitch": True,
        "enabled": True,
        "is_default": True,
        "config": {"output_dir": "tmp/"},
    },
    "tts-engine-silero-prep": {
        "profile_id": "tts-engine-silero-prep",
        "display_name": "Silero TTS подготовка",
        "engine_kind": "silero_tts",
        "module_name": "SileroTTS",
        "provider_label": "Подготовка архитектуры для нового русского TTS",
        "runtime_state": "planned",
        "supports_speech_rate": True,
        "supports_pitch": False,
        "enabled": False,
        "is_default": False,
        "config": {
            "model_id": silero_model_id,
            "speaker": silero_default_speaker,
            "sample_rate": silero_sample_rate,
            "device": silero_device,
            "cache_dir": silero_cache_dir,
            "language": "ru",
            "use_ssml": True,
            "put_accent": True,
            "put_yo": True,
            "num_threads": 2
        },
    },
}

for profile_id, desired in tts_engine_profiles.items():
    path = tts_engines_dir / f"{profile_id}.json"
    current = load_json(path, {})
    created_at = int(current.get("created_at", 0) or 0) or now
    payload = {**current, **desired, "created_at": created_at, "updated_at": now}
    save_json(path, payload)

voice_profiles = {
    "voice-ru-default": {
        "profile_id": "voice-ru-default",
        "display_name": "Русский голос: Светлана",
        "tts_engine_profile_id": "tts-engine-edge-default",
        "voice_name": "ru-RU-SvetlanaNeural",
        "provider_label": "Взрослый женский голос Microsoft Edge",
        "language": "ru-RU",
        "voice_type": "adult",
        "speech_rate": 1.0,
        "pitch": 1.0,
        "enabled": True,
    },
    "voice-ru-man": {
        "profile_id": "voice-ru-man",
        "display_name": "Русский голос: Дмитрий",
        "tts_engine_profile_id": "tts-engine-edge-default",
        "voice_name": "ru-RU-DmitryNeural",
        "provider_label": "Взрослый мужской голос Microsoft Edge",
        "language": "ru-RU",
        "voice_type": "adult",
        "speech_rate": 1.0,
        "pitch": 1.0,
        "enabled": True,
    },
    "voice-ru-kid-girl": {
        "profile_id": "voice-ru-kid-girl",
        "display_name": "Русский голос: девочка",
        "tts_engine_profile_id": "tts-engine-edge-default",
        "voice_name": "ru-RU-SvetlanaNeural",
        "provider_label": "Детский пресет на базе женского голоса Microsoft Edge",
        "language": "ru-RU",
        "voice_type": "child",
        "speech_rate": 1.12,
        "pitch": 1.28,
        "enabled": True,
    },
    "voice-ru-kid-boy": {
        "profile_id": "voice-ru-kid-boy",
        "display_name": "Русский голос: мальчик",
        "tts_engine_profile_id": "tts-engine-edge-default",
        "voice_name": "ru-RU-DmitryNeural",
        "provider_label": "Детский пресет на базе мужского голоса Microsoft Edge",
        "language": "ru-RU",
        "voice_type": "child",
        "speech_rate": 1.08,
        "pitch": 1.18,
        "enabled": True,
    },
}

for profile_id, desired in voice_profiles.items():
    path = voices_dir / f"{profile_id}.json"
    current = load_json(path, {})
    created_at = int(current.get("created_at", 0) or 0) or now
    payload = {**current, **desired, "created_at": created_at, "updated_at": now}
    save_json(path, payload)

assistant_path = assistants_dir / "assistant-gosha-default.json"
assistant = load_json(assistant_path, {})
assistant_created_at = int(assistant.get("created_at", 0) or 0) or now
assistant.update({
    "profile_id": "assistant-gosha-default",
    "display_name": "Гоша основной",
    "assistant_name": "Гоша",
    "role_template": assistant.get("role_template", "главный помощник") or "главный помощник",
    "role_description": assistant.get("role_description", "Основной голосовой ассистент платформы Гоша") or "Основной голосовой ассистент платформы Гоша",
    "system_prompt": "Ты — голосовой ассистент по имени Гоша. Всегда отвечай только по-русски, если оператор прямо не попросил другой язык. Никогда сам не переходи на китайский, японский или английский. Если фраза распознана неуверенно или выглядит искажённой, коротко попроси повторить по-русски. Отвечай дружелюбно, коротко и понятно.",
    "dialogue_language": "ru-RU",
    "voice_profile_id": assistant.get("voice_profile_id", "voice-ru-default") or "voice-ru-default",
    "memory_profile_id": assistant.get("memory_profile_id", "memory-short-default") or "memory-short-default",
    "mcp_bundle_id": assistant.get("mcp_bundle_id", "mcp-basic-default") or "mcp-basic-default",
    "enabled": True,
    "is_default": True,
    "created_at": assistant_created_at,
    "updated_at": now,
})
save_json(assistant_path, assistant)
PY

python3 "${APP_DIR}/ops/render_backend_config.py" \
  "${BACKEND_CONFIG_FILE}" \
  "${WS_URL}" \
  "${PANEL_PORT}" \
  "${INTERNAL_PROXY_TOKEN}" \
  "${APP_ROOT}" \
  "${ASR_PROVIDER_KEY}" \
  "${VOSK_CONTAINER_MODEL_PATH}" \
  "${AUDIO_SAMPLE_RATE}"

copy_if_missing() {
  local src="$1"
  local dst="$2"
  if [[ -f "${src}" && ! -f "${dst}" ]]; then
    mkdir -p "$(dirname "${dst}")"
    cp "${src}" "${dst}"
  fi
}

if [[ -d /opt/ai_robot/robots ]] && [[ -z "$(find "${APP_ROOT}/robots" -mindepth 1 -maxdepth 1 2>/dev/null | head -n 1)" ]]; then
  cp -a /opt/ai_robot/robots/. "${APP_ROOT}/robots/"
fi

copy_if_missing /opt/ai_robot/mobile/onboarding_codes.json "${APP_ROOT}/mobile/onboarding_codes.json"
copy_if_missing /opt/ai_robot/mobile/panel_client_tokens.json "${APP_ROOT}/mobile/panel_client_tokens.json"
copy_if_missing /opt/ai_robot/share/maxcorp-connector-debug.apk "${APP_ROOT}/share/maxcorp-connector-debug.apk"
copy_if_missing /opt/ai_robot/share/maxcorp-admin-connector-debug.apk "${APP_ROOT}/share/maxcorp-admin-connector-debug.apk"
copy_if_missing /opt/ai_robot/share/legal/gosha-privacy-policy.html "${APP_ROOT}/share/legal/gosha-privacy-policy.html"
copy_if_missing /opt/ai_robot/share/legal/gosha-terms-of-use.html "${APP_ROOT}/share/legal/gosha-terms-of-use.html"

if [[ ! -f "${APP_ROOT}/share/maxcorp-connector-debug.apk" ]]; then
  dd if=/dev/zero of="${APP_ROOT}/share/maxcorp-connector-debug.apk" bs=1024 count=128 status=none
fi
if [[ ! -f "${APP_ROOT}/share/maxcorp-admin-connector-debug.apk" ]]; then
  dd if=/dev/zero of="${APP_ROOT}/share/maxcorp-admin-connector-debug.apk" bs=1024 count=128 status=none
fi
if [[ ! -f "${APP_ROOT}/share/legal/gosha-privacy-policy.html" ]]; then
  cat > "${APP_ROOT}/share/legal/gosha-privacy-policy.html" <<'EOF'
<html><body><h1>Гоша</h1><p>Privacy policy</p><p>max.corp.org@yandex.ru</p></body></html>
EOF
fi
if [[ ! -f "${APP_ROOT}/share/legal/gosha-terms-of-use.html" ]]; then
  cat > "${APP_ROOT}/share/legal/gosha-terms-of-use.html" <<'EOF'
<html><body><h1>Условия пользования</h1><p>Гоша</p><p>max.corp.org@yandex.ru</p></body></html>
EOF
fi
if [[ ! -f "${APP_ROOT}/mobile/onboarding_codes.json" ]]; then
  printf '{\n  "codes": []\n}\n' > "${APP_ROOT}/mobile/onboarding_codes.json"
fi
if [[ ! -f "${APP_ROOT}/mobile/panel_client_tokens.json" ]]; then
  printf '{}\n' > "${APP_ROOT}/mobile/panel_client_tokens.json"
fi

install -m 755 "${APP_DIR}/platform/add_robot.sh" "${APP_ROOT}/bin/add_robot.sh"
bash "${APP_DIR}/bin/ensure_panel_python_deps.sh"

install -m 644 "${APP_DIR}/ops/systemd/gosha-backend.service" /etc/systemd/system/gosha-backend.service
install -m 644 "${APP_DIR}/ops/systemd/gosha-agent-gateway.service" /etc/systemd/system/gosha-agent-gateway.service
install -m 644 "${APP_DIR}/ops/systemd/gosha-panel.service" /etc/systemd/system/gosha-panel.service
install -m 644 "${APP_DIR}/ops/systemd/gosha-observer.service" /etc/systemd/system/gosha-observer.service
install -m 644 "${APP_DIR}/ops/systemd/gosha-observer.timer" /etc/systemd/system/gosha-observer.timer

systemctl daemon-reload

if [[ "${PHASE}" == "panel" ]]; then
  systemctl enable gosha-agent-gateway.service
  systemctl restart gosha-agent-gateway.service
  systemctl enable gosha-panel.service
  systemctl restart gosha-panel.service
  systemctl enable --now gosha-observer.timer
  systemctl start gosha-observer.service || true
elif [[ "${PHASE}" == "backend" ]]; then
  systemctl enable gosha-backend.service
  systemctl restart gosha-backend.service
else
  systemctl enable gosha-backend.service
  systemctl restart gosha-backend.service
  systemctl enable gosha-agent-gateway.service
  systemctl restart gosha-agent-gateway.service
  systemctl enable gosha-panel.service
  systemctl restart gosha-panel.service
  systemctl enable --now gosha-observer.timer
  systemctl start gosha-observer.service || true
fi

echo "GOSHA server install complete. phase=${PHASE}"
echo "Panel URL: configured in ${ENV_ROOT}/panel.env as PUBLIC_PANEL_URL"
echo "WebSocket backend URL: configured in ${ENV_ROOT}/panel.env as SELFHOST_XIAOZHI_WS_URL"
echo "Operator password file: ${PANEL_PASSWORD_FILE}"
