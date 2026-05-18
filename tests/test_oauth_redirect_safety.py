from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import oauth_reviewer.app as reviewer_app
from oauth_executor.config import Settings
from oauth_executor.executor_service import ExecutorService
from oauth_reviewer.github_api import fetch_branch, sanitize_internal_redirect_path


def build_executor_settings(repo_path: Path) -> Settings:
    return Settings(
        session_secret="test-secret",
        github_client_id="client-id",
        github_client_secret="client-secret",
        github_redirect_uri="http://127.0.0.1/callback",
        github_scope="read:user repo",
        github_executor_token="executor-token",
        github_webhook_secret="webhook-secret",
        reviewer_logins=("chatgpt-codex-connector[bot]",),
        allowed_repos=("MaxCorpOrg/GOSHA_PLATFORM",),
        repo_path=repo_path,
        worktree_root=repo_path / "worktrees",
        validate_command="",
        codex_command="/bin/echo",
        codex_model="",
        codex_profile="",
        codex_timeout_seconds=30,
        comment_on_pr=False,
        cookie_secure=False,
        git_author_name="Test Executor",
        git_author_email="executor@example.com",
    )


class DummyRequest:
    def __init__(self, session: dict | None = None) -> None:
        self.session = session or {}


class RedirectPathSanitizerTest(unittest.TestCase):
    def test_rejects_external_and_non_internal_paths(self) -> None:
        self.assertEqual("/", sanitize_internal_redirect_path("https://attacker.example"))
        self.assertEqual("/", sanitize_internal_redirect_path("//attacker.example/path"))
        self.assertEqual("/", sanitize_internal_redirect_path("dashboard"))
        self.assertEqual("/", sanitize_internal_redirect_path("/\\attacker"))

    def test_keeps_internal_paths(self) -> None:
        self.assertEqual("/", sanitize_internal_redirect_path("/"))
        self.assertEqual("/jobs/1?tab=log#tail", sanitize_internal_redirect_path("/jobs/1?tab=log#tail"))


class GitHubBranchApiTest(unittest.TestCase):
    @patch("oauth_reviewer.github_api._request_json", return_value={"protected": False})
    def test_fetch_branch_urlencodes_branch_name(self, mock_request_json) -> None:
        fetch_branch("token", "MaxCorpOrg/GOSHA_PLATFORM", "codex/e2e-oauth")

        self.assertEqual(
            "https://api.github.com/repos/MaxCorpOrg/GOSHA_PLATFORM/branches/codex%2Fe2e-oauth",
            mock_request_json.call_args.args[0],
        )


class ReviewerOAuthRedirectSafetyTest(unittest.TestCase):
    def test_auth_start_saves_only_internal_next_path(self) -> None:
        request = DummyRequest()
        reviewer_settings = SimpleNamespace(
            github_ready=True,
            github_client_id="client-id",
            github_redirect_uri="http://127.0.0.1/callback",
            github_scope="read:user repo",
        )

        with patch.object(reviewer_app, "settings", reviewer_settings):
            response = reviewer_app.auth_github_start(request, next="https://attacker.example/login")

        self.assertEqual("/", request.session["github_oauth_next"])
        self.assertEqual(307, response.status_code)

    def test_auth_callback_rechecks_session_next_path(self) -> None:
        request = DummyRequest(
            {
                "github_oauth_state": "state-123",
                "github_oauth_next": "https://attacker.example/login",
            }
        )
        reviewer_settings = SimpleNamespace(
            github_client_id="client-id",
            github_client_secret="client-secret",
            github_redirect_uri="http://127.0.0.1/callback",
        )

        with (
            patch.object(reviewer_app, "settings", reviewer_settings),
            patch.object(reviewer_app, "exchange_code_for_token", return_value="token"),
            patch.object(reviewer_app, "fetch_user", return_value={"login": "octocat", "id": 7}),
        ):
            response = reviewer_app.auth_github_callback(request, code="code-123", state="state-123")

        self.assertEqual("/", response.headers["location"])


class ExecutorOAuthRedirectSafetyTest(unittest.TestCase):
    def test_auth_start_saves_only_internal_next_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = ExecutorService(build_executor_settings(Path(tmp_dir)))
            request = DummyRequest()

            auth_url = service.github_auth_start(request, "https://attacker.example/login")

            self.assertEqual("/", request.session["github_oauth_next"])
            self.assertIn("https://github.com/login/oauth/authorize?", auth_url)

    def test_auth_callback_rechecks_session_next_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = ExecutorService(build_executor_settings(Path(tmp_dir)))
            request = DummyRequest(
                {
                    "github_oauth_state": "state-123",
                    "github_oauth_next": "https://attacker.example/login",
                }
            )

            with (
                patch("oauth_executor.executor_service.exchange_code_for_token", return_value="token"),
                patch("oauth_executor.executor_service.fetch_user", return_value={"login": "octocat", "id": 7}),
            ):
                next_path = service.github_auth_callback(request, code="code-123", state="state-123")

            self.assertEqual("/", next_path)
