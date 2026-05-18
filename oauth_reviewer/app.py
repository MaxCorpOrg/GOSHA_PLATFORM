from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from oauth_reviewer.codex_review import (
    CodexReviewError,
    codex_login_status,
    generate_review_markdown_via_codex,
)
from oauth_reviewer.config import Settings
from oauth_reviewer.github_api import (
    GitHubApiError,
    create_pull_request_review,
    exchange_code_for_token,
    sanitize_internal_redirect_path,
    fetch_pull_request,
    fetch_pull_request_files,
    fetch_user,
    github_authorization_url,
)
from oauth_reviewer.openai_review import OpenAIReviewError, build_review_prompt, generate_review_markdown
from oauth_reviewer.repo_guidance import collect_relevant_agents, ensure_repo_allowed


APP_DIR = Path(__file__).resolve().parent
STATIC_INDEX = APP_DIR / "static" / "index.html"
settings = Settings.from_env()

app = FastAPI(title="GOSHA OAuth Reviewer", version="1.0.0")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret or "unsafe-dev-secret",
    same_site="lax",
    https_only=settings.cookie_secure,
)


class ReviewRequest(BaseModel):
    repo_full_name: str = Field(..., examples=["MaxCorpOrg/GOSHA_PLATFORM"])
    pr_number: int = Field(..., ge=1, examples=[1])


def _resolve_review_backend() -> tuple[str, dict[str, str | bool]]:
    codex_status = codex_login_status(settings.codex_command)
    requested = settings.review_backend

    if requested == "codex_cli":
        if bool(codex_status["available"]) and bool(codex_status["logged_in"]):
            return "codex_cli", codex_status
        return "", codex_status

    if requested == "openai_api":
        return ("openai_api", codex_status) if settings.openai_ready else ("", codex_status)

    if bool(codex_status["available"]) and bool(codex_status["logged_in"]):
        return "codex_cli", codex_status
    if settings.openai_ready:
        return "openai_api", codex_status
    return "", codex_status


def _session_payload(request: Request) -> dict:
    session = request.session
    review_backend, codex_status = _resolve_review_backend()
    return {
        "authenticated": bool(session.get("github_access_token")),
        "github_login": session.get("github_login", ""),
        "github_id": session.get("github_id", 0),
        "allowed_repos": list(settings.allowed_repos),
        "github_ready": settings.github_ready,
        "review_ready": bool(review_backend),
        "review_backend": review_backend,
        "requested_review_backend": settings.review_backend,
        "codex_ready": bool(codex_status["available"]) and bool(codex_status["logged_in"]),
        "codex_status_text": str(codex_status["status_text"]),
        "openai_ready": settings.openai_ready,
        "repo_path": str(settings.repo_path),
    }


