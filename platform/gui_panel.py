#!/usr/bin/env python3
import base64
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import gosha_assistant_store as assistant_store
import gosha_agent_gateway_client as agent_gateway_client
import gosha_agent_store as agent_store
import selfhost_xiaozhi_common as selfhost_xiaozhi

try:
    from websockets.sync.client import connect as ws_connect
except Exception:
    ws_connect = None


def env_or_file_value(var_name, file_var_name):
    file_path = str(os.environ.get(file_var_name, "") or "").strip()
    if file_path:
        try:
            return Path(file_path).read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            return ""
    return str(os.environ.get(var_name, "") or "").strip()


APP_ROOT = Path(os.environ.get("APP_ROOT", "/opt/gosha_platform/runtime/app_root")).resolve()
REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SERVER_SCRIPT = REPO_ROOT / "ops" / "install_server.sh"
ROBOTS_DIR = APP_ROOT / "robots"
MEMORY_ROOT = APP_ROOT / "memory"
EDGE_PAIRINGS_PATH = Path(os.environ.get("EDGE_PAIRINGS_PATH", str(APP_ROOT / "edge" / "pairings.json"))).resolve()
HTML_PATH = Path(__file__).with_name("panel_index.html")
APK_SHARE_PATH = Path(os.environ.get("APK_SHARE_PATH", str(APP_ROOT / "share" / "maxcorp-connector-debug.apk"))).resolve()
ADMIN_APK_SHARE_PATH = Path(os.environ.get("ADMIN_APK_SHARE_PATH", str(APP_ROOT / "share" / "maxcorp-admin-connector-debug.apk"))).resolve()
PRIVACY_POLICY_SHARE_PATH = Path(
    os.environ.get(
        "PRIVACY_POLICY_SHARE_PATH",
        str(APP_ROOT / "share" / "legal" / "gosha-privacy-policy.html"),
    )
).resolve()
TERMS_OF_USE_SHARE_PATH = Path(
    os.environ.get(
        "TERMS_OF_USE_SHARE_PATH",
        str(APP_ROOT / "share" / "legal" / "gosha-terms-of-use.html"),
    )
).resolve()
EDGE_HUB_LOCAL_URL = os.environ.get("EDGE_HUB_LOCAL_URL", "http://127.0.0.1:8890").rstrip("/")
EDGE_AGENT_STALE_SECONDS = int(os.environ.get("EDGE_AGENT_STALE_SECONDS", "45"))
DIRECT_PROBE_TIMEOUT_SECONDS = float(os.environ.get("DIRECT_PROBE_TIMEOUT_SECONDS", "2.5"))
EDGE_CONTROL_PROBE_TIMEOUT_SECONDS = float(os.environ.get("EDGE_CONTROL_PROBE_TIMEOUT_SECONDS", "6.0"))
MOBILE_DIR = APP_ROOT / "mobile"
MOBILE_CODES_PATH = MOBILE_DIR / "onboarding_codes.json"
PUBLIC_PANEL_URL = os.environ.get("PUBLIC_PANEL_URL", "http://151.241.228.232:18876").rstrip("/")
PUBLIC_EDGE_HUB_URL = os.environ.get("PUBLIC_EDGE_HUB_URL", "ws://151.241.228.232:18080/mcp").rstrip("/")
PANEL_PUBLIC_SCHEME = urlparse(PUBLIC_PANEL_URL).scheme.lower()
GOSHA_INTERNAL_OPENAI_PROXY_TOKEN = env_or_file_value(
    "GOSHA_INTERNAL_OPENAI_PROXY_TOKEN",
    "GOSHA_INTERNAL_OPENAI_PROXY_TOKEN_FILE",
)
GOSHA_BACKEND_PROXY_PROFILE_ID = str(os.environ.get("GOSHA_BACKEND_PROXY_PROFILE_ID", "") or "").strip()
GOSHA_INTERNAL_OPENAI_PROXY_TIMEOUT_SECONDS = max(
    5.0,
    float(os.environ.get("GOSHA_INTERNAL_OPENAI_PROXY_TIMEOUT_SECONDS", "180")),
)
ROBOT_ID_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
CONTROL_TRANSPORTS = {"cloud-mcp", "edge-hub", "local-ws"}
USER_SERVICE_ORDER = ["knowledge", "memory", "telegram", "email", "call"]
MOBILE_PANEL_TOKENS_PATH = MOBILE_DIR / "panel_client_tokens.json"
MOBILE_CODE_TTL_SECONDS = max(0, int(os.environ.get("MOBILE_CODE_TTL_SECONDS", "2592000")))
PANEL_OPERATOR_USER = env_or_file_value("PANEL_OPERATOR_USER", "PANEL_OPERATOR_USER_FILE")
PANEL_OPERATOR_PASSWORD = env_or_file_value("PANEL_OPERATOR_PASSWORD", "PANEL_OPERATOR_PASSWORD_FILE")
PANEL_SESSION_COOKIE = "ai_robot_panel_session"
PANEL_SESSION_TTL_SECONDS = int(os.environ.get("PANEL_SESSION_TTL_SECONDS", "43200"))
OPERATOR_SESSIONS = {}
XIAOZHI_API_BASE_URL = os.environ.get("XIAOZHI_API_BASE_URL", "https://xiaozhi.me/api").rstrip("/")
XIAOZHI_API_TOKEN = env_or_file_value("XIAOZHI_API_TOKEN", "XIAOZHI_API_TOKEN_FILE")
XIAOZHI_STATUS_CACHE_TTL_SECONDS = max(5, int(os.environ.get("XIAOZHI_STATUS_CACHE_TTL_SECONDS", "45")))
XIAOZHI_STATUS_CACHE = {}
RUNTIME_CLASS_RUNTIME = "runtime"
RUNTIME_CLASS_TEMPLATE = "template"
DEFAULT_TEMPLATE_ROBOT_IDS = {"golden-template"}
SUPPORT_ROBOT_IDS = {"rustore-moderation"}
DETECTION_SNAPSHOT_FILENAME = "panel_detection.json"
MOBILE_PRESENCE_SNAPSHOT_FILENAME = "mobile_presence.json"
MOBILE_PRESENCE_TTL_SECONDS = max(30, int(os.environ.get("MOBILE_PRESENCE_TTL_SECONDS", "180")))
MOBILE_PRESENCE_STATES = {
    "home_wifi_local",
    "robot_hotspot_visible",
    "phone_on_robot_wifi",
    "not_found",
}
MOBILE_PRESENCE_SOURCE_ANDROID = "android_local_discovery"
ACTIVITY_PRESENCE_FRESH_SECONDS = max(60, int(os.environ.get("ACTIVITY_PRESENCE_FRESH_SECONDS", "900")))
ACTIVITY_PRESENCE_RECENT_SECONDS = max(
    ACTIVITY_PRESENCE_FRESH_SECONDS,
    int(os.environ.get("ACTIVITY_PRESENCE_RECENT_SECONDS", "21600")),
)
ACTIVITY_PRESENCE_HISTORY_SECONDS = max(
    ACTIVITY_PRESENCE_RECENT_SECONDS,
    int(os.environ.get("ACTIVITY_PRESENCE_HISTORY_SECONDS", "259200")),
)
KNOWN_MEMORY_FILES = [
    "client_profile.json",
    "events.jsonl",
    "notes.md",
    "prefs.json",
    "contacts.json",
]
SERVICE_TOOL_MAP = {
    "knowledge": "knowledge-tools",
    "memory": "memory-tools",
    "telegram": "telegram-tools",
    "email": "email-tools",
    "call": "call-tools",
}
LEGACY_DISABLED_MCP_TOOLS = {
    "music-tools": {
        "reason": "reserved_for_future_gosha_media",
        "stub_service": "gosha.media.stub",
    },
}
PLAN_CATALOG = {
    "start": {
        "code": "start",
        "name": "Старт",
        "description": "Базовая база знаний и память клиента.",
        "services": {"knowledge": True, "memory": True, "telegram": False, "email": False, "call": False},
        "limits": {"clients": 100, "memory_mb": 256, "operators": 1},
    },
    "business": {
        "code": "business",
        "name": "Бизнес",
        "description": "Для клиентского сервиса с мессенджерами и email.",
        "services": {"knowledge": True, "memory": True, "telegram": True, "email": True, "call": False},
        "limits": {"clients": 1000, "memory_mb": 1024, "operators": 3},
    },
    "max": {
        "code": "max",
        "name": "MAX",
        "description": "Все доступные сервисы и расширенные лимиты.",
        "services": {"knowledge": True, "memory": True, "telegram": True, "email": True, "call": True},
        "limits": {"clients": 5000, "memory_mb": 4096, "operators": 10},
    },
    "custom": {
        "code": "custom",
        "name": "Индивидуальный",
        "description": "Ручная настройка сервисов и лимитов под клиента.",
        "services": {"knowledge": True, "memory": True, "telegram": False, "email": False, "call": False},
        "limits": {"clients": 100, "memory_mb": 256, "operators": 1},
    },
}


def json_bytes(payload):
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def now_ts():
    return int(time.time())


def ts_to_iso(ts):
    try:
        ts_int = int(float(ts))
    except (TypeError, ValueError):
        return ""
    if ts_int <= 0:
        return ""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts_int))


def panel_event(event, **fields):
    payload = {"event": str(event or "").strip() or "unknown", "ts": ts_to_iso(now_ts())}
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            payload[key] = value
            continue
        if isinstance(value, (int, float)):
            payload[key] = value
            continue
        text = str(value).strip()
        if not text:
            continue
        payload[key] = text.replace("\r", " ").replace("\n", " ")[:240]
    sys.stdout.write("panel_event: " + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def run_cmd(cmd, timeout=10):
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "code": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "cmd": cmd,
        }
    except Exception as exc:
        return {"ok": False, "code": -1, "stdout": "", "stderr": str(exc), "cmd": cmd}


def http_request_json(url, timeout=3.0, headers=None):
    req = Request(url, headers=headers or {})
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8"))
        return {"ok": True, "status": 200, "data": data}
    except HTTPError as exc:
        raw = exc.read()
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            data = None
        return {"ok": False, "status": exc.code, "data": data, "error": f"http {exc.code}", "detail": str(exc)}
    except URLError as exc:
        return {"ok": False, "status": 0, "data": None, "error": str(exc.reason)}
    except Exception as exc:
        return {"ok": False, "status": 0, "data": None, "error": str(exc)}


