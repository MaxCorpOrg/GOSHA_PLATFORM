from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen


class GitHubApiError(RuntimeError):
    pass


def _request_json(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, body: dict[str, Any] | None = None) -> Any:
    request_headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "gosha-oauth-reviewer/1.0",
    }
    if headers:
        request_headers.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8")
        except Exception:
            raw = exc.reason or ""
        raise GitHubApiError(f"GitHub API вернул HTTP {exc.code}: {raw}") from exc
    except URLError as exc:
        raise GitHubApiError(f"Не удалось обратиться к GitHub API: {exc.reason}") from exc


def github_authorization_url(*, client_id: str, redirect_uri: str, scope: str, state: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
            "allow_signup": "true",
        }
    )
    return f"https://github.com/login/oauth/authorize?{query}"


def sanitize_internal_redirect_path(next_path: str | None) -> str:
    candidate = str(next_path or "").strip()
    if not candidate:
        return "/"
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        return "/"
    if not candidate.startswith("/") or candidate.startswith("//") or "\\" in candidate:
        return "/"
    return candidate


def exchange_code_for_token(*, client_id: str, client_secret: str, code: str, redirect_uri: str) -> str:
    payload = _request_json(
        "https://github.com/login/oauth/access_token",
        method="POST",
        headers={"Accept": "application/json"},
        body={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )
    token = str(payload.get("access_token", "") or "").strip()
    if not token:
        raise GitHubApiError(f"GitHub OAuth не вернул access token: {payload}")
    return token


def fetch_user(access_token: str) -> dict[str, Any]:
    return _request_json(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {access_token}"},
    )


def fetch_pull_request(access_token: str, repo_full_name: str, pr_number: int) -> dict[str, Any]:
    return _request_json(
        f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}",
        headers={"Authorization": f"Bearer {access_token}"},
    )


def fetch_branch(access_token: str, repo_full_name: str, branch_name: str) -> dict[str, Any]:
    encoded_branch = quote(branch_name, safe="")
    return _request_json(
        f"https://api.github.com/repos/{repo_full_name}/branches/{encoded_branch}",
        headers={"Authorization": f"Bearer {access_token}"},
    )


def fetch_pull_request_files(access_token: str, repo_full_name: str, pr_number: int) -> list[dict[str, Any]]:
    page = 1
    files: list[dict[str, Any]] = []
    while True:
        batch = _request_json(
            f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/files?per_page=100&page={page}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if not isinstance(batch, list):
            raise GitHubApiError("GitHub API вернул неожиданный формат списка файлов PR.")
        files.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < 100:
            return files
        page += 1


def fetch_pull_request_reviews(access_token: str, repo_full_name: str, pr_number: int) -> list[dict[str, Any]]:
    page = 1
    reviews: list[dict[str, Any]] = []
    while True:
        batch = _request_json(
            f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/reviews?per_page=100&page={page}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if not isinstance(batch, list):
            raise GitHubApiError("GitHub API вернул неожиданный формат списка review Pull Request.")
        reviews.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < 100:
            return reviews
        page += 1


def fetch_pull_request_review_comments(access_token: str, repo_full_name: str, pr_number: int) -> list[dict[str, Any]]:
    page = 1
    comments: list[dict[str, Any]] = []
    while True:
        batch = _request_json(
            f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/comments?per_page=100&page={page}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if not isinstance(batch, list):
            raise GitHubApiError("GitHub API вернул неожиданный формат списка inline-комментариев Pull Request.")
        comments.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < 100:
            return comments
        page += 1


def fetch_issue_comments(access_token: str, repo_full_name: str, issue_number: int) -> list[dict[str, Any]]:
    page = 1
    comments: list[dict[str, Any]] = []
    while True:
        batch = _request_json(
            f"https://api.github.com/repos/{repo_full_name}/issues/{issue_number}/comments?per_page=100&page={page}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if not isinstance(batch, list):
            raise GitHubApiError("GitHub API вернул неожиданный формат списка верхнеуровневых комментариев.")
        comments.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < 100:
            return comments
        page += 1


def create_pull_request_review(access_token: str, repo_full_name: str, pr_number: int, body: str) -> dict[str, Any]:
    return _request_json(
        f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/reviews",
        method="POST",
        headers={"Authorization": f"Bearer {access_token}"},
        body={"event": "COMMENT", "body": body},
    )


def create_issue_comment(access_token: str, repo_full_name: str, issue_number: int, body: str) -> dict[str, Any]:
    return _request_json(
        f"https://api.github.com/repos/{repo_full_name}/issues/{issue_number}/comments",
        method="POST",
        headers={"Authorization": f"Bearer {access_token}"},
        body={"body": body},
    )
