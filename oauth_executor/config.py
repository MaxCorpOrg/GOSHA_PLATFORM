from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(str(value or "").strip())
    except Exception:
        return default


@dataclass(frozen=True)
class Settings:
    session_secret: str
    session_store_dir: Path
    session_ttl_seconds: int
    protected_branches: tuple[str, ...]
    github_client_id: str
    github_client_secret: str
    github_redirect_uri: str
    github_scope: str
    github_executor_token: str
    github_webhook_secret: str
    reviewer_logins: tuple[str, ...]
    allowed_repos: tuple[str, ...]
    repo_path: Path
    worktree_root: Path
    validate_command: str
    codex_command: str
    codex_model: str
    codex_profile: str
    codex_timeout_seconds: int
    comment_on_pr: bool
    cookie_secure: bool
    git_author_name: str
    git_author_email: str

    @property
    def github_ready(self) -> bool:
        return bool(self.github_client_id and self.github_client_secret and self.github_redirect_uri and self.session_secret)

    @property
    def service_token_ready(self) -> bool:
        return bool(self.github_executor_token)

    @property
    def webhook_ready(self) -> bool:
        return bool(self.github_webhook_secret and self.github_executor_token and self.reviewer_logins)

    @property
    def codex_ready(self) -> bool:
        if not self.codex_command:
            return False
        if os.path.sep in self.codex_command:
            return Path(self.codex_command).exists()
        return bool(shutil.which(self.codex_command))

    @classmethod
    def from_env(cls) -> "Settings":
        allowed_repos_raw = str(os.environ.get("OAUTH_EXECUTOR_ALLOWED_REPOS", "MaxCorpOrg/GOSHA_PLATFORM") or "")
        reviewer_logins_raw = str(os.environ.get("OAUTH_EXECUTOR_REVIEWER_LOGINS", "") or "")
        protected_branches_raw = str(os.environ.get("OAUTH_EXECUTOR_PROTECTED_BRANCHES", "main,master,release") or "")
        return cls(
            session_secret=str(os.environ.get("OAUTH_EXECUTOR_SESSION_SECRET", "") or ""),
            session_store_dir=Path(
                str(
                    os.environ.get(
                        "OAUTH_EXECUTOR_SESSION_STORE_DIR",
                        "/home/max/GOSHA_PLATFORM/local_only/oauth_executor_sessions",
                    )
                    or "/home/max/GOSHA_PLATFORM/local_only/oauth_executor_sessions"
                )
            ).resolve(),
            session_ttl_seconds=_as_int(os.environ.get("OAUTH_EXECUTOR_SESSION_TTL_SECONDS"), 43200),
            protected_branches=tuple(item.strip() for item in protected_branches_raw.split(",") if item.strip()),
            github_client_id=str(os.environ.get("GITHUB_OAUTH_CLIENT_ID", "") or ""),
            github_client_secret=str(os.environ.get("GITHUB_OAUTH_CLIENT_SECRET", "") or ""),
            github_redirect_uri=str(os.environ.get("GITHUB_OAUTH_REDIRECT_URI", "") or ""),
            github_scope=str(os.environ.get("GITHUB_OAUTH_SCOPE", "read:user repo") or "read:user repo"),
            github_executor_token=str(os.environ.get("GITHUB_EXECUTOR_TOKEN", "") or ""),
            github_webhook_secret=str(os.environ.get("OAUTH_EXECUTOR_GITHUB_WEBHOOK_SECRET", "") or ""),
            reviewer_logins=tuple(item.strip() for item in reviewer_logins_raw.split(",") if item.strip()),
            allowed_repos=tuple(item.strip() for item in allowed_repos_raw.split(",") if item.strip()),
            repo_path=Path(str(os.environ.get("OAUTH_EXECUTOR_REPO_PATH", "/home/max/GOSHA_PLATFORM") or "/home/max/GOSHA_PLATFORM")).resolve(),
            worktree_root=Path(str(os.environ.get("OAUTH_EXECUTOR_WORKTREE_ROOT", "/home/max/GOSHA_PLATFORM/local_only/oauth_executor_worktrees") or "/home/max/GOSHA_PLATFORM/local_only/oauth_executor_worktrees")).resolve(),
            validate_command=str(os.environ.get("OAUTH_EXECUTOR_VALIDATE_COMMAND", "bash bin/ci_validate.sh") or "bash bin/ci_validate.sh").strip(),
            codex_command=str(os.environ.get("OAUTH_EXECUTOR_CODEX_COMMAND", "codex") or "codex").strip(),
            codex_model=str(os.environ.get("OAUTH_EXECUTOR_CODEX_MODEL", "") or "").strip(),
            codex_profile=str(os.environ.get("OAUTH_EXECUTOR_CODEX_PROFILE", "") or "").strip(),
            codex_timeout_seconds=_as_int(os.environ.get("OAUTH_EXECUTOR_CODEX_TIMEOUT_SECONDS"), 1800),
            comment_on_pr=_as_bool(os.environ.get("OAUTH_EXECUTOR_COMMENT_ON_PR"), True),
            cookie_secure=_as_bool(os.environ.get("OAUTH_EXECUTOR_COOKIE_SECURE"), False),
            git_author_name=str(os.environ.get("OAUTH_EXECUTOR_GIT_AUTHOR_NAME", "GOSHA Codex Executor") or "GOSHA Codex Executor").strip(),
            git_author_email=str(os.environ.get("OAUTH_EXECUTOR_GIT_AUTHOR_EMAIL", "codex-executor@local") or "codex-executor@local").strip(),
        )
