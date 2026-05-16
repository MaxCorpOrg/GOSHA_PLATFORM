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
PUBLIC_HOST="${GOSHA_PUBLIC_HOST:-151.241.228.232}"
PANEL_PORT="${GOSHA_PANEL_PORT:-18876}"
WS_PORT="${GOSHA_WS_PORT:-18080}"
HTTP_PORT="${GOSHA_HTTP_PORT:-18083}"
WEB_PORT="${GOSHA_WEB_PORT:-18082}"
AGENT_GATEWAY_PORT="${GOSHA_AGENT_GATEWAY_PORT:-18110}"
PANEL_URL="http://${PUBLIC_HOST}:${PANEL_PORT}"
WS_URL="ws://${PUBLIC_HOST}:${WS_PORT}/xiaozhi/v1/"
MCP_BASE="ws://${PUBLIC_HOST}:${WS_PORT}/mcp/"
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
PUBLIC_EDGE_HUB_URL=ws://${PUBLIC_HOST}:18080/mcp
PANEL_OPERATOR_USER=operator
PANEL_OPERATOR_PASSWORD_FILE=${PANEL_PASSWORD_FILE}
PANEL_SESSION_TTL_SECONDS=43200
GOSHA_AGENT_GATEWAY_URL=http://127.0.0.1:${AGENT_GATEWAY_PORT}
GOSHA_AGENT_GATEWAY_TIMEOUT_SECONDS=5
GOSHA_INTERNAL_OPENAI_PROXY_TOKEN_FILE=${INTERNAL_PROXY_TOKEN_FILE}
GOSHA_BACKEND_PROXY_PROFILE_ID=${DEFAULT_PROVIDER_PROFILE_ID}
SELFHOST_XIAOZHI_PUBLIC_HTTP_BASE=${PANEL_URL}
SELFHOST_GOSHA_OTA_URL=${PANEL_URL}/gosha/ota/
SELFHOST_GOSHA_ACTIVATE_URL=${PANEL_URL}/gosha/ota/activate
SELFHOST_XIAOZHI_OTA_URL=${PANEL_URL}/gosha/ota/
SELFHOST_XIAOZHI_ACTIVATE_URL=${PANEL_URL}/gosha/ota/activate
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
ensure_env_key "${ENV_ROOT}/panel.env" "PUBLIC_EDGE_HUB_URL" "ws://${PUBLIC_HOST}:18080/mcp"
ensure_env_key "${ENV_ROOT}/panel.env" "PANEL_OPERATOR_USER" "operator"
ensure_env_key "${ENV_ROOT}/panel.env" "PANEL_OPERATOR_PASSWORD_FILE" "${PANEL_PASSWORD_FILE}"
ensure_env_key "${ENV_ROOT}/panel.env" "PANEL_SESSION_TTL_SECONDS" "43200"
ensure_env_key "${ENV_ROOT}/panel.env" "GOSHA_AGENT_GATEWAY_URL" "http://127.0.0.1:${AGENT_GATEWAY_PORT}"
ensure_env_key "${ENV_ROOT}/panel.env" "GOSHA_AGENT_GATEWAY_TIMEOUT_SECONDS" "5"
ensure_env_key "${ENV_ROOT}/panel.env" "GOSHA_INTERNAL_OPENAI_PROXY_TOKEN_FILE" "${INTERNAL_PROXY_TOKEN_FILE}"
ensure_env_key "${ENV_ROOT}/panel.env" "GOSHA_BACKEND_PROXY_PROFILE_ID" "${DEFAULT_PROVIDER_PROFILE_ID}"
ensure_env_key "${ENV_ROOT}/panel.env" "SELFHOST_XIAOZHI_PUBLIC_HTTP_BASE" "${PANEL_URL}"
ensure_env_key "${ENV_ROOT}/panel.env" "SELFHOST_GOSHA_OTA_URL" "${PANEL_URL}/gosha/ota/"
ensure_env_key "${ENV_ROOT}/panel.env" "SELFHOST_GOSHA_ACTIVATE_URL" "${PANEL_URL}/gosha/ota/activate"
ensure_env_key "${ENV_ROOT}/panel.env" "SELFHOST_XIAOZHI_OTA_URL" "${PANEL_URL}/gosha/ota/"
ensure_env_key "${ENV_ROOT}/panel.env" "SELFHOST_XIAOZHI_ACTIVATE_URL" "${PANEL_URL}/gosha/ota/activate"
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
        "config": {"sample_rate": 24000, "speaker": "xenia"},
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

