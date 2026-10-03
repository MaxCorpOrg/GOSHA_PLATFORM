"""Serve the built local editor. This process never opens USB or sends commands."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import quote

from aiohttp import web


@web.middleware
async def local_headers(request, handler):
    host = request.headers.get("Host", "").split(":", 1)[0].lower()
    if host not in {"127.0.0.1", "localhost"}:
        raise web.HTTPForbidden(text="Motion Studio is available on this computer only.")
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        response = exc
    response.headers.update({
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "same-origin",
        "Cache-Control": "no-cache",
        "Content-Security-Policy": "frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
    })
    return response


def create_app(dist_dir: Path) -> web.Application:
    dist_dir = dist_dir.resolve()
    if not (dist_dir / "index.html").is_file() or not (dist_dir / "models/gosha.glb").is_file():
        raise ValueError("Built Motion Studio and its full robot model are required.")
    app = web.Application(middlewares=[local_headers], client_max_size=2 * 1024 * 1024)

    async def index(_request):
        return web.FileResponse(dist_dir / "index.html")

    async def export(request):
        if request.headers.get("Origin") != f"{request.scheme}://{request.host}":
            raise web.HTTPForbidden(text="Export must be requested by this editor.")
        fields = await request.post()
        name = fields.get("name", "motion.motion.json")
        if not isinstance(name, str) or not name.endswith(".json") or len(name) > 120:
            raise web.HTTPBadRequest(text="Invalid file name.")
        if any(char in name for char in "/\\\r\n\x00"):
            raise web.HTTPBadRequest(text="Invalid file name.")
        raw = fields.get("json", "")
        if not isinstance(raw, str):
            raise web.HTTPBadRequest(text="Invalid document.")
        if fields.get("raw") == "1":
            body = raw.encode("utf-8")
        else:
            try:
                value = json.loads(raw)
            except (ValueError, TypeError):
                raise web.HTTPBadRequest(text="Invalid JSON.")
            if not isinstance(value, dict):
                raise web.HTTPBadRequest(text="An editor document is required.")
            body = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        return web.Response(
            body=body,
            content_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=motion.json; filename*=UTF-8''{quote(name, safe='')}"},
        )

    app.router.add_get("/", index)
    app.router.add_post("/api/export", export)
    app.router.add_static("/", dist_dir, show_index=False, follow_symlinks=False)
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path(__file__).resolve().parents[1] / "dist")
    parser.add_argument("--port", type=int, default=5176)
    args = parser.parse_args(argv)
    web.run_app(create_app(args.dist), host="127.0.0.1", port=args.port, access_log=None)


if __name__ == "__main__":
    main()
