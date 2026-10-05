import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from battle_test.web.jobs import DONE, FAILED, QUEUED, RUNNING, JobStore, QueueFull, QuotaExceeded


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


class UsageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.tmp.name))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def start(self, user_id=1, **kwargs):
        return self.store.create(user_id, "UT", "facts", "facts text", 1, "t", **kwargs)

    def backdate(self, job, when):
        with self.store._db:
            self.store._db.execute("UPDATE usage SET created_at = ? WHERE case_id = ?", (when, job.id))

    def test_limit_refuses_the_case_past_the_allowance(self):
        for _ in range(2):
            self.start(limit=2)
        with self.assertRaises(QuotaExceeded):
            self.start(limit=2)
        self.assertEqual(len(self.store.list(1)), 2)
        self.assertEqual(len(list(Path(self.tmp.name).glob("*/input.md"))), 2)  # nothing saved for the refused one
        self.start(user_id=2, limit=2)  # each user has their own allowance
        self.start(limit=None)          # no limit
        self.assertEqual(self.store.used([1]), 3)

    def test_pooled_limit_counts_everyone_in_the_pool(self):
        self.start(user_id=1)
        self.start(user_id=2)
        self.start(user_id=3)  # not in the pool
        self.assertEqual(self.store.used([1, 2]), 2)
        self.start(user_id=1, limit=3, pool=[1, 2])
        with self.assertRaises(QuotaExceeded):
            self.start(user_id=2, limit=3, pool=[1, 2])
        self.start(user_id=3, limit=2)

    def test_max_active_caps_queued_and_running_cases_per_user(self):
        running, queued = self.start(max_active=2), self.start(max_active=2)
        self.store.update(running.id, status=RUNNING)
        with self.assertRaises(QueueFull):
            self.start(max_active=2)
        self.assertEqual(self.store.used([1]), 2)  # the refused one used nothing
        self.start(user_id=2, max_active=2)        # the cap is per user
        self.start(max_active=0)                   # 0 = no cap
        self.store.delete(queued.id)
        for job in self.store.list(1):
            self.store.update(job.id, status=DONE)
        self.start(max_active=1)                   # finished cases free their place

    def test_zero_limit_allows_nothing(self):
        with self.assertRaises(QuotaExceeded):
            self.start(limit=0)

    def test_only_cases_since_the_period_started_count(self):
        self.backdate(self.start(), "2026-09-30T23:59:59")
        self.backdate(self.start(), "2026-10-01T00:00:00")
        self.assertEqual(self.store.used([1]), 2)
        self.assertEqual(self.store.used([1], "2026-10-01T00:00:00"), 1)
        with self.assertRaises(QuotaExceeded):
            self.start(limit=1, since="2026-10-01T00:00:00")
        self.start(limit=3)

    def test_deleted_and_expired_cases_stay_counted(self):
        deleted, expired = self.start(), self.start()
        for job in (deleted, expired):
            self.store.update(job.id, status=RUNNING)
            self.store.update(job.id, status=DONE)
        self.store.delete(deleted.id)
        self.assertEqual(self.store.purge_expired(1, datetime.now() + timedelta(days=5)), 1)
        self.assertEqual(self.store.list(1), [])
        self.assertEqual(self.store.used([1]), 2)

    def test_failed_runs_and_cases_deleted_while_queued_are_not_counted(self):
        failed, queued = self.start(), self.start()
        self.store.update(failed.id, status=RUNNING)
        self.assertEqual(self.store.used([1]), 2)  # running and queued cases hold their place
        self.store.update(failed.id, status=FAILED, error="boom")
        self.store.delete(queued.id)
        self.assertEqual(self.store.used([1]), 0)
        self.store.delete(failed.id)
        self.assertEqual(self.store.usage_by_user(), {1: (0, 2)})

    def test_usage_by_user_and_month(self):
        self.backdate(self.start(), "2026-09-15T10:00:00")
        self.backdate(self.start(), "2026-10-02T10:00:00")
        self.backdate(self.start(user_id=2), "2026-10-03T10:00:00")
        self.assertEqual(self.store.usage_by_user("2026-10-01T00:00:00", "2026-11-01T00:00:00"),
                         {1: (1, 0), 2: (1, 0)})
        self.assertEqual(self.store.usage_by_user(), {1: (2, 0), 2: (1, 0)})

    def test_usage_holds_nothing_from_the_case(self):
        self.store.create(1, "UT", "facts", "privileged facts", 1, "Whitfield v. Summit")
        row = self.store._db.execute("SELECT * FROM usage").fetchone()
        self.assertFalse({"privileged facts", "Whitfield v. Summit"} & set(row))


if __name__ == "__main__":
    unittest.main()
