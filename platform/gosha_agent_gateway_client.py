#!/usr/bin/env python3
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = os.environ.get("GOSHA_AGENT_GATEWAY_URL", "http://127.0.0.1:18110").rstrip("/")
DEFAULT_TIMEOUT_SECONDS = max(2.0, float(os.environ.get("GOSHA_AGENT_GATEWAY_TIMEOUT_SECONDS", "5")))


def gateway_base_url():
    return os.environ.get("GOSHA_AGENT_GATEWAY_URL", DEFAULT_BASE_URL).rstrip("/")


def gateway_request(path, *, method="GET", payload=None, headers=None, timeout=None):
    url = gateway_base_url() + path
    body = None
    req_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    req = Request(url, data=body, headers=req_headers, method=method)
    try:
        with urlopen(req, timeout=timeout or DEFAULT_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8")) if raw else {}
        return {"ok": True, "status": 200, "url": url, "data": data}
    except HTTPError as exc:
        raw = exc.read()
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            data = {}
        return {"ok": False, "status": exc.code, "url": url, "data": data, "error": f"http {exc.code}", "detail": str(exc)}
    except URLError as exc:
        return {"ok": False, "status": 0, "url": url, "data": {}, "error": str(exc.reason)}
    except Exception as exc:
        return {"ok": False, "status": 0, "url": url, "data": {}, "error": str(exc)}


def health_snapshot():
    res = gateway_request("/healthz", timeout=DEFAULT_TIMEOUT_SECONDS)
    if not res.get("ok"):
        return {
            "ok": False,
            "url": res.get("url", gateway_base_url() + "/healthz"),
            "error": res.get("error", "request_failed"),
            "status": int(res.get("status", 0) or 0),
        }
    data = res.get("data", {}) if isinstance(res.get("data"), dict) else {}
    return {
        "ok": bool(data.get("ok", True)),
        "url": res.get("url", gateway_base_url() + "/healthz"),
        "status": int(res.get("status", 200) or 200),
        "service": str(data.get("service", "gosha-agent-gateway") or "gosha-agent-gateway"),
        "storage_backend": str(data.get("storage_backend", "file") or "file"),
        "profiles_count": int(data.get("profiles_count", 0) or 0),
        "enabled_profiles_count": int(data.get("enabled_profiles_count", 0) or 0),
        "default_profiles_count": int(data.get("default_profiles_count", 0) or 0),
        "supported_providers": data.get("supported_providers", []),
    }