def http_get_json(url, timeout=3.0):
    try:
        with urlopen(url, timeout=timeout) as resp:
            raw = resp.read()
        return {"ok": True, "data": json.loads(raw.decode("utf-8"))}
    except HTTPError as exc:
        return {"ok": False, "error": f"http {exc.code}", "detail": str(exc)}
    except URLError as exc:
        return {"ok": False, "error": str(exc.reason)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def safe_robot_id(robot_id):
    return bool(ROBOT_ID_RE.match(robot_id or ""))


def require_robot_dir(robot_id):
    if not safe_robot_id(robot_id):
        raise ValueError("invalid robot_id")
    robot_dir = ROBOTS_DIR / robot_id
    if not robot_dir.exists():
        raise ValueError("robot not found")
    return robot_dir


def robot_runtime_class(robot_id, env=None):
    details = env if isinstance(env, dict) else load_env(robot_env_path(robot_id))
    runtime_class = str((details or {}).get("ROBOT_RUNTIME_CLASS", "") or "").strip().lower()
    if runtime_class in {RUNTIME_CLASS_RUNTIME, RUNTIME_CLASS_TEMPLATE}:
        return runtime_class
    if robot_id in DEFAULT_TEMPLATE_ROBOT_IDS:
        return RUNTIME_CLASS_TEMPLATE
    return RUNTIME_CLASS_RUNTIME


def robot_visible_in_panel(robot_id, env=None):
    return robot_runtime_class(robot_id, env=env) == RUNTIME_CLASS_RUNTIME


def robot_fleet_state(robot_id, runtime_class=None, endpoint_ready=False, service_state_value=""):
    runtime_kind = runtime_class or robot_runtime_class(robot_id)
    service_value = str(service_state_value or "").strip().lower()

    if runtime_kind == RUNTIME_CLASS_TEMPLATE:
        return "template"
    if robot_id in SUPPORT_ROBOT_IDS:
        return "test"
    if endpoint_ready and service_value == "active":
        return "live"
    return "staged"


def build_fleet_readiness(robot_id, runtime_class, endpoint_ready, service_state_value, cloud_console):
    cloud = cloud_console if isinstance(cloud_console, dict) else {}
    cloud_state = str(cloud.get("state", "") or "").strip().lower() or "unknown"
    provider = str(cloud.get("provider", "") or selfhost_xiaozhi.BACKEND_MODE_SELF_HOSTED).strip().lower()
    xiaozhi_configured = bool(cloud.get("configured"))
    if provider == selfhost_xiaozhi.BACKEND_MODE_SELF_HOSTED:
        agent_meta_ready = bool(cloud.get("device_claimed") or cloud.get("mcp_endpoint_ready") or cloud.get("websocket_token_configured"))
    else:
        agent_meta_ready = bool(xiaozhi_configured and cloud.get("agent_id"))
    service_active = str(service_state_value or "").strip().lower() == "active"
    state = robot_fleet_state(
        robot_id,
        runtime_class=runtime_class,
        endpoint_ready=endpoint_ready,
        service_state_value=service_state_value,
    )
    missing = []
    if runtime_class != RUNTIME_CLASS_TEMPLATE and robot_id not in SUPPORT_ROBOT_IDS:
        if not endpoint_ready:
            missing.append("endpoint")
        if not agent_meta_ready:
            missing.append("xiaozhi_agent")
        if not service_active:
            missing.append("bridge_service")

    return {
        "state": state,
        "runtime_class": runtime_class,
        "endpoint_configured": bool(endpoint_ready),
        "backend_provider": provider,
        "xiaozhi_configured": xiaozhi_configured,
        "xiaozhi_agent_configured": agent_meta_ready,
        "service_active": service_active,
        "cloud_state": cloud_state,
        "needs_endpoint_or_token": bool(runtime_class != RUNTIME_CLASS_TEMPLATE and robot_id not in SUPPORT_ROBOT_IDS and not agent_meta_ready),
        "needs_smoke_check": state in {"staged", "live"},
        "missing": missing,
    }


def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def operator_auth_enabled():
    return bool(PANEL_OPERATOR_USER and PANEL_OPERATOR_PASSWORD)


def validate_operator_credentials(username, password):
    if not operator_auth_enabled():
        return True
    clean_user = str(username or "").strip()
    clean_password = str(password or "")
    return secrets.compare_digest(clean_user, PANEL_OPERATOR_USER) and secrets.compare_digest(clean_password, PANEL_OPERATOR_PASSWORD)


def cleanup_operator_sessions():
    now = int(time.time())
    for token, entry in list(OPERATOR_SESSIONS.items()):
        expires_at = int((entry or {}).get("expires_at", 0) or 0)
        if expires_at <= now:
            OPERATOR_SESSIONS.pop(token, None)


def create_operator_session(username):
    cleanup_operator_sessions()
    token = secrets.token_urlsafe(32)
    OPERATOR_SESSIONS[token] = {
        "user": str(username or "").strip() or "operator",
        "expires_at": int(time.time()) + PANEL_SESSION_TTL_SECONDS,
    }
    return token


def get_operator_session(token):
    cleanup_operator_sessions()
    entry = OPERATOR_SESSIONS.get(str(token or "").strip())
    if not isinstance(entry, dict):
        return None
    entry["expires_at"] = int(time.time()) + PANEL_SESSION_TTL_SECONDS
    return entry


def drop_operator_session(token):
    OPERATOR_SESSIONS.pop(str(token or "").strip(), None)


def load_env(path):
    if not path.exists():
        return {}
    env = {}
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in raw:
            continue
        key, val = raw.split("=", 1)
        cleaned = val.strip()
        try:
            parsed = shlex.split(cleaned)
            if len(parsed) == 1:
                cleaned = parsed[0]
        except Exception:
            pass
        env[key.strip()] = cleaned
    return env


def shell_env_value(value):
    clean = str(value or "").replace("\n", "").replace("\r", "")
    return shlex.quote(clean)


def save_env_updates(path, updates):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if path.exists():
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    used = set()
    out = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw:
            out.append(raw)
            continue
        key, _ = raw.split("=", 1)
        key = key.strip()
        if key in updates:
            out.append(f"{key}={shell_env_value(updates[key])}")
            used.add(key)
        else:
            out.append(raw)

    for key, value in updates.items():
        if key in used:
            continue
        out.append(f"{key}={shell_env_value(value)}")

    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def normalize_ws_url(raw):
    value = str(raw or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = f"ws://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"ws", "wss"}:
        raise ValueError("ws_url must start with ws:// or wss://")
    if not parsed.netloc:
        raise ValueError("ws_url host is missing")
    path = parsed.path or "/ws"
    if not path.startswith("/"):
        path = "/" + path
    final = f"{parsed.scheme}://{parsed.netloc}{path}"
    if parsed.query:
        final += "?" + parsed.query
    return final


def is_mcp_ws_endpoint(raw):
    value = str(raw or "").strip()
    if not value:
        return False
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    return parsed.scheme in {"ws", "wss"} and parsed.path.rstrip("/").endswith("/mcp")


def robot_backend_mode(env):
    details = env if isinstance(env, dict) else {}
    mode = str((details or {}).get("ROBOT_BACKEND_MODE", "") or "").strip().lower()
    if not mode:
        return selfhost_xiaozhi.BACKEND_MODE_SELF_HOSTED
    if mode == selfhost_xiaozhi.BACKEND_MODE_SELF_HOSTED:
        return selfhost_xiaozhi.BACKEND_MODE_SELF_HOSTED
    return selfhost_xiaozhi.BACKEND_MODE_XIAOZHI_CLOUD


def robot_env_path(robot_id):
    return ROBOTS_DIR / robot_id / "robot.env"


def mcp_endpoint_path(robot_id):
    return ROBOTS_DIR / robot_id / "mcp_endpoint.txt"


def subscription_path(robot_id):
    return ROBOTS_DIR / robot_id / "subscription.json"


def owner_path(robot_id):
    return ROBOTS_DIR / robot_id / "owner.json"


def users_path(robot_id):
    return ROBOTS_DIR / robot_id / "users.json"


def detection_snapshot_path(robot_id):
    return ROBOTS_DIR / robot_id / DETECTION_SNAPSHOT_FILENAME


def mobile_presence_snapshot_path(robot_id):
    return ROBOTS_DIR / robot_id / MOBILE_PRESENCE_SNAPSHOT_FILENAME


def resolve_script_path(script_name):
    app_script = APP_ROOT / "bin" / script_name
    if app_script.exists():
        return app_script
    repo_script = Path(__file__).resolve().parent / script_name
    if repo_script.exists():
        return repo_script
    raise FileNotFoundError(f"{script_name} not found in {APP_ROOT / 'bin'} or {repo_script.parent}")


def refresh_backend_runtime(trigger="manual"):
    script_path = INSTALL_SERVER_SCRIPT
    if not script_path.exists():
        return {
            "ok": False,
            "error": f"install script not found: {script_path}",
            "service_state": "unknown",
        }
    panel_event("backend_runtime_refresh_start", trigger=trigger, script=str(script_path))
    result = run_cmd(["bash", str(script_path), "--phase", "backend"], timeout=300)
    service_state = "unknown"
    if shutil.which("systemctl") is not None:
        state_result = run_cmd(["systemctl", "is-active", "gosha-backend.service"], timeout=10)
        service_state = (state_result.get("stdout") or "unknown").strip() or "unknown"
    payload = {
        "ok": bool(result.get("ok")),
        "code": int(result.get("code", -1)),
        "service_state": service_state,
        "stdout": str(result.get("stdout", "") or "").strip()[:400],
        "stderr": str(result.get("stderr", "") or "").strip()[:400],
    }
    if not payload["ok"] and not payload["stderr"]:
        payload["stderr"] = "backend refresh failed"
    if not payload["ok"] and not payload["service_state"]:
        payload["service_state"] = "failed"
    panel_event(
        "backend_runtime_refresh_finish",
        trigger=trigger,
        ok=payload["ok"],
        code=payload["code"],
        service_state=payload["service_state"],
        stderr=payload["stderr"],
    )
    return payload


def get_robot_mcp_endpoint(robot_id):
    path = mcp_endpoint_path(robot_id)
    if not path.exists():
        return ""
    value = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not value or "REPLACE_WITH_ROBOT_TOKEN" in value:
        return ""
    try:
        return normalize_ws_url(value)
    except Exception:
        return value


def normalize_detection_snapshot(data, *, fallback_mode=""):
    raw = data if isinstance(data, dict) else {}
    try:
        checked_at = int(raw.get("checked_at") or 0)
    except (TypeError, ValueError):
        checked_at = 0
    try:
        last_seen = int(raw.get("last_seen") or 0)
    except (TypeError, ValueError):
        last_seen = 0
    try:
        duration_ms = max(0, int(raw.get("duration_ms") or 0))
    except (TypeError, ValueError):
        duration_ms = 0
    snapshot = {
        "checked_at": checked_at,
        "checked_at_iso": str(raw.get("checked_at_iso", "") or ts_to_iso(checked_at)),
        "mode": str(raw.get("mode", "") or fallback_mode or ""),
        "kind": str(raw.get("kind", "") or ""),
        "state": str(raw.get("state", "") or ""),
        "verified_now": bool(raw.get("verified_now", False)),
        "reached_robot": bool(raw.get("reached_robot", False)),
        "method": str(raw.get("method", "") or ""),
        "request_id": str(raw.get("request_id", "") or ""),
        "duration_ms": duration_ms,
        "error": str(raw.get("error", "") or ""),
        "error_type": str(raw.get("error_type", "") or ""),
        "detail": str(raw.get("detail", "") or ""),
        "next_step": str(raw.get("next_step", "") or ""),
        "protocol_phase": str(raw.get("protocol_phase", "") or ""),
        "lifecycle_path": str(raw.get("lifecycle_path", "") or ""),
        "last_seen": last_seen,
    }
    return snapshot


def detection_snapshot_from_probe(probe, *, fallback_mode=""):
    snapshot = normalize_detection_snapshot(probe, fallback_mode=fallback_mode)
    if not snapshot.get("mode"):
        snapshot["mode"] = str(probe.get("mode", "") or fallback_mode or "")
    return snapshot


def load_detection_snapshot(robot_id, *, fallback_mode=""):
    path = detection_snapshot_path(robot_id)
    raw = load_json(path, {})
    if not isinstance(raw, dict):
        raw = {}
    raw_mode = str(raw.get("mode", "") or "")
    if fallback_mode and raw_mode and raw_mode != fallback_mode:
        return normalize_detection_snapshot({}, fallback_mode=fallback_mode)
    return normalize_detection_snapshot(raw, fallback_mode=fallback_mode)


def save_detection_snapshot(robot_id, probe, *, fallback_mode=""):
    snapshot = detection_snapshot_from_probe(probe, fallback_mode=fallback_mode)
    save_json_atomic(detection_snapshot_path(robot_id), snapshot)
    return snapshot


def normalize_mobile_presence_host(raw):
    value = str(raw or "").strip()
    if not value:
        return ""
    if "://" in value:
        try:
            parsed = urlparse(value)
            value = parsed.hostname or parsed.netloc or ""
        except Exception:
            return ""
    value = value.strip().strip("/")
    if not value or len(value) > 255:
        return ""
    lowered = value.lower()
    if lowered in {"localhost", "0.0.0.0"} or lowered.startswith("127."):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", value):
        return ""
    return value


def normalize_mobile_presence_snapshot(data):
    raw = data if isinstance(data, dict) else {}
    state = str(raw.get("state", "") or "").strip().lower()
    if state not in MOBILE_PRESENCE_STATES:
        state = ""
    source = str(raw.get("source", "") or "").strip().lower()
    if source and source != MOBILE_PRESENCE_SOURCE_ANDROID:
        source = MOBILE_PRESENCE_SOURCE_ANDROID
    if state and not source:
        source = MOBILE_PRESENCE_SOURCE_ANDROID
    local_host = normalize_mobile_presence_host(raw.get("local_host"))
    if state != "home_wifi_local":
        local_host = ""
    try:
        received_at = int(raw.get("received_at") or 0)
    except (TypeError, ValueError):
        received_at = 0
    age_seconds = max(0, now_ts() - received_at) if received_at > 0 else 0
    if not state or received_at <= 0:
        status = "missing"
    elif age_seconds <= MOBILE_PRESENCE_TTL_SECONDS:
        status = "fresh"
    else:
        status = "stale"
    return {
        "state": state,
        "status": status,
        "source": source,
        "local_host": local_host,
        "received_at": received_at,
        "received_at_iso": ts_to_iso(received_at),
        "age_seconds": age_seconds,
        "ttl_seconds": MOBILE_PRESENCE_TTL_SECONDS,
        "fresh": status == "fresh",
        "stale": status == "stale",
    }


def load_mobile_presence_snapshot(robot_id):
    path = mobile_presence_snapshot_path(robot_id)
    raw = load_json(path, {})
    return normalize_mobile_presence_snapshot(raw)


def save_mobile_presence_snapshot(robot_id, *, state, source=MOBILE_PRESENCE_SOURCE_ANDROID, local_host=""):
    snapshot = normalize_mobile_presence_snapshot(
        {
            "state": state,
            "source": source or MOBILE_PRESENCE_SOURCE_ANDROID,
            "local_host": local_host,
            "received_at": now_ts(),
        }
    )
    save_json_atomic(mobile_presence_snapshot_path(robot_id), snapshot)
    panel_event(
        "mobile_presence_update",
        robot_id=robot_id,
        state=snapshot.get("state"),
        status=snapshot.get("status"),
        source=snapshot.get("source"),
        local_host=snapshot.get("local_host"),
    )
    return snapshot


def update_mobile_presence(robot_id, payload):
    state = str((payload or {}).get("state", "") or "").strip().lower()
    if state not in MOBILE_PRESENCE_STATES:
        raise ValueError("invalid mobile presence state")
    source = str((payload or {}).get("source", "") or "").strip().lower() or MOBILE_PRESENCE_SOURCE_ANDROID
    if source != MOBILE_PRESENCE_SOURCE_ANDROID:
        raise ValueError("invalid mobile presence source")
    local_host = normalize_mobile_presence_host((payload or {}).get("local_host"))
    if state != "home_wifi_local":
        local_host = ""
    snapshot = save_mobile_presence_snapshot(
        robot_id,
        state=state,
        source=source,
        local_host=local_host,
    )
    return {
        "ok": True,
        "accepted_at": snapshot["received_at"],
        "accepted_at_iso": snapshot["received_at_iso"],
        "data": snapshot,
    }


def extract_xiaozhi_mcp_token(endpoint):
    value = str(endpoint or "").strip()
    if not value:
        return ""
    try:
        parsed = urlparse(value)
    except Exception:
        return ""
    if parsed.netloc != "api.xiaozhi.me" or parsed.path.rstrip("/") != "/mcp":
        return ""
    return str(parse_qs(parsed.query).get("token", [""])[0] or "").strip()


def decode_jwt_payload(token):
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    try:
        payload += "=" * (-len(payload) % 4)
        raw = base64.urlsafe_b64decode(payload.encode("utf-8"))
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def get_xiaozhi_agent_meta(robot_id):
    endpoint = get_robot_mcp_endpoint(robot_id)
    token = extract_xiaozhi_mcp_token(endpoint)
    payload = decode_jwt_payload(token)
    agent_id = payload.get("agentId")
    user_id = payload.get("userId")
    endpoint_id = payload.get("endpointId")
    exp = payload.get("exp")
    return {
        "configured": bool(token and agent_id),
        "endpoint": endpoint,
        "agent_id": str(agent_id).strip() if agent_id is not None else "",
        "user_id": str(user_id).strip() if user_id is not None else "",
        "endpoint_id": str(endpoint_id).strip() if endpoint_id is not None else "",
        "token_exp": int(exp or 0) if str(exp or "").strip() else 0,
        "token_exp_iso": ts_to_iso(exp),
    }


def fetch_xiaozhi_agent_devices(agent_id):
    agent_key = str(agent_id or "").strip()
    now = now_ts()
    cached = XIAOZHI_STATUS_CACHE.get(agent_key)
    if isinstance(cached, dict) and (now - int(cached.get("checked_at", 0) or 0)) <= XIAOZHI_STATUS_CACHE_TTL_SECONDS:
        return cached

    if not agent_key:
        return {"configured": False, "available": False, "state": "missing", "checked_at": now, "checked_at_iso": ts_to_iso(now)}

    if not XIAOZHI_API_TOKEN:
        return {
            "configured": True,
            "available": False,
            "state": "auth_missing",
            "error": "XIAOZHI_API_TOKEN is not configured",
            "checked_at": now,
            "checked_at_iso": ts_to_iso(now),
        }

    url = f"{XIAOZHI_API_BASE_URL}/agents/{agent_key}/devices"
    result = http_request_json(
        url,
        timeout=6.0,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {XIAOZHI_API_TOKEN}",
        },
    )
    payload = result.get("data") if isinstance(result.get("data"), dict) else {}
    checked = {
        "configured": True,
        "available": True,
        "agent_id": agent_key,
        "checked_at": now,
        "checked_at_iso": ts_to_iso(now),
        "api_url": url,
    }
    if not result.get("ok"):
        checked.update(
            {
                "state": "error",
                "error": str(payload.get("message") or result.get("error") or "request failed"),
                "status": int(result.get("status") or 0),
                "devices_count": 0,
                "online_count": 0,
                "devices": [],
            }
        )
        XIAOZHI_STATUS_CACHE[agent_key] = checked
        return checked

    devices = payload.get("data") if isinstance(payload.get("data"), list) else []
    compact_devices = []
    online_count = 0
    latest_connected_at = ""
    for item in devices:
        if not isinstance(item, dict):
            continue
        online = bool(item.get("online"))
        if online:
            online_count += 1
        last_connected_at = str(item.get("last_connected_at", "") or "").strip()
        if last_connected_at and last_connected_at > latest_connected_at:
            latest_connected_at = last_connected_at
        compact_devices.append(
            {
                "id": item.get("id"),
                "alias": str(item.get("alias", "") or ""),
                "board_name": str(item.get("board_name", "") or ""),
                "mac_address": str(item.get("mac_address", "") or ""),
                "app_version": str(item.get("app_version", "") or ""),
                "online": online,
                "last_connected_at": last_connected_at,
            }
        )

    checked.update(
        {
            "state": "online" if online_count > 0 else "offline",
            "devices_count": len(compact_devices),
            "online_count": online_count,
            "latest_connected_at": latest_connected_at,
            "agent_template_id": payload.get("agent_template_id"),
            "devices": compact_devices[:12],
        }
    )
    XIAOZHI_STATUS_CACHE[agent_key] = checked
    return checked


def summarize_xiaozhi_console(robot_id):
    meta = get_xiaozhi_agent_meta(robot_id)
    if not meta.get("configured"):
        return {
            "configured": False,
            "available": False,
            "state": "missing",
            "agent_id": "",
            "detail": "Идентификатор агента не найден в совместимом облачном MCP-адресе",
        }
    devices = fetch_xiaozhi_agent_devices(meta.get("agent_id"))
    state = str(devices.get("state", "unknown") or "unknown")
    if state == "online":
        detail = f"Совместимый облачный контур: в сети {devices.get('online_count', 0)} из {devices.get('devices_count', 0)} устройств"
    elif state == "offline":
        detail = f"Совместимый облачный контур: ни одно устройство не помечено как подключённое ({devices.get('devices_count', 0)} всего)"
    elif state == "auth_missing":
        detail = "Токен совместимого облачного API не настроен"
    elif state == "error":
        detail = str(devices.get("error", "") or "ошибка проверки совместимого облачного контура")
    else:
        detail = str(devices.get("detail", "") or "статус совместимого облачного контура не определён")
    return {
        "configured": True,
        "available": bool(devices.get("available")),
        "state": state,
        "detail": detail,
        "agent_id": meta.get("agent_id", ""),
        "user_id": meta.get("user_id", ""),
        "endpoint_id": meta.get("endpoint_id", ""),
        "token_exp": meta.get("token_exp", 0),
        "token_exp_iso": meta.get("token_exp_iso", ""),
        "checked_at": devices.get("checked_at", 0),
        "checked_at_iso": devices.get("checked_at_iso", ""),
        "status": devices.get("status", 0),
        "error": str(devices.get("error", "") or ""),
        "devices_count": int(devices.get("devices_count", 0) or 0),
        "online_count": int(devices.get("online_count", 0) or 0),
        "latest_connected_at": str(devices.get("latest_connected_at", "") or ""),
        "devices": devices.get("devices", []) if isinstance(devices.get("devices"), list) else [],
        "api_url": devices.get("api_url", ""),
    }


def summarize_selfhost_backend(robot_id, env=None):
    details = env if isinstance(env, dict) else load_env(robot_env_path(robot_id))
    summary = selfhost_xiaozhi.build_robot_runtime_claim(robot_id, env=details)
    backend = summary.get("backend", {}) if isinstance(summary.get("backend"), dict) else {}
    state = str(summary.get("state", "") or "missing").strip().lower()
    if state == "claimed":
        detail = "Платформа Гоша: устройство привязано к нашей платформе"
    elif state == "awaiting_claim":
        detail = "Платформа Гоша: режим включён, но устройство ещё не привязано"
    else:
        detail = "Платформа Гоша не настроена"
    return {
        "provider": selfhost_xiaozhi.BACKEND_MODE_SELF_HOSTED,
        "configured": bool(summary.get("configured")),
        "available": True,
        "state": state,
        "detail": detail,
        "backend_mode": selfhost_xiaozhi.BACKEND_MODE_SELF_HOSTED,
        "device_claimed": bool(summary.get("device_claimed")),
        "device_id": str(summary.get("device_id", "") or ""),
        "client_id": str(summary.get("client_id", "") or ""),
        "serial_number": str(summary.get("serial_number", "") or ""),
        "claimed_at": int(summary.get("claimed_at", 0) or 0),
        "claimed_at_iso": str(summary.get("claimed_at_iso", "") or ""),
        "last_seen": int(summary.get("last_seen", 0) or 0),
        "last_seen_iso": str(summary.get("last_seen_iso", "") or ""),
        "board_name": str(summary.get("board_name", "") or ""),
        "board_ip": str(summary.get("board_ip", "") or ""),
        "app_version": str(summary.get("app_version", "") or ""),
        "remote_addr": str(summary.get("remote_addr", "") or ""),
        "checked_at": int(backend.get("checked_at", now_ts()) or now_ts()),
        "checked_at_iso": str(backend.get("checked_at_iso", "") or ts_to_iso(backend.get("checked_at", now_ts()))),
        "api_url": str(backend.get("ota_url", "") or ""),
        "websocket_url": str(summary.get("websocket_url", "") or backend.get("websocket_url", "")),
        "websocket_token_configured": bool(summary.get("websocket_token_configured")),
        "control_mcp_endpoint": str(summary.get("control_mcp_endpoint", "") or ""),
        "mcp_endpoint_ready": bool(summary.get("control_mcp_endpoint")),
    }


def summarize_robot_backend(robot_id, env=None):
    details = env if isinstance(env, dict) else load_env(robot_env_path(robot_id))
    if robot_backend_mode(details) == selfhost_xiaozhi.BACKEND_MODE_SELF_HOSTED:
        return summarize_selfhost_backend(robot_id, env=details)
    return summarize_xiaozhi_console(robot_id)


def selfhost_gateway_state():
    state = selfhost_xiaozhi.load_state()
    backend = state.get("backend", {})
    pending = selfhost_xiaozhi.list_pending_devices(state=state)
    claimed = selfhost_xiaozhi.list_claimed_devices(state=state)
    return {
        "backend": backend,
        "pending_devices": pending,
        "claimed_devices": claimed,
        "pending_count": len(pending),
        "claimed_count": len(claimed),
        "provider": selfhost_xiaozhi.BACKEND_MODE_SELF_HOSTED,
        "transport": "websocket_only",
    }


def default_subscription(plan_code="start"):
    plan = PLAN_CATALOG.get(plan_code) or PLAN_CATALOG["start"]
    return {
        "plan_code": plan["code"],
        "plan_name": plan["name"],
        "services": dict(plan["services"]),
        "limits": dict(plan["limits"]),
        "billing": {
            "start_date": "",
            "end_date": "",
            "payment_status": "trial",
        },
        "notes": "",
    }


def extract_services_from_servers(servers):
    services = {key: False for key in USER_SERVICE_ORDER}
    for service_name, tool_name in SERVICE_TOOL_MAP.items():
        if service_name == "memory":
            services[service_name] = tool_name in servers and not bool(servers[tool_name].get("disabled", False))
        else:
            services[service_name] = tool_name in servers and not bool(servers[tool_name].get("disabled", False))
    return services


def enforce_reserved_mcp_policy(servers):
    if not isinstance(servers, dict):
        return []
    changed = []
    for tool_name in LEGACY_DISABLED_MCP_TOOLS:
        entry = servers.get(tool_name)
        if not isinstance(entry, dict):
            continue
        if not bool(entry.get("disabled", False)):
            entry["disabled"] = True
            changed.append(tool_name)
    return changed


def normalize_subscription(raw, servers=None):
    servers = servers or {}
    base = default_subscription("start")
    plan_code = str((raw or {}).get("plan_code", base["plan_code"])).strip().lower() or base["plan_code"]
    plan = PLAN_CATALOG.get(plan_code) or PLAN_CATALOG["custom"]

    plan_name = str((raw or {}).get("plan_name", plan["name"])).strip() or plan["name"]
    incoming_services = (raw or {}).get("services", {})
    services = dict(plan["services"])
    if isinstance(incoming_services, dict):
        for key in USER_SERVICE_ORDER:
            if key in incoming_services:
                services[key] = bool(incoming_services[key])
    elif servers:
        services = extract_services_from_servers(servers)

    incoming_limits = (raw or {}).get("limits", {})
    limits = dict(plan["limits"])
    if isinstance(incoming_limits, dict):
        for key in ("clients", "memory_mb", "operators"):
            if key in incoming_limits:
                try:
                    limits[key] = max(0, int(incoming_limits[key]))
                except Exception:
                    pass

    incoming_billing = (raw or {}).get("billing", {})
    billing = {
        "start_date": str((incoming_billing or {}).get("start_date", "")).strip(),
        "end_date": str((incoming_billing or {}).get("end_date", "")).strip(),
        "payment_status": str((incoming_billing or {}).get("payment_status", "trial")).strip().lower() or "trial",
    }

    return {
        "plan_code": plan["code"],
        "plan_name": plan_name,
        "services": services,
        "limits": limits,
        "billing": billing,
        "notes": str((raw or {}).get("notes", "")).strip(),
    }


def load_subscription(robot_id, servers=None):
    path = subscription_path(robot_id)
    if isinstance(servers, dict):
        changed = enforce_reserved_mcp_policy(servers)
        if changed:
            cfg_path = ROBOTS_DIR / robot_id / "mcp_config.json"
            cfg = load_json(cfg_path, {"mcpServers": {}})
            cfg["mcpServers"] = servers
            save_json_atomic(cfg_path, cfg)
    raw = load_json(path, {})
    if not raw:
        raw = default_subscription("start")
        if servers:
            raw["services"] = extract_services_from_servers(servers)
        save_json_atomic(path, raw)
    return normalize_subscription(raw, servers=servers)


def save_subscription(robot_id, subscription):
    path = subscription_path(robot_id)
    save_json_atomic(path, subscription)


def default_owner():
    return {
        "name": "",
        "email": "",
        "phone": "",
        "company": "",
        "contact": "",
        "comment": "",
    }


def normalize_owner(raw):
    base = default_owner()
    if isinstance(raw, dict):
        for key in base.keys():
            base[key] = str(raw.get(key, "")).strip()
    return base


def load_owner(robot_id):
    path = owner_path(robot_id)
    raw = load_json(path, {})
    owner = normalize_owner(raw)
    if not path.exists():
        save_json_atomic(path, owner)
    return owner


def save_owner(robot_id, owner):
    save_json_atomic(owner_path(robot_id), normalize_owner(owner))


def normalize_user(raw):
    return {
        "user_id": str((raw or {}).get("user_id", "")).strip() or f"user-{secrets.token_hex(4)}",
        "name": str((raw or {}).get("name", "")).strip(),
        "contact": str((raw or {}).get("contact", "")).strip(),
        "role": str((raw or {}).get("role", "client")).strip() or "client",
    }


def load_users(robot_id):
    path = users_path(robot_id)
    raw = load_json(path, [])
    if not isinstance(raw, list):
        raw = []
    users = [normalize_user(item) for item in raw if isinstance(item, dict)]
    if not path.exists():
        save_json_atomic(path, users)
    return users


def save_users(robot_id, users):
    save_json_atomic(users_path(robot_id), [normalize_user(item) for item in (users or [])])


def add_user(robot_id, payload):
    user = normalize_user(payload)
    if not user["name"] and not user["contact"]:
        raise ValueError("user name or contact is required")
    users = load_users(robot_id)
    users.append(user)
    save_users(robot_id, users)
    return {"ok": True, "user": user, "users": users}


def delete_user(robot_id, user_id):
    user_id = str(user_id or "").strip()
    if not user_id:
        raise ValueError("user_id is required")
    users = load_users(robot_id)
    filtered = [user for user in users if user.get("user_id") != user_id]
    if len(filtered) == len(users):
        raise ValueError("user not found")
    save_users(robot_id, filtered)
    return {"ok": True, "users": filtered}


def load_mobile_codes():
    raw = load_json(MOBILE_CODES_PATH, {})
    return raw if isinstance(raw, dict) else {}


def save_mobile_codes(data):
    save_json_atomic(MOBILE_CODES_PATH, data)


def load_mobile_panel_tokens():
    raw = load_json(MOBILE_PANEL_TOKENS_PATH, {})
    return raw if isinstance(raw, dict) else {}


def save_mobile_panel_tokens(data):
    save_json_atomic(MOBILE_PANEL_TOKENS_PATH, data)


def normalize_mobile_code_entry(entry, fallback_robot_id=""):
    base = {
        "robot_id": str(fallback_robot_id or "").strip(),
        "created_at": 0,
        "activated_at": 0,
        "expires_at": 0,
        "revoked_at": 0,
        "revoked_reason": "",
    }
    if isinstance(entry, dict):
        base["robot_id"] = str(entry.get("robot_id", base["robot_id"])).strip()
        for key in ("created_at", "activated_at", "expires_at", "revoked_at"):
            try:
                base[key] = max(0, int(entry.get(key, base[key]) or 0))
            except Exception:
                pass
        base["revoked_reason"] = str(entry.get("revoked_reason", "")).strip()
    if not base["expires_at"] and MOBILE_CODE_TTL_SECONDS > 0 and base["created_at"] > 0:
        base["expires_at"] = base["created_at"] + MOBILE_CODE_TTL_SECONDS
    return base


def mobile_code_status(entry, now=None):
    normalized = normalize_mobile_code_entry(entry)
    timestamp = int(now or time.time())
    if normalized["revoked_at"] > 0:
        return "revoked"
    if normalized["activated_at"] > 0:
        return "used"
    if normalized["expires_at"] > 0 and normalized["expires_at"] <= timestamp:
        return "expired"
    return "ready"


def mobile_code_payload(code, entry, now=None):
    normalized = normalize_mobile_code_entry(entry)
    return {
        "code": str(code or "").strip().upper(),
        "robot_id": normalized["robot_id"],
        "created_at": normalized["created_at"],
        "activated_at": normalized["activated_at"],
        "expires_at": normalized["expires_at"],
        "revoked_at": normalized["revoked_at"],
        "revoked_reason": normalized["revoked_reason"],
        "status": mobile_code_status(normalized, now=now),
        "is_current": False,
    }


def revoke_mobile_code_record(record, reason="operator", timestamp=None):
    normalized = normalize_mobile_code_entry(record)
    if normalized["revoked_at"] > 0:
        return normalized
    normalized["revoked_at"] = int(timestamp or time.time())
    normalized["revoked_reason"] = str(reason or "operator").strip() or "operator"
    return normalized


def ensure_mobile_panel_token(robot_id):
    tokens = load_mobile_panel_tokens()
    current = tokens.get(robot_id)
    token = ""
    if isinstance(current, dict):
        token = str(current.get("token", "")).strip()
    if not token:
        token = secrets.token_urlsafe(24)
    tokens[robot_id] = {
        "token": token,
        "issued_at": int(time.time()),
    }
    save_mobile_panel_tokens(tokens)
    return token


def agent_gateway_status():
    return agent_gateway_client.health_snapshot()


def default_provider_profile_id():
    if GOSHA_BACKEND_PROXY_PROFILE_ID:
        profile = agent_store.get_agent_profile(GOSHA_BACKEND_PROXY_PROFILE_ID)
        if profile and profile.get("enabled"):
            return GOSHA_BACKEND_PROXY_PROFILE_ID
    for robot in list_robots():
        robot_id = str((robot or {}).get("robot_id", "") or "").strip()
        if not robot_id:
            continue
        try:
            binding = assistant_store.load_robot_binding(robot_id)
        except Exception:
            binding = {}
        for key in ("active_profile_id", "fallback_profile_id"):
            profile_id = str((binding or {}).get(key, "") or "").strip()
            if not profile_id:
                continue
            profile = agent_store.get_agent_profile(profile_id)
            if profile and profile.get("enabled"):
                return profile_id
    for item in agent_store.list_agent_profiles():
        if item.get("enabled"):
            return str(item.get("profile_id", "") or "").strip()
    return ""


def internal_openai_proxy_status():
    profile_id = default_provider_profile_id()
    profile = agent_store.get_agent_profile(profile_id) if profile_id else None
    return {
        "enabled": bool(GOSHA_INTERNAL_OPENAI_PROXY_TOKEN),
        "default_profile_id": profile_id,
        "default_profile": agent_store.profile_public_view(profile) if profile else None,
        "gateway": agent_gateway_status(),
    }


def _gateway_raw_request(path, *, method="POST", payload=None, headers=None, timeout=None):
    url = agent_gateway_client.gateway_base_url() + path
    body = None
    req_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    req = Request(url, data=body, headers=req_headers, method=method)
    try:
        with urlopen(req, timeout=timeout or GOSHA_INTERNAL_OPENAI_PROXY_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
            return int(resp.status), raw, dict(resp.headers.items())
    except HTTPError as exc:
        return int(exc.code), exc.read(), dict(exc.headers.items())
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc


def dedupe_openai_tools_payload(payload):
    if not isinstance(payload, dict):
        return payload
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return payload
    seen_names = set()
    filtered_tools = []
    for item in tools:
        if not isinstance(item, dict):
            filtered_tools.append(item)
            continue
        if str(item.get("type", "") or "").strip() != "function":
            filtered_tools.append(item)
            continue
        function = item.get("function")
        if not isinstance(function, dict):
            filtered_tools.append(item)
            continue
        tool_name = str(function.get("name", "") or "").strip()
        if not tool_name:
            filtered_tools.append(item)
            continue
        if tool_name in seen_names:
            continue
        seen_names.add(tool_name)
        filtered_tools.append(item)
    payload["tools"] = filtered_tools
    return payload


def proxy_internal_openai_request(path, payload=None):
    if not GOSHA_INTERNAL_OPENAI_PROXY_TOKEN:
        raise ValueError("internal proxy token is not configured")
    outbound = dict(payload or {})
    if path == "/v1/chat/completions":
        outbound = dedupe_openai_tools_payload(outbound)
        if not outbound.get("profile_id") and not outbound.get("robot_id"):
            profile_id = default_provider_profile_id()
            if not profile_id:
                raise ValueError("no enabled provider profile is configured for backend proxy")
            outbound["profile_id"] = profile_id
    status, raw, headers = _gateway_raw_request(
        path,
        method="POST" if payload is not None else "GET",
        payload=outbound if payload is not None else None,
        timeout=GOSHA_INTERNAL_OPENAI_PROXY_TIMEOUT_SECONDS,
    )
    return status, raw, headers


def list_agent_profiles():
    return [agent_store.profile_public_view(item) for item in agent_store.list_agent_profiles()]


def upsert_agent_profile(payload):
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    profile_id = str(payload.get("profile_id", "") or "").strip()
    if not agent_store.safe_profile_id(profile_id):
        raise ValueError("invalid profile_id")
    profile = agent_store.save_agent_profile(profile_id, payload)
    return {"ok": True, "profile": agent_store.profile_public_view(profile)}


def get_robot_agent_assignment(robot_id):
    if not safe_robot_id(robot_id):
        raise ValueError("invalid robot_id")
    summary = agent_store.effective_robot_agent(robot_id)
    assistant_control = assistant_store.effective_robot_assistant_config(robot_id)
    return {
        "ok": True,
        "gateway": agent_gateway_status(),
        "profiles": list_agent_profiles(),
        "assignment": summary,
        "assistant_control": assistant_control,
    }


def save_robot_agent_assignment(robot_id, active_profile_id, fallback_profile_id=""):
    if not safe_robot_id(robot_id):
        raise ValueError("invalid robot_id")
    require_robot_dir(robot_id)
    binding = agent_store.save_robot_binding(robot_id, active_profile_id, fallback_profile_id)
    updates = {
        "ROBOT_AGENT_PROFILE_ID": str(binding.get("active_profile_id", "") or "").strip(),
        "ROBOT_AGENT_FALLBACK_PROFILE_ID": str(binding.get("fallback_profile_id", "") or "").strip(),
    }
    save_env_updates(robot_env_path(robot_id), updates)
    return {
        "ok": True,
        "gateway": agent_gateway_status(),
        "assignment": agent_store.effective_robot_agent(robot_id),
        "assistant_control": assistant_store.effective_robot_assistant_config(robot_id),
    }


def assistant_control_catalog():
    snapshot = assistant_store.catalog_snapshot()
    return {
        "ok": True,
        "gateway": agent_gateway_status(),
        "internal_openai_proxy": internal_openai_proxy_status(),
        **snapshot,
    }


def list_assistant_profiles():
    return [assistant_store.public_assistant_profile(item) for item in assistant_store.list_assistant_profiles()]


def upsert_assistant_profile(payload):
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    profile_id = str(payload.get("profile_id", "") or "").strip()
    if not agent_store.safe_profile_id(profile_id):
        raise ValueError("invalid profile_id")
    profile = assistant_store.save_assistant_profile(profile_id, payload)
    apply_result = refresh_backend_runtime(f"assistant_profile:{profile_id}")
    return {
        "ok": True,
        "profile": assistant_store.public_assistant_profile(profile),
        "apply": apply_result,
    }


def list_tts_engine_profiles():
    return [assistant_store.public_tts_engine_profile(item) for item in assistant_store.list_tts_engine_profiles()]


def upsert_tts_engine_profile(payload):
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    profile_id = str(payload.get("profile_id", "") or "").strip()
    if not agent_store.safe_profile_id(profile_id):
        raise ValueError("invalid profile_id")
    profile = assistant_store.save_tts_engine_profile(profile_id, payload)
    apply_result = refresh_backend_runtime(f"tts_engine_profile:{profile_id}")
    return {
        "ok": True,
        "profile": assistant_store.public_tts_engine_profile(profile),
        "apply": apply_result,
    }


def list_voice_profiles():
    return [assistant_store.public_voice_profile(item) for item in assistant_store.list_voice_profiles()]


def upsert_voice_profile(payload):
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    profile_id = str(payload.get("profile_id", "") or "").strip()
    if not agent_store.safe_profile_id(profile_id):
        raise ValueError("invalid profile_id")
    profile = assistant_store.save_voice_profile(profile_id, payload)
    apply_result = refresh_backend_runtime(f"voice_profile:{profile_id}")
    return {
        "ok": True,
        "profile": assistant_store.public_voice_profile(profile),
        "apply": apply_result,
    }


def list_memory_profiles():
    return [assistant_store.public_memory_profile(item) for item in assistant_store.list_memory_profiles()]


def upsert_memory_profile(payload):
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    profile_id = str(payload.get("profile_id", "") or "").strip()
    if not agent_store.safe_profile_id(profile_id):
        raise ValueError("invalid profile_id")
    profile = assistant_store.save_memory_profile(profile_id, payload)
    return {"ok": True, "profile": assistant_store.public_memory_profile(profile)}


def list_mcp_bundles():
    return [assistant_store.public_mcp_bundle(item) for item in assistant_store.list_mcp_bundles()]


def upsert_mcp_bundle(payload):
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    profile_id = str(payload.get("profile_id", "") or "").strip()
    if not agent_store.safe_profile_id(profile_id):
        raise ValueError("invalid profile_id")
    profile = assistant_store.save_mcp_bundle(profile_id, payload)
    return {"ok": True, "profile": assistant_store.public_mcp_bundle(profile)}


def list_knowledge_profiles():
    return [assistant_store.public_knowledge_profile(item) for item in assistant_store.list_knowledge_profiles()]


def upsert_knowledge_profile(payload):
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    profile_id = str(payload.get("profile_id", "") or "").strip()
    if not agent_store.safe_profile_id(profile_id):
        raise ValueError("invalid profile_id")
    profile = assistant_store.save_knowledge_profile(profile_id, payload)
    return {"ok": True, "profile": assistant_store.public_knowledge_profile(profile)}


def list_screen_profiles():
    return [assistant_store.public_screen_profile(item) for item in assistant_store.list_screen_profiles()]


def upsert_screen_profile(payload):
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    profile_id = str(payload.get("profile_id", "") or "").strip()
    if not agent_store.safe_profile_id(profile_id):
        raise ValueError("invalid profile_id")
    profile = assistant_store.save_screen_profile(profile_id, payload)
    return {"ok": True, "profile": assistant_store.public_screen_profile(profile)}


def list_wake_profiles():
    return [assistant_store.public_wake_profile(item) for item in assistant_store.list_wake_profiles()]


def upsert_wake_profile(payload):
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    profile_id = str(payload.get("profile_id", "") or "").strip()
    if not agent_store.safe_profile_id(profile_id):
        raise ValueError("invalid profile_id")
    profile = assistant_store.save_wake_profile(profile_id, payload)
    return {"ok": True, "profile": assistant_store.public_wake_profile(profile)}


def get_robot_assistant_config(robot_id):
    if not safe_robot_id(robot_id):
        raise ValueError("invalid robot_id")
    require_robot_dir(robot_id)
    return {
        "ok": True,
        "gateway": agent_gateway_status(),
        "catalog": assistant_store.catalog_snapshot(),
        "config": assistant_store.effective_robot_assistant_config(robot_id),
    }


def save_robot_assistant_config(robot_id, payload):
    if not safe_robot_id(robot_id):
        raise ValueError("invalid robot_id")
    require_robot_dir(robot_id)
    binding = assistant_store.save_robot_binding(robot_id, payload if isinstance(payload, dict) else {})
    apply_result = refresh_backend_runtime(f"robot_assistant_config:{robot_id}")
    return {
        "ok": True,
        "gateway": agent_gateway_status(),
        "binding": binding,
        "config": assistant_store.effective_robot_assistant_config(robot_id),
        "apply": apply_result,
    }


def validate_mobile_panel_token(robot_id, token):
    current = load_mobile_panel_tokens().get(robot_id)
    expected = ""
    if isinstance(current, dict):
        expected = str(current.get("token", "")).strip()
    provided = str(token or "").strip()
    return bool(expected and provided and secrets.compare_digest(expected, provided))


def validate_mobile_access_code(robot_id, code):
    clean_code = str(code or "").strip().upper()
    if not clean_code:
        return False
    entry = load_mobile_codes().get(clean_code)
    if not isinstance(entry, dict):
        return False
    normalized = normalize_mobile_code_entry(entry, fallback_robot_id=str(entry.get("robot_id", "")).strip())
    if normalized["robot_id"] != robot_id:
        return False
    if normalized["revoked_at"] > 0:
        return False
    return normalized["activated_at"] > 0


def validate_mobile_access(robot_id, token=None, code=None):
    return validate_mobile_panel_token(robot_id, token) or validate_mobile_access_code(robot_id, code)


def mobile_codes_for_robot(robot_id):
    items = []
    current_assigned = False
    now = int(time.time())
    for code, entry in load_mobile_codes().items():
        if not isinstance(entry, dict):
            continue
        normalized = normalize_mobile_code_entry(entry, fallback_robot_id=str(entry.get("robot_id", "")).strip())
        if normalized["robot_id"] != robot_id:
            continue
        items.append(mobile_code_payload(code, normalized, now=now))
    items.sort(key=lambda item: item.get("created_at", 0), reverse=True)
    for item in items:
        if not current_assigned and item.get("status") == "ready":
            item["is_current"] = True
            current_assigned = True
    return items


def generate_onboarding_code(length=8, existing_codes=None):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    existing = existing_codes if isinstance(existing_codes, dict) else load_mobile_codes()
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(length))
        if code not in existing:
            return code


def onboarding_bundle(robot_id, code=None, include_panel_client_token=False):
    env = load_env(robot_env_path(robot_id))
    cfg = load_json(ROBOTS_DIR / robot_id / "mcp_config.json", {"mcpServers": {}})
    subscription = load_subscription(robot_id, servers=cfg.get("mcpServers", {}))
    owner = load_owner(robot_id)
    users = load_users(robot_id)
    backend_mode = robot_backend_mode(env)
    selfhost_bundle = selfhost_gateway_state() if backend_mode == selfhost_xiaozhi.BACKEND_MODE_SELF_HOSTED else None
    bundle = {
        "code": code,
        "panel_url": PUBLIC_PANEL_URL,
        "edge_hub_url": PUBLIC_EDGE_HUB_URL,
        "robot_id": robot_id,
        "robot_name": env.get("ROBOT_NAME", robot_id) or robot_id,
        "cloud_endpoint": get_robot_mcp_endpoint(robot_id),
        "backend_mode": backend_mode,
        "subscription": subscription,
        "owner": owner,
        "users": users,
        "instruction": "Введите код подключения. Данные клиента можно заполнить сразу или позже. После регистрации откройте шаг подключения робота к Wi‑Fi.",
    }
    mobile_profile = {
        "brand": "GOSHA",
        "panel_url": PUBLIC_PANEL_URL,
        "mcp_endpoint_base": "",
        "websocket_url": "",
        "portal_url": "http://192.168.4.1",
        "robot_wifi_prefixes": ["GOSHA-", "Xiaozhi-"],
        "preferred_backend_mode": selfhost_xiaozhi.BACKEND_MODE_SELF_HOSTED,
    }
    if isinstance(selfhost_bundle, dict):
        backend = selfhost_bundle.get("backend") or {}
        websocket_url = str(backend.get("websocket_url", "") or "")
        mcp_endpoint_base = str(backend.get("mcp_endpoint_base", "") or "")
        bundle["selfhost_xiaozhi"] = {
            "provider": selfhost_xiaozhi.BACKEND_MODE_SELF_HOSTED,
            "ota_url": str(backend.get("ota_url", "") or ""),
            "activate_url": str(backend.get("activate_url", "") or ""),
            "websocket_url": websocket_url,
            "mcp_endpoint_base": mcp_endpoint_base,
        }
        mobile_profile["websocket_url"] = websocket_url
        mobile_profile["mcp_endpoint_base"] = mcp_endpoint_base
    elif bundle["cloud_endpoint"]:
        cloud_endpoint = str(bundle["cloud_endpoint"]).strip()
        mobile_profile["mcp_endpoint_base"] = cloud_endpoint.split("?", 1)[0]
    bundle["mobile_profile"] = mobile_profile
    if include_panel_client_token:
        bundle["panel_client_token"] = ensure_mobile_panel_token(robot_id)
    return bundle


def create_mobile_onboarding_code(robot_id, robot_name=None, plan_code="start", endpoint=None, owner=None):
    if not safe_robot_id(robot_id):
        raise ValueError("invalid robot_id")
    robot_dir = ROBOTS_DIR / robot_id
    if not robot_dir.exists():
        create_robot(robot_id=robot_id, robot_name=robot_name, plan_code=plan_code, endpoint=endpoint, owner=owner or {})
    else:
        if robot_name:
            set_robot_name(robot_id, robot_name)
        if endpoint:
            set_robot_endpoint(robot_id, endpoint)
        if owner:
            save_owner(robot_id, owner)
        if plan_code:
            current = load_subscription(robot_id)
            merged = normalize_subscription({"plan_code": plan_code, "billing": current.get("billing", {}), "notes": current.get("notes", "")})
            save_subscription(robot_id, merged)
            apply_subscription_to_config(robot_id, merged)

    codes = load_mobile_codes()
    now = int(time.time())
    revoked_codes = []
    for existing_code, entry in list(codes.items()):
        if not isinstance(entry, dict):
            continue
        normalized = normalize_mobile_code_entry(entry, fallback_robot_id=str(entry.get("robot_id", "")).strip())
        if normalized["robot_id"] != robot_id:
            continue
        if mobile_code_status(normalized, now=now) == "ready":
            codes[existing_code] = revoke_mobile_code_record(normalized, reason="replaced", timestamp=now)
            revoked_codes.append(str(existing_code).strip().upper())

    code = generate_onboarding_code(existing_codes=codes)
    codes[code] = normalize_mobile_code_entry(
        {
            "robot_id": robot_id,
            "created_at": now,
            "activated_at": 0,
            "expires_at": now + MOBILE_CODE_TTL_SECONDS if MOBILE_CODE_TTL_SECONDS > 0 else 0,
            "revoked_at": 0,
            "revoked_reason": "",
        },
        fallback_robot_id=robot_id,
    )
    save_mobile_codes(codes)
    return {
        "ok": True,
        "code": code,
        "bundle": onboarding_bundle(robot_id, code=code),
        "revoked_codes": revoked_codes,
    }


def resolve_mobile_onboarding_code(code):
    code = str(code or "").strip().upper()
    if not code:
        raise ValueError("code is required")
    data = load_mobile_codes()
    entry = data.get(code)
    if not isinstance(entry, dict):
        raise ValueError("code not found")
    normalized = normalize_mobile_code_entry(entry, fallback_robot_id=str(entry.get("robot_id", "")).strip())
    robot_id = normalized["robot_id"]
    if not safe_robot_id(robot_id):
        raise ValueError("invalid robot_id in code")
    status = mobile_code_status(normalized)
    if status == "expired":
        raise ValueError("code expired")
    if status == "revoked":
        raise ValueError("code revoked")
    bundle = onboarding_bundle(robot_id, code=code, include_panel_client_token=False)
    bundle["activated_at"] = normalized["activated_at"]
    bundle["expires_at"] = normalized["expires_at"]
    bundle["code_status"] = status
    return {"ok": True, "bundle": bundle}


def activate_mobile_onboarding_code(code, owner=None):
    code = str(code or "").strip().upper()
    if not code:
        raise ValueError("code is required")
    data = load_mobile_codes()
    entry = data.get(code)
    if not isinstance(entry, dict):
        raise ValueError("code not found")
    normalized = normalize_mobile_code_entry(entry, fallback_robot_id=str(entry.get("robot_id", "")).strip())
    robot_id = normalized["robot_id"]
    if not safe_robot_id(robot_id):
        raise ValueError("invalid robot_id in code")
    status = mobile_code_status(normalized)
    if status == "expired":
        raise ValueError("code expired")
    if status == "revoked":
        raise ValueError("code revoked")
    if isinstance(owner, dict):
        current_owner = load_owner(robot_id)
        merged_owner = dict(current_owner)
        for key, value in normalize_owner(owner).items():
            if str(value or "").strip():
                merged_owner[key] = str(value).strip()
        save_owner(robot_id, merged_owner)
    if normalized["activated_at"] <= 0:
        normalized["activated_at"] = int(time.time())
    data[code] = normalized
    save_mobile_codes(data)
    return {
        "ok": True,
        "bundle": onboarding_bundle(robot_id, code=code, include_panel_client_token=True),
        "activated_at": normalized["activated_at"],
    }


def revoke_mobile_onboarding_code(robot_id, code, reason="operator"):
    clean_code = str(code or "").strip().upper()
    if not clean_code:
        raise ValueError("code is required")
    data = load_mobile_codes()
    entry = data.get(clean_code)
    if not isinstance(entry, dict):
        raise ValueError("code not found")
    normalized = normalize_mobile_code_entry(entry, fallback_robot_id=str(entry.get("robot_id", "")).strip())
    if normalized["robot_id"] != robot_id:
        raise ValueError("code does not belong to robot")
    if normalized["revoked_at"] <= 0:
        normalized = revoke_mobile_code_record(normalized, reason=reason)
        data[clean_code] = normalized
        save_mobile_codes(data)
    return {"ok": True, "code": clean_code, "entry": mobile_code_payload(clean_code, normalized)}


def apply_subscription_to_config(robot_id, subscription):
    cfg_path = ROBOTS_DIR / robot_id / "mcp_config.json"
    cfg = load_json(cfg_path, {})
    servers = cfg.setdefault("mcpServers", {})
    services = subscription.get("services", {})

    for service_name, tool_name in SERVICE_TOOL_MAP.items():
        if tool_name not in servers:
            continue
        enabled = bool(services.get(service_name, False))
        if enabled:
            servers[tool_name].pop("disabled", None)
        else:
            servers[tool_name]["disabled"] = True

    enforce_reserved_mcp_policy(servers)
    save_json_atomic(cfg_path, cfg)
    return cfg


def policy_status(subscription, servers):
    desired = subscription.get("services", {})
    actual = extract_services_from_servers(servers)
    mismatches = [name for name in USER_SERVICE_ORDER if bool(desired.get(name, False)) != bool(actual.get(name, False))]
    return {"applied": len(mismatches) == 0, "mismatches": mismatches, "actual": actual}


def set_robot_name(robot_id, robot_name):
    if robot_name is None:
        return
    clean = str(robot_name).strip()
    if not clean:
        clean = robot_id
    save_env_updates(robot_env_path(robot_id), {"ROBOT_NAME": clean})


def set_robot_endpoint(robot_id, endpoint):
    if endpoint is None:
        return
    value = str(endpoint).strip()
    if not value:
        return
    normalized = normalize_ws_url(value)
    mcp_endpoint_path(robot_id).write_text(normalized + "\n", encoding="utf-8")


def compose_local_ws_url(env):
    ws_url = env.get("ROBOT_DEVICE_WS_URL", "").strip()
    if ws_url:
        try:
            normalized = normalize_ws_url(ws_url)
        except Exception:
            normalized = ws_url
        try:
            parsed = urlparse(normalized)
        except Exception:
            parsed = None
        if parsed and is_mcp_ws_endpoint(normalized):
            normalized = ""
        if normalized:
            return normalized

    host = env.get("ROBOT_DEVICE_IP", "").strip() or env.get("ROBOT_DEVICE_HOST", "").strip()
    if not host:
        return ""
    scheme = env.get("ROBOT_DEVICE_WS_SCHEME", "ws").strip() or "ws"
    port = env.get("ROBOT_DEVICE_PORT", "8080").strip() or "8080"
    path = env.get("ROBOT_DEVICE_WS_PATH", "/ws").strip() or "/ws"
    if not path.startswith("/"):
        path = "/" + path
    return f"{scheme}://{host}:{port}{path}"


def infer_control_transport(robot_id, env):
    raw = (env.get("ROBOT_CONTROL_TRANSPORT", "") or "").strip().lower()
    if raw in CONTROL_TRANSPORTS:
        return raw

    raw_ws = (env.get("ROBOT_DEVICE_WS_URL", "") or "").strip()
    if raw_ws:
        try:
            parsed = urlparse(raw_ws)
        except Exception:
            parsed = None
        if parsed and is_mcp_ws_endpoint(raw_ws):
            return "cloud-mcp"
        if parsed and parsed.path.rstrip("/") == f"/control/{robot_id}":
            return "edge-hub"
        return "local-ws"

    if is_mcp_ws_endpoint(get_robot_mcp_endpoint(robot_id)) or get_robot_mcp_endpoint(robot_id):
        return "cloud-mcp"
    return "local-ws"


def get_control_config(robot_id):
    env = load_env(robot_env_path(robot_id))
    transport = infer_control_transport(robot_id, env)
    cloud_url = get_robot_mcp_endpoint(robot_id)
    device_url = compose_local_ws_url(env)
    backend_mode = robot_backend_mode(env)
    backend_summary = summarize_robot_backend(robot_id, env=env)

    if transport == "cloud-mcp":
        target = cloud_url
        editable = False
        source = "mcp_endpoint.txt"
    else:
        target = device_url
        editable = True
        source = "robot.env"

    return {
        "robot_name": env.get("ROBOT_NAME", robot_id) or robot_id,
        "backend_mode": backend_mode,
        "transport": transport,
        "target": target,
        "ws_url": target,
        "configured": bool(target),
        "editable_target": True,
        "source": source,
        "cloud_endpoint": cloud_url,
        "fallback_ws_url": device_url,
        "backend_summary": backend_summary,
    }


def set_control_config(robot_id, transport=None, ws_url=None, cloud_endpoint=None, robot_name=None, backend_mode=None):
    env = load_env(robot_env_path(robot_id))
    current = get_control_config(robot_id)
    selected = (transport or current.get("transport") or "").strip().lower()
    if selected not in CONTROL_TRANSPORTS:
        raise ValueError(f"invalid transport: {selected}")

    updates = {"ROBOT_CONTROL_TRANSPORT": selected}
    chosen_backend_mode = str(backend_mode or current.get("backend_mode") or "").strip().lower()
    if chosen_backend_mode == selfhost_xiaozhi.BACKEND_MODE_SELF_HOSTED:
        updates["ROBOT_BACKEND_MODE"] = selfhost_xiaozhi.BACKEND_MODE_SELF_HOSTED
    else:
        updates["ROBOT_BACKEND_MODE"] = selfhost_xiaozhi.BACKEND_MODE_XIAOZHI_CLOUD
    if robot_name is not None:
        set_robot_name(robot_id, robot_name)

    if cloud_endpoint is not None:
        value = str(cloud_endpoint).strip()
        if value:
            set_robot_endpoint(robot_id, value)
        else:
            try:
                mcp_endpoint_path(robot_id).unlink(missing_ok=True)
            except Exception:
                pass

    if selected != "cloud-mcp":
        candidate = str(ws_url or "").strip()
        if candidate:
            updates["ROBOT_DEVICE_WS_URL"] = normalize_ws_url(candidate)
        elif not current.get("fallback_ws_url"):
            raise ValueError("ws_url is required for edge-hub/local-ws mode")

    save_env_updates(robot_env_path(robot_id), updates)
    return {"ok": True, "config": get_control_config(robot_id)}


def probe_ws_endpoint(ws_url, timeout=2.0):
    if not ws_url:
        return {"state": "missing", "error": ""}
    if ws_connect is None:
        return {"state": "unknown", "error": "websockets library is not installed in panel runtime"}
    try:
        with ws_connect(
            ws_url,
            open_timeout=timeout,
            close_timeout=1,
            ping_interval=None,
            compression=None,
            max_size=256_000,
        ):
            pass
        return {"state": "reachable", "error": ""}
    except Exception as exc:
        return {"state": "unreachable", "error": str(exc)}


def new_probe_request_id():
    return secrets.randbits(31)


def safe_unreachable_detail():
    return (
        "Нет ответа. Возможные причины: робот выключен, нет сети, "
        "неверно настроены MCP-адрес или ключ доступа, "
        "не запущен клиент робота."
    )


def safe_auth_error_detail():
    return (
        "Ошибка доступа. Панель не смогла выполнить безопасную проверку MCP: "
        "проверьте авторизацию, MCP-адрес и права доступа."
    )


def safe_mcp_client_missing_detail():
    return (
        "MCP-клиент робота не подключён. Рабочий мост или среда выполнения сейчас "
        "не держат активную сессию MCP для этого робота."
    )


def safe_method_not_supported_detail(method_name):
    return (
        f"Диагностический метод не поддерживается: {method_name}. "
        "Нужен другой безопасный метод MCP только для чтения или обновление "
        "MCP на стороне робота."
    )


def mcp_bridge_not_robot_detail():
    return (
        "Облачный мост MCP доступен, но физический робот не подтвердил ответ. "
        "Для статуса «Подтверждённо в сети» нужна полноценная проверка MCP "
        "с новым request_id и совпадающим результатом JSON-RPC."
    )


def base_probe(kind, request_id=None):
    probe = {
        "kind": kind,
        "request_id": request_id,
        "method": "",
        "protocol_phase": "",
        "lifecycle_path": "",
        "state": "unknown",
        "verified_now": False,
        "reached_robot": False,
        "detail": "Проверка связи по MCP пока не дала уверенного результата.",
        "error": "",
        "error_type": "",
        "duration_ms": 0,
        "next_step": "",
        "tools_count": None,
    }
    return probe


def jsonrpc_tools_list_payload(request_id):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/list",
        "params": {"cursor": ""},
    }


