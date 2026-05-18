from __future__ import annotations

import unittest

from oauth_reviewer.repo_guidance import ensure_repo_allowed


class EnsureRepoAllowedTest(unittest.TestCase):
    def test_rejects_empty_allow_list_as_configuration_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            ensure_repo_allowed("MaxCorpOrg/GOSHA_PLATFORM", ())

        self.assertIn("Разрешённый список репозиториев пуст", str(ctx.exception))

    def test_rejects_repo_outside_allow_list(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            ensure_repo_allowed("OtherOrg/OtherRepo", ("MaxCorpOrg/GOSHA_PLATFORM",))

        self.assertIn("не входит в разрешённый список", str(ctx.exception))
