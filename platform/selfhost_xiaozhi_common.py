#!/usr/bin/env python3
import json
import os
import re
import secrets
import shlex
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


APP_ROOT = Path(os.environ.get("APP_ROOT", "/opt/gosha_platform/runtime/app_root")).resolve()
ROBOTS_DIR = APP_ROOT / "robots"
SELFHOST_DIR = APP_ROOT / "selfhost_xiaozhi"
STATE_PATH = SELFHOST_DIR / "state.json"
ROBOT_ID_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
BACKEND_MODE_SELF_HOSTED = "self_hosted_xiaozhi"
BACKEND_MODE_XIAOZHI_CLOUD = "xiaozhi_cloud"


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
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines() if path.exists() else []
    used = set()
    out = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw:
            out.append(raw)
            continue
        key, _ = raw.split("=", 1)
        clean_key = key.strip()
        if clean_key in updates:
            out.append(f"{clean_key}={shell_env_value(updates[clean_key])}")
            used.add(clean_key)
        else:
            out.append(raw)
    for key, value in updates.items():
        if key in used:
            continue
        out.append(f"{key}={shell_env_value(value)}")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def robot_env_path(robot_id):
    return ROBOTS_DIR / robot_id / "robot.env"


def mcp_endpoint_path(robot_id):
    return ROBOTS_DIR / robot_id / "mcp_endpoint.txt"


def safe_robot_id(robot_id):
    return bool(ROBOT_ID_RE.fullmatch(str(robot_id or "").strip()))


def ensure_trailing_slash(value):
    text = str(value or "").strip()
    if not text:
        return ""
    return text if text.endswith("/") else text + "/"


def normalize_url(value, default_scheme="http"):
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"{default_scheme}://{text}"
    parsed = urlparse(text)
    if not parsed.netloc:
        return ""
    path = parsed.path or ""
    return urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, "")).rstrip("/")


def derive_ws_base(http_base, default_path):
    parsed = urlparse(normalize_url(http_base, default_scheme="http"))
    if not parsed.netloc:
        return ""
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, ensure_trailing_slash(default_path), "", "", "")).rstrip("/") + "/"


def append_query(url, params):
    parsed = urlparse(url)
    merged = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key, value in params.items():
        if value is None:
            continue
        merged[str(key)] = str(value)
    query = urlencode(merged)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", query, ""))


def generate_claim_code(length=6):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(max(4, int(length or 6))))


def default_backend_config():
    public_http_base = normalize_url(
        os.environ.get("SELFHOST_XIAOZHI_PUBLIC_HTTP_BASE")
        or os.environ.get("PUBLIC_PANEL_URL")
        or "http://151.241.228.232:18876",
        default_scheme="http",
    )
    ota_url = ensure_trailing_slash(
        normalize_url(
            os.environ.get("SELFHOST_GOSHA_OTA_URL")
            or os.environ.get("SELFHOST_XIAOZHI_OTA_URL"),
            default_scheme="http",
        )
        or f"{public_http_base}/gosha/ota"
    )
    activate_url = normalize_url(
        os.environ.get("SELFHOST_GOSHA_ACTIVATE_URL")
        or os.environ.get("SELFHOST_XIAOZHI_ACTIVATE_URL"),
        default_scheme="http",
    ) or f"{ota_url}activate"
    websocket_url = ensure_trailing_slash(
        normalize_url(
            os.environ.get("SELFHOST_GOSHA_WS_URL")
            or os.environ.get("SELFHOST_XIAOZHI_WS_URL"),
            default_scheme="ws",
        )
        or derive_ws_base(public_http_base, "/xiaozhi/v1/")
    )
    mcp_endpoint_base = ensure_trailing_slash(
        normalize_url(os.environ.get("SELFHOST_XIAOZHI_MCP_ENDPOINT_BASE"), default_scheme="ws")
        or derive_ws_base(public_http_base, "/mcp/")
    )
    return {
        "provider": BACKEND_MODE_SELF_HOSTED,
        "backend_mode": BACKEND_MODE_SELF_HOSTED,
        "transport": "websocket_only",
        "public_http_base": public_http_base,
        "ota_url": ota_url,
        "activate_url": activate_url,
        "websocket_url": websocket_url,
        "mcp_endpoint_base": mcp_endpoint_base,
        "upstream_repo": "https://github.com/xinnan-tech/xiaozhi-esp32-server",
        "checked_at": now_ts(),
        "checked_at_iso": ts_to_iso(now_ts()),
    }


