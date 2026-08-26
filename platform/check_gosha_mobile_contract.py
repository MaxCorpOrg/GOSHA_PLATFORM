#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = os.environ.get("PANEL_URL") or os.environ.get("PUBLIC_PANEL_URL") or ""
PRIVACY_ROUTE = "/legal/gosha-privacy-policy.html"
TERMS_ROUTE = "/legal/gosha-terms-of-use.html"
PRIVACY_ALIAS_ROUTE = "/gosha/privacy"
TERMS_ALIAS_ROUTE = "/gosha/terms"


def request(base_url, path, *, method="GET", timeout=8, body=None, retries=1, retry_delay=0.6):
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    attempts = max(1, int(retries or 1))
    last_result = {"ok": False, "status": 0, "headers": {}, "body": b"", "url": url}
    for attempt in range(attempts):
        try:
            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return {
                    "ok": 200 <= resp.status < 300,
                    "status": resp.status,
                    "headers": dict(resp.headers.items()),
                    "body": raw,
                    "url": url,
                }
        except HTTPError as exc:
            raw = exc.read()
            return {
                "ok": False,
                "status": exc.code,
                "headers": dict(exc.headers.items()),
                "body": raw,
                "url": url,
            }
        except URLError as exc:
            last_result = {"ok": False, "status": 0, "headers": {}, "body": b"", "url": url, "error": str(exc.reason)}
        except Exception as exc:
            last_result = {"ok": False, "status": 0, "headers": {}, "body": b"", "url": url, "error": str(exc)}
        if attempt < attempts - 1:
            time.sleep(max(0.0, float(retry_delay or 0)))
    return last_result


def content_length(headers):
    try:
        return int(headers.get("Content-Length", "0") or "0")
    except ValueError:
        return 0


def content_type(headers):
    return (headers.get("Content-Type") or "").lower()


def print_result(ok, label, detail):
    mark = "OK" if ok else "FAIL"
    print(f"[{mark}] {label}: {detail}")


def check_head_file(base_url, path, label, expected_type, min_bytes, timeout, retries, retry_delay):
    result = request(base_url, path, method="HEAD", timeout=timeout, retries=retries, retry_delay=retry_delay)
    size = content_length(result["headers"])
    ctype = content_type(result["headers"])
    ok = result["status"] == 200 and expected_type in ctype and size >= min_bytes
    detail = f"HTTP {result['status']}, type={ctype or '-'}, bytes={size}, url={result['url']}"
    print_result(ok, label, detail)
    return ok


def check_plans(base_url, timeout, retries, retry_delay):
    result = request(base_url, "/api/mobile/plans", timeout=timeout, retries=retries, retry_delay=retry_delay)
    detail = f"HTTP {result['status']}, url={result['url']}"
    if result["status"] != 200:
        print_result(False, "mobile plans", detail)
        return False
    try:
        payload = json.loads(result["body"].decode("utf-8"))
    except Exception as exc:
        print_result(False, "mobile plans", f"{detail}, invalid json: {exc}")
        return False
    codes = {str(item.get("code", "")) for item in payload.get("plans", []) if isinstance(item, dict)}
    ok = payload.get("ok") is True and "start" in codes and bool(codes)
    print_result(ok, "mobile plans", f"{detail}, plans={', '.join(sorted(codes)) or '-'}")
    return ok


def check_legal_body(base_url, path, label, required_terms, timeout, retries, retry_delay):
    result = request(base_url, path, timeout=timeout, retries=retries, retry_delay=retry_delay)
    detail = f"HTTP {result['status']}, url={result['url']}"
    if result["status"] != 200:
        print_result(False, label, detail)
        return False
    text = result["body"].decode("utf-8", errors="ignore")
    ok = all(term in text for term in required_terms)
    print_result(ok, label, f"{detail}, chars={len(text)}")
    return ok


