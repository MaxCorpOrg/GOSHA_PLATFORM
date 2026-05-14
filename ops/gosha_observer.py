#!/usr/bin/env python3
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


INSTALL_ROOT = Path(os.environ.get("GOSHA_INSTALL_ROOT", "/opt/gosha_platform")).resolve()
APP_DIR = INSTALL_ROOT / "app"
RUNTIME_ROOT = INSTALL_ROOT / "runtime"
REPORTS_DIR = RUNTIME_ROOT / "reports"
PANEL_URL = os.environ.get("GOSHA_OBSERVER_PANEL_URL", "http://127.0.0.1:18876")
WS_HOST = os.environ.get("GOSHA_OBSERVER_WS_HOST", "127.0.0.1")
WS_PORT = int(os.environ.get("GOSHA_OBSERVER_WS_PORT", "18080"))
AGENT_GATEWAY_URL = os.environ.get("GOSHA_OBSERVER_AGENT_GATEWAY_URL", "http://127.0.0.1:18110").rstrip("/")
NOW = int(time.time())


def run(cmd):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def git_last_touch(path):
    code, out, _ = run(["git", "-C", str(APP_DIR), "log", "-1", "--format=%ct", "--", str(path)])
    if code != 0 or not out:
        return 0
    try:
        return int(out)
    except ValueError:
        return 0


def check_doc(rel_path, max_age_days):
    path = APP_DIR / rel_path
    touched = git_last_touch(rel_path)
    age_days = None
    if touched > 0:
        age_days = round((NOW - touched) / 86400, 1)
    ok = path.exists() and touched > 0 and (NOW - touched) <= max_age_days * 86400
    return {
        "path": rel_path,
        "exists": path.exists(),
        "last_git_touch": touched,
        "last_git_touch_iso": iso(touched) if touched > 0 else "",
        "age_days": age_days,
        "ok": ok,
    }


def check_service(name):
    code, out, _ = run(["systemctl", "is-active", name])
    return {"name": name, "active": out == "active", "state": out or ("inactive" if code else "")}


def check_http(path):
    url = PANEL_URL.rstrip("/") + path
    return check_http_url(url)


def check_http_url(url):
    try:
        with urlopen(url, timeout=5) as resp:
            body = resp.read(256)
        return {"url": url, "ok": 200 <= resp.status < 300, "status": resp.status, "sample": body.decode("utf-8", errors="ignore")}
    except URLError as exc:
        return {"url": url, "ok": False, "status": 0, "error": str(exc.reason)}
    except Exception as exc:
        return {"url": url, "ok": False, "status": 0, "error": str(exc)}


def check_tcp(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(4)
    try:
        sock.connect((host, port))
        return {"host": host, "port": port, "ok": True}
    except Exception as exc:
        return {"host": host, "port": port, "ok": False, "error": str(exc)}
    finally:
        sock.close()


def git_summary():
    branch = run(["git", "-C", str(APP_DIR), "branch", "--show-current"])[1]
    commit = run(["git", "-C", str(APP_DIR), "rev-parse", "--short", "HEAD"])[1]
    remote = run(["git", "-C", str(APP_DIR), "remote", "get-url", "origin"])[1]
    code, out, _ = run(["git", "-C", str(APP_DIR), "log", "-1", "--format=%ct"])
    head_ts = int(out) if code == 0 and out.isdigit() else 0
    return {
        "branch": branch,
        "commit": commit,
        "remote": remote,
        "head_ts": head_ts,
        "head_ts_iso": iso(head_ts) if head_ts > 0 else "",
    }


def write_reports(status):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    status_path = REPORTS_DIR / "status.json"
    report_path = REPORTS_DIR / "LAST_REPORT_RU.md"
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# GOSHA Observer Report",
        "",
        f"- Сформирован: {status['generated_at_iso']}",
        f"- Итог: {'OK' if status['ok'] else 'WARN'}",
        "",
        "## Git",
        f"- branch: {status['git']['branch'] or '-'}",
        f"- commit: {status['git']['commit'] or '-'}",
        f"- remote: {status['git']['remote'] or '-'}",
        "",
        "## Документы",
    ]
    for item in status["docs"]:
        state = "OK" if item["ok"] else "WARN"
        lines.append(
            f"- [{state}] {item['path']} | exists={item['exists']} | last_git_touch={item['last_git_touch_iso'] or '-'} | age_days={item['age_days'] if item['age_days'] is not None else '-'}"
        )
    lines.extend(["", "## Сервисы"])
    for item in status["services"]:
        state = "OK" if item["active"] else "WARN"
        lines.append(f"- [{state}] {item['name']} -> {item['state']}")
    lines.extend(["", "## HTTP"])
    for item in status["http"]:
        state = "OK" if item["ok"] else "WARN"
        lines.append(f"- [{state}] {item['url']} -> {item.get('status', 0)}")
    lines.extend(["", "## WebSocket"])
    ws = status["websocket"]
    lines.append(f"- [{'OK' if ws['ok'] else 'WARN'}] tcp {ws['host']}:{ws['port']}")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    docs = [
        check_doc("AGENTS.md", 45),
        check_doc("START_HERE_FOR_NEW_CHAT.md", 45),
        check_doc("docs/AGENT_CHECKPOINT_RU.md", 30),
        check_doc("docs/PROJECT_STATUS_RU.md", 30),
        check_doc("platform/AGENTS.md", 45),
        check_doc("ops/AGENTS.md", 45),
    ]
    services = [
        check_service("gosha-backend.service"),
        check_service("gosha-agent-gateway.service"),
        check_service("gosha-panel.service"),
        check_service("gosha-observer.timer"),
    ]
    http = [
        check_http("/api/operator/selfhost-xiaozhi"),
        check_http("/api/mobile/plans"),
        check_http_url(f"{AGENT_GATEWAY_URL}/healthz"),
    ]
    websocket = check_tcp(WS_HOST, WS_PORT)
    status = {
        "generated_at": NOW,
        "generated_at_iso": iso(NOW),
        "git": git_summary(),
        "docs": docs,
        "services": services,
        "http": http,
        "websocket": websocket,
    }
    status["ok"] = all(item["ok"] for item in docs) and all(item["active"] for item in services) and all(item["ok"] for item in http) and websocket["ok"]
    write_reports(status)
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
