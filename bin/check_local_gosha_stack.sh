#!/usr/bin/env bash
set -euo pipefail

PANEL_URL="${1:-http://127.0.0.1:18876}"
GATEWAY_URL="${2:-http://127.0.0.1:18110}"

python3 - "$PANEL_URL" "$GATEWAY_URL" <<'PY'
import json
import os
import time
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

panel_url = sys.argv[1].rstrip("/")
gateway_url = sys.argv[2].rstrip("/")

cookie_jar = CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))


def fatal(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def password_from_env() -> str:
    direct = str(os.environ.get("PANEL_PASSWORD", "") or "").strip()
    if direct:
        return direct
    password_file = str(os.environ.get("PANEL_PASSWORD_FILE", "") or "").strip()
    if password_file:
        try:
            return Path(password_file).read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            return ""
    return ""


def request_json(url: str, method: str = "GET", payload: dict | None = None, allow_401: bool = False) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        if allow_401 and exc.code == 401:
            return {"ok": False, "error": "unauthorized", "auth_required": True}
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        raise RuntimeError(f"HTTP {exc.code}: {body or exc.reason}") from exc


def request_json_retry(url: str, *, method: str = "GET", payload: dict | None = None, allow_401: bool = False, attempts: int = 20, delay: float = 0.5) -> dict:
    last_exc: Exception | None = None
    for _ in range(max(1, attempts)):
        try:
            return request_json(url, method=method, payload=payload, allow_401=allow_401)
        except urllib.error.URLError as exc:
            last_exc = exc
            time.sleep(delay)
        except RuntimeError as exc:
            last_exc = exc
            time.sleep(delay)
    if last_exc:
        raise last_exc
    raise RuntimeError("request failed without explicit exception")


def login_if_needed() -> tuple[bool, str]:
    session = request_json_retry(f"{panel_url}/api/operator/session", allow_401=True)
    auth_enabled = bool(session.get("auth_enabled"))
    authenticated = bool(session.get("authenticated"))
    if not auth_enabled or authenticated:
        return auth_enabled, "already_authenticated" if authenticated else "auth_disabled"
    username = str(os.environ.get("PANEL_USER", "operator") or "operator").strip()
    password = password_from_env()
    if not username or not password:
        fatal(
            "Панель требует логин оператора, но для smoke-check не переданы учётные данные.\n"
            "Укажите PANEL_USER и PANEL_PASSWORD или PANEL_PASSWORD_FILE."
        )
    login = request_json_retry(
        f"{panel_url}/api/operator/login",
        method="POST",
        payload={"username": username, "password": password},
    )
    if not login.get("ok"):
        fatal("Не удалось войти в операторскую панель для smoke-check.")
    return auth_enabled, "logged_in"


def fetch_json(url: str) -> dict:
    return request_json_retry(url)


def is_template_robot(robot: dict) -> bool:
    runtime_class = str(robot.get("runtime_class", "") or "").strip().lower()
    fleet_state = str((robot.get("fleet") or {}).get("state", "") or "").strip().lower()
    return runtime_class == "template" or fleet_state == "template"


def is_support_robot(robot: dict) -> bool:
    fleet_state = str((robot.get("fleet") or {}).get("state", "") or "").strip().lower()
    robot_id = str(robot.get("robot_id", "") or "").strip().lower()
    return fleet_state == "test" or robot_id == "rustore-moderation"


gateway = None
if gateway_url and gateway_url != "-":
    try:
        gateway = fetch_json(f"{gateway_url}/healthz")
    except urllib.error.URLError as exc:
        fatal(
            "Не удалось достучаться до шлюза ИИ-агентов.\n"
            f"Ожидался адрес: {gateway_url}/healthz\n"
            f"Ошибка: {exc}"
        )
    except Exception as exc:
        fatal(f"Шлюз ИИ-агентов ответил неожиданно: {exc}")

auth_enabled, auth_state = login_if_needed()

try:
    robots_payload = fetch_json(f"{panel_url}/api/operator/robots")
except urllib.error.URLError as exc:
    fatal(
        "Не удалось достучаться до панели.\n"
        f"Ожидался адрес: {panel_url}/api/operator/robots\n"
        f"Ошибка: {exc}"
    )
except Exception as exc:
    fatal(f"Панель ответила неожиданно на /api/operator/robots: {exc}")

try:
    assistant_payload = fetch_json(f"{panel_url}/api/operator/assistant-control/catalog")
except urllib.error.URLError as exc:
    fatal(
        "Панель поднята, но каталог ассистента не отвечает.\n"
        f"Ожидался адрес: {panel_url}/api/operator/assistant-control/catalog\n"
        f"Ошибка: {exc}"
    )
except Exception as exc:
    fatal(f"Панель ответила неожиданно на /api/operator/assistant-control/catalog: {exc}")

try:
    selfhost_payload = fetch_json(f"{panel_url}/api/operator/selfhost-xiaozhi")
except Exception:
    selfhost_payload = {"ok": False, "state": {}}

robots = robots_payload.get("robots") or []
catalog = assistant_payload.get("data") or {}
selfhost_state = selfhost_payload.get("state") or {}
first_robot = next((item for item in robots if not is_template_robot(item) and not is_support_robot(item)), robots[0] if robots else {})
cloud = first_robot.get("cloud_console") or {}
gateway_status = catalog.get("gateway") or {}

print("Smoke-check панели Гоша: OK")
print(f"- панель: {panel_url}")
if gateway is not None:
    print(f"- шлюз ИИ-агентов: {gateway_url}")
else:
    print("- шлюз ИИ-агентов: прямой внешний адрес не проверялся, использован статус через панель")
print(f"- auth: {'включена' if auth_enabled else 'отключена'} ({auth_state})")
physical_robots = [item for item in robots if not is_template_robot(item) and not is_support_robot(item)]
print(f"- роботов в панели: {len(robots)}")
print(f"- боевых роботов: {len(physical_robots)}")
print(f"- профилей ассистента: {len(catalog.get('assistants') or [])}")
print(f"- голосов: {len(catalog.get('voices') or [])}")
print(f"- движков TTS: {len(catalog.get('tts_engines') or [])}")
print(f"- pending устройств: {len(selfhost_state.get('pending_devices') or [])}")
print(f"- claimed устройств: {len(selfhost_state.get('claimed_devices') or [])}")

if first_robot:
    print("- первый робот:")
    print(f"  - robot_id: {first_robot.get('robot_id')}")
    print(f"  - backend_mode: {first_robot.get('backend_mode')}")
    print(f"  - cloud_state: {cloud.get('state')}")
    print(f"  - last_seen_iso: {cloud.get('last_seen_iso') or '—'}")
    print(f"  - board_name: {cloud.get('board_name') or '—'}")
    print(f"  - app_version: {cloud.get('app_version') or '—'}")
    print(f"  - remote_addr: {cloud.get('remote_addr') or '—'}")

gateway_ok = bool(gateway_status.get("ok"))
if not gateway_ok:
    fatal("Каталог ассистента не подтвердил связность панели со шлюзом ИИ-агентов.")
PY
