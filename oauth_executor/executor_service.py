from __future__ import annotations

import hmac
import json
import subprocess
import threading
from hashlib import sha256
from pathlib import Path
from typing import Any

from oauth_executor.codex_runner import (
    COMMIT_MESSAGE_FILE,
    SUMMARY_FILE,
    CodexExecutionError,
    build_executor_prompt,
    run_codex_exec,
)
from oauth_executor.config import Settings
from oauth_executor.git_worktree import (
    WorktreeError,
    commit_all_changes,
    current_head_sha,
    has_changes,
    prepare_worktree,
    push_head_to_branch,
)
from oauth_executor.jobs import ExecutionJob, JobStore
from oauth_reviewer.github_api import (
    GitHubApiError,
    create_issue_comment,
    exchange_code_for_token,
    fetch_branch,
    fetch_issue_comments,
    fetch_pull_request,
    fetch_pull_request_review_comments,
    fetch_pull_request_reviews,
    fetch_pull_request_files,
    fetch_user,
    github_authorization_url,
    sanitize_internal_redirect_path,
)
from oauth_reviewer.repo_guidance import collect_relevant_agents, ensure_repo_allowed


class ExecutorServiceError(RuntimeError):
    pass


EXECUTOR_COMMENT_MARKER = "Исполнительный агент `Codex`"


def verify_webhook_signature(secret: str, body: bytes, signature_header: str) -> bool:
    if not secret:
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header or "")


