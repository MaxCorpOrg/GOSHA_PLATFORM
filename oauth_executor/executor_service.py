from __future__ import annotations

import hmac
import json
import secrets
import subprocess
import threading
import time
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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
from oauth_executor.jobs import JobStore
from oauth_shared.local_terminal import LocalTerminalMonitor
from oauth_shared.session_store import SessionStore
from oauth_reviewer.github_api import (
    GitHubApiError,
    create_issue_comment,
    exchange_code_for_token,
    fetch_pull_request,
    fetch_pull_request_review_comments,
    fetch_pull_request_reviews,
    fetch_pull_request_files,
    fetch_user,
    github_authorization_url,
)
from oauth_reviewer.repo_guidance import collect_relevant_agents, ensure_repo_allowed
from oauth_shared.process_stream import collect_process_output


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
        self.session_store = SessionStore(settings.session_store_dir, settings.session_ttl_seconds)
        self._webhook_lock = threading.Lock()
        self._processed_webhook_keys: set[tuple[str, int, str, str]] = set()
        self._auto_run_history: dict[tuple[str, int], list[float]] = {}

    def session_payload(self, request) -> dict[str, Any]:
        github_session = self.github_session(request)
        return {
            "authenticated": bool(github_session.get("github_access_token")),
            "github_login": github_session.get("github_login", ""),
            "github_id": github_session.get("github_id", 0),
            "allowed_repos": list(self.settings.allowed_repos),
            "github_ready": self.settings.github_ready,
            "service_token_ready": self.settings.service_token_ready,
            "webhook_ready": self.settings.webhook_ready,
            "codex_ready": self.settings.codex_ready,
            "codex_model": self.settings.codex_model,
            "codex_reasoning_effort": self.settings.codex_reasoning_effort,
            "codex_profile": self.settings.codex_profile,
            "repo_path": str(self.settings.repo_path),
            "worktree_root": str(self.settings.worktree_root),
        }

    def github_session(self, request) -> dict[str, Any]:
        session_id = self._request_session_id(request)
        if not session_id:
            return {}
        return self.session_store.get(session_id)

    def _safe_next_path(self, next_path: str) -> str:
        candidate = str(next_path or "").strip()
        if not candidate.startswith("/") or candidate.startswith("//"):
            return "/"
        parsed = urlsplit(candidate)
        if parsed.scheme or parsed.netloc:
            return "/"
        return candidate

    def github_auth_start(self, request, next_path: str) -> str:
        state = secrets.token_urlsafe(24)
        session_id = self._request_session_id(request, create=True)
        self.session_store.patch(
            session_id,
            github_oauth_state=state,
            github_oauth_next=self._safe_next_path(next_path),
        )
        return github_authorization_url(
            client_id=self.settings.github_client_id,
            redirect_uri=self.settings.github_redirect_uri,
            scope=self.settings.github_scope,
            state=state,
        )

    def github_auth_callback(self, request, code: str, state: str) -> str:
        session_id = self._request_session_id(request, create=True)
        session_data = self.session_store.get(session_id)
        expected_state = str(session_data.get("github_oauth_state", "") or "")
        if not code or not state or not expected_state or state != expected_state:
            raise ExecutorServiceError("OAuth state не совпал или GitHub не вернул code.")
        access_token = exchange_code_for_token(
            client_id=self.settings.github_client_id,
            client_secret=self.settings.github_client_secret,
            code=code,
            redirect_uri=self.settings.github_redirect_uri,
        )
        user = fetch_user(access_token)
        next_path = str(session_data.get("github_oauth_next", "/") or "/")
        self.session_store.put(
            session_id,
            {
                "github_access_token": access_token,
                "github_login": user.get("login", ""),
                "github_id": user.get("id", 0),
            },
        )
        return next_path

    def logout_session(self, request) -> None:
        session_id = self._request_session_id(request)
        if session_id:
            self.session_store.delete(session_id)
        request.session.clear()

    def start_manual_job(self, *, repo_full_name: str, pr_number: int, access_token: str, github_login: str) -> dict[str, Any]:
        self._ensure_runtime_ready()
        ensure_repo_allowed(repo_full_name, self.settings.allowed_repos)
        job, created = self.jobs.create_or_get_active(repo_full_name=repo_full_name, pr_number=pr_number, trigger="manual")
        if not created:
            return job.to_dict()
        thread = threading.Thread(
            target=self._run_job,
            kwargs={
                "job_id": job.job_id,
                "repo_full_name": repo_full_name,
                "pr_number": pr_number,
                "access_token": access_token,
                "manual_reviewer_login": github_login,
            },
            daemon=True,
        )
        thread.start()
        return job.to_dict()

    def start_webhook_job(self, *, repo_full_name: str, pr_number: int, trigger_review_id: int, trigger_review_login: str) -> dict[str, Any]:
        self._ensure_runtime_ready()
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
                "trigger_review_id": trigger_review_id,
                "trigger_review_login": trigger_review_login,
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

    def _log(self, job_id: str, message: str) -> None:
        self.jobs.append_log(job_id, message)

    def _request_session_id(self, request, *, create: bool = False) -> str:
        session_id = str(request.session.get("server_session_id", "") or "").strip()
        if session_id:
            return session_id
        if not create:
            return ""
        session_id = self.session_store.new_session_id()
        request.session["server_session_id"] = session_id
        return session_id

    def _normalized_reviewer_login_set(self, *items: str) -> set[str]:
        return {str(item or "").strip().lower() for item in items if str(item or "").strip()}

    def _normalize_review_line(self, text: str) -> str:
        normalized = str(text or "").strip().lower()
        normalized = normalized.lstrip("#>*-0123456789. )\t")
        normalized = normalized.strip("`*_:- ")
        normalized = " ".join(normalized.split())
        return normalized.rstrip(".! ")

    def _is_clean_review_summary(self, body: str) -> bool:
        lines = [
            self._normalize_review_line(line)
            for line in str(body or "").splitlines()
        ]
        normalized_lines = [line for line in lines if line]
        if not normalized_lines:
            return False

        benign_headings = {"итог", "замечания", "summary", "findings"}
        benign_phrases = {
            "критичных p0/p1 замечаний не найдено",
            "критичных p0 и p1 замечаний не найдено",
            "критичных замечаний не найдено",
            "серьёзных замечаний не найдено",
            "серьезных замечаний не найдено",
            "замечаний не найдено",
            "нет замечаний",
            "без замечаний",
            "no findings",
            "no issues found",
            "nothing to fix",
            "looks good",
            "looks good to me",
            "lgtm",
        }
        if not any(line in benign_phrases for line in normalized_lines):
            return False
        return all(line in benign_headings or line in benign_phrases for line in normalized_lines)

    def _review_has_inline_comments(
        self,
        *,
        review_id: int,
        review_comments: list[dict[str, Any]],
        allowed_reviewer_logins: set[str] | None = None,
    ) -> bool:
        if review_id <= 0:
            return False
        for item in review_comments:
            if int(item.get("pull_request_review_id", 0) or 0) != review_id:
                continue
            if not str(item.get("body", "") or "").strip():
                continue
            if allowed_reviewer_logins is not None:
                login = str(((item.get("user") or {}).get("login") or "")).strip().lower()
                if login not in allowed_reviewer_logins:
                    continue
            return True
        return False

    def _review_sort_key(self, item: dict[str, Any]) -> tuple[str, int]:
        return (
            str(item.get("submitted_at", "") or ""),
            int(item.get("id", 0) or 0),
        )

    def _filter_prompt_feedback(
        self,
        *,
        reviews: list[dict[str, Any]],
        review_comments: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        prompt_reviews: list[dict[str, Any]] = []
        for item in reviews:
            body = str(item.get("body", "") or "").strip()
            if not body:
                continue
            if self._is_clean_review_summary(body):
                continue
            prompt_reviews.append(item)
        return prompt_reviews, list(review_comments)

    def _should_skip_clean_review_webhook(
        self,
        *,
        review: dict[str, Any],
        review_comments: list[dict[str, Any]],
        allowed_reviewer_logins: set[str],
    ) -> bool:
        review_state = str(review.get("state", "") or "").strip().lower()
        review_body = str(review.get("body", "") or "").strip()
        review_id = int(review.get("id", 0) or 0)
        if review_state != "commented":
            return False
        if not self._is_clean_review_summary(review_body):
            return False
        return not self._review_has_inline_comments(
            review_id=review_id,
            review_comments=review_comments,
            allowed_reviewer_logins=allowed_reviewer_logins,
        )

    def _ensure_head_branch_is_safe(self, *, pr_payload: dict[str, Any], head_branch: str) -> None:
        protected = {item.strip().lower() for item in self.settings.protected_branches if item.strip()}
        base_branch = str(((pr_payload.get("base") or {}).get("ref") or "")).strip()
        default_branch = str((((pr_payload.get("base") or {}).get("repo") or {}).get("default_branch") or "")).strip()
        for value in (base_branch, default_branch):
            if value:
                protected.add(value.lower())
        if head_branch.strip().lower() in protected:
            raise ExecutorServiceError(
                f"Executor не должен отправлять изменения в защищённую ветку `{head_branch}`. "
                "Нужна отдельная рабочая head-ветка Pull Request."
            )

    def _reviewed_commit_key(self, *, review: dict[str, Any], pr: dict[str, Any]) -> str:
        reviewed_commit = str(review.get("commit_id", "") or "").strip().lower()
        if reviewed_commit:
            return reviewed_commit
        return str((((pr.get("head") or {}).get("sha")) or "")).strip().lower()

    def _pr_history_key(self, *, repo_full_name: str, pr_number: int) -> tuple[str, int]:
        return (repo_full_name.strip().lower(), int(pr_number))

    def _allow_webhook_auto_run(
        self,
        *,
        repo_full_name: str,
        pr_number: int,
        reviewer_login: str,
        reviewed_commit_key: str,
    ) -> tuple[bool, str]:
        dedupe_key = (
            repo_full_name.strip().lower(),
            int(pr_number),
            reviewer_login.strip().lower(),
            reviewed_commit_key.strip().lower() or "unknown-commit",
        )
        history_key = self._pr_history_key(repo_full_name=repo_full_name, pr_number=pr_number)
        now = time.time()
        window_seconds = max(60, int(self.settings.auto_run_window_seconds))

        with self._webhook_lock:
            if dedupe_key in self._processed_webhook_keys:
                return (
                    False,
                    "Повторный review по тому же коммиту уже был обработан. Новый автозапуск не нужен.",
                )

            history = [
                timestamp
                for timestamp in self._auto_run_history.get(history_key, [])
                if now - timestamp <= window_seconds
            ]
            self._auto_run_history[history_key] = history
            max_runs = int(self.settings.max_auto_runs_per_pr)
            if max_runs > 0 and len(history) >= max_runs:
                return (
                    False,
                    f"Лимит автоматических прогонов по PR уже достигнут: {max_runs} за окно {window_seconds} секунд. "
                    "Нужен ручной запуск или человеческая проверка.",
                )

            self._processed_webhook_keys.add(dedupe_key)
            history.append(now)
            self._auto_run_history[history_key] = history
            return True, ""

    def _review_scope(
        self,
        *,
        reviews: list[dict[str, Any]],
        review_comments: list[dict[str, Any]],
        allowed_reviewer_logins: set[str],
        trigger_review_id: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        actionable_states = {"commented", "changes_requested"}
        allowed_reviews = [
            item
            for item in reviews
            if str(((item.get("user") or {}).get("login") or "")).strip().lower() in allowed_reviewer_logins
        ]
        eligible_reviews = [
            item
            for item in allowed_reviews
            if str(item.get("state", "") or "").strip().lower() in actionable_states
        ]

        selected_reviews: list[dict[str, Any]] = []
        selected_review_ids: set[int] = set()

        if trigger_review_id > 0:
            selected_reviews = [
                item
                for item in eligible_reviews
                if int(item.get("id", 0) or 0) == trigger_review_id
            ]
            selected_review_ids = {
                int(item.get("id", 0) or 0)
                for item in selected_reviews
                if int(item.get("id", 0) or 0) > 0
            }
            scope_note = "Переданы только замечания из того review, которое запустило webhook."
        else:
            latest_by_reviewer: dict[str, dict[str, Any]] = {}
            for item in allowed_reviews:
                login = str(((item.get("user") or {}).get("login") or "")).strip().lower()
                if not login:
                    continue
                known_item = latest_by_reviewer.get(login)
                if known_item is None or self._review_sort_key(item) >= self._review_sort_key(known_item):
                    latest_by_reviewer[login] = item
            selected_reviews = sorted(
                (
                    item
                    for item in latest_by_reviewer.values()
                    if str(item.get("state", "") or "").strip().lower() in actionable_states
                ),
                key=self._review_sort_key,
                reverse=True,
            )
            selected_review_ids = {
                int(item.get("id", 0) or 0)
                for item in selected_reviews
                if int(item.get("id", 0) or 0) > 0
            }
            scope_note = "Переданы только последние review от разрешённых проверяющих логинов. Верхнеуровневые issue-комментарии исключены."

        selected_review_comments = [
            item
            for item in review_comments
            if str(item.get("body", "") or "").strip()
            and int(item.get("pull_request_review_id", 0) or 0) in selected_review_ids
            and str(((item.get("user") or {}).get("login") or "")).strip().lower() in allowed_reviewer_logins
        ]
        return selected_reviews, selected_review_comments, scope_note

    def _run_shell_command(self, *, command: str, cwd: Path, job_id: str, log_cb=None) -> None:
        logger = log_cb or (lambda message: self._log(job_id, message))
        logger(f"$ {command}")
        process = subprocess.Popen(
            ["bash", "-lc", command],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        try:
            collect_process_output(
                process,
                timeout_seconds=self.settings.codex_timeout_seconds,
                on_line=logger,
                kill_tree_on_timeout=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExecutorServiceError(
                f"Проверочная команда не уложилась в лимит {self.settings.codex_timeout_seconds} секунд: {command}"
            ) from exc
        code = process.returncode
        if code != 0:
            raise ExecutorServiceError(f"Команда завершилась с кодом {code}: {command}")

    def _run_job(
        self,
        *,
        job_id: str,
        repo_full_name: str,
        pr_number: int,
        access_token: str,
        manual_reviewer_login: str = "",
        trigger_review_id: int = 0,
        trigger_review_login: str = "",
    ) -> None:
        self.jobs.start(job_id)
        monitor: LocalTerminalMonitor | None = None
        try:
            monitor = LocalTerminalMonitor(
                title=f"GOSHA executor PR#{pr_number}",
                enabled=self.settings.open_terminal,
                runtime_root=self.settings.terminal_runtime_dir,
                preferred_terminal_command=self.settings.terminal_command,
            )
            terminal_message = monitor.start()

            def log(message: str) -> None:
                self._log(job_id, message)
                if monitor is not None:
                    monitor.append(message)

            def set_stage(stage: str) -> None:
                self.jobs.set_stage(job_id, stage)
                log(f"[этап] {stage}")

            set_stage("Подготовка задачи")
            log(terminal_message)
            set_stage("Проверка доступа к репозиторию")
            ensure_repo_allowed(repo_full_name, self.settings.allowed_repos)
            if not access_token:
                raise ExecutorServiceError("Нет токена GitHub для исполнительного агента.")

            log(f"Начинаю обработку PR #{pr_number} в {repo_full_name}")
            set_stage("Загрузка Pull Request")
            pr_payload = fetch_pull_request(access_token, repo_full_name, pr_number)
            if not isinstance((pr_payload.get("head") or {}).get("repo"), dict):
                raise ExecutorServiceError("GitHub не вернул head-репозиторий Pull Request.")
            head_repo_full_name = str(((pr_payload.get("head") or {}).get("repo") or {}).get("full_name", "") or "").strip()
            if head_repo_full_name != repo_full_name:
                raise ExecutorServiceError("Текущая версия исполнителя поддерживает только Pull Request из того же репозитория.")
            head_branch = str((pr_payload.get("head") or {}).get("ref", "") or "").strip()
            if not head_branch:
                raise ExecutorServiceError("GitHub не вернул head-ветку Pull Request.")
            self._ensure_head_branch_is_safe(pr_payload=pr_payload, head_branch=head_branch)

            set_stage("Загрузка review-замечаний")
            review_comments = fetch_pull_request_review_comments(access_token, repo_full_name, pr_number)
            reviews = fetch_pull_request_reviews(access_token, repo_full_name, pr_number)
            file_payloads = fetch_pull_request_files(access_token, repo_full_name, pr_number)

            allowed_reviewer_logins = self._normalized_reviewer_login_set(*self.settings.reviewer_logins)
            if trigger_review_login:
                allowed_reviewer_logins = self._normalized_reviewer_login_set(trigger_review_login)
            elif not allowed_reviewer_logins and manual_reviewer_login:
                allowed_reviewer_logins = self._normalized_reviewer_login_set(manual_reviewer_login)
            if not allowed_reviewer_logins:
                raise ExecutorServiceError("Не удалось определить разрешённый логин проверяющего для этого прогона.")

            scoped_reviews, scoped_review_comments, review_scope_note = self._review_scope(
                reviews=reviews,
                review_comments=review_comments,
                allowed_reviewer_logins=allowed_reviewer_logins,
                trigger_review_id=trigger_review_id,
            )
            actionable_reviews, actionable_inline = self._filter_prompt_feedback(
                reviews=scoped_reviews,
                review_comments=scoped_review_comments,
            )
            if not actionable_reviews and not actionable_inline:
                status_note = "После фильтрации не осталось review-замечаний, которые нужно передавать исполнителю."
                self._log(job_id, status_note)
                self.jobs.finish(
                    job_id,
                    result={
                        "repo_full_name": repo_full_name,
                        "pr_number": pr_number,
                        "status_note": status_note,
                        "summary": "",
                    },
                )
                return

            set_stage("Подготовка рабочей копии")
            worktree_path, local_branch = prepare_worktree(
                repo_path=self.settings.repo_path,
                worktree_root=self.settings.worktree_root,
                repo_full_name=repo_full_name,
                branch_name=head_branch,
                pr_number=pr_number,
            )
            log(f"Рабочая копия подготовлена: {worktree_path}")
            log(f"Локальная рабочая ветка: {local_branch}")
            log(f"Текущий head до правок: {current_head_sha(worktree_path)}")
            log(
                f"В prompt передаю только review-замечания: review={len(actionable_reviews)}, inline={len(actionable_inline)}.",
            )

            set_stage("Сбор правил и prompt")
            changed_files = [str(item.get("filename", "") or "").strip() for item in file_payloads if item.get("filename")]
            agents_sections = collect_relevant_agents(self.settings.repo_path, changed_files)
            prompt = build_executor_prompt(
                repo_full_name=repo_full_name,
                pr_payload=pr_payload,
                agents_sections=agents_sections,
                reviews=actionable_reviews,
                review_comments=actionable_inline,
                review_scope_note=review_scope_note,
                validate_command=self.settings.validate_command,
            )

            set_stage("Ожидание ответа модели в Codex CLI")
            log("Запускаю локальный Codex как исполнительный агент.")
            run_codex_exec(
                codex_command=self.settings.codex_command,
                worktree_path=worktree_path,
                prompt=prompt,
                model=self.settings.codex_model,
                reasoning_effort=self.settings.codex_reasoning_effort,
                profile=self.settings.codex_profile,
                timeout_seconds=self.settings.codex_timeout_seconds,
                log_cb=log,
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
                set_stage("Проверка изменений")
                self._run_shell_command(
                    command=self.settings.validate_command,
                    cwd=worktree_path,
                    job_id=job_id,
                    log_cb=log,
                )

            if not has_changes(worktree_path):
                result = {
                    "repo_full_name": repo_full_name,
                    "pr_number": pr_number,
                    "head_branch": head_branch,
                    "worktree_path": str(worktree_path),
                    "status_note": "После автоматического прогона изменений в рабочей копии не осталось.",
                    "summary": summary_text,
                    "terminal_log_path": str(monitor.log_path) if monitor is not None else "",
                }
                if self.settings.comment_on_pr:
                    create_issue_comment(
                        access_token,
                        repo_full_name,
                        pr_number,
                        f"{EXECUTOR_COMMENT_MARKER} завершил прогон, но правок в рабочей копии не осталось. Возможно, замечания уже были закрыты ранее.",
                    )
                set_stage("Задача завершена без новых правок")
                self.jobs.finish(job_id, result=result)
                if monitor is not None:
                    monitor.finish("Executor завершил задачу без новых правок.")
                return

            set_stage("Коммит и отправка изменений")
            commit_sha = commit_all_changes(
                worktree_path=worktree_path,
                message=commit_message,
                author_name=self.settings.git_author_name,
                author_email=self.settings.git_author_email,
            )
            log(f"Создан коммит: {commit_sha}")
            push_head_to_branch(
                worktree_path=worktree_path,
                repo_full_name=repo_full_name,
                branch_name=head_branch,
                access_token=access_token,
            )
            log(f"Изменения отправлены в ветку {head_branch}")

            result = {
                "repo_full_name": repo_full_name,
                "pr_number": pr_number,
                "head_branch": head_branch,
                "worktree_path": str(worktree_path),
                "commit_sha": commit_sha,
                "summary": summary_text,
                "terminal_log_path": str(monitor.log_path) if monitor is not None else "",
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
            if monitor is not None:
                monitor.finish("Executor завершил задачу успешно.")
        except (ExecutorServiceError, GitHubApiError, WorktreeError, CodexExecutionError, ValueError) as exc:
            if monitor is not None:
                try:
                    monitor.finish(f"Executor завершился с ошибкой: {exc}")
                except Exception:
                    pass
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
        except Exception as exc:
            if monitor is not None:
                try:
                    monitor.finish(f"Executor завершился с неожиданной ошибкой: {exc}")
                except Exception:
                    pass
            self.jobs.fail(job_id, f"Неожиданный сбой executor: {exc}")

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
        normalized_login = login.lower()
        repo_full_name = str(repo.get("full_name", "") or "").strip()
        pr_number = int(payload.get("pull_request", {}).get("number", 0) or 0)
        review_state = str(review.get("state", "") or "").strip().lower()
        allowed_reviewer_logins = self._normalized_reviewer_login_set(*self.settings.reviewer_logins)

        if action != "submitted":
            return {"accepted": False, "reason": f"Событие review с действием `{action}` не запускает исполнителя."}
        if normalized_login not in allowed_reviewer_logins:
            return {"accepted": False, "reason": f"Автор review `{login}` не входит в разрешённый список логинов проверяющего сервиса."}
        if review_state not in {"commented", "changes_requested"}:
            return {"accepted": False, "reason": f"Состояние review `{review_state}` не запускает исполнителя."}
        if not repo_full_name or not pr_number:
            raise ExecutorServiceError("Webhook GitHub не передал repository.full_name или номер Pull Request.")
        ensure_repo_allowed(repo_full_name, self.settings.allowed_repos)
        active_job = self.jobs.find_active(repo_full_name=repo_full_name, pr_number=pr_number)
        if active_job is not None:
            return {
                "accepted": True,
                "job": active_job.to_dict(),
                "reason": "По этому Pull Request уже идёт активная задача исполнителя.",
            }

        if review_state == "commented" and self._is_clean_review_summary(str(review.get("body", "") or "")):
            review_comments = fetch_pull_request_review_comments(self.settings.github_executor_token, repo_full_name, pr_number)
            if self._should_skip_clean_review_webhook(
                review=review,
                review_comments=review_comments,
                allowed_reviewer_logins=allowed_reviewer_logins,
            ):
                return {
                    "accepted": False,
                    "reason": "Чистый COMMENTED review без inline-замечаний пропущен: автоматическая правка не требуется.",
                }

        reviewed_commit_key = self._reviewed_commit_key(review=review, pr=pr)
        allowed, reason = self._allow_webhook_auto_run(
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            reviewer_login=login,
            reviewed_commit_key=reviewed_commit_key,
        )
        if not allowed:
            return {"accepted": False, "reason": reason}

        job = self.start_webhook_job(
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            trigger_review_id=int(review.get("id", 0) or 0),
            trigger_review_login=login,
        )
        return {"accepted": True, "job": job, "reviewed_commit": reviewed_commit_key}