python3 - <<'PY' "${BACKEND_CONFIG_FILE}" "${WS_URL}" "${PANEL_PORT}" "${INTERNAL_PROXY_TOKEN}" "${APP_ROOT}" "${ASR_PROVIDER_KEY}" "${VOSK_CONTAINER_MODEL_PATH}"
import sys
import json
from pathlib import Path

config_path = Path(sys.argv[1])
ws_url = sys.argv[2]
panel_port = sys.argv[3]
proxy_token = sys.argv[4]
app_root = Path(sys.argv[5])
asr_provider_key = sys.argv[6]
vosk_model_path = sys.argv[7]

existing = ""
if config_path.exists():
    existing = config_path.read_text(encoding="utf-8", errors="ignore").strip()

managed_markers = ("managed-by-gosha", "GoshaProxyLLM:")
if existing not in ("", "{}", "null") and not any(marker in existing for marker in managed_markers):
    raise SystemExit(0)

default_model_name = "deepseek-v4-flash"
profile_id = ""
profile_payload = {}

panel_env_path = app_root.parent / "env" / "panel.env"
if panel_env_path.exists():
    try:
        for line in panel_env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("GOSHA_BACKEND_PROXY_PROFILE_ID="):
                profile_id = line.split("=", 1)[1].strip()
                break
    except Exception:
        profile_id = ""

def normalize_model(base_url, model_name):
    base = str(base_url or "").strip().lower()
    model = str(model_name or "").strip()
    if "api.deepseek.com" in base and model in ("", "deepseek-chat", "deepseek-reasoner", "gosha-assistant"):
        return default_model_name
    return model or default_model_name

if profile_id:
    profile_path = app_root / "agents" / "profiles" / f"{profile_id}.json"
    try:
        profile_payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception:
        profile_payload = {}

model_name = normalize_model(profile_payload.get("base_url", ""), profile_payload.get("model", ""))

def clamp_float(value, default, min_value, max_value):
    try:
        result = float(value)
    except Exception:
        result = float(default)
    if result < min_value:
        return min_value
    if result > max_value:
        return max_value
    return result

def rate_to_edge(rate_multiplier):
    percent = round((rate_multiplier - 1.0) * 100)
    return f"{percent:+d}%"

def pitch_to_edge(pitch_multiplier):
    hz = round((pitch_multiplier - 1.0) * 50)
    return f"{hz:+d}Hz"

assistants_dir = app_root / "agents" / "assistants"
tts_engines_dir = app_root / "agents" / "tts_engines"
bindings_dir = app_root / "agents" / "bindings"
voices_dir = app_root / "agents" / "voices"

def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

default_assistant_payload = {}
for path in sorted(assistants_dir.glob("*.json")):
    data = load_json(path)
    if data.get("enabled", True) and data.get("is_default"):
        default_assistant_payload = data
        break

voice_profile_id = str(default_assistant_payload.get("voice_profile_id", "") or "").strip()
if not voice_profile_id:
    for path in sorted(bindings_dir.glob("*.json")):
        data = load_json(path)
        candidate = str(data.get("voice_profile_id", "") or "").strip()
        if candidate:
            voice_profile_id = candidate
            break

