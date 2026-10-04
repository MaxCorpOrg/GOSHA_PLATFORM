"""Authenticated delivery of the same Motion Studio build used locally."""
from pathlib import Path
import json
import re
from urllib.parse import parse_qs, quote, unquote

ROOT = Path(__file__).with_name("motion_studio") / "dist"
PANEL_ORIGIN = "https://gosha.website"
MAX_EXPORT_BYTES = 2 * 1024 * 1024
ASSET = re.compile(r"assets/[A-Za-z0-9_-]+\.(js|css)\Z")
HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Content-Security-Policy": "frame-ancestors 'self'; object-src 'none'; base-uri 'self'",
}


def handle(handler, parsed, *, root=ROOT):
    if not parsed.path.startswith("/api/operator/motion-studio/") and parsed.path != "/api/operator/motion-studio":
        return False
    if not handler._require_operator_auth():
        return True
    relative = unquote(parsed.path.removeprefix("/api/operator/motion-studio")).lstrip("/")
    if handler.command == "POST" and relative == "api/export":
        export(handler)
        return True
    if handler.command not in {"GET", "HEAD"}:
        handler._send_json(405, {"ok": False, "error": "method not allowed"})
        return True
    if not relative and parsed.path == "/api/operator/motion-studio":
        handler.send_response(302)
        handler.send_header("Location", "/api/operator/motion-studio/")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return True
    relative = relative or "index.html"
    asset = ASSET.fullmatch(relative)
    if relative == "index.html":
        content_type = "text/html; charset=utf-8"
    elif relative == "models/gosha.glb":
        content_type = "model/gltf-binary"
    elif asset:
        content_type = "text/javascript; charset=utf-8" if asset[1] == "js" else "text/css; charset=utf-8"
    else:
        handler._send_json(404, {"ok": False, "error": "editor asset not found"})
        return True
    base = root.resolve()
    path = (base / relative).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        handler._send_json(404, {"ok": False, "error": "editor asset not found"})
        return True
    body = path.read_bytes() if handler.command == "GET" else None
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body) if body is not None else path.stat().st_size))
    handler.send_header("Cache-Control", "no-cache")
    for key, value in HEADERS.items():
        handler.send_header(key, value)
    handler.end_headers()
    if body is not None:
        handler.wfile.write(body)
    return True


def export(handler):
    if handler.headers.get("Origin") != PANEL_ORIGIN:
        handler._send_json(403, {"ok": False, "error": "export origin not allowed"})
        return
    try:
        size = int(handler.headers.get("Content-Length", "0"))
        if not 0 < size <= MAX_EXPORT_BYTES:
            raise ValueError()
        if not handler.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
            raise ValueError()
        fields = parse_qs(handler.rfile.read(size).decode("utf-8"), keep_blank_values=True, max_num_fields=3)
        name = fields.get("name", ["motion.motion.json"])[0]
        if not name.endswith(".json") or len(name) > 120 or any(c in name for c in "/\\\r\n\x00"):
            raise ValueError()
        raw = fields.get("json", [""])[0]
        if fields.get("raw", ["0"])[0] == "1":
            body = raw.encode("utf-8")
        else:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError()
            body = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        handler._send_json(400, {"ok": False, "error": "invalid editor export"})
        return
    handler._send_bytes(200, body, "application/json; charset=utf-8", {
        **HEADERS,
        "Content-Disposition": f"attachment; filename=motion.json; filename*=UTF-8''{quote(name, safe='')}",
    })