def normalize_device_record(raw, *, device_id=""):
    item = raw if isinstance(raw, dict) else {}
    first_seen = int(item.get("first_seen", 0) or 0)
    last_seen = int(item.get("last_seen", 0) or 0)
    claimed_at = int(item.get("claimed_at", 0) or 0)
    return {
        "device_id": str(item.get("device_id", "") or device_id or "").strip(),
        "client_id": str(item.get("client_id", "") or "").strip(),
        "serial_number": str(item.get("serial_number", "") or "").strip(),
        "claim_code": str(item.get("claim_code", "") or generate_claim_code()).strip(),
        "activation_challenge": str(item.get("activation_challenge", "") or secrets.token_hex(16)).strip(),
        "remote_addr": str(item.get("remote_addr", "") or "").strip(),
        "board": str(item.get("board", "") or "").strip(),
        "app_version": str(item.get("app_version", "") or "").strip(),
        "payload": item.get("payload", {}) if isinstance(item.get("payload"), dict) else {},
        "headers": item.get("headers", {}) if isinstance(item.get("headers"), dict) else {},
        "status": str(item.get("status", "") or "pending").strip(),
        "robot_id": str(item.get("robot_id", "") or "").strip(),
        "first_seen": first_seen,
        "first_seen_iso": str(item.get("first_seen_iso", "") or ts_to_iso(first_seen)),
        "last_seen": last_seen,
        "last_seen_iso": str(item.get("last_seen_iso", "") or ts_to_iso(last_seen)),
        "claimed_at": claimed_at,
        "claimed_at_iso": str(item.get("claimed_at_iso", "") or ts_to_iso(claimed_at)),
        "websocket_url": str(item.get("websocket_url", "") or "").strip(),
        "websocket_token": str(item.get("websocket_token", "") or "").strip(),
        "control_mcp_endpoint": str(item.get("control_mcp_endpoint", "") or "").strip(),
    }


def load_state():
    raw = load_json(STATE_PATH, {})
    pending_raw = raw.get("pending_devices", {}) if isinstance(raw.get("pending_devices"), dict) else {}
    claims_raw = raw.get("claims", {}) if isinstance(raw.get("claims"), dict) else {}
    state = {
        "backend": default_backend_config(),
        "pending_devices": {},
        "claims": {},
    }
    if isinstance(raw.get("backend"), dict):
        state["backend"].update(raw.get("backend"))
    for device_id, item in pending_raw.items():
        normalized = normalize_device_record(item, device_id=device_id)
        if normalized["device_id"]:
            state["pending_devices"][normalized["device_id"]] = normalized
    for device_id, item in claims_raw.items():
        normalized = normalize_device_record(item, device_id=device_id)
        if normalized["device_id"]:
            normalized["status"] = "claimed"
            state["claims"][normalized["device_id"]] = normalized
    return state


def save_state(state):
    payload = {
        "backend": state.get("backend", default_backend_config()),
        "pending_devices": state.get("pending_devices", {}),
        "claims": state.get("claims", {}),
    }
    save_json_atomic(STATE_PATH, payload)


def find_claim_by_robot(robot_id, state=None):
    data = state if isinstance(state, dict) else load_state()
    robot_key = str(robot_id or "").strip()
    if not robot_key:
        return None
    for item in (data.get("claims") or {}).values():
        if str((item or {}).get("robot_id", "")).strip() == robot_key:
            return normalize_device_record(item, device_id=item.get("device_id", ""))
    return None


def find_claim_by_device(device_id, state=None):
    data = state if isinstance(state, dict) else load_state()
    device_key = str(device_id or "").strip()
    if not device_key:
        return None
    item = (data.get("claims") or {}).get(device_key)
    if not isinstance(item, dict):
        return None
    normalized = normalize_device_record(item, device_id=device_key)
    normalized["status"] = "claimed"
    return normalized


def list_pending_devices(state=None):
    data = state if isinstance(state, dict) else load_state()
    items = [normalize_device_record(item, device_id=device_id) for device_id, item in (data.get("pending_devices") or {}).items()]
    items.sort(key=lambda item: int(item.get("last_seen", 0) or 0), reverse=True)
    return items


def list_claimed_devices(state=None):
    data = state if isinstance(state, dict) else load_state()
    items = [normalize_device_record(item, device_id=device_id) for device_id, item in (data.get("claims") or {}).items()]
    for item in items:
        item["status"] = "claimed"
    items.sort(key=lambda item: int(item.get("claimed_at", 0) or 0), reverse=True)
    return items


