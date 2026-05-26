from __future__ import annotations

import secrets
import threading
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

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
    fetch_pull_request,
    fetch_pull_request_files,
    fetch_user,
    github_authorization_url,
)
from oauth_reviewer.jobs import JobStore
from oauth_reviewer.openai_review import OpenAIReviewError, build_review_prompt, generate_review_markdown
from oauth_reviewer.repo_guidance import collect_relevant_agents, ensure_repo_allowed
from oauth_shared.local_terminal import LocalTerminalMonitor
from oauth_shared.session_store import SessionStore


APP_DIR = Path(__file__).resolve().parent
STATIC_INDEX = APP_DIR / "static" / "index.html"
SESSION_COOKIE_NAME = "gosha_oauth_reviewer_session"
settings = Settings.from_env()
session_store = SessionStore(settings.session_store_dir, settings.session_ttl_seconds)
review_jobs = JobStore()
SESSION_ID_KEY = "server_session_id"

app = FastAPI(title="GOSHA OAuth Reviewer", version="1.0.0")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret or "unsafe-dev-secret",
    session_cookie=SESSION_COOKIE_NAME,
    same_site="lax",
    https_only=settings.cookie_secure,
    max_age=settings.session_ttl_seconds,
)


class ReviewRequest(BaseModel):
    repo_full_name: str = Field(..., examples=["MaxCorpOrg/GOSHA_PLATFORM"])
    pr_number: int = Field(..., ge=1, examples=[1])


class ReviewStartRequest(ReviewRequest):
    mode: Literal["preview", "publish"] = Field(..., examples=["preview"])


def _request_session_id(request: Request, *, create: bool = False) -> str:
    session_id = str(request.session.get(SESSION_ID_KEY, "") or "").strip()
    if session_id:
        return session_id
    if not create:
        return ""
    session_id = session_store.new_session_id()
    request.session[SESSION_ID_KEY] = session_id
    return session_id


def _github_session(request: Request) -> dict:
    session_id = _request_session_id(request)
    if not session_id:
        return {}
    return session_store.get(session_id)


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


def _safe_next_path(next_path: str) -> str:
    candidate = str(next_path or "").strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        return "/"
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        return "/"
    return candidate


def _session_payload(request: Request) -> dict:
    github_session = _github_session(request)
    review_backend, codex_status = _resolve_review_backend()
    return {
        "authenticated": bool(github_session.get("github_access_token")),
        "github_login": github_session.get("github_login", ""),
        "github_id": github_session.get("github_id", 0),
        "allowed_repos": list(settings.allowed_repos),
        "github_ready": settings.github_ready,
        "review_ready": bool(review_backend),
        "review_backend": review_backend,
        "requested_review_backend": settings.review_backend,
        "codex_ready": bool(codex_status["available"]) and bool(codex_status["logged_in"]),
        "codex_status_text": str(codex_status["status_text"]),
        "codex_model": settings.codex_model,
        "codex_reasoning_effort": settings.codex_reasoning_effort,
        "codex_profile": settings.codex_profile,
        "openai_ready": settings.openai_ready,
        "repo_path": str(settings.repo_path),
    }


