import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from battle_test.web.jobs import DONE, FAILED, QUEUED, RUNNING, JobStore


class JobStoreRetentionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.tmp.name))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def make(self, status, days_old):
        job = self.store.create(1, "UT", "facts", "facts text", 1, f"{status} {days_old}d")
        created = (datetime.now() - timedelta(days=days_old)).isoformat(timespec="seconds")
        with self.store._db:
            self.store._db.execute("UPDATE cases SET status = ?, created_at = ? WHERE id = ?",
                                   (status, created, job.id))
        return job

    def test_delete_removes_row_and_files(self):
        job = self.make(DONE, 1)
        folder = Path(self.tmp.name) / job.id
        self.assertTrue((folder / "input.md").exists())
        self.store.delete(job.id)
        self.assertIsNone(self.store.get(job.id))
        self.assertFalse(folder.exists())

    def test_only_old_finished_cases_expire(self):
        old_done, old_failed = self.make(DONE, 91), self.make(FAILED, 120)
        self.make(DONE, 89)
        self.make(QUEUED, 200)   # never expires while waiting
        self.make(RUNNING, 200)  # never expires while running
        expired = {j.id for j in self.store.expired(90)}
        self.assertEqual(expired, {old_done.id, old_failed.id})

        self.assertEqual(self.store.purge_expired(90), 2)
        self.assertEqual(len(self.store.list()), 3)
        self.assertFalse((Path(self.tmp.name) / old_done.id).exists())

    def test_zero_days_keeps_everything(self):
        self.make(DONE, 10_000)
        self.assertEqual(self.store.purge_expired(0), 0)
        self.assertEqual(len(self.store.list()), 1)

    def test_list_is_per_user(self):
        a = self.store.create(1, "UT", "facts", "x", 1, "a")
        self.store.create(2, "UT", "facts", "x", 1, "b")
        self.assertEqual([j.id for j in self.store.list(1)], [a.id])
        self.assertEqual(len(self.store.list()), 2)


if __name__ == "__main__":
    unittest.main()
