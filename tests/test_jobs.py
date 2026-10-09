import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from battle_test.pipeline import CaseRun
from battle_test.web.jobs import DONE, FAILED, QUEUED, RUNNING, JobStore, QueueFull, QuotaExceeded, Worker


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


class WorkerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.tmp.name))
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.running = self.most_at_once = 0
        self.started = []

    def tearDown(self):
        self.release.set()
        self.store.close()
        self.tmp.cleanup()

    def runner(self, job, text, on_stage, on_token):
        """Holds each run open until released, and records how many overlap."""
        with self.lock:
            self.running += 1
            self.most_at_once = max(self.most_at_once, self.running)
            self.started.append(job.title)
        on_token(job.title)
        self.release.wait(10)
        with self.lock:
            self.running -= 1
        return CaseRun(job.state_code, job.rounds, "p", "d"), f"report for {job.title}"

    def submit(self, worker, count):
        jobs = [self.store.create(n, "UT", "facts", "x", 1, f"case {n}") for n in range(1, count + 1)]
        for job in jobs:
            worker.submit(job.id)
        return jobs

    def wait_for(self, condition):
        deadline = time.monotonic() + 10
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(condition())

    def test_runs_as_many_at_once_as_there_are_workers_and_queues_the_rest(self):
        worker = Worker(self.store, self.runner, workers=3)
        worker.start()
        jobs = self.submit(worker, 5)
        self.wait_for(lambda: self.running == 3)
        time.sleep(0.1)  # long enough for a fourth to start, if anything let it
        self.assertEqual(self.most_at_once, 3)
        self.assertEqual(sorted(self.started), ["case 1", "case 2", "case 3"])  # oldest first
        self.assertEqual([self.store.get(j.id).status for j in jobs], [RUNNING] * 3 + [QUEUED] * 2)
        self.assertEqual([self.store.queue_position(self.store.get(j.id)) for j in jobs[3:]], [1, 2])

        self.release.set()
        self.assertTrue(worker.wait_idle())
        self.assertEqual({self.store.get(j.id).status for j in jobs}, {DONE})
        self.assertEqual(self.most_at_once, 3)
        for job in jobs:  # each case kept its own result and its own progress log
            self.assertEqual(self.store.result_markdown_path(job.id).read_text(encoding="utf-8"),
                             f"report for {job.title}")
            self.assertEqual([e for e in worker.progress(job.id).since(0) if e["type"] == "text"],
                             [{"type": "text", "text": job.title}])

    def test_one_worker_still_runs_one_at_a_time(self):
        worker = Worker(self.store, self.runner)
        worker.start()
        jobs = self.submit(worker, 2)
        self.wait_for(lambda: self.running == 1)
        time.sleep(0.1)
        self.assertEqual([self.store.get(j.id).status for j in jobs], [RUNNING, QUEUED])
        self.release.set()
        self.assertTrue(worker.wait_idle())
        self.assertEqual(self.most_at_once, 1)

    def test_one_failed_run_leaves_the_others_and_the_worker_alone(self):
        def runner(job, text, on_stage, on_token):
            if job.title == "case 2":
                raise RuntimeError("boom")
            return self.runner(job, text, on_stage, on_token)

        worker = Worker(self.store, runner, workers=2)
        worker.start()
        self.release.set()
        jobs = self.submit(worker, 4)
        self.assertTrue(worker.wait_idle())
        self.assertEqual([self.store.get(j.id).status for j in jobs], [DONE, FAILED, DONE, DONE])

    def test_restart_fails_every_interrupted_run_and_resumes_the_queue(self):
        first, second, waiting = (self.store.create(n, "UT", "facts", "x", 1, f"case {n}") for n in (1, 2, 3))
        for job in (first, second):  # two were running when the server stopped
            self.store.update(job.id, status=RUNNING)
        self.release.set()
        worker = Worker(self.store, self.runner, workers=2)
        worker.start()
        self.assertTrue(worker.wait_idle())
        self.assertEqual([self.store.get(j.id).status for j in (first, second, waiting)], [FAILED, FAILED, DONE])
        self.assertIn("Interrupted", self.store.get(second.id).error)


if __name__ == "__main__":
    unittest.main()
