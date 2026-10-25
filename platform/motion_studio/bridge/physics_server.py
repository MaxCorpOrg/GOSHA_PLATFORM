"""Loopback-only editor with an isolated, cancellable CPU simulation worker."""
from __future__ import annotations

import argparse
import asyncio
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import sys
import uuid

from aiohttp import web
from .studio_server import create_app
from physics.model import DEFAULT_SETTINGS, PROFILE, SETTING_LIMITS, fingerprint, validate_request

STUDIO = Path(__file__).resolve().parents[1]
ENGINE_VERSIONS = {"newton": "1.6.1", "warp-lang": "1.17.0", "mujoco": "3.12.0"}


def engine_status():
    try:
        engines = {name: version(name) for name in ENGINE_VERSIONS}
        return engines, engines == ENGINE_VERSIONS
    except PackageNotFoundError:
        return {}, False


def check_origin(request):
    if request.headers.get("Origin") != f"{request.scheme}://{request.host}":
        raise web.HTTPForbidden(text="Запрос разрешён только из этого редактора.")


class PhysicsJobs:
    def __init__(self):
        self.jobs = {}
        self.active = None

    async def start(self, payload):
        if self.active and not self.active["task"].done():
            raise web.HTTPConflict(text="Уже идёт расчёт. Дождитесь результата или отмените его.")
        while len(self.jobs) >= 4:
            self.jobs.pop(next(iter(self.jobs)))
        job = {"id": uuid.uuid4().hex, "status": "running", "request_sha256": fingerprint(payload)}
        self.jobs[job["id"]] = self.active = job
        job["task"] = asyncio.create_task(self.run(job, payload))
        return self.public(job)

    @staticmethod
    def public(job):
        return {key: value for key, value in job.items() if key not in {"task", "process"}}

    async def run(self, job, payload):
        process = None
        try:
            env = dict(os.environ)
            env.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
                       GOSHA_PHYSICS_CACHE=str(STUDIO / "local_only/physics-cache"))
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "physics.runner", cwd=STUDIO, env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            job["process"] = process
            output, errors = await asyncio.wait_for(
                process.communicate(json.dumps(payload, allow_nan=False).encode()), timeout=90)
            if len(output) > 8 * 1024 * 1024:
                raise ValueError("Результат превысил допустимый размер.")
            result = json.loads(output)
            if process.returncode or "error" in result:
                raise ValueError(result.get("error", "Расчёт завершился с ошибкой."))
            job.update(status="completed", result=result)
        except asyncio.CancelledError:
            job["status"] = "cancelled"
        except asyncio.TimeoutError:
            job.update(status="failed", error="Расчёт остановлен: превышено 90 секунд.")
        except Exception as exc:
            job.update(status="failed", error=str(exc)[:600])
        finally:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
            job.pop("process", None)

    async def close(self, _app):
        for job in self.jobs.values():
            if not job["task"].done():
                job["task"].cancel()
        await asyncio.gather(*(job["task"] for job in self.jobs.values()), return_exceptions=True)


def physics_app(dist_dir, *, simulation_only=False):
    jobs = PhysicsJobs()

    async def capabilities(_request):
        engines, available = engine_status()
        return web.json_response({"schema_version": 1, "available": available, "engine": engines,
                                  "device": "cpu", "model": PROFILE, "defaults": DEFAULT_SETTINGS,
                                  "limits": SETTING_LIMITS, "simulation_only": simulation_only,
                                  "preview_only": True, "hardware_validated": False})

    async def submit(request):
        check_origin(request)
        if not engine_status()[1]:
            raise web.HTTPServiceUnavailable(text="Окружение физики не готово. Установите requirements-physics.txt.")
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="Нужен JSON движения.")
        if request.content_length and request.content_length > 2 * 1024 * 1024:
            raise web.HTTPRequestEntityTooLarge(max_size=2 * 1024 * 1024, actual_size=request.content_length)
        try:
            payload = validate_request(await request.json())
        except (ValueError, TypeError) as exc:
            raise web.HTTPBadRequest(text=str(exc))
        return web.json_response(await jobs.start(payload), status=202)

    def lookup(request):
        job = jobs.jobs.get(request.match_info["job_id"])
        if job is None:
            raise web.HTTPNotFound(text="Расчёт больше не доступен. Запустите его заново.")
        return job

    async def status(request):
        return web.json_response(jobs.public(lookup(request)))

    async def cancel(request):
        check_origin(request)
        job = lookup(request)
        if not job["task"].done():
            job["task"].cancel()
            await asyncio.gather(job["task"], return_exceptions=True)
            job["status"] = "cancelled"
        return web.json_response(jobs.public(job))

    def configure(app):
        app.router.add_get("/api/physics/capabilities", capabilities)
        app.router.add_post("/api/physics/jobs", submit)
        app.router.add_get("/api/physics/jobs/{job_id}", status)
        app.router.add_delete("/api/physics/jobs/{job_id}", cancel)
        app.on_cleanup.append(jobs.close)

    # A recorded physical report contains many more samples than its source
    # project. Allow its normal form download while bounding motion submissions.
    return create_app(dist_dir, configure=configure, client_max_size=16 * 1024 * 1024)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=STUDIO / "dist")
    parser.add_argument("--port", type=int, default=18780)
    parser.add_argument("--simulation-only", action="store_true")
    args = parser.parse_args()
    web.run_app(physics_app(args.dist, simulation_only=args.simulation_only),
                host="127.0.0.1", port=args.port, access_log=None)


if __name__ == "__main__":
    main()
