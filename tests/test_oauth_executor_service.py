from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from oauth_executor.config import Settings
from oauth_executor.executor_service import ExecutorService, ExecutorServiceError


def build_settings(repo_path: Path, *, allowed_repos: tuple[str, ...]) -> Settings:
    return Settings(
        session_secret="test-secret",
        github_client_id="client-id",
        github_client_secret="client-secret",
        github_redirect_uri="http://127.0.0.1/callback",
        github_scope="read:user repo",
        github_executor_token="executor-token",
        github_webhook_secret="webhook-secret",
        reviewer_logins=("chatgpt-codex-connector[bot]",),
        allowed_repos=allowed_repos,
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


class ExecutorServiceAllowedRepoTest(unittest.TestCase):
    def test_start_manual_job_rejects_disallowed_repo_before_job_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = ExecutorService(
                build_settings(Path(tmp_dir), allowed_repos=("MaxCorpOrg/GOSHA_PLATFORM",))
            )

            with self.assertRaises(ExecutorServiceError) as ctx:
                service.start_manual_job(
                    repo_full_name="OtherOrg/OtherRepo",
                    pr_number=1,
                    access_token="token",
                )

            self.assertIn("не входит в разрешённый список", str(ctx.exception))
            self.assertEqual([], service.list_jobs())

    def test_run_job_marks_failed_when_repo_guard_triggers_inside_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = ExecutorService(
                build_settings(Path(tmp_dir), allowed_repos=("MaxCorpOrg/GOSHA_PLATFORM",))
            )
            job, created = service.jobs.create_or_get_active(
                repo_full_name="OtherOrg/OtherRepo",
                pr_number=1,
                trigger="manual",
            )

            self.assertTrue(created)
            service._run_job(
                job_id=job.job_id,
                repo_full_name=job.repo_full_name,
                pr_number=job.pr_number,
                access_token="token",
            )

            saved_job = service.get_job(job.job_id)
            assert saved_job is not None
            self.assertEqual("failed", saved_job["status"])
            self.assertIn("не входит в разрешённый список", saved_job["error"])
            self.assertTrue(any(line.startswith("ERROR: ") for line in saved_job["logs"]))
