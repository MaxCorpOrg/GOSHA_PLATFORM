#!/usr/bin/env python3
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import gosha_assistant_store as assistant_store
import gosha_agent_store as agent_store


GATEWAY_HOST = os.environ.get("GOSHA_AGENT_GATEWAY_HOST", "127.0.0.1").strip() or "127.0.0.1"
GATEWAY_PORT = int(os.environ.get("GOSHA_AGENT_GATEWAY_PORT", "18110"))
DEFAULT_TIMEOUT_SECONDS = max(3.0, float(os.environ.get("GOSHA_AGENT_GATEWAY_TIMEOUT_SECONDS", "45")))
DEFAULT_PROFILE_ID = os.environ.get("GOSHA_AGENT_GATEWAY_DEFAULT_PROFILE_ID", "").strip()


def json_bytes(payload):
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def log_event(event, **fields):
    payload = {"event": str(event or "").strip() or "unknown"}
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, (bool, int, float)):
            payload[key] = value
            continue
        text = str(value or "").strip()
        if text:
            payload[key] = text[:240]
    sys.stdout.write("agent_gateway: " + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def provider_chat_url(profile):
    base_url = str(profile.get("base_url", "") or "").rstrip("/")
    if not base_url:
        raise ValueError("base_url is required")
    if base_url.endswith("/chat/completions"):
        return base_url
    return base_url + "/chat/completions"


def provider_models_url(profile):
    base_url = str(profile.get("base_url", "") or "").rstrip("/")
    if not base_url:
        raise ValueError("base_url is required")
    if base_url.endswith("/models"):
        return base_url
    return base_url + "/models"


def build_provider_headers(profile):
    headers = {"Content-Type": "application/json"}
    api_key = agent_store.resolve_api_key(profile)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    headers.update(agent_store.normalize_headers(profile.get("headers", {})))
    return headers


def forward_json_request(url, payload, headers, timeout):
    body = json_bytes(payload)
    req = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, raw, dict(resp.headers.items())
    except HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers.items())
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc


def fetch_provider_models(profile):
    req = Request(provider_models_url(profile), headers=build_provider_headers(profile), method="GET")
    try:
        with urlopen(req, timeout=max(3.0, float(profile.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)))) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    item = agent_store.profile_public_view(profile)
    model_id = item.get("model") or item.get("profile_id")
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "owned_by": f"gosha-profile:{item.get('profile_id')}",
            }
        ],
    }


def resolve_default_profile():
    if DEFAULT_PROFILE_ID:
        profile = agent_store.get_agent_profile(DEFAULT_PROFILE_ID)
        if profile and profile.get("enabled"):
            return profile
    for item in agent_store.list_agent_profiles():
        if item.get("enabled"):
            return item
    return None


def resolve_profile_for_request(payload, headers):
    profile_id = str((payload or {}).get("profile_id", "") or "").strip()
    robot_id = str((payload or {}).get("robot_id", "") or headers.get("X-Gosha-Robot-Id", "") or "").strip()
    if profile_id:
        profile = agent_store.get_agent_profile(profile_id)
        if not profile:
            raise ValueError("agent profile not found")
        if not profile.get("enabled"):
            raise ValueError("agent profile is disabled")
        return robot_id, profile
    if not robot_id:
        profile = resolve_default_profile()
        if not profile:
            raise ValueError("robot_id or profile_id is required")
        return "", profile
    effective = assistant_store.effective_robot_assistant_config(robot_id)
    provider_profile = effective.get("provider_profile") or {}
    provider_profile_id = str(provider_profile.get("profile_id", "") or "").strip()
    if not provider_profile_id:
        raise ValueError("robot has no active assistant/provider profile")
    profile = agent_store.get_agent_profile(provider_profile_id)
    if not profile:
        raise ValueError("provider profile not found")
    if not profile.get("enabled"):
        raise ValueError("provider profile is disabled")
    return robot_id, profile


