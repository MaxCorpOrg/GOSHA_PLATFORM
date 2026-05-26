from __future__ import annotations

import unittest

from oauth_executor.jobs import JobStore


class ExecutorJobStoreTests(unittest.TestCase):
    def test_job_store_tracks_stage_and_last_update(self) -> None:
        store = JobStore()
        job, created = store.create_or_get_active(
            repo_full_name="MaxCorpOrg/GOSHA_PLATFORM",
            pr_number=2,
            trigger="manual",
        )
        self.assertTrue(created)
        self.assertEqual(job.current_stage, "Ожидание запуска")
        self.assertEqual(job.last_log_at, 0.0)

        store.start(job.job_id)
        store.set_stage(job.job_id, "Проверка изменений")
        store.append_log(job.job_id, "Логовая строка")
        stored = store.get(job.job_id)

        assert stored is not None
        self.assertEqual(stored.status, "running")
        self.assertEqual(stored.current_stage, "Проверка изменений")
        self.assertGreater(stored.last_log_at, 0.0)
        self.assertIn("Логовая строка", stored.logs)


if __name__ == "__main__":
    unittest.main()
