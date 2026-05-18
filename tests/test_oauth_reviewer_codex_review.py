from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oauth_reviewer.codex_review import CodexReviewError, generate_review_markdown_via_codex


class CodexReviewTimeoutTest(unittest.TestCase):
    def test_generate_review_passes_timeout_to_subprocess_and_reads_last_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_path = Path(tmp_dir)
            captured: dict[str, object] = {}

            def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
                captured["command"] = command
                captured["kwargs"] = kwargs
                last_message_path = Path(command[command.index("--output-last-message") + 1])
                last_message_path.write_text("review ok\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="partial stdout", stderr=None)

            with patch("oauth_reviewer.codex_review.subprocess.run", side_effect=fake_run):
                review_markdown = generate_review_markdown_via_codex(
                    codex_command="/bin/echo",
                    repo_path=repo_path,
                    prompt="review prompt",
                    model="gpt-test",
                    profile="reviewer",
                    timeout_seconds=42,
                )

        self.assertEqual("review ok", review_markdown)
        self.assertEqual("review prompt", captured["kwargs"]["input"])
        self.assertEqual(42, captured["kwargs"]["timeout"])
        self.assertEqual(subprocess.STDOUT, captured["kwargs"]["stderr"])

    def test_generate_review_raises_timeout_error_with_tail_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_path = Path(tmp_dir)
            timeout_error = subprocess.TimeoutExpired(
                cmd=["/bin/echo", "exec"],
                timeout=9,
                output="строка без перевода\nпоследняя строка",
            )

            with patch("oauth_reviewer.codex_review.subprocess.run", side_effect=timeout_error):
                with self.assertRaises(CodexReviewError) as ctx:
                    generate_review_markdown_via_codex(
                        codex_command="/bin/echo",
                        repo_path=repo_path,
                        prompt="review prompt",
                        model="",
                        profile="",
                        timeout_seconds=9,
                    )

        message = str(ctx.exception)
        self.assertIn("не уложился в лимит 9 секунд", message)
        self.assertIn("последняя строка", message)