def extract_device_runtime_meta(item):
    payload = item.get("payload") if isinstance(item, dict) else {}
    board_raw = payload.get("board") if isinstance(payload, dict) else {}
    application_raw = payload.get("application") if isinstance(payload, dict) else {}

    board_name = str((item or {}).get("board", "") or "").strip()
    board_ip = ""
    if isinstance(board_raw, dict):
        board_name = str(
            board_raw.get("type")
            or board_raw.get("name")
            or board_raw.get("board_name")
            or board_name
            or ""
        ).strip()
        board_ip = str(
            board_raw.get("ip")
            or board_raw.get("local_ip")
            or board_raw.get("wifi_ip")
            or board_raw.get("sta_ip")
            or ""
        ).strip()
    elif isinstance(board_raw, str) and board_raw.strip():
        board_name = board_raw.strip()

    app_version = str((item or {}).get("app_version", "") or "").strip()
    if not app_version and isinstance(application_raw, dict):
        app_version = str(application_raw.get("version", "") or "").strip()

    return {
        "board_name": board_name,
        "board_ip": board_ip,
        "app_version": app_version,
        "remote_addr": str((item or {}).get("remote_addr", "") or "").strip(),
    }


def record_device_contact(*, device_id, client_id="", serial_number="", payload=None, headers=None, remote_addr=""):
    device_key = str(device_id or "").strip()
    if not device_key:
        raise ValueError("device_id is required")
    state = load_state()
    claimed_raw = (state.get("claims") or {}).get(device_key)
    claimed = normalize_device_record(claimed_raw, device_id=device_key) if isinstance(claimed_raw, dict) else {"device_id": ""}
    now = now_ts()
    if claimed["device_id"]:
        claimed["client_id"] = str(client_id or claimed.get("client_id", "") or "").strip()
        claimed["serial_number"] = str(serial_number or claimed.get("serial_number", "") or "").strip()
        claimed["last_seen"] = now
        claimed["last_seen_iso"] = ts_to_iso(now)
        claimed["remote_addr"] = str(remote_addr or claimed.get("remote_addr", "") or "").strip()
        if isinstance(payload, dict) and payload:
            claimed["payload"] = payload
        if isinstance(headers, dict) and headers:
            claimed["headers"] = headers
        state["claims"][device_key] = claimed
        save_state(state)
        return claimed

    pending_raw = (state.get("pending_devices") or {}).get(device_key)
    current = normalize_device_record(pending_raw, device_id=device_key) if isinstance(pending_raw, dict) else normalize_device_record({"device_id": device_key}, device_id=device_key)
    if not current["first_seen"]:
        current["first_seen"] = now
        current["first_seen_iso"] = ts_to_iso(now)
    current["last_seen"] = now
    current["last_seen_iso"] = ts_to_iso(now)
    current["status"] = "pending"
    current["client_id"] = str(client_id or current.get("client_id", "") or "").strip()
    current["serial_number"] = str(serial_number or current.get("serial_number", "") or "").strip()
    current["remote_addr"] = str(remote_addr or current.get("remote_addr", "") or "").strip()
    if isinstance(payload, dict) and payload:
        current["payload"] = payload
        current["board"] = str(payload.get("board", current.get("board", "")) or "").strip()
        current["app_version"] = str(payload.get("application", {}).get("version", current.get("app_version", "")) or current.get("app_version", "")).strip()
    if isinstance(headers, dict) and headers:
        current["headers"] = headers
    state["pending_devices"][device_key] = current
    save_state(state)
    return current


def claim_device_to_robot(device_id, robot_id, *, websocket_url="", websocket_token="", control_mcp_endpoint=""):
    device_key = str(device_id or "").strip()
    robot_key = str(robot_id or "").strip()
    if not device_key:
        raise ValueError("device_id is required")
    if not safe_robot_id(robot_key):
        raise ValueError("invalid robot_id")

    state = load_state()
    backend = state.get("backend") or default_backend_config()
    pending_raw = (state.get("pending_devices") or {}).get(device_key)
    pending = normalize_device_record(pending_raw, device_id=device_key) if isinstance(pending_raw, dict) else {"device_id": ""}
    existing = find_claim_by_robot(robot_key, state=state)
    if existing and existing.get("device_id") != device_key:
        state["claims"].pop(existing["device_id"], None)
    claim_raw = (state.get("claims") or {}).get(device_key)
    current = pending if pending.get("device_id") else (
        normalize_device_record(claim_raw, device_id=device_key) if isinstance(claim_raw, dict) else {"device_id": ""}
    )
    if not current["device_id"]:
        raise ValueError("device not found")

    token = str(websocket_token or current.get("websocket_token", "") or secrets.token_urlsafe(24)).strip()
    resolved_ws_url = ensure_trailing_slash(str(websocket_url or current.get("websocket_url", "") or backend.get("websocket_url", "")).strip())
    resolved_mcp_endpoint = str(control_mcp_endpoint or current.get("control_mcp_endpoint", "") or "").strip()
    if not resolved_mcp_endpoint:
        resolved_mcp_endpoint = append_query(backend.get("mcp_endpoint_base", ""), {"token": token, "robot_id": robot_key})

    claimed_at = now_ts()
    current["robot_id"] = robot_key
    current["status"] = "claimed"
    current["claimed_at"] = claimed_at
    current["claimed_at_iso"] = ts_to_iso(claimed_at)
    current["last_seen"] = max(current.get("last_seen", 0), claimed_at)
    current["last_seen_iso"] = ts_to_iso(current["last_seen"])
    current["websocket_url"] = resolved_ws_url
    current["websocket_token"] = token
    current["control_mcp_endpoint"] = resolved_mcp_endpoint

    state["claims"][device_key] = current
    state["pending_devices"].pop(device_key, None)
    save_state(state)
    return current