def jsonrpc_tools_call_payload(request_id, tool_name, arguments=None):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments or {},
        },
    }


def jsonrpc_initialize_request_payload(request_id):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "ai-robot-panel", "version": "1"},
        },
    }


def jsonrpc_result_payload(request_id, result):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


def jsonrpc_initialize_result_payload(request_id):
    return jsonrpc_result_payload(
        request_id,
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "ai-robot-panel", "version": "1"},
        },
    )


def jsonrpc_initialized_notification_payload():
    return {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }


def finalize_probe(probe, started_at):
    probe["duration_ms"] = max(0, int((time.monotonic() - started_at) * 1000))
    return probe


def set_probe_failure(probe, started_at, *, state, error, detail, next_step="", error_type=None):
    probe["state"] = state
    probe["error"] = error
    probe["error_type"] = error_type or error
    probe["detail"] = detail
    probe["next_step"] = next_step
    return finalize_probe(probe, started_at)


def classify_auth_error(text):
    value = str(text or "").lower()
    return any(token in value for token in ("401", "403", "unauthorized", "forbidden", "auth"))


def classify_method_not_supported(text):
    value = str(text or "").lower()
    return any(
        token in value
        for token in (
            "method not implemented",
            "method not found",
            "unknown tool",
            "not supported",
            "not implemented",
        )
    )