voice_payload = load_json(voices_dir / f"{voice_profile_id}.json") if voice_profile_id else {}
tts_engine_profile_id = str(voice_payload.get("tts_engine_profile_id", "") or "").strip() or "tts-engine-edge-default"
tts_engine_payload = load_json(tts_engines_dir / f"{tts_engine_profile_id}.json") if tts_engine_profile_id else {}
tts_engine_kind = str(tts_engine_payload.get("engine_kind", "") or "").strip() or "edge_tts"
tts_engine_module = str(tts_engine_payload.get("module_name", "") or "").strip() or "EdgeTTS"
tts_engine_runtime_state = str(tts_engine_payload.get("runtime_state", "") or "").strip() or "ready"
if tts_engine_kind != "edge_tts" or tts_engine_runtime_state != "ready" or not bool(tts_engine_payload.get("enabled", True)):
    tts_engine_kind = "edge_tts"
    tts_engine_module = "EdgeTTS"
tts_voice_name = str(voice_payload.get("voice_name", "") or "").strip() or "ru-RU-SvetlanaNeural"
tts_speech_rate = clamp_float(voice_payload.get("speech_rate", 1.0), 1.0, 0.5, 2.0)
tts_pitch = clamp_float(voice_payload.get("pitch", 1.0), 1.0, 0.5, 2.0)
tts_rate = rate_to_edge(tts_speech_rate)
tts_pitch_hz = pitch_to_edge(tts_pitch)
prompt_lines = [
    "Ты — голосовой ассистент по имени Гоша.",
    "Всегда отвечай только по-русски, если оператор прямо не попросил другой язык.",
    "Никогда сам не переходи на китайский, японский или английский.",
    "Если фраза распознана неуверенно или выглядит искажённой, коротко попроси повторить по-русски.",
    "Отвечай доброжелательно, короткими понятными фразами.",
]
custom_prompt = str(default_assistant_payload.get("system_prompt", "") or "").strip()
if custom_prompt:
    prompt_lines = [custom_prompt]

if profile_payload:
    if str(profile_payload.get("model", "") or "").strip() != model_name:
        profile_payload["model"] = model_name
        profile_path.write_text(json.dumps(profile_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if asr_provider_key == "VoskASR":
    asr_lines = [
        "selected_module:",
        "  ASR: VoskASR",
        "  LLM: GoshaProxyLLM",
        f"  TTS: {tts_engine_module}",
        "ASR:",
        "  VoskASR:",
        "    type: vosk",
        f"    model_path: {vosk_model_path}",
        "    output_dir: tmp/",
    ]
else:
    asr_lines = [
        "selected_module:",
        "  ASR: FunASR",
        "  LLM: GoshaProxyLLM",
        f"  TTS: {tts_engine_module}",
        "ASR:",
        "  FunASR:",
        "    language: ru",
    ]

config_path.write_text(
    "\n".join(
        [
            "# managed-by-gosha",
            f"# requested-tts-engine-profile: {tts_engine_profile_id or 'tts-engine-edge-default'}",
            f"# effective-tts-module: {tts_engine_module}",
            "server:",
            f"  websocket: {ws_url}",
            "prompt: |",
            *[f"  {line}" for line in prompt_lines],
            *asr_lines,
            "LLM:",
            "  GoshaProxyLLM:",
            "    type: openai",
            f"    model_name: {model_name}",
            f"    url: http://host.docker.internal:{panel_port}/api/internal/openai/v1",
            f"    api_key: {proxy_token}",
            "TTS:",
            f"  {tts_engine_module}:",
            "    type: edge",
            f"    voice: {tts_voice_name}",
            f"    speech_rate: {tts_speech_rate}",
            f"    pitch: {tts_pitch}",
            f"    rate: {tts_rate}",
            f"    pitch_hz: {tts_pitch_hz}",
            "    output_dir: tmp/",
            "",
        ]
    ),
    encoding="utf-8",
)
PY

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
echo "Panel: ${PANEL_URL}"
echo "WebSocket backend: ${WS_URL}"
echo "Operator password file: ${PANEL_PASSWORD_FILE}"