class ExecutorService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.jobs = JobStore()

    def session_payload(self, request) -> dict[str, Any]:
        session = request.session
        return {
            "authenticated": bool(session.get("github_access_token")),
            "github_login": session.get("github_login", ""),
            "github_id": session.get("github_id", 0),
            "allowed_repos": list(self.settings.allowed_repos),
            "github_ready": self.settings.github_ready,
            "service_token_ready": self.settings.service_token_ready,
            "webhook_ready": self.settings.webhook_ready,
            "codex_ready": self.settings.codex_ready,
            "repo_path": str(self.settings.repo_path),
            "worktree_root": str(self.settings.worktree_root),
        }

    def github_auth_start(self, request, next_path: str) -> str:
        state = __import__("secrets").token_urlsafe(24)
        request.session["github_oauth_state"] = state
        request.session["github_oauth_next"] = sanitize_internal_redirect_path(next_path)
        return github_authorization_url(
            client_id=self.settings.github_client_id,
            redirect_uri=self.settings.github_redirect_uri,
            scope=self.settings.github_scope,
            state=state,
        )

    def github_auth_callback(self, request, code: str, state: str) -> str:
        expected_state = str(request.session.get("github_oauth_state", "") or "")
        if not code or not state or not expected_state or state != expected_state:
            raise ExecutorServiceError("OAuth state не совпал или GitHub не вернул code.")
        access_token = exchange_code_for_token(
            client_id=self.settings.github_client_id,
            client_secret=self.settings.github_client_secret,
            code=code,
            redirect_uri=self.settings.github_redirect_uri,
        )
        user = fetch_user(access_token)
        request.session["github_access_token"] = access_token
        request.session["github_login"] = user.get("login", "")
        request.session["github_id"] = user.get("id", 0)
        next_path = sanitize_internal_redirect_path(request.session.pop("github_oauth_next", "/"))
        request.session.pop("github_oauth_state", None)
        return next_path

    def start_manual_job(
        self,
        *,
        repo_full_name: str,
        pr_number: int,
        access_token: str,
        github_login: str,
    ) -> dict[str, Any]:
        self._ensure_runtime_ready()
        self._ensure_manual_login_allowed(github_login)
        self._ensure_repo_allowed(repo_full_name)
        job, created = self.jobs.create_or_get_active(repo_full_name=repo_full_name, pr_number=pr_number, trigger="manual")
        if not created:
            return job.to_dict()
        thread = threading.Thread(
            target=self._run_job,
            kwargs={"job_id": job.job_id, "repo_full_name": repo_full_name, "pr_number": pr_number, "access_token": access_token},
            daemon=True,
        )
        thread.start()
        return job.to_dict()

    def start_webhook_job(self, *, repo_full_name: str, pr_number: int) -> dict[str, Any]:
        self._ensure_runtime_ready()
        self._ensure_repo_allowed(repo_full_name)
        job, created = self.jobs.create_or_get_active(repo_full_name=repo_full_name, pr_number=pr_number, trigger="webhook")
        if not created:
            return job.to_dict()
        thread = threading.Thread(
            target=self._run_job,
            kwargs={
                "job_id": job.job_id,
                "repo_full_name": repo_full_name,
                "pr_number": pr_number,
                "access_token": self.settings.github_executor_token,
            },
            daemon=True,
        )
        thread.start()
        return job.to_dict()

    def _ensure_runtime_ready(self) -> None:
        if not self.settings.repo_path.exists():
            raise ExecutorServiceError(f"Не найден локальный репозиторий исполнителя: {self.settings.repo_path}")
        if not self.settings.codex_ready:
            raise ExecutorServiceError(f"Не найден локальный исполняемый файл Codex: {self.settings.codex_command}")

    def _ensure_repo_allowed(self, repo_full_name: str) -> None:
        try:
            ensure_repo_allowed(repo_full_name, self.settings.allowed_repos)
        except ValueError as exc:
            raise ExecutorServiceError(str(exc)) from exc

    def _ensure_manual_login_allowed(self, github_login: str) -> None:
        login = str(github_login or "").strip()
        if not login:
            raise ExecutorServiceError("Ручной запуск исполнителя запрещён: GitHub OAuth не вернул логин пользователя.")
        if not self.settings.reviewer_logins:
            raise ExecutorServiceError(
                "Ручной запуск исполнителя запрещён: пуст список `OAUTH_EXECUTOR_REVIEWER_LOGINS`."
            )
        if login not in self.settings.reviewer_logins:
            raise ExecutorServiceError(
                f"Логин GitHub `{login}` не входит в разрешённый список ручного запуска исполнителя."
            )

    def _log(self, job_id: str, message: str) -> None:
        self.jobs.append_log(job_id, message)

    def _ensure_head_branch_allows_executor_push(
        self,
        *,
        access_token: str,
        repo_full_name: str,
        head_branch: str,
        pr_payload: dict[str, Any],
    ) -> None:
        head_repo = (pr_payload.get("head") or {}).get("repo") or {}
        default_branch = str(head_repo.get("default_branch", "") or "").strip()
        if default_branch and head_branch == default_branch:
            raise ExecutorServiceError(
                "Исполнитель не отправляет правки в head-ветку PR, совпадающую с default branch репозитория. Нужна отдельная ветка Pull Request."
            )

        branch_payload = fetch_branch(access_token, repo_full_name, head_branch)
        if bool((branch_payload or {}).get("protected")):
            raise ExecutorServiceError(
                "Исполнитель не отправляет правки в защищённую head-ветку Pull Request. Нужна отдельная ветка PR."
            )

    def _run_shell_command(self, *, command: str, cwd: Path, job_id: str) -> None:
        self._log(job_id, f"$ {command}")
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            self._log(job_id, line.rstrip())
        code = process.wait()
        if code != 0:
            raise ExecutorServiceError(f"Команда завершилась с кодом {code}: {command}")

    def _run_job(self, *, job_id: str, repo_full_name: str, pr_number: int, access_token: str) -> None:
        self.jobs.start(job_id)
        try:
            self._ensure_repo_allowed(repo_full_name)
            if not access_token:
                raise ExecutorServiceError("Нет токена GitHub для исполнительного агента.")

            self._log(job_id, f"Начинаю обработку PR #{pr_number} в {repo_full_name}")
            pr_payload = fetch_pull_request(access_token, repo_full_name, pr_number)
            if not isinstance((pr_payload.get("head") or {}).get("repo"), dict):
                raise ExecutorServiceError("GitHub не вернул head-репозиторий Pull Request.")
            head_repo_full_name = str(((pr_payload.get("head") or {}).get("repo") or {}).get("full_name", "") or "").strip()
            if head_repo_full_name != repo_full_name:
                raise ExecutorServiceError("Текущая версия исполнителя поддерживает только Pull Request из того же репозитория.")
            head_branch = str((pr_payload.get("head") or {}).get("ref", "") or "").strip()
            if not head_branch:
                raise ExecutorServiceError("GitHub не вернул head-ветку Pull Request.")
            self._ensure_head_branch_allows_executor_push(
                access_token=access_token,
                repo_full_name=head_repo_full_name,
                head_branch=head_branch,
                pr_payload=pr_payload,
            )

            review_comments = fetch_pull_request_review_comments(access_token, repo_full_name, pr_number)
            reviews = [
                item
                for item in fetch_pull_request_reviews(access_token, repo_full_name, pr_number)
                if str(item.get("state", "") or "").strip().lower() in {"commented", "changes_requested"}
            ]
            issue_comments = [
                item
                for item in fetch_issue_comments(access_token, repo_full_name, pr_number)
                if EXECUTOR_COMMENT_MARKER not in str(item.get("body", "") or "")
            ]
            file_payloads = fetch_pull_request_files(access_token, repo_full_name, pr_number)

            actionable_reviews = [item for item in reviews if str(item.get("body", "") or "").strip()]
            actionable_inline = [item for item in review_comments if str(item.get("body", "") or "").strip()]
            actionable_issue = [item for item in issue_comments if str(item.get("body", "") or "").strip()]
            if not actionable_reviews and not actionable_inline and not actionable_issue:
                raise ExecutorServiceError("Не найдено замечаний, по которым нужно вносить правки.")

            worktree_path, local_branch = prepare_worktree(
                repo_path=self.settings.repo_path,
                worktree_root=self.settings.worktree_root,
                repo_full_name=repo_full_name,
                branch_name=head_branch,
                pr_number=pr_number,
            )
            self._log(job_id, f"Рабочая копия подготовлена: {worktree_path}")
            self._log(job_id, f"Локальная рабочая ветка: {local_branch}")
            self._log(job_id, f"Текущий head до правок: {current_head_sha(worktree_path)}")

            changed_files = [str(item.get("filename", "") or "").strip() for item in file_payloads if item.get("filename")]
            agents_sections = collect_relevant_agents(self.settings.repo_path, changed_files)
            prompt = build_executor_prompt(
                repo_full_name=repo_full_name,
                pr_payload=pr_payload,
                agents_sections=agents_sections,
                issue_comments=issue_comments,
                reviews=reviews,
                review_comments=review_comments,
                validate_command=self.settings.validate_command,
            )

            self._log(job_id, "Запускаю локальный Codex как исполнительный агент.")
            run_codex_exec(
                codex_command=self.settings.codex_command,
                worktree_path=worktree_path,
                prompt=prompt,
                model=self.settings.codex_model,
                profile=self.settings.codex_profile,
                timeout_seconds=self.settings.codex_timeout_seconds,
                log_cb=lambda message: self._log(job_id, message),
            )

            commit_message_path = worktree_path / COMMIT_MESSAGE_FILE
            summary_path = worktree_path / SUMMARY_FILE
            commit_message = "Исправить замечания по PR"
            summary_text = ""
            if commit_message_path.exists():
                commit_message = commit_message_path.read_text(encoding="utf-8", errors="ignore").strip() or commit_message
                commit_message_path.unlink()
            if summary_path.exists():
                summary_text = summary_path.read_text(encoding="utf-8", errors="ignore").strip()
                summary_path.unlink()

            if self.settings.validate_command:
                self._run_shell_command(command=self.settings.validate_command, cwd=worktree_path, job_id=job_id)

            if not has_changes(worktree_path):
                result = {
                    "repo_full_name": repo_full_name,
                    "pr_number": pr_number,
                    "head_branch": head_branch,
                    "worktree_path": str(worktree_path),
                    "status_note": "После автоматического прогона изменений в рабочей копии не осталось.",
                    "summary": summary_text,
                }
                if self.settings.comment_on_pr:
                    create_issue_comment(
                        access_token,
                        repo_full_name,
                        pr_number,
                        f"{EXECUTOR_COMMENT_MARKER} завершил прогон, но правок в рабочей копии не осталось. Возможно, замечания уже были закрыты ранее.",
                    )
                self.jobs.finish(job_id, result=result)
                return

            commit_sha = commit_all_changes(
                worktree_path=worktree_path,
                message=commit_message,
                author_name=self.settings.git_author_name,
                author_email=self.settings.git_author_email,
            )
            self._log(job_id, f"Создан коммит: {commit_sha}")
            push_head_to_branch(
                worktree_path=worktree_path,
                repo_full_name=repo_full_name,
                branch_name=head_branch,
                access_token=access_token,
            )
            self._log(job_id, f"Изменения отправлены в ветку {head_branch}")

            result = {
                "repo_full_name": repo_full_name,
                "pr_number": pr_number,
                "head_branch": head_branch,
                "worktree_path": str(worktree_path),
                "commit_sha": commit_sha,
                "summary": summary_text,
            }
            if self.settings.comment_on_pr:
                body = (
                    f"{EXECUTOR_COMMENT_MARKER} внёс правки по замечаниям и отправил их в ветку PR.\n\n"
                    f"- Ветка: `{head_branch}`\n"
                    f"- Коммит: `{commit_sha}`\n"
                )
                if summary_text:
                    body += f"\nКраткое резюме:\n\n{summary_text}\n"
                create_issue_comment(access_token, repo_full_name, pr_number, body)
            self.jobs.finish(job_id, result=result)
        except (ExecutorServiceError, GitHubApiError, WorktreeError, CodexExecutionError) as exc:
            if self.settings.comment_on_pr and access_token:
                try:
                    create_issue_comment(
                        access_token,
                        repo_full_name,
                        pr_number,
                        f"{EXECUTOR_COMMENT_MARKER} не смог завершить автоматический цикл.\n\n"
                        f"Причина: {exc}",
                    )
                except Exception:
                    pass
            self.jobs.fail(job_id, str(exc))

    def list_jobs(self) -> list[dict[str, Any]]:
        return self.jobs.list_jobs()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = self.jobs.get(job_id)
        return job.to_dict() if job else None

    def handle_webhook(self, *, event_name: str, body: bytes) -> dict[str, Any]:
        if not self.settings.webhook_ready:
            raise ExecutorServiceError("Webhook-режим ещё не настроен: нужен секрет webhook, сервисный токен GitHub и список логинов проверяющего сервиса.")

        payload = json.loads(body.decode("utf-8"))
        if event_name != "pull_request_review":
            return {"accepted": False, "reason": "Сейчас автоматический запуск поддерживается только для события pull_request_review."}

        action = str(payload.get("action", "") or "").strip()
        review = payload.get("review") or {}
        pr = payload.get("pull_request") or {}
        repo = payload.get("repository") or {}
        login = str((review.get("user") or {}).get("login", "") or "").strip()
        repo_full_name = str(repo.get("full_name", "") or "").strip()
        pr_number = int(payload.get("pull_request", {}).get("number", 0) or 0)
        review_state = str(review.get("state", "") or "").strip().lower()

        if action != "submitted":
            return {"accepted": False, "reason": f"Событие review с действием `{action}` не запускает исполнителя."}
        if login not in self.settings.reviewer_logins:
            return {"accepted": False, "reason": f"Автор review `{login}` не входит в разрешённый список логинов проверяющего сервиса."}
        if review_state not in {"commented", "changes_requested"}:
            return {"accepted": False, "reason": f"Состояние review `{review_state}` не запускает исполнителя."}
        if not repo_full_name or not pr_number:
            raise ExecutorServiceError("Webhook GitHub не передал repository.full_name или номер Pull Request.")

        job = self.start_webhook_job(repo_full_name=repo_full_name, pr_number=pr_number)
        return {"accepted": True, "job": job}