def unwrap_jsonrpc_message(raw):
    try:
        obj = json.loads(raw) if isinstance(raw, str) else {}
    except Exception:
        return {}
    if not isinstance(obj, dict):
        return {}
    payload = obj.get("payload")
    if isinstance(payload, dict) and (payload.get("jsonrpc") or payload.get("id") is not None):
        return payload
    return obj


def apply_tools_list_response(probe, obj, request_id):
    if not isinstance(obj, dict) or obj.get("id") != request_id:
        return False

    result = obj.get("result")
    if isinstance(result, dict):
        tools = result.get("tools")
        probe["state"] = "verified_online"
        probe["verified_now"] = True
        probe["reached_robot"] = True
        probe["tools_count"] = len(tools) if isinstance(tools, list) else None
        probe["error_type"] = ""
        probe["next_step"] = "Повторная проверка не нужна: робот ответил на безопасную проверку MCP."
        suffix = f", сервисов: {probe['tools_count']}" if probe["tools_count"] is not None else ""
        probe["detail"] = (
            f"Подтверждённо в сети: робот ответил на прямую диагностическую проверку "
            f"с request_id={request_id}{suffix}."
        )
        return True

    if isinstance(obj.get("error"), dict):
        probe["state"] = "unreachable"
        probe["error"] = "jsonrpc_error"
        probe["error_type"] = "jsonrpc_error"
        probe["detail"] = safe_unreachable_detail()
        probe["next_step"] = "Проверьте питание робота, сеть и настройки MCP, затем повторите проверку."
        return True

    probe["state"] = "unreachable"
    probe["error"] = "invalid_jsonrpc_result"
    probe["error_type"] = "invalid_jsonrpc_result"
    probe["detail"] = safe_unreachable_detail()
    probe["next_step"] = "Повторите проверку MCP. Если ответ снова некорректен, проверьте мост и среду выполнения."
    return True


def apply_tools_call_response(probe, obj, request_id):
    if not isinstance(obj, dict) or obj.get("id") != request_id:
        return False

    result = obj.get("result")
    if isinstance(result, dict):
        probe["state"] = "verified_online"
        probe["verified_now"] = True
        probe["reached_robot"] = True
        probe["error"] = ""
        probe["error_type"] = ""
        probe["next_step"] = "Робот ответил на проверку MCP. Дополнительных действий не требуется."
        probe["detail"] = (
            f"Подтверждённо в сети: проверка MCP вернула подтверждение с request_id={request_id} "
            f"через безопасный метод {probe.get('method') or 'tools/call'}."
        )
        return True

    error_obj = obj.get("error")
    if isinstance(error_obj, dict):
        message = str(error_obj.get("message", "") or "").strip()
        if classify_auth_error(message):
            probe["state"] = "auth_error"
            probe["error"] = "auth_error"
            probe["error_type"] = "auth_error"
            probe["detail"] = safe_auth_error_detail()
            probe["next_step"] = "Проверьте авторизацию и MCP-адрес, затем повторите проверку."
            return True
        if classify_method_not_supported(message):
            probe["state"] = "method_not_supported"
            probe["error"] = "method_not_supported"
            probe["error_type"] = "method_not_supported"
            probe["detail"] = safe_method_not_supported_detail(probe.get("method") or "tools/call")
            probe["next_step"] = "Используйте другой безопасный диагностический метод или обновите MCP на стороне робота."
            return True
        probe["state"] = "unreachable"
        probe["error"] = "jsonrpc_error"
        probe["error_type"] = "jsonrpc_error"
        probe["detail"] = safe_unreachable_detail()
        probe["next_step"] = "Проверьте сеть и MCP-клиент робота, затем повторите проверку."
        return True

    probe["state"] = "unreachable"
    probe["error"] = "invalid_jsonrpc_result"
    probe["error_type"] = "invalid_jsonrpc_result"
    probe["detail"] = safe_unreachable_detail()
    probe["next_step"] = "Повторите проверку MCP. Если ответ снова некорректен, проверьте мост и среду выполнения."
    return True


