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
SENSEVOICE_MODEL_DIR="${BACKEND_STORAGE_ROOT}/models/SenseVoiceSmall"
SENSEVOICE_MODEL_FILE="${SENSEVOICE_MODEL_DIR}/model.pt"
SENSEVOICE_MODEL_URL="${SELFHOST_XIAOZHI_SENSEVOICE_URL:-https://modelscope.cn/models/iic/SenseVoiceSmall/resolve/master/model.pt}"
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
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_ASR_LANGUAGE" "ru"
ensure_env_key "${ENV_ROOT}/selfhost-backend.env" "SELFHOST_XIAOZHI_STORAGE_ROOT" "${BACKEND_STORAGE_ROOT}"

ensure_env_key "${ENV_ROOT}/agent-gateway.env" "APP_ROOT" "${APP_ROOT}"
ensure_env_key "${ENV_ROOT}/agent-gateway.env" "GOSHA_AGENT_GATEWAY_HOST" "127.0.0.1"
ensure_env_key "${ENV_ROOT}/agent-gateway.env" "GOSHA_AGENT_GATEWAY_PORT" "${AGENT_GATEWAY_PORT}"
ensure_env_key "${ENV_ROOT}/agent-gateway.env" "GOSHA_AGENT_GATEWAY_TIMEOUT_SECONDS" "45"
ensure_env_key "${ENV_ROOT}/agent-gateway.env" "GOSHA_AGENT_GATEWAY_DEFAULT_PROFILE_ID" "${DEFAULT_PROVIDER_PROFILE_ID}"

if [[ ! -s "${SENSEVOICE_MODEL_FILE}" ]]; then
  echo "Downloading SenseVoiceSmall model to ${SENSEVOICE_MODEL_FILE}"
  curl -fL --retry 3 --retry-delay 5 -C - -o "${SENSEVOICE_MODEL_FILE}" "${SENSEVOICE_MODEL_URL}"
fi

python3 - <<'PY' "${BACKEND_CONFIG_FILE}" "${WS_URL}" "${PANEL_PORT}" "${INTERNAL_PROXY_TOKEN}" "${APP_ROOT}"
import sys
import json
from pathlib import Path

config_path = Path(sys.argv[1])
ws_url = sys.argv[2]
panel_port = sys.argv[3]
proxy_token = sys.argv[4]
app_root = Path(sys.argv[5])

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

if profile_payload:
    if str(profile_payload.get("model", "") or "").strip() != model_name:
        profile_payload["model"] = model_name
        profile_path.write_text(json.dumps(profile_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

config_path.write_text(
    "\n".join(
        [
            "# managed-by-gosha",
            "server:",
            f"  websocket: {ws_url}",
            "prompt: |",
            "  Ты — голосовой ассистент по имени Гоша.",
            "  Отвечай по-русски, доброжелательно и короткими понятными фразами.",
            "selected_module:",
            "  ASR: FunASR",
            "  LLM: GoshaProxyLLM",
            "  TTS: EdgeTTS",
            "ASR:",
            "  FunASR:",
            "    language: ru",
            "LLM:",
            "  GoshaProxyLLM:",
            "    type: openai",
            f"    model_name: {model_name}",
            f"    url: http://host.docker.internal:{panel_port}/api/internal/openai/v1",
            f"    api_key: {proxy_token}",
            "TTS:",
            "  EdgeTTS:",
            "    type: edge",
            "    voice: ru-RU-SvetlanaNeural",
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
