from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from oauth_executor.config import Settings
from oauth_executor.executor_service import ExecutorService, ExecutorServiceError


def _build_settings(root: Path) -> Settings:
    session_dir = root / "sessions"
    worktree_dir = root / "worktrees"
    session_dir.mkdir(parents=True, exist_ok=True)
    worktree_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        session_secret="test-secret",
        session_store_dir=session_dir,
        session_ttl_seconds=3600,
        protected_branches=("main",),
        github_client_id="client-id",
        github_client_secret="client-secret",
        github_redirect_uri="http://127.0.0.1/callback",
        github_scope="read:user repo",
        github_executor_token="ghs_test",
        github_webhook_secret="whsec_test",
        reviewer_logins=("chatgpt-codex-connector[bot]",),
        allowed_repos=("MaxCorpOrg/GOSHA_PLATFORM",),
        repo_path=root,
        worktree_root=worktree_dir,
        validate_command="bash bin/ci_validate.sh",
        codex_command="python3",
        codex_model="",
        codex_reasoning_effort="xhigh",
        codex_profile="",
        codex_timeout_seconds=60,
        open_terminal=False,
        terminal_command="",
        terminal_runtime_dir=root / "terminals",
        max_auto_runs_per_pr=3,
        auto_run_window_seconds=21600,
        comment_on_pr=False,
        cookie_secure=False,
        git_author_name="Test Executor",
        git_author_email="executor@example.com",
    )


class ExecutorServiceReviewWebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.service = ExecutorService(_build_settings(self.root))

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _webhook_body(self, *, review_body: str) -> bytes:
        payload = {
            "action": "submitted",
            "review": {
                "id": 501,
                "state": "commented",
                "body": review_body,
                "user": {"login": "chatgpt-codex-connector[bot]"},
            },
            "pull_request": {"number": 2},
            "repository": {"full_name": "MaxCorpOrg/GOSHA_PLATFORM"},
        }
        return json.dumps(payload).encode("utf-8")

    def test_filter_prompt_feedback_ignores_clean_summary_without_inline_comments(self) -> None:
        reviews = [
            {
                "id": 501,
                "state": "commented",
                "body": "## Итог\nКритичных P0/P1 замечаний не найдено.\n\n## Замечания\nНет замечаний.\n",
            }
        ]

        prompt_reviews, prompt_comments = self.service._filter_prompt_feedback(
            reviews=reviews,
            review_comments=[],
        )

        self.assertEqual(prompt_reviews, [])
        self.assertEqual(prompt_comments, [])

    def test_handle_webhook_skips_clean_summary_without_inline_comments(self) -> None:
        with patch("oauth_executor.executor_service.fetch_pull_request_review_comments", return_value=[]), patch.object(
            self.service,
            "start_webhook_job",
        ) as start_webhook_job:
            result = self.service.handle_webhook(
                event_name="pull_request_review",
                body=self._webhook_body(review_body="## Итог\nКритичных P0/P1 замечаний не найдено.\n"),
            )

        self.assertFalse(result["accepted"])
        self.assertIn("автоматическая правка не требуется", result["reason"])
        start_webhook_job.assert_not_called()

    def test_handle_webhook_keeps_running_when_inline_comment_exists(self) -> None:
        review_comments = [
            {
                "pull_request_review_id": 501,
                "body": "Нужно пропустить чистый итог до запуска executor.",
                "user": {"login": "chatgpt-codex-connector[bot]"},
            }
        ]
        expected_job = {"job_id": "job-1", "status": "queued"}
        with patch("oauth_executor.executor_service.fetch_pull_request_review_comments", return_value=review_comments), patch.object(
            self.service,
            "start_webhook_job",
            return_value=expected_job,
        ) as start_webhook_job:
            result = self.service.handle_webhook(
                event_name="pull_request_review",
                body=self._webhook_body(review_body="## Итог\nКритичных P0/P1 замечаний не найдено.\n"),
            )

        self.assertTrue(result["accepted"])
        self.assertEqual(result["job"], expected_job)
        start_webhook_job.assert_called_once_with(
            repo_full_name="MaxCorpOrg/GOSHA_PLATFORM",
            pr_number=2,
            trigger_review_id=501,
            trigger_review_login="chatgpt-codex-connector[bot]",
        )

    def test_handle_webhook_rolls_back_auto_run_marker_when_start_fails(self) -> None:
        with patch.object(
            self.service,
            "start_webhook_job",
            side_effect=ExecutorServiceError("executor queue is unavailable"),
        ):
            with self.assertRaisesRegex(ExecutorServiceError, "queue is unavailable"):
                self.service.handle_webhook(
                    event_name="pull_request_review",
                    body=self._webhook_body(review_body="Нужно починить порядок меток webhook."),
                )

        self.assertEqual(self.service._processed_webhook_keys, set())
        self.assertEqual(self.service._auto_run_history, {})

        expected_job = {"job_id": "job-2", "status": "queued"}
        with patch.object(
            self.service,
            "start_webhook_job",
            return_value=expected_job,
        ) as start_webhook_job:
            result = self.service.handle_webhook(
                event_name="pull_request_review",
                body=self._webhook_body(review_body="Нужно починить порядок меток webhook."),
            )

        self.assertTrue(result["accepted"])
        self.assertEqual(result["job"], expected_job)
        start_webhook_job.assert_called_once_with(
            repo_full_name="MaxCorpOrg/GOSHA_PLATFORM",
            pr_number=2,
            trigger_review_id=501,
            trigger_review_login="chatgpt-codex-connector[bot]",
        )

    def test_review_scope_ignores_older_change_request_after_later_approval(self) -> None:
        reviews = [
            {
                "id": 501,
                "state": "CHANGES_REQUESTED",
                "submitted_at": "2026-05-18T10:00:00Z",
                "body": "Нужно исправить фильтрацию review.",
                "user": {"login": "chatgpt-codex-connector[bot]"},
            },
            {
                "id": 502,
                "state": "APPROVED",
                "submitted_at": "2026-05-18T11:00:00Z",
                "body": "LGTM",
                "user": {"login": "chatgpt-codex-connector[bot]"},
            },
        ]
        review_comments = [
            {
                "pull_request_review_id": 501,
                "body": "Старый inline-комментарий не должен возвращаться после approve.",
                "user": {"login": "chatgpt-codex-connector[bot]"},
            }
        ]

        scoped_reviews, scoped_comments, scope_note = self.service._review_scope(
            reviews=reviews,
            review_comments=review_comments,
            allowed_reviewer_logins={"chatgpt-codex-connector[bot]"},
            trigger_review_id=0,
        )

        self.assertEqual(scoped_reviews, [])
        self.assertEqual(scoped_comments, [])
        self.assertIn("последние review", scope_note.lower())

    def test_run_job_marks_validate_timeout_as_failed(self) -> None:
        self.service.settings = replace(
            self.service.settings,
            codex_timeout_seconds=0.2,
            validate_command='python3 -c "import time; time.sleep(60)"',
        )
        job, created = self.service.jobs.create_or_get_active(
            repo_full_name="MaxCorpOrg/GOSHA_PLATFORM",
            pr_number=2,
            trigger="manual",
        )
        self.assertTrue(created)

        pr_payload = {
            "number": 2,
            "title": "Test PR",
            "body": "Body",
            "base": {"ref": "main", "repo": {"default_branch": "main"}},
            "head": {"ref": "feature/test", "repo": {"full_name": "MaxCorpOrg/GOSHA_PLATFORM"}},
        }
        reviews = [
            {
                "id": 501,
                "state": "CHANGES_REQUESTED",
                "body": "Нужно исправить зависающий validate.",
                "user": {"login": "chatgpt-codex-connector[bot]"},
            }
        ]

        with patch("oauth_executor.executor_service.fetch_pull_request", return_value=pr_payload), patch(
            "oauth_executor.executor_service.fetch_pull_request_review_comments",
            return_value=[],
        ), patch(
            "oauth_executor.executor_service.fetch_pull_request_reviews",
            return_value=reviews,
        ), patch(
            "oauth_executor.executor_service.fetch_pull_request_files",
            return_value=[],
        ), patch(
            "oauth_executor.executor_service.prepare_worktree",
            return_value=(self.root, "local-feature-test"),
        ), patch(
            "oauth_executor.executor_service.current_head_sha",
            return_value="abc123",
        ), patch(
            "oauth_executor.executor_service.collect_relevant_agents",
            return_value=[],
        ), patch(
            "oauth_executor.executor_service.run_codex_exec",
            return_value="",
        ):
            self.service._run_job(
                job_id=job.job_id,
                repo_full_name="MaxCorpOrg/GOSHA_PLATFORM",
                pr_number=2,
                access_token="ghs_test",
            )

        stored = self.service.jobs.get(job.job_id)
        assert stored is not None
        self.assertEqual(stored.status, "failed")
        self.assertIn("Проверочная команда не уложилась", stored.error)


if __name__ == "__main__":
    unittest.main()