def mcp_tools_list_probe(ws_url, *, kind, timeout, envelope=False):
    request_id = new_probe_request_id()
    probe = base_probe(kind, request_id=request_id)
    probe["method"] = "tools/list"
    started_at = time.monotonic()

    if not ws_url:
        probe["state"] = "missing"
        probe["detail"] = "Проверка недоступна: канал управления не настроен."
        probe["error_type"] = "missing"
        probe["next_step"] = "Сначала настройте канал управления для этого робота."
        return finalize_probe(probe, started_at)

    if ws_connect is None:
        probe["state"] = "unknown"
        probe["error"] = "websockets_missing"
        probe["detail"] = "Проверка недоступна: в среде выполнения панели нет библиотеки `websockets`."
        probe["error_type"] = "websockets_missing"
        probe["next_step"] = "Проверьте среду выполнения панели: библиотека `websockets` должна быть установлена."
        return finalize_probe(probe, started_at)

    payload = jsonrpc_tools_list_payload(request_id)
    outgoing = {"type": "mcp", "payload": payload} if envelope else payload
    saw_message = False
    try:
        with ws_connect(
            ws_url,
            open_timeout=timeout,
            close_timeout=1,
            ping_interval=None,
            compression=None,
            max_size=512_000,
        ) as ws:
            ws.send(json.dumps(outgoing, ensure_ascii=False))
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                raw = ws.recv(timeout=remaining)
                saw_message = True
                obj = unwrap_jsonrpc_message(raw)
                if apply_tools_list_response(probe, obj, request_id):
                    return finalize_probe(probe, started_at)
    except TimeoutError:
        return set_probe_failure(
            probe,
            started_at,
            state="unreachable",
            error="invalid_or_mismatched_ack" if saw_message else "timeout",
            detail=safe_unreachable_detail(),
            next_step="Проверьте сеть и клиент на стороне робота, затем повторите проверку MCP.",
        )
    except Exception as exc:
        text = str(exc or "")
        if classify_auth_error(text):
            return set_probe_failure(
                probe,
                started_at,
                state="auth_error",
                error="auth_error",
                error_type="auth_error",
                detail=safe_auth_error_detail(),
                next_step="Проверьте авторизацию и MCP-адрес, затем повторите проверку MCP.",
            )
        return set_probe_failure(
            probe,
            started_at,
            state="unreachable",
            error="request_failed",
            detail=safe_unreachable_detail(),
            next_step="Проверьте сеть и MCP-клиент робота, затем повторите проверку MCP.",
        )

    return set_probe_failure(
        probe,
        started_at,
        state="unreachable",
        error="invalid_or_mismatched_ack" if saw_message else "timeout",
        detail=safe_unreachable_detail(),
        next_step="Повторите проверку MCP. Если подтверждение не приходит, проверьте среду выполнения и авторизацию.",
    )


def direct_robot_tools_list_probe(ws_url, *, kind, timeout):
    raw_probe = mcp_tools_list_probe(ws_url, kind=kind, timeout=timeout, envelope=False)
    if raw_probe.get("verified_now"):
        return raw_probe

    envelope_probe = mcp_tools_list_probe(
        ws_url,
        kind=f"{kind}-envelope",
        timeout=timeout,
        envelope=True,
    )
    if envelope_probe.get("verified_now"):
        return envelope_probe
    if raw_probe.get("state") == "missing" or raw_probe.get("error") == "websockets_missing":
        return raw_probe
    envelope_probe["first_attempt_error"] = raw_probe.get("error", "")
    return envelope_probe


def cloud_mcp_bridge_probe(ws_url, *, timeout, robot_id=""):
    probe = base_probe("cloud-mcp-tools-call")
    probe["method"] = "self.otto.get_status"
    probe["protocol_phase"] = "connect"
    probe["lifecycle_path"] = "client_initiated"
    started_at = time.monotonic()

    def trace(event, **fields):
        payload = {
            "robot_id": robot_id,
            "request_id": probe.get("request_id"),
            "method": probe.get("method"),
            "state": probe.get("state"),
            "error": probe.get("error"),
            "error_type": probe.get("error_type"),
            "protocol_phase": probe.get("protocol_phase"),
            "lifecycle_path": probe.get("lifecycle_path"),
        }
        payload.update(fields)
        panel_event(event, **payload)

    trace("cloud_probe_start")
    if not ws_url:
        failed = set_probe_failure(
            probe,
            started_at,
            state="missing",
            error="missing",
            error_type="missing",
            detail="Проверка недоступна: облачный MCP-адрес не настроен.",
            next_step="Сначала заполните рабочий MCP-адрес для этого робота.",
        )
        trace("cloud_probe_finish", state=failed.get("state"), error=failed.get("error"), error_type=failed.get("error_type"))
        return failed

    if ws_connect is None:
        failed = set_probe_failure(
            probe,
            started_at,
            state="unknown",
            error="websockets_missing",
            error_type="websockets_missing",
            detail="Проверка недоступна: в среде выполнения панели нет библиотеки `websockets`.",
            next_step="Проверьте среду выполнения панели: библиотека `websockets` должна быть установлена.",
        )
        trace("cloud_probe_finish", state=failed.get("state"), error=failed.get("error"), error_type=failed.get("error_type"))
        return failed

    initialize_request_id = new_probe_request_id()
    call_request_id = new_probe_request_id()
    probe["request_id"] = call_request_id
    initialize_payload = jsonrpc_initialize_request_payload(initialize_request_id)
    initialized_payload = jsonrpc_initialized_notification_payload()
    call_payload = jsonrpc_tools_call_payload(call_request_id, "self.otto.get_status", {})
    initialize_sent = False
    initialize_acked = False
    initialized_notice_sent = False
    request_sent = False
    saw_message = False
    remote_initialized = False
    remote_notification = False
    tools_list_served = False

    def send_initialized_notice(ws):
        nonlocal initialized_notice_sent
        if initialized_notice_sent:
            return
        ws.send(json.dumps(initialized_payload, ensure_ascii=False))
        initialized_notice_sent = True
        probe["protocol_phase"] = "notifications_initialized_sent"
        trace("cloud_probe_phase")

    def send_call_request(ws):
        nonlocal request_sent
        if request_sent:
            return
        ws.send(json.dumps(call_payload, ensure_ascii=False))
        request_sent = True
        probe["protocol_phase"] = "tools_call_sent"
        trace("cloud_probe_phase")

    try:
        with ws_connect(
            ws_url,
            open_timeout=timeout,
            close_timeout=1,
            ping_interval=None,
            compression=None,
            max_size=512_000,
        ) as ws:
            ws.send(json.dumps(initialize_payload, ensure_ascii=False))
            initialize_sent = True
            probe["protocol_phase"] = "initialize_sent"
            trace("cloud_probe_phase")
            deadline = time.monotonic() + max(timeout, 6.0)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    raw = ws.recv(timeout=min(remaining, 2.0))
                except TimeoutError:
                    continue
                saw_message = True
                obj = unwrap_jsonrpc_message(raw)
                if not isinstance(obj, dict):
                    continue
                if apply_tools_call_response(probe, obj, call_request_id):
                    probe["protocol_phase"] = "tools_call_ack"
                    trace("cloud_probe_phase")
                    finished = finalize_probe(probe, started_at)
                    trace(
                        "cloud_probe_finish",
                        state=finished.get("state"),
                        verified_now=finished.get("verified_now"),
                        reached_robot=finished.get("reached_robot"),
                        duration_ms=finished.get("duration_ms"),
                    )
                    return finished
                method = str(obj.get("method", "") or "")
                incoming_id = obj.get("id")
                if incoming_id == initialize_request_id:
                    if isinstance(obj.get("result"), dict):
                        initialize_acked = True
                        probe["protocol_phase"] = "initialize_ack"
                        if probe.get("lifecycle_path") != "remote_initiated":
                            probe["lifecycle_path"] = "client_initiated"
                        trace("cloud_probe_phase", message_method="initialize")
                        send_initialized_notice(ws)
                        send_call_request(ws)
                        continue
                    if isinstance(obj.get("error"), dict):
                        message = str((obj.get("error") or {}).get("message", "") or "").strip()
                        if classify_auth_error(message):
                            failed = set_probe_failure(
                                probe,
                                started_at,
                                state="auth_error",
                                error="auth_error",
                                error_type="auth_error",
                                detail=safe_auth_error_detail(),
                                next_step="Проверьте авторизацию и MCP-адрес, затем повторите проверку MCP.",
                            )
                            trace("cloud_probe_finish", state=failed.get("state"), error=failed.get("error"), error_type=failed.get("error_type"))
                            return failed
                        failed = set_probe_failure(
                            probe,
                            started_at,
                            state="unreachable",
                            error="initialize_failed",
                            error_type="initialize_failed",
                            detail=safe_unreachable_detail(),
                            next_step="Проверьте рабочую среду MCP: начальная инициализация не завершилась.",
                        )
                        trace("cloud_probe_finish", state=failed.get("state"), error=failed.get("error"), error_type=failed.get("error_type"))
                        return failed
                if method == "initialize" and incoming_id is not None:
                    ws.send(json.dumps(jsonrpc_initialize_result_payload(incoming_id), ensure_ascii=False))
                    remote_initialized = True
                    probe["lifecycle_path"] = "remote_initiated"
                    probe["protocol_phase"] = "initialize_remote"
                    trace("cloud_probe_phase", message_method=method)
                    continue
                if method == "notifications/initialized":
                    remote_notification = True
                    if remote_initialized or not initialize_acked:
                        probe["lifecycle_path"] = "remote_initiated"
                    probe["protocol_phase"] = "notifications_initialized_remote"
                    trace("cloud_probe_phase", message_method=method)
                    if initialize_acked:
                        send_initialized_notice(ws)
                    send_call_request(ws)
                    continue
                if method == "ping" and incoming_id is not None:
                    ws.send(json.dumps(jsonrpc_result_payload(incoming_id, {}), ensure_ascii=False))
                    continue
                if method == "tools/list" and incoming_id is not None:
                    ws.send(json.dumps(jsonrpc_result_payload(incoming_id, {"tools": []}), ensure_ascii=False))
                    tools_list_served = True
                    if remote_initialized or remote_notification or not initialize_acked:
                        probe["lifecycle_path"] = "remote_initiated"
                    probe["protocol_phase"] = "tools_list_remote"
                    trace("cloud_probe_phase", message_method=method)
                    if initialize_acked:
                        send_initialized_notice(ws)
                    send_call_request(ws)
                    continue
                if initialize_acked and not request_sent:
                    send_initialized_notice(ws)
                    send_call_request(ws)
    except Exception as exc:
        text = str(exc or "")
        if classify_auth_error(text):
            failed = set_probe_failure(
                probe,
                started_at,
                state="auth_error",
                error="auth_error",
                error_type="auth_error",
                detail=safe_auth_error_detail(),
                next_step="Проверьте авторизацию и MCP-адрес, затем повторите проверку MCP.",
            )
            trace(
                "cloud_probe_exception",
                exception_type=exc.__class__.__name__,
                state=failed.get("state"),
                error=failed.get("error"),
                error_type=failed.get("error_type"),
            )
            return failed
        failed = set_probe_failure(
            probe,
            started_at,
            state="unreachable",
            error="request_failed",
            detail=safe_unreachable_detail(),
            next_step="Проверьте сеть и MCP-клиент робота, затем повторите проверку MCP.",
        )
        trace(
            "cloud_probe_exception",
            exception_type=exc.__class__.__name__,
            state=failed.get("state"),
            error=failed.get("error"),
            error_type=failed.get("error_type"),
        )
        return failed

    if request_sent:
        probe["protocol_phase"] = "waiting_tools_call_ack"
    elif initialize_acked:
        probe["protocol_phase"] = "waiting_post_initialize"
    elif remote_initialized or remote_notification or tools_list_served:
        probe["protocol_phase"] = "waiting_remote_lifecycle"
    elif initialize_sent:
        probe["protocol_phase"] = "waiting_initialize_ack"

    if request_sent or initialize_acked or remote_initialized or remote_notification or tools_list_served:
        failed = set_probe_failure(
            probe,
            started_at,
            state="unreachable",
            error="timeout" if request_sent or initialize_acked else "no_ack",
            error_type="timeout" if request_sent or initialize_acked else "no_ack",
            detail=safe_unreachable_detail(),
            next_step="Робот не вернул подтверждение на безопасную проверку MCP. Проверьте последовательность инициализации MCP, Wi-Fi, MCP-клиент и рабочий мост.",
        )
        trace(
            "cloud_probe_finish",
            state=failed.get("state"),
            error=failed.get("error"),
            error_type=failed.get("error_type"),
            duration_ms=failed.get("duration_ms"),
        )
        return failed

    if saw_message:
        failed = set_probe_failure(
            probe,
            started_at,
            state="mcp_client_missing",
            error="mcp_client_missing",
            error_type="mcp_client_missing",
            detail=safe_mcp_client_missing_detail(),
            next_step="Проверьте `ai-robot-bridge` и среду выполнения MCP, затем повторите проверку.",
        )
        trace(
            "cloud_probe_finish",
            state=failed.get("state"),
            error=failed.get("error"),
            error_type=failed.get("error_type"),
            duration_ms=failed.get("duration_ms"),
        )
        return failed

    failed = set_probe_failure(
        probe,
        started_at,
        state="unknown",
        error="unknown",
        error_type="unknown",
        detail="Проверка связи по MCP не дала ответа от рабочей среды MCP.",
        next_step="Проверьте `ai-robot-bridge`, MCP-адрес и доступность облачного канала.",
    )
    trace(
        "cloud_probe_finish",
        state=failed.get("state"),
        error=failed.get("error"),
        error_type=failed.get("error_type"),
        duration_ms=failed.get("duration_ms"),
    )
    return failed


