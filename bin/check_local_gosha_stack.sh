#!/usr/bin/env bash
set -euo pipefail

PANEL_URL="${1:-http://127.0.0.1:18876}"
GATEWAY_URL="${2:-http://127.0.0.1:18110}"

python3 - "$PANEL_URL" "$GATEWAY_URL" <<'PY'
import json
import sys
import urllib.error
import urllib.request

panel_url = sys.argv[1].rstrip("/")
gateway_url = sys.argv[2].rstrip("/")


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)


def fatal(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


try:
    gateway = fetch_json(f"{gateway_url}/healthz")
except urllib.error.URLError as exc:
    fatal(
        "Не удалось достучаться до локального шлюза ИИ-агентов.\n"
        f"Ожидался адрес: {gateway_url}/healthz\n"
        f"Ошибка: {exc}"
    )
except Exception as exc:
    fatal(f"Шлюз ИИ-агентов ответил неожиданно: {exc}")

try:
    robots_payload = fetch_json(f"{panel_url}/api/operator/robots")
except urllib.error.URLError as exc:
    fatal(
        "Не удалось достучаться до локальной панели.\n"
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

robots = robots_payload.get("robots") or []
catalog = assistant_payload.get("data") or {}
first_robot = robots[0] if robots else {}
cloud = first_robot.get("cloud_console") or {}

print("Локальный smoke-check Гоши: OK")
print(f"- панель: {panel_url}")
print(f"- шлюз ИИ-агентов: {gateway_url}")
print(f"- роботов в панели: {len(robots)}")
print(f"- профилей ассистента: {len(catalog.get('assistants') or [])}")
print(f"- голосов: {len(catalog.get('voices') or [])}")
print(f"- движков TTS: {len(catalog.get('tts_engines') or [])}")

if first_robot:
    print("- первый робот:")
    print(f"  - robot_id: {first_robot.get('robot_id')}")
    print(f"  - backend_mode: {first_robot.get('backend_mode')}")
    print(f"  - cloud_state: {cloud.get('state')}")
    print(f"  - last_seen_iso: {cloud.get('last_seen_iso') or '—'}")
    print(f"  - board_name: {cloud.get('board_name') or '—'}")
    print(f"  - app_version: {cloud.get('app_version') or '—'}")
    print(f"  - remote_addr: {cloud.get('remote_addr') or '—'}")

gateway_ok = bool((catalog.get("gateway") or {}).get("ok"))
if not gateway_ok:
    fatal("Каталог ассистента не подтвердил связность со шлюзом ИИ-агентов.")
PY
