from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from oauth_executor.config import Settings
from oauth_executor.executor_service import ExecutorService, ExecutorServiceError, verify_webhook_signature


APP_DIR = Path(__file__).resolve().parent
STATIC_INDEX = APP_DIR / "static" / "index.html"
settings = Settings.from_env()
service = ExecutorService(settings)

app = FastAPI(title="GOSHA OAuth Executor", version="1.0.0")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret or "unsafe-dev-secret",
    same_site="lax",
    https_only=settings.cookie_secure,
)


class ExecutionRequest(BaseModel):
    repo_full_name: str = Field(..., examples=["MaxCorpOrg/GOSHA_PLATFORM"])
    pr_number: int = Field(..., ge=1, examples=[1])


def _require_session_token(request: Request) -> str:
    token = str(request.session.get("github_access_token", "") or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Сначала войди через GitHub OAuth.")
    return token


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_INDEX)


@app.get("/healthz")
def healthz() -> dict:
    return {
        "ok": True,
        "service": "gosha-oauth-executor",
        "github_ready": settings.github_ready,
        "service_token_ready": settings.service_token_ready,
        "webhook_ready": settings.webhook_ready,
        "codex_ready": settings.codex_ready,
    }


@app.get("/api/session")
def api_session(request: Request) -> dict:
    return {"ok": True, "session": service.session_payload(request)}


@app.get("/auth/github/start")
def auth_github_start(request: Request, next: str = "/") -> RedirectResponse:
    if not settings.github_ready:
        raise HTTPException(status_code=500, detail="GitHub OAuth ещё не настроен в env сервиса.")
    return RedirectResponse(service.github_auth_start(request, next))


@app.get("/auth/github/callback")
def auth_github_callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    try:
        next_path = service.github_auth_callback(request, code, state)
    except ExecutorServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(next_path)


@app.post("/auth/logout")
def auth_logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@app.post("/api/executions/start")
def executions_start(request: Request, payload: ExecutionRequest) -> dict:
    access_token = _require_session_token(request)
    try:
        job = service.start_manual_job(
            repo_full_name=payload.repo_full_name,
            pr_number=payload.pr_number,
            access_token=access_token,
        )
    except ExecutorServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "job": job}


@app.get("/api/executions")
def executions_list() -> dict:
    return {"ok": True, "jobs": service.list_jobs()}


@app.get("/api/executions/{job_id}")
def executions_get(job_id: str) -> dict:
    job = service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задача исполнения не найдена.")
    return {"ok": True, "job": job}


@app.post("/webhooks/github")
async def webhook_github(request: Request) -> dict:
    signature = request.headers.get("X-Hub-Signature-256", "")
    event_name = request.headers.get("X-GitHub-Event", "")
    body = await request.body()
    if not verify_webhook_signature(settings.github_webhook_secret, body, signature):
        raise HTTPException(status_code=401, detail="Подпись webhook не прошла проверку.")
    try:
        result = service.handle_webhook(event_name=event_name, body=body)
    except ExecutorServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)