def _require_session_token(request: Request) -> str:
    token = str(_github_session(request).get("github_access_token", "") or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Сначала войди через GitHub OAuth.")
    return token


def _run_review(access_token: str, repo_full_name: str, pr_number: int, log_cb=None, stage_cb=None) -> dict:
    def log(message: str) -> None:
        if log_cb is not None:
            log_cb(message)

    def set_stage(stage: str) -> None:
        if stage_cb is not None:
            stage_cb(stage)

    set_stage("Проверка доступа к репозиторию")
    log("Проверяю разрешённый список репозиториев.")
    ensure_repo_allowed(repo_full_name, settings.allowed_repos)
    set_stage("Выбор backend reviewer")
    log("Определяю доступный backend reviewer.")
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
        set_stage("Загрузка Pull Request")
        log(f"Загружаю Pull Request #{pr_number} из {repo_full_name}.")
        pr_payload = fetch_pull_request(access_token, repo_full_name, pr_number)
        set_stage("Загрузка изменённых файлов")
        log("Получаю список изменённых файлов Pull Request.")
        file_payloads = fetch_pull_request_files(access_token, repo_full_name, pr_number)
        changed_files = [str(item.get("filename", "") or "").strip() for item in file_payloads if item.get("filename")]
        log(f"Изменённых файлов: {len(changed_files)}.")
        set_stage("Чтение правил проекта")
        log("Читаю правила проекта из AGENTS.md.")
        agents_sections = collect_relevant_agents(settings.repo_path, changed_files)
        log(f"Найдено релевантных файлов правил: {len(agents_sections)}.")
        set_stage("Подготовка prompt reviewer")
        log("Формирую prompt reviewer.")
        prompt = build_review_prompt(
            repo_full_name=repo_full_name,
            pr_payload=pr_payload,
            file_payloads=file_payloads,
            agents_sections=agents_sections,
            max_patch_chars_per_file=settings.max_patch_chars_per_file,
            max_total_patch_chars=settings.max_total_patch_chars,
        )
        if review_backend == "codex_cli":
            set_stage("Ожидание ответа модели в Codex CLI")
            log("Запускаю локальный Codex CLI для генерации review.")
            review_markdown = generate_review_markdown_via_codex(
                codex_command=settings.codex_command,
                repo_path=settings.repo_path,
                prompt=prompt,
                model=settings.codex_model,
                reasoning_effort=settings.codex_reasoning_effort,
                profile=settings.codex_profile,
                timeout_seconds=settings.codex_timeout_seconds,
                log_cb=log,
            )
        else:
            set_stage("Ожидание ответа HTTP-backend reviewer")
            log("Отправляю запрос в HTTP-backend reviewer.")
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

    set_stage("Сбор результата review")
    log("Review успешно сгенерирован.")
    return {
        "repo_full_name": repo_full_name,
        "pr_number": pr_number,
        "pr_title": pr_payload.get("title", ""),
        "changed_files": changed_files,
        "review_backend": review_backend,
        "review_markdown": review_markdown,
    }


def _job_owner_session_id(request: Request) -> str:
    session_id = _request_session_id(request)
    if not session_id:
        raise HTTPException(status_code=401, detail="Сначала войди через GitHub OAuth.")
    return session_id


def _get_review_job_for_request(request: Request, job_id: str) -> dict:
    session_id = _job_owner_session_id(request)
    job = review_jobs.get(job_id)
    if not job or job.owner_session_id != session_id:
        raise HTTPException(status_code=404, detail="Задача review не найдена.")
    return job.to_dict()


def _run_review_job(
    *,
    job_id: str,
    access_token: str,
    repo_full_name: str,
    pr_number: int,
    mode: str,
) -> None:
    review_jobs.start(job_id)
    monitor: LocalTerminalMonitor | None = None

    def log(message: str) -> None:
        review_jobs.append_log(job_id, message)

    def set_stage(stage: str) -> None:
        review_jobs.set_stage(job_id, stage)

    try:
        set_stage("Подготовка задачи review")
        monitor = LocalTerminalMonitor(
            title=f"GOSHA reviewer PR#{pr_number} {mode}",
            enabled=settings.open_terminal,
            runtime_root=settings.terminal_runtime_dir,
            preferred_terminal_command=settings.terminal_command,
        )
        terminal_message = monitor.start()
        base_log = log

        def log_with_terminal(message: str) -> None:
            base_log(message)
            if monitor is not None:
                monitor.append(message)

        log = log_with_terminal
        log(terminal_message)
        log(f"Старт задачи reviewer: режим `{mode}`, PR #{pr_number}.")
        result = _run_review(access_token, repo_full_name, pr_number, log_cb=log, stage_cb=set_stage)
        if mode == "publish":
            set_stage("Публикация review в Pull Request")
            log("Публикую review в Pull Request.")
            github_review = create_pull_request_review(
                access_token,
                repo_full_name,
                pr_number,
                result["review_markdown"],
            )
            result["github_review"] = github_review
            log("Review опубликован в Pull Request.")
        else:
            set_stage("Preview review готов")
            log("Preview review готов без публикации в Pull Request.")
        result["terminal_log_path"] = str(monitor.log_path) if monitor is not None else ""
        review_jobs.finish(job_id, result=result)
        if monitor is not None:
            monitor.finish("Reviewer завершил задачу успешно.")
    except HTTPException as exc:
        try:
            if monitor is not None:
                monitor.finish(f"Reviewer завершился с ошибкой: {exc.detail}")
        except Exception:
            pass
        review_jobs.fail(job_id, str(exc.detail))
    except (GitHubApiError, OpenAIReviewError, CodexReviewError, ValueError) as exc:
        try:
            if monitor is not None:
                monitor.finish(f"Reviewer завершился с ошибкой: {exc}")
        except Exception:
            pass
        review_jobs.fail(job_id, str(exc))
    except Exception as exc:
        try:
            if monitor is not None:
                monitor.finish(f"Reviewer завершился с неожиданной ошибкой: {exc}")
        except Exception:
            pass
        review_jobs.fail(job_id, f"Неожиданный сбой reviewer: {exc}")


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


@app.get("/api/reviews")
def reviews_list(request: Request) -> dict:
    session_id = _job_owner_session_id(request)
    return {"ok": True, "jobs": review_jobs.list_jobs(owner_session_id=session_id)}


@app.get("/api/reviews/{job_id}")
def reviews_get(request: Request, job_id: str) -> dict:
    return {"ok": True, "job": _get_review_job_for_request(request, job_id)}


@app.get("/auth/github/start")
def auth_github_start(request: Request, next: str = "/") -> RedirectResponse:
    if not settings.github_ready:
        raise HTTPException(status_code=500, detail="GitHub OAuth ещё не настроен в env сервиса.")
    state = secrets.token_urlsafe(24)
    session_id = _request_session_id(request, create=True)
    session_store.patch(
        session_id,
        github_oauth_state=state,
        github_oauth_next=_safe_next_path(next),
    )
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
    session_id = _request_session_id(request, create=True)
    session_data = session_store.get(session_id)
    expected_state = str(session_data.get("github_oauth_state", "") or "")
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

    next_path = str(session_data.get("github_oauth_next", "/") or "/")
    session_store.put(
        session_id,
        {
            "github_access_token": access_token,
            "github_login": user.get("login", ""),
            "github_id": user.get("id", 0),
        },
    )
    return RedirectResponse(next_path)


@app.post("/auth/logout")
def auth_logout(request: Request) -> dict:
    session_id = _request_session_id(request)
    if session_id:
        session_store.delete(session_id)
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


@app.post("/api/reviews/start")
def reviews_start(request: Request, payload: ReviewStartRequest) -> dict:
    access_token = _require_session_token(request)
    session_id = _job_owner_session_id(request)
    job, created = review_jobs.create_or_get_active(
        owner_session_id=session_id,
        repo_full_name=payload.repo_full_name,
        pr_number=payload.pr_number,
        mode=payload.mode,
    )
    if created:
        thread = threading.Thread(
            target=_run_review_job,
            kwargs={
                "job_id": job.job_id,
                "access_token": access_token,
                "repo_full_name": payload.repo_full_name,
                "pr_number": payload.pr_number,
                "mode": payload.mode,
            },
            daemon=True,
        )
        thread.start()
    return {"ok": True, "job": job.to_dict()}


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)
