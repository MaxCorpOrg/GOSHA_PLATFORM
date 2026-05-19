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
    github_client_id: str
    github_client_secret: str
    github_redirect_uri: str
    github_scope: str
    allowed_repos: tuple[str, ...]
    repo_path: Path
    review_backend: str
    codex_command: str
    codex_model: str
    codex_reasoning_effort: str
    codex_profile: str
    codex_timeout_seconds: int
    open_terminal: bool
    terminal_command: str
    terminal_runtime_dir: Path
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    max_patch_chars_per_file: int
    max_total_patch_chars: int
    cookie_secure: bool

    @property
    def github_ready(self) -> bool:
        return bool(self.github_client_id and self.github_client_secret and self.github_redirect_uri and self.session_secret)

    @property
    def codex_ready(self) -> bool:
        if not self.codex_command:
            return False
        if os.path.sep in self.codex_command:
            return Path(self.codex_command).exists()
        return shutil.which(self.codex_command) is not None

    @property
    def openai_ready(self) -> bool:
        return bool(self.openai_api_key and self.openai_base_url and self.openai_model)

    @classmethod
    def from_env(cls) -> "Settings":
        allowed_repos_raw = str(os.environ.get("OAUTH_REVIEWER_ALLOWED_REPOS", "MaxCorpOrg/GOSHA_PLATFORM") or "")
        allowed_repos = tuple(item.strip() for item in allowed_repos_raw.split(",") if item.strip())
        repo_path = Path(str(os.environ.get("OAUTH_REVIEWER_REPO_PATH", "/home/max/GOSHA_PLATFORM") or "/home/max/GOSHA_PLATFORM")).resolve()
        return cls(
            session_secret=str(os.environ.get("OAUTH_REVIEWER_SESSION_SECRET", "") or ""),
            session_store_dir=Path(
                str(
                    os.environ.get(
                        "OAUTH_REVIEWER_SESSION_STORE_DIR",
                        "/home/max/GOSHA_PLATFORM/local_only/oauth_reviewer_sessions",
                    )
                    or "/home/max/GOSHA_PLATFORM/local_only/oauth_reviewer_sessions"
                )
            ).resolve(),
            session_ttl_seconds=_as_int(os.environ.get("OAUTH_REVIEWER_SESSION_TTL_SECONDS"), 43200),
            github_client_id=str(os.environ.get("GITHUB_OAUTH_CLIENT_ID", "") or ""),
            github_client_secret=str(os.environ.get("GITHUB_OAUTH_CLIENT_SECRET", "") or ""),
            github_redirect_uri=str(os.environ.get("GITHUB_OAUTH_REDIRECT_URI", "") or ""),
            github_scope=str(os.environ.get("GITHUB_OAUTH_SCOPE", "read:user repo") or "read:user repo"),
            allowed_repos=allowed_repos,
            repo_path=repo_path,
            review_backend=str(os.environ.get("OAUTH_REVIEWER_BACKEND", "auto") or "auto").strip().lower(),
            codex_command=str(os.environ.get("OAUTH_REVIEWER_CODEX_COMMAND", "codex") or "codex"),
            codex_model=str(os.environ.get("OAUTH_REVIEWER_CODEX_MODEL", "gpt-5.4") or "gpt-5.4").strip(),
            codex_reasoning_effort=str(os.environ.get("OAUTH_REVIEWER_CODEX_REASONING_EFFORT", "xhigh") or "xhigh").strip(),
            codex_profile=str(os.environ.get("OAUTH_REVIEWER_CODEX_PROFILE", "") or ""),
            codex_timeout_seconds=_as_int(os.environ.get("OAUTH_REVIEWER_CODEX_TIMEOUT_SECONDS"), 1800),
            open_terminal=_as_bool(os.environ.get("OAUTH_REVIEWER_OPEN_TERMINAL"), False),
            terminal_command=str(os.environ.get("OAUTH_REVIEWER_TERMINAL_COMMAND", "") or "").strip(),
            terminal_runtime_dir=Path(
                str(
                    os.environ.get(
                        "OAUTH_REVIEWER_TERMINAL_RUNTIME_DIR",
                        "/home/max/GOSHA_PLATFORM/local_only/oauth_reviewer_terminals",
                    )
                    or "/home/max/GOSHA_PLATFORM/local_only/oauth_reviewer_terminals"
                )
            ).resolve(),
            openai_api_key=str(os.environ.get("OPENAI_API_KEY", "") or ""),
            openai_base_url=str(os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1") or "https://api.openai.com/v1").rstrip("/"),
            openai_model=str(os.environ.get("OPENAI_MODEL", "gpt-5.4-mini") or "gpt-5.4-mini"),
            max_patch_chars_per_file=_as_int(os.environ.get("OAUTH_REVIEWER_MAX_PATCH_CHARS_PER_FILE"), 12000),
            max_total_patch_chars=_as_int(os.environ.get("OAUTH_REVIEWER_MAX_TOTAL_PATCH_CHARS"), 90000),
            cookie_secure=_as_bool(os.environ.get("OAUTH_REVIEWER_COOKIE_SECURE"), False),
        )
