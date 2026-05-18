from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


class ExecutorServiceProtectedBranchTest(unittest.TestCase):
    @patch("oauth_executor.executor_service.prepare_worktree")
    @patch("oauth_executor.executor_service.fetch_pull_request")
    def test_run_job_marks_failed_when_head_branch_matches_default_branch(
        self,
        mock_fetch_pull_request,
        mock_prepare_worktree,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = ExecutorService(
                build_settings(Path(tmp_dir), allowed_repos=("MaxCorpOrg/GOSHA_PLATFORM",))
            )
            job, created = service.jobs.create_or_get_active(
                repo_full_name="MaxCorpOrg/GOSHA_PLATFORM",
                pr_number=1,
                trigger="manual",
            )

            self.assertTrue(created)
            mock_fetch_pull_request.return_value = {
                "head": {
                    "ref": "main",
                    "repo": {
                        "full_name": "MaxCorpOrg/GOSHA_PLATFORM",
                        "default_branch": "main",
                    },
                }
            }

            service._run_job(
                job_id=job.job_id,
                repo_full_name=job.repo_full_name,
                pr_number=job.pr_number,
                access_token="token",
            )

            saved_job = service.get_job(job.job_id)
            assert saved_job is not None
            self.assertEqual("failed", saved_job["status"])
            self.assertIn("default branch", saved_job["error"])
            mock_prepare_worktree.assert_not_called()

    @patch("oauth_executor.executor_service.prepare_worktree")
    @patch("oauth_executor.executor_service.fetch_branch")
    @patch("oauth_executor.executor_service.fetch_pull_request")
    def test_run_job_marks_failed_when_head_branch_is_protected(
        self,
        mock_fetch_pull_request,
        mock_fetch_branch,
        mock_prepare_worktree,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = ExecutorService(
                build_settings(Path(tmp_dir), allowed_repos=("MaxCorpOrg/GOSHA_PLATFORM",))
            )
            job, created = service.jobs.create_or_get_active(
                repo_full_name="MaxCorpOrg/GOSHA_PLATFORM",
                pr_number=1,
                trigger="manual",
            )

            self.assertTrue(created)
            mock_fetch_pull_request.return_value = {
                "head": {
                    "ref": "release",
                    "repo": {
                        "full_name": "MaxCorpOrg/GOSHA_PLATFORM",
                        "default_branch": "main",
                    },
                }
            }
            mock_fetch_branch.return_value = {"name": "release", "protected": True}

            service._run_job(
                job_id=job.job_id,
                repo_full_name=job.repo_full_name,
                pr_number=job.pr_number,
                access_token="token",
            )

            saved_job = service.get_job(job.job_id)
            assert saved_job is not None
            self.assertEqual("failed", saved_job["status"])
            self.assertIn("защищённую head-ветку", saved_job["error"])
            mock_prepare_worktree.assert_not_called()