def _require_session_token(request: Request) -> str:
    token = str(request.session.get("github_access_token", "") or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Сначала войди через GitHub OAuth.")
    return token


def _run_review(access_token: str, repo_full_name: str, pr_number: int) -> dict:
    ensure_repo_allowed(repo_full_name, settings.allowed_repos)
    review_backend, codex_status = _resolve_review_backend()
    if not review_backend:
        if settings.review_backend == "codex_cli":
            raise HTTPException(
                status_code=500,
                detail=(
                    "Reviewer настроен на локальный Codex CLI, но он сейчас не готов. "
                    f"Статус: {codex_status['status_text']}"
                ),
            )
        raise HTTPException(
            status_code=500,
            detail=(
                "Не найден готовый backend reviewer. "
                "Нужен либо локально авторизованный Codex CLI, либо настроенный OpenAI-совместимый backend."
            ),
        )

    try:
        pr_payload = fetch_pull_request(access_token, repo_full_name, pr_number)
        file_payloads = fetch_pull_request_files(access_token, repo_full_name, pr_number)
        changed_files = [str(item.get("filename", "") or "").strip() for item in file_payloads if item.get("filename")]
        agents_sections = collect_relevant_agents(settings.repo_path, changed_files)
        prompt = build_review_prompt(
            repo_full_name=repo_full_name,
            pr_payload=pr_payload,
            file_payloads=file_payloads,
            agents_sections=agents_sections,
            max_patch_chars_per_file=settings.max_patch_chars_per_file,
            max_total_patch_chars=settings.max_total_patch_chars,
        )
        if review_backend == "codex_cli":
            review_markdown = generate_review_markdown_via_codex(
                codex_command=settings.codex_command,
                repo_path=settings.repo_path,
                prompt=prompt,
                model=settings.codex_model,
                profile=settings.codex_profile,
                timeout_seconds=settings.codex_timeout_seconds,
            )
        else:
            review_markdown = generate_review_markdown(
                openai_api_key=settings.openai_api_key,
                openai_base_url=settings.openai_base_url,
                openai_model=settings.openai_model,
                prompt=prompt,
            )
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except GitHubApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (OpenAIReviewError, CodexReviewError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "repo_full_name": repo_full_name,
        "pr_number": pr_number,
        "pr_title": pr_payload.get("title", ""),
        "changed_files": changed_files,
        "review_backend": review_backend,
        "review_markdown": review_markdown,
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_INDEX)


@app.get("/healthz")
def healthz() -> dict:
    review_backend, codex_status = _resolve_review_backend()
    return {
        "ok": True,
        "service": "gosha-oauth-reviewer",
        "github_ready": settings.github_ready,
        "review_ready": bool(review_backend),
        "review_backend": review_backend,
        "requested_review_backend": settings.review_backend,
        "codex_ready": bool(codex_status["available"]) and bool(codex_status["logged_in"]),
        "openai_ready": settings.openai_ready,
    }


@app.get("/api/session")
def api_session(request: Request) -> dict:
    return {"ok": True, "session": _session_payload(request)}


@app.get("/auth/github/start")
def auth_github_start(request: Request, next: str = "/") -> RedirectResponse:
    if not settings.github_ready:
        raise HTTPException(status_code=500, detail="GitHub OAuth ещё не настроен в env сервиса.")
    state = secrets.token_urlsafe(24)
    request.session["github_oauth_state"] = state
    request.session["github_oauth_next"] = sanitize_internal_redirect_path(next)
    return RedirectResponse(
        github_authorization_url(
            client_id=settings.github_client_id,
            redirect_uri=settings.github_redirect_uri,
            scope=settings.github_scope,
            state=state,
        )
    )


@app.get("/auth/github/callback")
def auth_github_callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    expected_state = str(request.session.get("github_oauth_state", "") or "")
    if not code or not state or not expected_state or state != expected_state:
        raise HTTPException(status_code=400, detail="OAuth state не совпал или GitHub не вернул code.")
    try:
        access_token = exchange_code_for_token(
            client_id=settings.github_client_id,
            client_secret=settings.github_client_secret,
            code=code,
            redirect_uri=settings.github_redirect_uri,
        )
        user = fetch_user(access_token)
    except GitHubApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    request.session["github_access_token"] = access_token
    request.session["github_login"] = user.get("login", "")
    request.session["github_id"] = user.get("id", 0)

    next_path = sanitize_internal_redirect_path(request.session.pop("github_oauth_next", "/"))
    request.session.pop("github_oauth_state", None)
    return RedirectResponse(next_path)


@app.post("/auth/logout")
def auth_logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@app.post("/api/reviews/preview")
def reviews_preview(request: Request, payload: ReviewRequest) -> dict:
    access_token = _require_session_token(request)
    result = _run_review(access_token, payload.repo_full_name, payload.pr_number)
    return {"ok": True, "result": result}


@app.post("/api/reviews/publish")
def reviews_publish(request: Request, payload: ReviewRequest) -> dict:
    access_token = _require_session_token(request)
    result = _run_review(access_token, payload.repo_full_name, payload.pr_number)
    try:
        github_review = create_pull_request_review(
            access_token,
            payload.repo_full_name,
            payload.pr_number,
            result["review_markdown"],
        )
    except GitHubApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "result": result, "github_review": github_review}


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)