def build_robot_runtime_claim(robot_id, env=None, state=None):
    details = env if isinstance(env, dict) else load_env(robot_env_path(robot_id))
    active_state = state if isinstance(state, dict) else load_state()
    claim = find_claim_by_robot(robot_id, state=active_state)
    backend = active_state.get("backend") or default_backend_config()
    backend_mode = str((details or {}).get("ROBOT_BACKEND_MODE", "") or "").strip().lower()
    if backend_mode == BACKEND_MODE_XIAOZHI_CLOUD and not claim:
        return {
            "provider": BACKEND_MODE_XIAOZHI_CLOUD,
            "backend_mode": BACKEND_MODE_XIAOZHI_CLOUD,
            "configured": False,
            "device_claimed": False,
            "state": "missing",
            "detail": "Платформа Гоша ещё не настроена",
        }
    runtime_meta = extract_device_runtime_meta(claim or {})
    return {
        "provider": BACKEND_MODE_SELF_HOSTED,
        "backend_mode": BACKEND_MODE_SELF_HOSTED,
        "configured": True,
        "device_claimed": bool(claim),
        "state": "claimed" if claim else "awaiting_claim",
        "detail": "Платформа Гоша: устройство привязано к роботу" if claim else "Платформа Гоша: режим включён, устройство ещё не привязано",
        "backend": backend,
        "device_id": (claim or {}).get("device_id", ""),
        "client_id": (claim or {}).get("client_id", ""),
        "serial_number": (claim or {}).get("serial_number", ""),
        "claimed_at": (claim or {}).get("claimed_at", 0),
        "claimed_at_iso": (claim or {}).get("claimed_at_iso", ""),
        "last_seen": (claim or {}).get("last_seen", 0),
        "last_seen_iso": (claim or {}).get("last_seen_iso", ""),
        "board_name": runtime_meta.get("board_name", ""),
        "board_ip": runtime_meta.get("board_ip", ""),
        "app_version": runtime_meta.get("app_version", ""),
        "remote_addr": runtime_meta.get("remote_addr", ""),
        "control_mcp_endpoint": (claim or {}).get("control_mcp_endpoint", ""),
        "websocket_url": (claim or {}).get("websocket_url", backend.get("websocket_url", "")),
        "websocket_token_configured": bool((claim or {}).get("websocket_token")),
    }


def ota_payload_for_device(device_id):
    state = load_state()
    backend = state.get("backend") or default_backend_config()
    device_key = str(device_id or "").strip()
    claim_raw = (state.get("claims") or {}).get(device_key)
    claim = normalize_device_record(claim_raw, device_id=device_key) if isinstance(claim_raw, dict) else {"device_id": ""}
    server_time = {
        "timestamp": int(time.time() * 1000),
        "timezone_offset": 180,
    }
    firmware = {
        "version": "",
        "url": "",
    }
    if claim["device_id"]:
        runtime_events_url = str(backend.get("public_http_base", "") or "").rstrip("/") + "/gosha/events"
        return {
            "activation": {},
            "websocket": {
                "url": claim.get("websocket_url") or backend.get("websocket_url", ""),
                "token": claim.get("websocket_token", ""),
                "version": 1,
            },
            "runtime_events": {
                "url": runtime_events_url,
                "token": claim.get("websocket_token", ""),
                "schema_version": "gosha.runtime.event.v1",
                "heartbeat_interval_seconds": 30,
            },
            "server_time": server_time,
            "firmware": firmware,
        }

    pending_raw = (state.get("pending_devices") or {}).get(device_key)
    pending = normalize_device_record(pending_raw, device_id=device_key) if isinstance(pending_raw, dict) else {"device_id": ""}
    if not pending["device_id"]:
        raise ValueError("device not registered")
    return {
        "activation": {
            "code": pending.get("claim_code", ""),
            "message": "Устройство ожидает привязки в панели MAX CORP",
            "challenge": pending.get("activation_challenge", ""),
            "timeout_ms": 10000,
        },
        "server_time": server_time,
        "firmware": firmware,
    }


def activation_response_status(device_id):
    state = load_state()
    device_key = str(device_id or "").strip()
    return 200 if device_key in (state.get("claims") or {}) else 202
