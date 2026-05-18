from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from oauth_executor.git_worktree import push_head_to_branch


class PushHeadToBranchTests(unittest.TestCase):
    def test_push_uses_askpass_without_putting_token_in_git_argv(self) -> None:
        captured: dict[str, object] = {}
        secret_token = "ghs_test_secret_token"

        def fake_run_git(args, *, cwd=None, env=None):
            captured["args"] = list(args)
            captured["cwd"] = cwd
            captured["env"] = dict(env or {})
            token_path = Path(captured["env"]["GOSHA_GIT_ASKPASS_TOKEN_FILE"])
            askpass_path = Path(captured["env"]["GIT_ASKPASS"])
            captured["token_path"] = token_path
            captured["askpass_path"] = askpass_path
            captured["token_file_text"] = token_path.read_text(encoding="utf-8")
            captured["askpass_text"] = askpass_path.read_text(encoding="utf-8")
            return object()

        with patch("oauth_executor.git_worktree._run_git", side_effect=fake_run_git):
            push_head_to_branch(
                worktree_path=Path("/tmp/worktree"),
                repo_full_name="MaxCorpOrg/GOSHA_PLATFORM",
                branch_name="feature/test",
                access_token=secret_token,
            )

        args = captured["args"]
        env = captured["env"]
        token_path = captured["token_path"]
        askpass_path = captured["askpass_path"]

        self.assertEqual(
            args,
            [
                "git",
                "-C",
                "/tmp/worktree",
                "push",
                "https://github.com/MaxCorpOrg/GOSHA_PLATFORM.git",
                "HEAD:feature/test",
            ],
        )
        self.assertNotIn(secret_token, " ".join(args))
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertIn("GIT_ASKPASS", env)
        self.assertIn("GOSHA_GIT_ASKPASS_TOKEN_FILE", env)
        self.assertNotIn(secret_token, "".join(str(value) for value in env.values()))
        self.assertEqual(captured["token_file_text"], secret_token)
        self.assertNotIn(secret_token, captured["askpass_text"])
        self.assertFalse(token_path.exists())
        self.assertFalse(askpass_path.exists())


if __name__ == "__main__":
    unittest.main()