class AgentGatewayHandler(BaseHTTPRequestHandler):
    server_version = "GoshaAgentGateway/1.0"

    def _send_bytes(self, status, body, content_type="application/json; charset=utf-8", extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if extra_headers:
            for key, value in extra_headers.items():
                if not key or value is None:
                    continue
                self.send_header(str(key), str(value))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status, payload):
        self._send_bytes(status, json_bytes(payload))

    def _body_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b"{}"
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise ValueError("invalid json body")
        if not isinstance(data, dict):
            raise ValueError("json body must be an object")
        return data

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/healthz":
            default_profile = resolve_default_profile()
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "gosha-agent-gateway",
                    "default_profile_id": (default_profile or {}).get("profile_id", ""),
                    **agent_store.gateway_health_snapshot(),
                },
            )
            return

        if path == "/v1/providers":
            self._send_json(200, {"ok": True, "providers": agent_store.supported_provider_catalog()})
            return

        if path == "/v1/profiles":
            profiles = [agent_store.profile_public_view(item) for item in agent_store.list_agent_profiles()]
            self._send_json(200, {"ok": True, "profiles": profiles})
            return

        if path == "/v1/models":
            profiles = [item for item in agent_store.list_agent_profiles() if item.get("enabled")]
            models = []
            for item in profiles:
                model_id = item.get("model") or item.get("profile_id")
                models.append(
                    {
                        "id": model_id,
                        "object": "model",
                        "owned_by": f"gosha-profile:{item.get('profile_id')}",
                        "metadata": {
                            "profile_id": item.get("profile_id"),
                            "display_name": item.get("display_name"),
                            "provider_kind": item.get("provider_kind"),
                            "base_url": item.get("base_url"),
                        },
                    }
                )
            self._send_json(200, {"object": "list", "data": models})
            return

        parts = [item for item in path.split("/") if item]
        if len(parts) == 4 and parts[0] == "v1" and parts[1] == "robots" and parts[3] == "active-profile":
            robot_id = parts[2]
            try:
                self._send_json(200, {"ok": True, "data": agent_store.effective_robot_agent(robot_id)})
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            payload = self._body_json()
        except Exception as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if path == "/v1/chat/completions":
            try:
                robot_id, profile = resolve_profile_for_request(payload, self.headers)
                outbound = dict(payload)
                outbound.pop("robot_id", None)
                outbound.pop("profile_id", None)
                if not outbound.get("model"):
                    outbound["model"] = profile.get("model", "")
                timeout = max(3.0, float(profile.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS))
                status, body, headers = forward_json_request(
                    provider_chat_url(profile),
                    outbound,
                    build_provider_headers(profile),
                    timeout,
                )
                log_event(
                    "chat_completion_forwarded",
                    robot_id=robot_id,
                    profile_id=profile.get("profile_id", ""),
                    model=outbound.get("model", ""),
                    status=status,
                )
                passthrough = {}
                for header_name in ("Content-Type",):
                    value = headers.get(header_name) or headers.get(header_name.lower())
                    if value:
                        passthrough[header_name] = value
                self._send_bytes(status, body, extra_headers=passthrough)
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        parts = [item for item in path.split("/") if item]
        if len(parts) == 4 and parts[:2] == ["v1", "robots"] and parts[3] == "active-profile":
            robot_id = parts[2]
            try:
                binding = agent_store.save_robot_binding(
                    robot_id,
                    str(payload.get("active_profile_id", "") or "").strip(),
                    str(payload.get("fallback_profile_id", "") or "").strip(),
                )
                self._send_json(200, {"ok": True, "data": agent_store.effective_robot_agent(robot_id), "binding": binding})
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        self._send_json(404, {"ok": False, "error": "not found"})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization,X-Gosha-Robot-Id")
        self.end_headers()

    def log_message(self, fmt, *args):
        sys.stdout.write("agent_gateway_http: " + (fmt % args) + "\n")


def main():
    server = ThreadingHTTPServer((GATEWAY_HOST, GATEWAY_PORT), AgentGatewayHandler)
    sys.stdout.write(
        f"gosha-agent-gateway listening on http://{GATEWAY_HOST}:{GATEWAY_PORT}\n"
    )
    sys.stdout.flush()
    server.serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
