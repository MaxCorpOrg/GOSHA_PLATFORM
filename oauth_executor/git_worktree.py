from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class WorktreeError(RuntimeError):
    pass


def _run_git(args: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        env=merged_env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise WorktreeError((result.stderr or result.stdout or "git command failed").strip())
    return result


def sanitize_name(value: str) -> str:
    safe = []
    for ch in value.lower():
        if ch.isalnum():
            safe.append(ch)
        else:
            safe.append("-")
    slug = "".join(safe).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "item"


def prepare_worktree(*, repo_path: Path, worktree_root: Path, repo_full_name: str, branch_name: str, pr_number: int) -> tuple[Path, str]:
    repo_slug = sanitize_name(repo_full_name.replace("/", "-"))
    branch_slug = sanitize_name(branch_name)
    local_branch = f"codex-executor-pr-{pr_number}-{branch_slug}"
    worktree_path = worktree_root / repo_slug / f"pr-{pr_number}-{branch_slug}"
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    _run_git(["git", "-C", str(repo_path), "worktree", "prune"])
    _run_git(["git", "-C", str(repo_path), "fetch", "origin", branch_name])

    if (worktree_path / ".git").exists():
        _run_git(["git", "-C", str(worktree_path), "fetch", "origin", branch_name])
        try:
            _run_git(["git", "-C", str(worktree_path), "checkout", local_branch])
        except WorktreeError:
            _run_git(["git", "-C", str(worktree_path), "checkout", "-B", local_branch, f"origin/{branch_name}"])
        _run_git(["git", "-C", str(worktree_path), "reset", "--hard", f"origin/{branch_name}"])
        _run_git(["git", "-C", str(worktree_path), "clean", "-fdx"])
        return worktree_path, local_branch

    if worktree_path.exists():
        shutil.rmtree(worktree_path)

    _run_git(
        [
            "git",
            "-C",
            str(repo_path),
            "worktree",
            "add",
            "--force",
            "-B",
            local_branch,
            str(worktree_path),
            f"origin/{branch_name}",
        ]
    )
    return worktree_path, local_branch


def current_head_sha(worktree_path: Path) -> str:
    result = _run_git(["git", "-C", str(worktree_path), "rev-parse", "HEAD"])
    return result.stdout.strip()


def has_changes(worktree_path: Path) -> bool:
    result = _run_git(["git", "-C", str(worktree_path), "status", "--short"])
    return bool(result.stdout.strip())


def commit_all_changes(
    *,
    worktree_path: Path,
    message: str,
    author_name: str,
    author_email: str,
) -> str:
    env = {
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": author_name,
        "GIT_COMMITTER_EMAIL": author_email,
    }
    _run_git(["git", "-C", str(worktree_path), "add", "-A"], env=env)
    _run_git(["git", "-C", str(worktree_path), "commit", "-m", message], env=env)
    return current_head_sha(worktree_path)


def _build_github_askpass_env(access_token: str) -> tuple[tempfile.TemporaryDirectory, dict[str, str]]:
    temp_dir = tempfile.TemporaryDirectory(prefix="gosha-git-askpass-")
    temp_path = Path(temp_dir.name)
    token_path = temp_path / "token.txt"
    script_path = temp_path / "askpass.sh"

    token_path.write_text(access_token, encoding="utf-8")
    token_path.chmod(0o600)
    script_path.write_text(
        """#!/usr/bin/env bash
set -eu
prompt="${1:-}"
case "$prompt" in
  *Username*) printf '%s' 'x-access-token' ;;
  *Password*) cat "$GOSHA_GIT_ASKPASS_TOKEN_FILE" ;;
  *) printf '%s' '' ;;
esac
""",
        encoding="utf-8",
    )
    script_path.chmod(0o700)
    return temp_dir, {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": str(script_path),
        "GOSHA_GIT_ASKPASS_TOKEN_FILE": str(token_path),
    }


def push_head_to_branch(*, worktree_path: Path, repo_full_name: str, branch_name: str, access_token: str) -> None:
    remote_url = f"https://github.com/{repo_full_name}.git"
    temp_dir, askpass_env = _build_github_askpass_env(access_token)
    try:
        _run_git(
            ["git", "-C", str(worktree_path), "push", remote_url, f"HEAD:{branch_name}"],
            env=askpass_env,
        )
    finally:
        temp_dir.cleanup()