def check_resolve_code(base_url, code, timeout, retries, retry_delay):
    if not code:
        return True
    result = request(
        base_url,
        "/api/mobile/resolve-code",
        method="POST",
        timeout=timeout,
        body={"code": code},
        retries=retries,
        retry_delay=retry_delay,
    )
    detail = f"HTTP {result['status']}, url={result['url']}"
    if result["status"] != 200:
        print_result(False, "resolve code", detail)
        return False
    try:
        payload = json.loads(result["body"].decode("utf-8"))
    except Exception as exc:
        print_result(False, "resolve code", f"{detail}, invalid json: {exc}")
        return False
    bundle = payload.get("bundle") if isinstance(payload, dict) else None
    if not isinstance(bundle, dict):
        bundle = {}
    required = {"code", "panel_url", "robot_id", "robot_name", "subscription", "owner", "users"}
    missing = sorted(required - set(bundle.keys()))
    mobile_profile = bundle.get("mobile_profile") if isinstance(bundle.get("mobile_profile"), dict) else {}
    required_mobile_profile = {
        "brand",
        "panel_url",
        "mcp_endpoint_base",
        "websocket_url",
        "portal_url",
        "robot_wifi_prefixes",
        "preferred_backend_mode",
    }
    missing_mobile_profile = sorted(required_mobile_profile - set(mobile_profile.keys()))
    ok = payload.get("ok") is True and not missing and not missing_mobile_profile
    print_result(
        ok,
        "resolve code",
        f"{detail}, missing={', '.join(missing) or '-'}, mobile_profile_missing={', '.join(missing_mobile_profile) or '-'}",
    )
    return ok


def main():
    parser = argparse.ArgumentParser(description="Smoke-check public mobile contract for the Android app 'Гоша'.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Panel base URL. Required unless PANEL_URL or PUBLIC_PANEL_URL is set.")
    parser.add_argument("--timeout", type=float, default=8.0, help="HTTP timeout in seconds.")
    parser.add_argument("--retries", type=int, default=3, help="Retries for transient connection failures.")
    parser.add_argument("--retry-delay", type=float, default=0.7, help="Delay between retries in seconds.")
    parser.add_argument("--code", default="", help="Optional onboarding code for non-mutating resolve-code check.")
    args = parser.parse_args()
    if not str(args.base_url or "").strip():
        parser.error("set --base-url, PANEL_URL, or PUBLIC_PANEL_URL")

    checks = [
        check_head_file(
            args.base_url,
            "/downloads/maxcorp-connector-debug.apk",
            "client apk HEAD",
            "application/vnd.android.package-archive",
            100_000,
            args.timeout,
            args.retries,
            args.retry_delay,
        ),
        check_head_file(
            args.base_url,
            "/downloads/maxcorp-admin-connector-debug.apk",
            "admin apk HEAD",
            "application/vnd.android.package-archive",
            100_000,
            args.timeout,
            args.retries,
            args.retry_delay,
        ),
        check_head_file(
            args.base_url,
            PRIVACY_ROUTE,
            "privacy HEAD",
            "text/html",
            100,
            args.timeout,
            args.retries,
            args.retry_delay,
        ),
        check_head_file(
            args.base_url,
            TERMS_ROUTE,
            "terms HEAD",
            "text/html",
            100,
            args.timeout,
            args.retries,
            args.retry_delay,
        ),
        check_head_file(
            args.base_url,
            PRIVACY_ALIAS_ROUTE,
            "privacy alias HEAD",
            "text/html",
            100,
            args.timeout,
            args.retries,
            args.retry_delay,
        ),
        check_head_file(
            args.base_url,
            TERMS_ALIAS_ROUTE,
            "terms alias HEAD",
            "text/html",
            100,
            args.timeout,
            args.retries,
            args.retry_delay,
        ),
        check_legal_body(
            args.base_url,
            PRIVACY_ROUTE,
            "privacy body",
            ["Гоша", "max.corp.org@yandex.ru"],
            args.timeout,
            args.retries,
            args.retry_delay,
        ),
        check_legal_body(
            args.base_url,
            TERMS_ROUTE,
            "terms body",
            ["Гоша", "Условия пользования", "max.corp.org@yandex.ru"],
            args.timeout,
            args.retries,
            args.retry_delay,
        ),
        check_legal_body(
            args.base_url,
            PRIVACY_ALIAS_ROUTE,
            "privacy alias body",
            ["Гоша", "max.corp.org@yandex.ru"],
            args.timeout,
            args.retries,
            args.retry_delay,
        ),
        check_legal_body(
            args.base_url,
            TERMS_ALIAS_ROUTE,
            "terms alias body",
            ["Гоша", "Условия пользования", "max.corp.org@yandex.ru"],
            args.timeout,
            args.retries,
            args.retry_delay,
        ),
        check_plans(args.base_url, args.timeout, args.retries, args.retry_delay),
        check_resolve_code(args.base_url, args.code, args.timeout, args.retries, args.retry_delay),
    ]

    if all(checks):
        print("Гоша mobile contract smoke-check passed.")
        return 0
    print("Гоша mobile contract smoke-check failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