def load_edge_pairings():
    if not EDGE_PAIRINGS_PATH.exists():
        return {}
    try:
        raw = json.loads(EDGE_PAIRINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    if isinstance(raw, dict):
        for robot_id, cfg in raw.items():
            if isinstance(cfg, str):
                token = str(cfg).strip()
            elif isinstance(cfg, dict):
                token = str(cfg.get("token", "")).strip()
            else:
                token = ""
            if token:
                out[str(robot_id).strip()] = token
    return out


def get_edge_pairing_token(robot_id):
    return str(load_edge_pairings().get(robot_id, "") or "").strip()


def local_edge_hub_ws_url():
    parsed = urlparse(EDGE_HUB_LOCAL_URL)
    if not parsed.netloc:
        return ""
    scheme = "wss" if parsed.scheme == "https" else "ws"
    base_path = parsed.path.rstrip("/")
    return f"{scheme}://{parsed.netloc}{base_path}"


def edge_hub_control_ws_url(robot_id, token):
    base = local_edge_hub_ws_url()
    if not base:
        return ""
    return f"{base}/control/{robot_id}?token={token}"


def probe_edge_hub_control(robot_id, timeout=EDGE_CONTROL_PROBE_TIMEOUT_SECONDS):
    probe = {
        "kind": "edge-hub-tools-list",
        "request_id": None,
        "state": "unknown",
        "verified_now": False,
        "reached_robot": False,
        "detail": "",
        "error": "",
        "tools_count": None,
    }

    token = get_edge_pairing_token(robot_id)
    if not token:
        probe["state"] = "missing"
        probe["error"] = "pairing_token_missing"
        probe["detail"] = "Проверка недоступна: ключ доступа для `edge-hub` не настроен."
        return probe

    if ws_connect is None:
        probe["state"] = "unknown"
        probe["error"] = "websockets_missing"
        probe["detail"] = "Проверка недоступна: в среде выполнения панели нет библиотеки `websockets`."
        return probe

    control_url = edge_hub_control_ws_url(robot_id, token)
    if not control_url:
        probe["state"] = "missing"
        probe["error"] = "edge_hub_control_missing"
        probe["detail"] = "Проверка недоступна: канал управления `edge-hub` не настроен."
        return probe

    return mcp_tools_list_probe(control_url, kind="edge-hub-tools-list", timeout=timeout)


def fetch_edge_snapshot():
    snapshot = {
        "hub_state": "offline",
        "hub_error": "",
        "agents": {},
    }

    health = http_get_json(f"{EDGE_HUB_LOCAL_URL}/healthz", timeout=2.0)
    if health.get("ok"):
        snapshot["hub_state"] = "online"
    else:
        snapshot["hub_error"] = health.get("error", "")
        return snapshot

    agents_res = http_get_json(f"{EDGE_HUB_LOCAL_URL}/api/agents", timeout=2.5)
    if not agents_res.get("ok"):
        snapshot["hub_state"] = "degraded"
        snapshot["hub_error"] = agents_res.get("error", "")
        return snapshot

    for item in (agents_res.get("data", {}) or {}).get("agents", []):
        robot_id = str(item.get("robot_id", "")).strip()
        if robot_id:
            snapshot["agents"][robot_id] = item
    return snapshot


def build_link_diagnostics(robot_id, control_cfg, edge_snapshot):
    target = control_cfg.get("target") or ""
    mode = control_cfg.get("transport") or "missing"
    diagnostics = {
        "mode": mode,
        "target": target,
        "source": control_cfg.get("source") or "",
        "hub_state": "n/a",
        "hub_error": "",
        "agent_state": "n/a",
        "agent_last_seen": None,
        "agent_meta": {},
        "transport_state": "unknown",
        "last_error": "",
    }

    if mode == "missing":
        diagnostics["transport_state"] = "missing"
        return diagnostics

    if mode == "cloud-mcp":
        diagnostics["transport_state"] = "configured" if target else "missing"
        return diagnostics

    if mode == "edge-hub":
        diagnostics["hub_state"] = edge_snapshot.get("hub_state", "offline")
        diagnostics["hub_error"] = edge_snapshot.get("hub_error", "")
        agent = (edge_snapshot.get("agents") or {}).get(robot_id)
        if not agent:
            diagnostics["agent_state"] = "offline"
            diagnostics["transport_state"] = "offline"
            diagnostics["last_error"] = "edge agent is offline"
            return diagnostics

        diagnostics["agent_meta"] = agent.get("meta") or {}
        diagnostics["agent_last_seen"] = agent.get("last_seen")
        last_seen = int(agent.get("last_seen") or 0)
        age = max(0, int(time.time()) - last_seen) if last_seen else None
        diagnostics["agent_state"] = "stale" if age is not None and age > EDGE_AGENT_STALE_SECONDS else "seen"

        status = agent.get("status") or {}
        ok = status.get("robot_ws_ok")
        if ok is True:
            diagnostics["transport_state"] = "reported-ready"
        elif ok is False:
            diagnostics["transport_state"] = "reported-unreachable"
        else:
            diagnostics["transport_state"] = "unknown"
        diagnostics["last_error"] = str(status.get("robot_ws_error", "") or "")
        return diagnostics

    diagnostics["transport_state"] = "configured" if target else "missing"
    return diagnostics


def build_robot_record(robot_id, edge_snapshot=None):
    require_robot_dir(robot_id)
    if edge_snapshot is None:
        edge_snapshot = fetch_edge_snapshot()

    env = load_env(robot_env_path(robot_id))
    config_path = ROBOTS_DIR / robot_id / "mcp_config.json"
    config = load_json(config_path, {"mcpServers": {}})
    servers = config.get("mcpServers", {})
    subscription = load_subscription(robot_id, servers=servers)
    policy = policy_status(subscription, servers)
    owner = load_owner(robot_id)
    mobile_codes = mobile_codes_for_robot(robot_id)
    current_mobile_code = next((item for item in mobile_codes if item.get("is_current")), None)
    tools = []
    for tool_name in sorted(servers.keys()):
        tool_cfg = servers.get(tool_name, {})
        tools.append(
            {
                "name": tool_name,
                "enabled": not bool(tool_cfg.get("disabled", False)),
            }
        )
    mem_enabled = True
    if "memory-tools" in servers and servers["memory-tools"].get("disabled", False):
        mem_enabled = False
    control_cfg = get_control_config(robot_id)
    diagnostics = build_link_diagnostics(robot_id, control_cfg, edge_snapshot)
    endpoint_ready = is_endpoint_configured(robot_id)
    service = service_state(robot_id)
    activity = summarize_tool_activity(robot_id, servers)
    cloud_console = summarize_robot_backend(robot_id, env=env)
    runtime_class = robot_runtime_class(robot_id, env=env)
    fleet = build_fleet_readiness(robot_id, runtime_class, endpoint_ready, service, cloud_console)
    detection = load_detection_snapshot(robot_id, fallback_mode=str(diagnostics.get("mode", "") or ""))
    mobile_presence = load_mobile_presence_snapshot(robot_id)
    activity_presence = summarize_activity_presence(activity)
    agent_assignment = agent_store.effective_robot_agent(robot_id)
    assistant_control = assistant_store.effective_robot_assistant_config(robot_id)

    return {
        "robot_id": robot_id,
        "robot_name": env.get("ROBOT_NAME", robot_id) or robot_id,
        "runtime_class": runtime_class,
        "fleet_state": fleet["state"],
        "fleet": fleet,
        "panel_visible": runtime_class == RUNTIME_CLASS_RUNTIME,
        "service_state": service,
        "endpoint_configured": endpoint_ready,
        "memory_enabled": mem_enabled,
        "memory": memory_stats(robot_id),
        "activity": activity,
        "activity_presence": activity_presence,
        "cloud_console": cloud_console,
        "backend_mode": robot_backend_mode(env),
        "detection": detection,
        "mobile_presence": mobile_presence,
        "tools": tools,
        "control": control_cfg,
        "agent": agent_assignment,
        "assistant_control": assistant_control,
        "diagnostics": diagnostics,
        "subscription": subscription,
        "owner": owner,
        "mobile_codes": mobile_codes,
        "current_mobile_code": current_mobile_code,
        "latest_mobile_code": mobile_codes[0] if mobile_codes else None,
        "admin_status": {
            "registered": True,
            "endpoint_issued": endpoint_ready,
            "bridge_online": service == "active",
            "tools_policy_applied": policy["applied"],
            "policy_mismatches": policy["mismatches"],
        },
    }


def probe_robot_record(robot_id):
    edge_snapshot = fetch_edge_snapshot()
    record = build_robot_record(robot_id, edge_snapshot=edge_snapshot)
    mode = str((record.get("diagnostics") or {}).get("mode", "") or "")
    panel_event(
        "robot_detect_start",
        robot_id=robot_id,
        mode=mode or "missing",
        service_state=record.get("service_state"),
        fleet_state=record.get("fleet_state"),
    )
    checked_at = now_ts()
    last_seen = (record.get("diagnostics") or {}).get("agent_last_seen")
    probe = {
        "checked_at": checked_at,
        "checked_at_iso": ts_to_iso(checked_at),
        "last_seen": last_seen,
        "mode": mode or "missing",
        "kind": "snapshot",
        "request_id": None,
        "state": "unknown",
        "verified_now": False,
        "reached_robot": False,
        "detail": "",
        "error": "",
    }

    if mode == "cloud-mcp":
        if record.get("service_state") != "active":
            probe.update(
                {
                    "kind": "cloud-mcp-tools-call",
                    "method": "self.otto.get_status",
                    "state": "mcp_client_missing",
                    "verified_now": False,
                    "reached_robot": False,
                    "detail": safe_mcp_client_missing_detail(),
                    "error": "mcp_client_missing",
                    "error_type": "mcp_client_missing",
                    "duration_ms": 0,
                    "next_step": "Запустите `ai-robot-bridge` и повторите проверку MCP.",
                }
            )
        else:
            probe.update(
                cloud_mcp_bridge_probe(
                    (record.get("control") or {}).get("target"),
                    timeout=DIRECT_PROBE_TIMEOUT_SECONDS,
                    robot_id=robot_id,
                )
            )
    elif mode == "local-ws":
        probe.update(
            direct_robot_tools_list_probe(
                (record.get("control") or {}).get("target"),
                kind="direct-robot-tools-list",
                timeout=DIRECT_PROBE_TIMEOUT_SECONDS,
            )
        )
    elif mode == "edge-hub":
        probe.update(probe_edge_hub_control(robot_id))
    elif mode == "missing":
        probe.update(
            {
                "kind": "missing-control",
                "state": "missing",
                "detail": "Проверка недоступна: канал управления не настроен.",
                "error": "",
            }
        )

    if probe.get("verified_now"):
        record["diagnostics"]["transport_state"] = "verified_online"
        record["diagnostics"]["last_error"] = ""
    elif probe.get("state") in {"unreachable", "offline", "missing", "unknown", "mcp_endpoint_alive_not_robot_ack"} and probe.get("error"):
        record["diagnostics"]["last_error"] = str(probe["error"])
        if mode in {"cloud-mcp", "local-ws"}:
            record["diagnostics"]["transport_state"] = str(probe["state"])
        elif mode == "edge-hub" and record["diagnostics"].get("agent_state") in {"seen", "stale"}:
            record["diagnostics"]["transport_state"] = "unreachable"

    probe["checked_at_iso"] = str(probe.get("checked_at_iso", "") or ts_to_iso(probe.get("checked_at")))
    detection = save_detection_snapshot(robot_id, probe, fallback_mode=mode or "missing")
    record["detection"] = detection
    panel_event(
        "robot_detect_result",
        robot_id=robot_id,
        mode=mode or "missing",
        state=detection.get("state"),
        verified_now=detection.get("verified_now"),
        error=detection.get("error"),
        error_type=detection.get("error_type"),
        protocol_phase=detection.get("protocol_phase"),
        lifecycle_path=detection.get("lifecycle_path"),
        request_id=detection.get("request_id"),
        duration_ms=detection.get("duration_ms"),
    )
    return {"robot": record, "probe": probe, "detection": detection}


def is_endpoint_configured(robot_id):
    endpoint = ROBOTS_DIR / robot_id / "mcp_endpoint.txt"
    if not endpoint.exists():
        return False
    content = endpoint.read_text(encoding="utf-8", errors="ignore").strip()
    if not content:
        return False
    return "REPLACE_WITH_ROBOT_TOKEN" not in content


def service_state(robot_id):
    if shutil.which("systemctl") is None:
        return "unknown"
    unit = f"ai-robot-bridge@{robot_id}"
    res = run_cmd(["systemctl", "is-active", unit], timeout=5)
    if not res["ok"]:
        return "inactive"
    return (res["stdout"] or "inactive").strip()


def memory_stats(robot_id):
    root = MEMORY_ROOT / robot_id / "clients"
    if not root.exists():
        return {"clients": 0, "bytes": 0, "files": 0, "latest_update_ts": 0, "latest_update_iso": ""}
    total = 0
    clients = 0
    files = 0
    latest_update_ts = 0
    for child in root.iterdir():
        if child.is_dir():
            clients += 1
            for file in child.rglob("*"):
                if file.is_file():
                    try:
                        stat = file.stat()
                        total += stat.st_size
                        files += 1
                        latest_update_ts = max(latest_update_ts, int(stat.st_mtime))
                    except OSError:
                        pass
    return {
        "clients": clients,
        "bytes": total,
        "files": files,
        "latest_update_ts": latest_update_ts,
        "latest_update_iso": ts_to_iso(latest_update_ts),
    }


def list_robots():
    robots = []
    if not ROBOTS_DIR.exists():
        return robots
    edge_snapshot = fetch_edge_snapshot()

    for item in sorted(ROBOTS_DIR.iterdir()):
        if not item.is_dir():
            continue
        robot_id = item.name
        if not safe_robot_id(robot_id):
            continue
        env = load_env(robot_env_path(robot_id))
        robots.append(build_robot_record(robot_id, edge_snapshot=edge_snapshot))
    return robots


def get_robot_runtime_snapshot(robot_id):
    require_robot_dir(robot_id)
    control_cfg = get_control_config(robot_id)
    diagnostics = build_link_diagnostics(robot_id, control_cfg, fetch_edge_snapshot())
    cfg = load_json(ROBOTS_DIR / robot_id / "mcp_config.json", {"mcpServers": {}})
    activity = summarize_tool_activity(robot_id, cfg.get("mcpServers", {}))
    return {
        "robot_id": robot_id,
        "robot_name": control_cfg.get("robot_name", robot_id) or robot_id,
        "control": control_cfg,
        "diagnostics": diagnostics,
        "activity": activity,
        "activity_presence": summarize_activity_presence(activity),
        "mobile_presence": load_mobile_presence_snapshot(robot_id),
    }


def set_service(robot_id, action):
    if action not in {"start", "stop", "restart"}:
        return {"ok": False, "error": "invalid action"}
    if shutil.which("systemctl") is None:
        return {"ok": False, "error": "systemctl not found"}
    unit = f"ai-robot-bridge@{robot_id}"
    res = run_cmd(["systemctl", action, unit], timeout=15)
    return {"ok": res["ok"], "result": res}


def set_memory_enabled(robot_id, enabled):
    cfg_path = ROBOTS_DIR / robot_id / "mcp_config.json"
    cfg = load_json(cfg_path, {})
    servers = cfg.setdefault("mcpServers", {})
    if "memory-tools" not in servers:
        return {"ok": False, "error": "memory-tools not found"}
    if enabled:
        servers["memory-tools"].pop("disabled", None)
    else:
        servers["memory-tools"]["disabled"] = True
    save_json_atomic(cfg_path, cfg)
    subscription = load_subscription(robot_id, servers=servers)
    subscription["services"]["memory"] = bool(enabled)
    save_subscription(robot_id, subscription)
    return {"ok": True}


def set_tool_enabled(robot_id, tool_name, enabled):
    cfg_path = ROBOTS_DIR / robot_id / "mcp_config.json"
    cfg = load_json(cfg_path, {})
    servers = cfg.setdefault("mcpServers", {})
    if tool_name in LEGACY_DISABLED_MCP_TOOLS:
        if tool_name in servers:
            servers[tool_name]["disabled"] = True
            save_json_atomic(cfg_path, cfg)
        return {
            "ok": False,
            "error": f"tool is reserved for future GOSHA media integration: {tool_name}",
        }
    if tool_name not in servers:
        return {"ok": False, "error": f"tool not found: {tool_name}"}
    if enabled:
        servers[tool_name].pop("disabled", None)
    else:
        servers[tool_name]["disabled"] = True
    save_json_atomic(cfg_path, cfg)
    reverse_map = {v: k for k, v in SERVICE_TOOL_MAP.items()}
    service_name = reverse_map.get(tool_name)
    if service_name:
        subscription = load_subscription(robot_id, servers=servers)
        subscription["services"][service_name] = bool(enabled)
        if subscription.get("plan_code") != "custom":
            subscription["plan_code"] = "custom"
            subscription["plan_name"] = PLAN_CATALOG["custom"]["name"]
        save_subscription(robot_id, subscription)
    return {"ok": True}


def update_subscription(robot_id, payload):
    cfg_path = ROBOTS_DIR / robot_id / "mcp_config.json"
    cfg = load_json(cfg_path, {"mcpServers": {}})
    servers = cfg.get("mcpServers", {})
    current = load_subscription(robot_id, servers=servers)
    merged = {
        "plan_code": payload.get("plan_code", current["plan_code"]),
        "plan_name": payload.get("plan_name", current["plan_name"]),
        "services": payload.get("services", current["services"]),
        "limits": payload.get("limits", current["limits"]),
        "billing": payload.get("billing", current.get("billing", {})),
        "notes": payload.get("notes", current["notes"]),
    }
    normalized = normalize_subscription(merged, servers=servers)
    save_subscription(robot_id, normalized)
    cfg = apply_subscription_to_config(robot_id, normalized)
    return {"ok": True, "subscription": normalized, "policy": policy_status(normalized, cfg.get("mcpServers", {}))}


def create_robot(robot_id, robot_name=None, plan_code="start", endpoint=None, owner=None):
    if not safe_robot_id(robot_id):
        raise ValueError("invalid robot_id")
    robot_dir = ROBOTS_DIR / robot_id
    if robot_dir.exists():
        raise ValueError("robot already exists")

    add_robot_script = resolve_script_path("add_robot.sh")
    res = run_cmd([str(add_robot_script), robot_id], timeout=30)
    if not res.get("ok"):
        raise RuntimeError(res.get("stderr") or res.get("stdout") or "failed to create robot")

    set_robot_name(robot_id, robot_name)
    if endpoint:
        set_robot_endpoint(robot_id, endpoint)

    subscription = normalize_subscription({"plan_code": plan_code})
    save_subscription(robot_id, subscription)
    apply_subscription_to_config(robot_id, subscription)
    save_owner(robot_id, owner or default_owner())
    default_profile = agent_store.default_profile()
    if default_profile:
        try:
            save_robot_agent_assignment(robot_id, default_profile.get("profile_id", ""))
        except Exception:
            pass
    return {"ok": True, "robot_id": robot_id, "subscription": subscription}


def claim_selfhost_device(robot_id, device_id):
    require_robot_dir(robot_id)
    env = load_env(robot_env_path(robot_id))
    current_endpoint = get_robot_mcp_endpoint(robot_id)
    current_claim = selfhost_xiaozhi.find_claim_by_robot(robot_id)
    preferred_token = str(
        env.get("ROBOT_SELFHOST_XIAOZHI_TOKEN", "")
        or (current_claim or {}).get("websocket_token", "")
        or ""
    ).strip()
    preferred_ws_url = str(
        env.get("ROBOT_SELFHOST_XIAOZHI_WS_URL", "")
        or (current_claim or {}).get("websocket_url", "")
        or (selfhost_gateway_state().get("backend") or {}).get("websocket_url", "")
    ).strip()
    preferred_mcp_endpoint = ""
    if current_endpoint and "xiaozhi.me" not in current_endpoint:
        preferred_mcp_endpoint = current_endpoint
    claim = selfhost_xiaozhi.claim_device_to_robot(
        device_id,
        robot_id,
        websocket_url=preferred_ws_url,
        websocket_token=preferred_token,
        control_mcp_endpoint=preferred_mcp_endpoint,
    )
    updates = {
        "ROBOT_BACKEND_MODE": selfhost_xiaozhi.BACKEND_MODE_SELF_HOSTED,
        "ROBOT_SELFHOST_XIAOZHI_DEVICE_ID": claim.get("device_id", ""),
        "ROBOT_SELFHOST_XIAOZHI_CLIENT_ID": claim.get("client_id", ""),
        "ROBOT_SELFHOST_XIAOZHI_SERIAL_NUMBER": claim.get("serial_number", ""),
        "ROBOT_SELFHOST_XIAOZHI_WS_URL": claim.get("websocket_url", ""),
        "ROBOT_SELFHOST_XIAOZHI_TOKEN": claim.get("websocket_token", ""),
    }
    save_env_updates(robot_env_path(robot_id), updates)
    if claim.get("control_mcp_endpoint"):
        set_robot_endpoint(robot_id, claim.get("control_mcp_endpoint"))
    return {
        "ok": True,
        "robot_id": robot_id,
        "device_id": claim.get("device_id", ""),
        "claim": claim,
        "control": get_control_config(robot_id),
    }


def update_owner(robot_id, payload):
    owner = normalize_owner(payload)
    save_owner(robot_id, owner)
    return {"ok": True, "owner": owner}


def list_clients(robot_id):
    clients_dir = MEMORY_ROOT / robot_id / "clients"
    if not clients_dir.exists():
        return ["default"]
    clients = sorted([d.name for d in clients_dir.iterdir() if d.is_dir()])
    return clients or ["default"]


def tail_text(path, max_lines=200, max_chars=20000):
    if not path.exists():
        return ""
    txt = path.read_text(encoding="utf-8", errors="ignore")
    lines = txt.splitlines()
    out = "\n".join(lines[-max_lines:])
    if len(out) > max_chars:
        out = out[-max_chars:]
    return out


def list_memory_files(client_dir):
    files = []
    if not client_dir.exists():
        return files
    ordered = []
    for path in client_dir.rglob("*"):
        if path.is_file():
            rel = path.relative_to(client_dir).as_posix()
            order = KNOWN_MEMORY_FILES.index(rel) if rel in KNOWN_MEMORY_FILES else len(KNOWN_MEMORY_FILES)
            ordered.append((order, rel, path))
    for _, rel, path in sorted(ordered, key=lambda item: (item[0], item[1])):
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append(
            {
                "name": path.name,
                "path": rel,
                "size_bytes": stat.st_size,
                "updated_at_ts": int(stat.st_mtime),
                "updated_at_iso": ts_to_iso(stat.st_mtime),
                "preview": tail_text(path, max_lines=400, max_chars=30000),
                "is_known": rel in KNOWN_MEMORY_FILES,
            }
        )
    return files


def summarize_memory_client(client_dir, files):
    summary = {
        "file_count": len(files),
        "extra_files": [item["path"] for item in files if not item.get("is_known")][:12],
        "events_count": 0,
        "event_types": {},
        "latest_event_ts": "",
        "latest_event_type": "",
        "notes_chars": 0,
        "important_facts_count": 0,
        "contacts_count": 0,
        "prefs_keys": [],
        "profile_name": "",
        "profile_address_as": "",
    }

    profile = load_json(client_dir / "client_profile.json", {})
    if isinstance(profile, dict):
        facts = profile.get("important_facts")
        summary["important_facts_count"] = len(facts) if isinstance(facts, list) else 0
        summary["profile_name"] = str(profile.get("name", "") or "").strip()
        summary["profile_address_as"] = str(profile.get("address_as", "") or "").strip()

    contacts = load_json(client_dir / "contacts.json", [])
    if isinstance(contacts, list):
        summary["contacts_count"] = len(contacts)
    elif isinstance(contacts, dict):
        summary["contacts_count"] = len(contacts.keys())

    prefs = load_json(client_dir / "prefs.json", {})
    if isinstance(prefs, dict):
        summary["prefs_keys"] = sorted([str(key) for key in prefs.keys()])[:20]

    notes_path = client_dir / "notes.md"
    if notes_path.exists():
        try:
            summary["notes_chars"] = len(notes_path.read_text(encoding="utf-8", errors="ignore").strip())
        except OSError:
            pass

    events_path = client_dir / "events.jsonl"
    if events_path.exists():
        try:
            for raw in events_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                summary["events_count"] += 1
                event_type = str(item.get("event_type", "") or "").strip()
                if event_type:
                    summary["event_types"][event_type] = int(summary["event_types"].get(event_type, 0)) + 1
                event_ts = str(item.get("ts", "") or "").strip()
                if event_ts and event_ts >= summary["latest_event_ts"]:
                    summary["latest_event_ts"] = event_ts
                    summary["latest_event_type"] = event_type
        except OSError:
            pass

    summary["event_types_top"] = [
        {"name": name, "count": count}
        for name, count in sorted(summary["event_types"].items(), key=lambda item: (-item[1], item[0]))[:8]
    ]
    return summary


def load_mcp_activity(robot_id):
    data = load_json(ROBOTS_DIR / robot_id / "mcp_activity.json", {})
    return data if isinstance(data, dict) else {}


def summarize_tool_activity(robot_id, servers):
    raw = load_mcp_activity(robot_id)
    targets_raw = raw.get("targets") if isinstance(raw.get("targets"), dict) else {}
    target_names = sorted(set(servers.keys()) | set(targets_raw.keys()))
    targets = []
    for name in target_names:
        source = targets_raw.get(name) if isinstance(targets_raw.get(name), dict) else {}
        target_last_seen = int(source.get("last_request_seen") or 0)
        target_last_tool_seen = int(source.get("last_tool_call_seen") or 0)
        targets.append(
            {
                "name": name,
                "configured": name in servers,
                "enabled": None if name not in servers else not bool((servers.get(name) or {}).get("disabled", False)),
                "request_count": int(source.get("request_count") or 0),
                "last_request_seen": target_last_seen,
                "last_request_seen_iso": str(source.get("last_request_seen_iso", "") or ts_to_iso(target_last_seen)),
                "last_request_method": str(source.get("last_request_method", "") or ""),
                "last_tool_name": str(source.get("last_tool_name", "") or ""),
                "tool_call_count": int(source.get("tool_call_count") or 0),
                "last_tool_call_seen": target_last_tool_seen,
                "last_tool_call_seen_iso": str(source.get("last_tool_call_seen_iso", "") or ts_to_iso(target_last_tool_seen)),
            }
        )

    updated_at = int(raw.get("updated_at") or 0)
    last_tool_call_seen = int(raw.get("last_tool_call_seen") or 0)
    return {
        "available": bool(raw),
        "updated_at": updated_at,
        "updated_at_iso": str(raw.get("updated_at_iso", "") or ts_to_iso(updated_at)),
        "request_count": int(raw.get("request_count") or 0),
        "last_request_method": str(raw.get("last_request_method", "") or ""),
        "last_request_target": str(raw.get("last_request_target", "") or ""),
        "last_request_seen": int(raw.get("last_request_seen") or 0),
        "last_request_seen_iso": str(raw.get("last_request_seen_iso", "") or ts_to_iso(raw.get("last_request_seen"))),
        "tool_call_count": int(raw.get("tool_call_count") or 0),
        "last_tool_name": str(raw.get("last_tool_name", "") or ""),
        "last_tool_target": str(raw.get("last_tool_target", "") or ""),
        "last_tool_call_seen": last_tool_call_seen,
        "last_tool_call_seen_iso": str(raw.get("last_tool_call_seen_iso", "") or ts_to_iso(last_tool_call_seen)),
        "targets": targets,
    }


def summarize_activity_presence(activity):
    raw = activity if isinstance(activity, dict) else {}
    last_request_seen = int(raw.get("last_request_seen") or 0)
    last_tool_call_seen = int(raw.get("last_tool_call_seen") or 0)
    updated_at = int(raw.get("updated_at") or 0)
    last_seen = max(last_request_seen, last_tool_call_seen, updated_at)
    if last_request_seen >= max(last_tool_call_seen, updated_at) and last_request_seen > 0:
        source = "request"
        last_method = str(raw.get("last_request_method", "") or "")
        last_target = str(raw.get("last_request_target", "") or "")
    elif last_tool_call_seen >= max(last_request_seen, updated_at) and last_tool_call_seen > 0:
        source = "tool_call"
        last_method = str(raw.get("last_tool_name", "") or "")
        last_target = str(raw.get("last_tool_target", "") or "")
    elif updated_at > 0:
        source = "updated"
        last_method = ""
        last_target = ""
    else:
        source = ""
        last_method = ""
        last_target = ""

    age_seconds = max(0, now_ts() - last_seen) if last_seen > 0 else 0
    if last_seen <= 0:
        status = "missing"
    elif age_seconds <= ACTIVITY_PRESENCE_FRESH_SECONDS:
        status = "fresh"
    elif age_seconds <= ACTIVITY_PRESENCE_RECENT_SECONDS:
        status = "recent"
    elif age_seconds <= ACTIVITY_PRESENCE_HISTORY_SECONDS:
        status = "stale"
    else:
        status = "old"

    return {
        "available": bool(raw.get("available") or raw),
        "status": status,
        "source": source,
        "last_seen": last_seen,
        "last_seen_iso": ts_to_iso(last_seen),
        "age_seconds": age_seconds,
        "fresh_seconds": ACTIVITY_PRESENCE_FRESH_SECONDS,
        "recent_seconds": ACTIVITY_PRESENCE_RECENT_SECONDS,
        "history_seconds": ACTIVITY_PRESENCE_HISTORY_SECONDS,
        "last_method": last_method,
        "last_target": last_target,
        "request_count": int(raw.get("request_count") or 0),
        "tool_call_count": int(raw.get("tool_call_count") or 0),
        "last_request_seen": last_request_seen,
        "last_request_seen_iso": str(raw.get("last_request_seen_iso", "") or ts_to_iso(last_request_seen)),
        "last_tool_call_seen": last_tool_call_seen,
        "last_tool_call_seen_iso": str(raw.get("last_tool_call_seen_iso", "") or ts_to_iso(last_tool_call_seen)),
    }


def get_memory_view(robot_id, client_id):
    client_dir = MEMORY_ROOT / robot_id / "clients" / client_id
    client_dir.mkdir(parents=True, exist_ok=True)
    files = list_memory_files(client_dir)
    summary = summarize_memory_client(client_dir, files)
    cfg = load_json(ROBOTS_DIR / robot_id / "mcp_config.json", {"mcpServers": {}})
    data = {
        "robot_id": robot_id,
        "client_id": client_id,
        "clients": list_clients(robot_id),
        "summary": summary,
        "files": files,
        "activity": summarize_tool_activity(robot_id, cfg.get("mcpServers", {})),
        "client_profile_json": tail_text(client_dir / "client_profile.json", max_lines=400),
        "events_jsonl": tail_text(client_dir / "events.jsonl"),
        "notes_md": tail_text(client_dir / "notes.md"),
        "prefs_json": tail_text(client_dir / "prefs.json", max_lines=400),
        "contacts_json": tail_text(client_dir / "contacts.json", max_lines=400),
    }
    return data


def nmcli_exists():
    return shutil.which("nmcli") is not None


def wifi_status():
    if not nmcli_exists():
        return {"available": False, "status": []}
    res = run_cmd(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev", "status"], timeout=8)
    rows = []
    for raw in (res["stdout"] or "").splitlines():
        parts = raw.split(":", 3)
        if len(parts) < 4:
            continue
        device, dev_type, state, connection = parts
        if dev_type != "wifi":
            continue
        rows.append(
            {
                "device": device,
                "state": state,
                "connection": connection,
            }
        )
    return {"available": True, "status": rows, "raw": res}


def wifi_scan():
    if not nmcli_exists():
        return {"available": False, "networks": []}
    res = run_cmd(
        ["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", "auto"],
        timeout=12,
    )
    out = []
    for raw in (res["stdout"] or "").splitlines():
        parts = raw.split(":", 3)
        if len(parts) < 4:
            continue
        in_use, ssid, signal, security = parts
        if not ssid:
            continue
        out.append(
            {
                "in_use": in_use == "*",
                "ssid": ssid,
                "signal": signal,
                "security": security,
            }
        )
    return {"available": True, "networks": out, "raw": res}


def wifi_connect(ssid, password=None, device=None):
    if not nmcli_exists():
        return {"ok": False, "error": "nmcli not found"}
    if not ssid:
        return {"ok": False, "error": "ssid is required"}
    cmd = ["nmcli", "dev", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]
    if device:
        cmd += ["ifname", device]
    res = run_cmd(cmd, timeout=20)
    return {"ok": res["ok"], "result": res}


def wifi_disconnect(connection=None, device=None):
    if not nmcli_exists():
        return {"ok": False, "error": "nmcli not found"}
    if connection:
        res = run_cmd(["nmcli", "con", "down", "id", connection], timeout=12)
        return {"ok": res["ok"], "result": res}
    if device:
        res = run_cmd(["nmcli", "dev", "disconnect", device], timeout=12)
        return {"ok": res["ok"], "result": res}
    status = wifi_status()
    if not status.get("status"):
        return {"ok": False, "error": "no active wifi connection"}
    candidate = status["status"][0]
    if candidate.get("connection"):
        res = run_cmd(["nmcli", "con", "down", "id", candidate["connection"]], timeout=12)
    else:
        res = run_cmd(["nmcli", "dev", "disconnect", candidate["device"]], timeout=12)
    return {"ok": res["ok"], "result": res}


def normalize_header_map(headers):
    out = {}
    try:
        items = headers.items()
    except Exception:
        items = []
    for key, value in items:
        clean_key = str(key or "").strip()
        if not clean_key:
            continue
        out[clean_key] = str(value or "").strip()
    return out


def extract_selfhost_device_identity(headers, payload):
    header_map = normalize_header_map(headers)
    lower_headers = {key.lower(): value for key, value in header_map.items()}
    body = payload if isinstance(payload, dict) else {}
    mac_address = str(body.get("mac_address", "") or body.get("device_id", "") or lower_headers.get("device-id", "") or "").strip()
    client_id = str(
        body.get("uuid", "")
        or body.get("client_id", "")
        or lower_headers.get("client-id", "")
        or ""
    ).strip()
    serial_number = str(
        body.get("serial_number", "")
        or body.get("chip_model_name", "")
        or lower_headers.get("serial-number", "")
        or ""
    ).strip()
    board_name = str(body.get("board", "") or body.get("board_name", "") or "").strip()
    app_version = str((body.get("application", {}) or {}).get("version", "") or "").strip()
    return {
        "device_id": mac_address or client_id,
        "client_id": client_id,
        "serial_number": serial_number,
        "board_name": board_name,
        "app_version": app_version,
        "headers": header_map,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "AIRobotPanel/1.0"

    def _send_bytes(self, code, body, content_type="application/json; charset=utf-8", extra_headers=None):
        payload = body if isinstance(body, (bytes, bytearray)) else bytes(body or b"")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, code, payload, extra_headers=None):
        body = json_bytes(payload)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, text, extra_headers=None):
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path, content_type, download_name=None):
        if not path.exists() or not path.is_file():
            self._send_json(404, {"ok": False, "error": "file not found"})
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(data)

    def _send_file_head(self, path, content_type, download_name=None):
        if not path.exists() or not path.is_file():
            body = json_bytes({"ok": False, "error": "file not found"})
            self.send_response(404)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(path.stat().st_size))
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()

    def _send_json_head(self, code, payload):
        body = json_bytes(payload)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

    def _body_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _path_parts(self):
        parsed = urlparse(self.path)
        parts = [x for x in parsed.path.split("/") if x]
        return parsed, parts

    def _cookie_map(self):
        out = {}
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            out[key.strip()] = value.strip()
        return out

    def _session_cookie_header(self, token, max_age):
        parts = [
            f"{PANEL_SESSION_COOKIE}={token}",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            f"Max-Age={int(max_age)}",
        ]
        if PANEL_PUBLIC_SCHEME == "https":
            parts.append("Secure")
        return "; ".join(parts)

    def _operator_session(self):
        if not operator_auth_enabled():
            return {"user": "open-access", "expires_at": int(time.time()) + PANEL_SESSION_TTL_SECONDS}
        token = self._cookie_map().get(PANEL_SESSION_COOKIE, "")
        return get_operator_session(token)

    def _require_operator_auth(self):
        if not operator_auth_enabled():
            return True
        if self._operator_session():
            return True
        self._send_json(401, {"ok": False, "error": "operator authentication required", "auth_required": True})
        return False

    def _require_mobile_access(self, robot_id):
        try:
            require_robot_dir(robot_id)
        except Exception as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return False
        token = self.headers.get("X-Mobile-Token", "")
        code = self.headers.get("X-Mobile-Code", "")
        if validate_mobile_access(robot_id, token=token, code=code):
            return True
        self._send_json(401, {"ok": False, "error": "mobile access denied"})
        return False

    def do_HEAD(self):
        parsed, _ = self._path_parts()

        if parsed.path == "/downloads/maxcorp-connector-debug.apk":
            self._send_file_head(
                APK_SHARE_PATH,
                "application/vnd.android.package-archive",
                download_name="maxcorp-connector-debug.apk",
            )
            return

        if parsed.path == "/downloads/maxcorp-admin-connector-debug.apk":
            self._send_file_head(
                ADMIN_APK_SHARE_PATH,
                "application/vnd.android.package-archive",
                download_name="maxcorp-admin-connector-debug.apk",
            )
            return

        if parsed.path in ("/legal/gosha-privacy-policy.html", "/gosha/privacy"):
            self._send_file_head(PRIVACY_POLICY_SHARE_PATH, "text/html; charset=utf-8")
            return

        if parsed.path in ("/legal/gosha-terms-of-use.html", "/gosha/terms"):
            self._send_file_head(TERMS_OF_USE_SHARE_PATH, "text/html; charset=utf-8")
            return

        if parsed.path == "/api/mobile/plans":
            self._send_json_head(200, {"ok": True, "plans": list(PLAN_CATALOG.values())})
            return

        self._send_json_head(404, {"ok": False, "error": "not found"})

    def do_GET(self):
        parsed, parts = self._path_parts()
        if parsed.path == "/":
            if not HTML_PATH.exists():
                self._send_html("<h1>panel_index.html not found</h1>")
                return
            self._send_html(HTML_PATH.read_text(encoding="utf-8", errors="ignore"))
            return

        if parsed.path == "/downloads/maxcorp-connector-debug.apk":
            self._send_file(
                APK_SHARE_PATH,
                "application/vnd.android.package-archive",
                download_name="maxcorp-connector-debug.apk",
            )
            return

        if parsed.path == "/downloads/maxcorp-admin-connector-debug.apk":
            self._send_file(
                ADMIN_APK_SHARE_PATH,
                "application/vnd.android.package-archive",
                download_name="maxcorp-admin-connector-debug.apk",
            )
            return

        if parsed.path in ("/legal/gosha-privacy-policy.html", "/gosha/privacy"):
            self._send_file(PRIVACY_POLICY_SHARE_PATH, "text/html; charset=utf-8")
            return

        if parsed.path in ("/legal/gosha-terms-of-use.html", "/gosha/terms"):
            self._send_file(TERMS_OF_USE_SHARE_PATH, "text/html; charset=utf-8")
            return

        if parsed.path.rstrip("/") in ("/xiaozhi/ota", "/gosha/ota"):
            identity = extract_selfhost_device_identity(self.headers, {})
            if not identity.get("device_id"):
                self._send_json(400, {"ok": False, "error": "device_id is required"})
                return
            selfhost_xiaozhi.record_device_contact(
                device_id=identity.get("device_id"),
                client_id=identity.get("client_id", ""),
                serial_number=identity.get("serial_number", ""),
                payload={
                    "board": identity.get("board_name", ""),
                    "application": {"version": identity.get("app_version", "")},
                },
                headers=identity.get("headers", {}),
                remote_addr=self.client_address[0] if self.client_address else "",
            )
            try:
                ota_payload = selfhost_xiaozhi.ota_payload_for_device(identity.get("device_id"))
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            panel_event(
                "selfhost_ota_get",
                device_id=identity.get("device_id"),
                client_id=identity.get("client_id", ""),
                serial_number=identity.get("serial_number", ""),
                remote_addr=self.client_address[0] if self.client_address else "",
            )
            self._send_json(200, ota_payload)
            return

        if parsed.path == "/api/operator/session":
            session = self._operator_session()
            self._send_json(
                200,
                {
                    "ok": True,
                    "auth_enabled": operator_auth_enabled(),
                    "authenticated": bool(session),
                    "user": (session or {}).get("user", ""),
                },
            )
            return

        if parsed.path == "/api/mobile/plans":
            self._send_json(200, {"ok": True, "plans": list(PLAN_CATALOG.values())})
            return

        if len(parts) == 5 and parts[0] == "api" and parts[1] == "mobile" and parts[2] == "robots" and parts[4] == "runtime":
            robot_id = parts[3]
            if not self._require_mobile_access(robot_id):
                return
            try:
                self._send_json(200, {"ok": True, "data": get_robot_runtime_snapshot(robot_id)})
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if len(parts) == 5 and parts[0] == "api" and parts[1] == "mobile" and parts[2] == "robots" and parts[4] == "subscription":
            robot_id = parts[3]
            if not self._require_mobile_access(robot_id):
                return
            cfg = load_json(ROBOTS_DIR / robot_id / "mcp_config.json", {"mcpServers": {}})
            subscription = load_subscription(robot_id, servers=cfg.get("mcpServers", {}))
            policy = policy_status(subscription, cfg.get("mcpServers", {}))
            self._send_json(200, {"ok": True, "data": {"subscription": subscription, "policy": policy}})
            return

        if len(parts) == 5 and parts[0] == "api" and parts[1] == "mobile" and parts[2] == "robots" and parts[4] == "owner":
            robot_id = parts[3]
            if not self._require_mobile_access(robot_id):
                return
            self._send_json(200, {"ok": True, "data": {"owner": load_owner(robot_id)}})
            return

        if len(parts) == 5 and parts[0] == "api" and parts[1] == "mobile" and parts[2] == "robots" and parts[4] == "users":
            robot_id = parts[3]
            if not self._require_mobile_access(robot_id):
                return
            self._send_json(200, {"ok": True, "data": {"users": load_users(robot_id)}})
            return

        if parsed.path == "/api/mobile/code":
            code = parse_qs(parsed.query).get("value", [""])[0]
            try:
                result = resolve_mobile_onboarding_code(code)
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return

        if parsed.path == "/api/internal/openai/v1/models":
            auth = str(self.headers.get("Authorization", "") or "").strip()
            expected = f"Bearer {GOSHA_INTERNAL_OPENAI_PROXY_TOKEN}"
            if not GOSHA_INTERNAL_OPENAI_PROXY_TOKEN or auth != expected:
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            try:
                status, raw, headers = proxy_internal_openai_request("/v1/models")
                content_type = headers.get("Content-Type") or headers.get("content-type") or "application/json; charset=utf-8"
                self._send_bytes(status, raw, content_type=content_type)
            except Exception as exc:
                self._send_json(502, {"ok": False, "error": str(exc)})
            return

        effective_path = parsed.path
        effective_parts = parts
        if parsed.path.startswith("/api/operator/"):
            if not self._require_operator_auth():
                return
            effective_parts = ["api"] + parts[2:]
            effective_path = "/api/" + "/".join(parts[2:]) if len(parts) > 2 else "/api"
        elif parsed.path.startswith("/api/") and not parsed.path.startswith("/api/mobile/"):
            if not self._require_operator_auth():
                return

        if effective_path == "/api/robots":
            self._send_json(200, {"ok": True, "robots": list_robots()})
            return

        if effective_path == "/api/selfhost-xiaozhi":
            self._send_json(200, {"ok": True, "data": selfhost_gateway_state()})
            return

        if effective_path == "/api/agent-gateway/status":
            self._send_json(200, {"ok": True, "data": agent_gateway_status()})
            return

        if effective_path == "/api/agent-profiles":
            self._send_json(
                200,
                {
                    "ok": True,
                    "data": {
                        "gateway": agent_gateway_status(),
                        "profiles": list_agent_profiles(),
                        "providers": agent_store.supported_provider_catalog(),
                    },
                },
            )
            return

        if effective_path == "/api/assistant-control/catalog":
            self._send_json(200, {"ok": True, "data": assistant_control_catalog()})
            return

        if effective_path == "/api/assistant-profiles":
            self._send_json(200, {"ok": True, "data": {"profiles": list_assistant_profiles()}})
            return

        if effective_path == "/api/tts-engine-profiles":
            self._send_json(200, {"ok": True, "data": {"profiles": list_tts_engine_profiles()}})
            return

        if effective_path == "/api/voice-profiles":
            self._send_json(200, {"ok": True, "data": {"profiles": list_voice_profiles()}})
            return

        if effective_path == "/api/memory-profiles":
            self._send_json(200, {"ok": True, "data": {"profiles": list_memory_profiles()}})
            return

        if effective_path == "/api/mcp-bundles":
            self._send_json(200, {"ok": True, "data": {"profiles": list_mcp_bundles()}})
            return

        if effective_path == "/api/knowledge-profiles":
            self._send_json(200, {"ok": True, "data": {"profiles": list_knowledge_profiles()}})
            return

        if effective_path == "/api/screen-profiles":
            self._send_json(200, {"ok": True, "data": {"profiles": list_screen_profiles()}})
            return

        if effective_path == "/api/wake-profiles":
            self._send_json(200, {"ok": True, "data": {"profiles": list_wake_profiles()}})
            return

        if effective_path == "/api/subscription/plans":
            self._send_json(200, {"ok": True, "plans": list(PLAN_CATALOG.values())})
            return

        if len(effective_parts) == 4 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "probe":
            robot_id = effective_parts[2]
            if not safe_robot_id(robot_id):
                self._send_json(400, {"ok": False, "error": "invalid robot_id"})
                return
            try:
                self._send_json(200, {"ok": True, "data": probe_robot_record(robot_id)})
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if len(effective_parts) == 4 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "detect":
            robot_id = effective_parts[2]
            if not safe_robot_id(robot_id):
                self._send_json(400, {"ok": False, "error": "invalid robot_id"})
                return
            try:
                self._send_json(200, {"ok": True, "data": probe_robot_record(robot_id)})
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if len(effective_parts) == 4 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "memory":
            robot_id = effective_parts[2]
            if not safe_robot_id(robot_id):
                self._send_json(400, {"ok": False, "error": "invalid robot_id"})
                return
            client_id = parse_qs(parsed.query).get("client_id", ["default"])[0]
            if not safe_robot_id(client_id):
                self._send_json(400, {"ok": False, "error": "invalid client_id"})
                return
            self._send_json(200, {"ok": True, "data": get_memory_view(robot_id, client_id)})
            return

        if len(effective_parts) == 4 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "subscription":
            robot_id = effective_parts[2]
            if not safe_robot_id(robot_id):
                self._send_json(400, {"ok": False, "error": "invalid robot_id"})
                return
            cfg = load_json(ROBOTS_DIR / robot_id / "mcp_config.json", {"mcpServers": {}})
            subscription = load_subscription(robot_id, servers=cfg.get("mcpServers", {}))
            policy = policy_status(subscription, cfg.get("mcpServers", {}))
            self._send_json(200, {"ok": True, "data": {"subscription": subscription, "policy": policy}})
            return

        if len(effective_parts) == 4 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "owner":
            robot_id = effective_parts[2]
            if not safe_robot_id(robot_id):
                self._send_json(400, {"ok": False, "error": "invalid robot_id"})
                return
            self._send_json(200, {"ok": True, "data": {"owner": load_owner(robot_id)}})
            return

        if len(effective_parts) == 4 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "users":
            robot_id = effective_parts[2]
            if not safe_robot_id(robot_id):
                self._send_json(400, {"ok": False, "error": "invalid robot_id"})
                return
            self._send_json(200, {"ok": True, "data": {"users": load_users(robot_id)}})
            return

        if len(effective_parts) == 4 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "mobile-codes":
            robot_id = effective_parts[2]
            if not safe_robot_id(robot_id):
                self._send_json(400, {"ok": False, "error": "invalid robot_id"})
                return
            self._send_json(200, {"ok": True, "data": {"codes": mobile_codes_for_robot(robot_id)}})
            return

        if len(effective_parts) == 5 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "control" and effective_parts[4] == "config":
            robot_id = effective_parts[2]
            if not safe_robot_id(robot_id):
                self._send_json(400, {"ok": False, "error": "invalid robot_id"})
                return
            self._send_json(200, {"ok": True, "data": get_control_config(robot_id)})
            return

        if len(effective_parts) == 4 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "agent":
            robot_id = effective_parts[2]
            try:
                self._send_json(200, {"ok": True, "data": get_robot_agent_assignment(robot_id)})
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if len(effective_parts) == 4 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "assistant-config":
            robot_id = effective_parts[2]
            try:
                self._send_json(200, {"ok": True, "data": get_robot_assistant_config(robot_id)})
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if len(effective_parts) == 5 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "agent" and effective_parts[4] == "effective":
            robot_id = effective_parts[2]
            try:
                self._send_json(200, {"ok": True, "data": agent_store.effective_robot_agent(robot_id)})
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if effective_path == "/api/wifi/status":
            self._send_json(200, {"ok": True, "data": wifi_status()})
            return

        if effective_path == "/api/wifi/networks":
            self._send_json(200, {"ok": True, "data": wifi_scan()})
            return

        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        parsed, parts = self._path_parts()
        payload = self._body_json()

        if parsed.path.rstrip("/") in ("/xiaozhi/ota", "/gosha/ota"):
            identity = extract_selfhost_device_identity(self.headers, payload)
            if not identity.get("device_id"):
                self._send_json(400, {"ok": False, "error": "device_id is required"})
                return
            selfhost_xiaozhi.record_device_contact(
                device_id=identity.get("device_id"),
                client_id=identity.get("client_id", ""),
                serial_number=identity.get("serial_number", ""),
                payload=payload if isinstance(payload, dict) else {},
                headers=identity.get("headers", {}),
                remote_addr=self.client_address[0] if self.client_address else "",
            )
            ota_payload = selfhost_xiaozhi.ota_payload_for_device(identity.get("device_id"))
            panel_event(
                "selfhost_ota_post",
                device_id=identity.get("device_id"),
                client_id=identity.get("client_id", ""),
                serial_number=identity.get("serial_number", ""),
                remote_addr=self.client_address[0] if self.client_address else "",
            )
            self._send_json(200, ota_payload)
            return

        if parsed.path.rstrip("/") in ("/xiaozhi/ota/activate", "/gosha/ota/activate"):
            identity = extract_selfhost_device_identity(self.headers, payload)
            device_id = identity.get("device_id") or str(payload.get("serial_number", "")).strip()
            if not device_id:
                self._send_json(400, {"ok": False, "error": "device_id is required"})
                return
            status = selfhost_xiaozhi.activation_response_status(device_id)
            panel_event(
                "selfhost_activate",
                device_id=device_id,
                status=status,
                remote_addr=self.client_address[0] if self.client_address else "",
            )
            self._send_json(status, {"ok": status == 200, "device_id": device_id, "status": "claimed" if status == 200 else "pending"})
            return

        if parsed.path == "/api/operator/login":
            username = str(payload.get("username", "")).strip()
            password = str(payload.get("password", ""))
            if not operator_auth_enabled():
                self._send_json(200, {"ok": True, "auth_enabled": False, "authenticated": True, "user": "open-access"})
                return
            if not validate_operator_credentials(username, password):
                self._send_json(401, {"ok": False, "error": "invalid username or password", "auth_required": True})
                return
            session_token = create_operator_session(username)
            self._send_json(
                200,
                {"ok": True, "auth_enabled": True, "authenticated": True, "user": username},
                extra_headers={"Set-Cookie": self._session_cookie_header(session_token, PANEL_SESSION_TTL_SECONDS)},
            )
            return

        if parsed.path == "/api/operator/logout":
            if operator_auth_enabled():
                drop_operator_session(self._cookie_map().get(PANEL_SESSION_COOKIE, ""))
            self._send_json(
                200,
                {"ok": True, "authenticated": False},
                extra_headers={"Set-Cookie": self._session_cookie_header("", 0)},
            )
            return

        if parsed.path == "/api/mobile/resolve-code":
            code = str(payload.get("code", "")).strip()
            try:
                result = resolve_mobile_onboarding_code(code)
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return

        if parsed.path == "/api/mobile/activate-code":
            code = str(payload.get("code", "")).strip()
            owner = payload.get("owner", {}) if isinstance(payload.get("owner", {}), dict) else {}
            try:
                result = activate_mobile_onboarding_code(code, owner=owner)
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return

        if parsed.path == "/api/internal/openai/v1/chat/completions":
            auth = str(self.headers.get("Authorization", "") or "").strip()
            expected = f"Bearer {GOSHA_INTERNAL_OPENAI_PROXY_TOKEN}"
            if not GOSHA_INTERNAL_OPENAI_PROXY_TOKEN or auth != expected:
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            try:
                status, raw, headers = proxy_internal_openai_request("/v1/chat/completions", payload)
                content_type = headers.get("Content-Type") or headers.get("content-type") or "application/json; charset=utf-8"
                self._send_bytes(status, raw, content_type=content_type)
            except Exception as exc:
                self._send_json(502, {"ok": False, "error": str(exc)})
            return

        if len(parts) == 5 and parts[0] == "api" and parts[1] == "mobile" and parts[2] == "robots" and parts[4] == "subscription":
            robot_id = parts[3]
            if not self._require_mobile_access(robot_id):
                return
            try:
                result = update_subscription(robot_id, payload)
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return

        if len(parts) == 5 and parts[0] == "api" and parts[1] == "mobile" and parts[2] == "robots" and parts[4] == "owner":
            robot_id = parts[3]
            if not self._require_mobile_access(robot_id):
                return
            try:
                result = update_owner(robot_id, payload)
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return

        if len(parts) == 5 and parts[0] == "api" and parts[1] == "mobile" and parts[2] == "robots" and parts[4] == "presence":
            robot_id = parts[3]
            if not self._require_mobile_access(robot_id):
                return
            try:
                result = update_mobile_presence(robot_id, payload)
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return

        if len(parts) == 5 and parts[0] == "api" and parts[1] == "mobile" and parts[2] == "robots" and parts[4] == "users":
            robot_id = parts[3]
            if not self._require_mobile_access(robot_id):
                return
            try:
                result = add_user(robot_id, payload)
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return

        if len(parts) == 6 and parts[0] == "api" and parts[1] == "mobile" and parts[2] == "robots" and parts[4] == "users" and parts[5] == "delete":
            robot_id = parts[3]
            if not self._require_mobile_access(robot_id):
                return
            try:
                result = delete_user(robot_id, payload.get("user_id"))
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return

        effective_path = parsed.path
        effective_parts = parts
        if parsed.path.startswith("/api/operator/"):
            if not self._require_operator_auth():
                return
            effective_parts = ["api"] + parts[2:]
            effective_path = "/api/" + "/".join(parts[2:]) if len(parts) > 2 else "/api"
        elif parsed.path.startswith("/api/") and not parsed.path.startswith("/api/mobile/"):
            if not self._require_operator_auth():
                return

        if effective_path == "/api/robots/create":
            robot_id = str(payload.get("robot_id", "")).strip()
            robot_name = str(payload.get("robot_name", "")).strip() or None
            plan_code = str(payload.get("plan_code", "start")).strip().lower() or "start"
            endpoint = str(payload.get("endpoint", "")).strip() or None
            owner = payload.get("owner", {}) if isinstance(payload.get("owner", {}), dict) else {}
            try:
                result = create_robot(robot_id=robot_id, robot_name=robot_name, plan_code=plan_code, endpoint=endpoint, owner=owner)
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return

        if effective_path == "/api/mobile/create-code":
            robot_id = str(payload.get("robot_id", "")).strip()
            robot_name = str(payload.get("robot_name", "")).strip() or None
            plan_code = str(payload.get("plan_code", "start")).strip().lower() or "start"
            endpoint = str(payload.get("endpoint", "")).strip() or None
            owner = payload.get("owner", {}) if isinstance(payload.get("owner", {}), dict) else {}
            try:
                result = create_mobile_onboarding_code(robot_id=robot_id, robot_name=robot_name, plan_code=plan_code, endpoint=endpoint, owner=owner)
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return

        if effective_path == "/api/selfhost-xiaozhi/claim":
            robot_id = str(payload.get("robot_id", "")).strip()
            device_id = str(payload.get("device_id", "")).strip()
            try:
                result = claim_selfhost_device(robot_id, device_id)
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return

        if effective_path == "/api/agent-profiles":
            try:
                self._send_json(200, upsert_agent_profile(payload))
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if effective_path == "/api/assistant-profiles":
            try:
                self._send_json(200, upsert_assistant_profile(payload))
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if effective_path == "/api/tts-engine-profiles":
            try:
                self._send_json(200, upsert_tts_engine_profile(payload))
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if effective_path == "/api/voice-profiles":
            try:
                self._send_json(200, upsert_voice_profile(payload))
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if effective_path == "/api/memory-profiles":
            try:
                self._send_json(200, upsert_memory_profile(payload))
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if effective_path == "/api/mcp-bundles":
            try:
                self._send_json(200, upsert_mcp_bundle(payload))
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if effective_path == "/api/knowledge-profiles":
            try:
                self._send_json(200, upsert_knowledge_profile(payload))
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if effective_path == "/api/screen-profiles":
            try:
                self._send_json(200, upsert_screen_profile(payload))
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if effective_path == "/api/wake-profiles":
            try:
                self._send_json(200, upsert_wake_profile(payload))
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if len(effective_parts) == 4 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "service":
            robot_id = effective_parts[2]
            if not safe_robot_id(robot_id):
                self._send_json(400, {"ok": False, "error": "invalid robot_id"})
                return
            action = str(payload.get("action", "")).strip().lower()
            result = set_service(robot_id, action)
            self._send_json(200 if result.get("ok") else 400, result)
            return

        if len(effective_parts) == 4 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "assistant-config":
            robot_id = effective_parts[2]
            try:
                self._send_json(200, save_robot_assistant_config(robot_id, payload))
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if len(effective_parts) == 4 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "detect":
            robot_id = effective_parts[2]
            if not safe_robot_id(robot_id):
                self._send_json(400, {"ok": False, "error": "invalid robot_id"})
                return
            try:
                self._send_json(200, {"ok": True, "data": probe_robot_record(robot_id)})
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if len(effective_parts) == 4 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "memory":
            robot_id = effective_parts[2]
            if not safe_robot_id(robot_id):
                self._send_json(400, {"ok": False, "error": "invalid robot_id"})
                return
            enabled = bool(payload.get("enabled", True))
            result = set_memory_enabled(robot_id, enabled)
            self._send_json(200 if result.get("ok") else 400, result)
            return

        if len(effective_parts) == 5 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "tools":
            robot_id = effective_parts[2]
            tool_name = effective_parts[4]
            if not safe_robot_id(robot_id):
                self._send_json(400, {"ok": False, "error": "invalid robot_id"})
                return
            if not tool_name.endswith("-tools"):
                self._send_json(400, {"ok": False, "error": "invalid tool_name"})
                return
            enabled = bool(payload.get("enabled", True))
            result = set_tool_enabled(robot_id, tool_name, enabled)
            self._send_json(200 if result.get("ok") else 400, result)
            return

        if len(effective_parts) == 4 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "subscription":
            robot_id = effective_parts[2]
            if not safe_robot_id(robot_id):
                self._send_json(400, {"ok": False, "error": "invalid robot_id"})
                return
            try:
                result = update_subscription(robot_id, payload)
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return

        if len(effective_parts) == 4 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "owner":
            robot_id = effective_parts[2]
            if not safe_robot_id(robot_id):
                self._send_json(400, {"ok": False, "error": "invalid robot_id"})
                return
            try:
                result = update_owner(robot_id, payload)
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return

        if len(effective_parts) == 4 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "users":
            robot_id = effective_parts[2]
            if not safe_robot_id(robot_id):
                self._send_json(400, {"ok": False, "error": "invalid robot_id"})
                return
            try:
                result = add_user(robot_id, payload)
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return

        if len(effective_parts) == 5 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "mobile-codes" and effective_parts[4] == "revoke":
            robot_id = effective_parts[2]
            if not safe_robot_id(robot_id):
                self._send_json(400, {"ok": False, "error": "invalid robot_id"})
                return
            try:
                result = revoke_mobile_onboarding_code(robot_id, payload.get("code"), reason="operator")
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return

        if len(effective_parts) == 5 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "users" and effective_parts[4] == "delete":
            robot_id = effective_parts[2]
            if not safe_robot_id(robot_id):
                self._send_json(400, {"ok": False, "error": "invalid robot_id"})
                return
            try:
                result = delete_user(robot_id, payload.get("user_id"))
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return

        if len(effective_parts) == 5 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "control" and effective_parts[4] == "config":
            robot_id = effective_parts[2]
            if not safe_robot_id(robot_id):
                self._send_json(400, {"ok": False, "error": "invalid robot_id"})
                return
            ws_url = str(payload.get("ws_url", "")).strip()
            cloud_endpoint = payload.get("cloud_endpoint")
            robot_name = payload.get("robot_name")
            backend_mode = payload.get("backend_mode")
            transport = str(payload.get("transport", "")).strip().lower() or None
            try:
                result = set_control_config(
                    robot_id,
                    transport=transport,
                    ws_url=ws_url,
                    cloud_endpoint=cloud_endpoint,
                    robot_name=robot_name,
                    backend_mode=backend_mode,
                )
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return

        if len(effective_parts) == 4 and effective_parts[0] == "api" and effective_parts[1] == "robots" and effective_parts[3] == "agent":
            robot_id = effective_parts[2]
            active_profile_id = str(payload.get("active_profile_id", "") or "").strip()
            fallback_profile_id = str(payload.get("fallback_profile_id", "") or "").strip()
            try:
                self._send_json(200, save_robot_agent_assignment(robot_id, active_profile_id, fallback_profile_id))
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if effective_path == "/api/wifi/connect":
            ssid = str(payload.get("ssid", "")).strip()
            password = str(payload.get("password", "")).strip() or None
            device = str(payload.get("device", "")).strip() or None
            result = wifi_connect(ssid=ssid, password=password, device=device)
            self._send_json(200 if result.get("ok") else 400, result)
            return

        if effective_path == "/api/wifi/disconnect":
            connection = str(payload.get("connection", "")).strip() or None
            device = str(payload.get("device", "")).strip() or None
            result = wifi_disconnect(connection=connection, device=device)
            self._send_json(200 if result.get("ok") else 400, result)
            return

        self._send_json(404, {"ok": False, "error": "not found"})

    def log_message(self, fmt, *args):
        sys.stdout.write("panel: " + (fmt % args) + "\n")
        sys.stdout.flush()


def main():
    host = os.environ.get("PANEL_HOST", "0.0.0.0")
    port = int(os.environ.get("PANEL_PORT", "8876"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"AI Robot GUI panel: http://{host}:{port}")
    print(f"APP_ROOT={APP_ROOT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
