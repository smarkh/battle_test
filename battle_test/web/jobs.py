"""Case jobs for the web UI: storage, live progress, and the worker that runs them.

With Ollama there's one GPU, so runs execute one at a time on a single
worker thread and later submissions wait in the queue. With Bedrock,
[web] workers can allow several at once. Each case's input and results live
in their own folder under the web data dir (inside gitignored cases/).
"""

# Postponed annotations: JobStore has a method named `list`, which would
# otherwise shadow the built-in in its own `-> list[Job]` annotations.
from __future__ import annotations

import json
import queue
import shutil
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

from battle_test.pipeline import CaseRun

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    state_code TEXT NOT NULL,
    input_mode TEXT NOT NULL,     -- "facts" or "complaint"
    rounds INTEGER NOT NULL,
    title TEXT NOT NULL,          -- short label for the case list
    status TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    finished_at TEXT,
    user_id INTEGER               -- owner; only they can see the case
);
-- One row per case started, for plan allowances and billing. It outlives the
-- case (deleted by its owner, or by retention) and holds nothing from it.
CREATE TABLE IF NOT EXISTS usage (
    case_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    state_code TEXT NOT NULL,
    input_mode TEXT NOT NULL,
    rounds INTEGER NOT NULL,
    counted INTEGER NOT NULL DEFAULT 1   -- 0 once the run fails, or is deleted before it starts
);
CREATE INDEX IF NOT EXISTS usage_by_user ON usage (user_id, created_at);
"""


class QueueFull(Exception):
    """The user already has as many cases waiting or running as they may."""


class QuotaExceeded(Exception):
    """The user has already started as many cases as their plan allows."""


@dataclass(frozen=True)
class Job:
    id: str
    created_at: str
    state_code: str
    input_mode: str
    rounds: int
    title: str
    status: str
    stage: str
    error: str
    finished_at: str | None
    user_id: int | None


class JobStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(root / "cases.sqlite", check_same_thread=False)
        self._db.executescript(_SCHEMA)
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(cases)")}
        if "user_id" not in columns:  # created before accounts existed
            self._db.execute("ALTER TABLE cases ADD COLUMN user_id INTEGER")

    def _dir(self, job_id: str) -> Path:
        return self.root / job_id

    def create(self, user_id: int, state_code: str, input_mode: str, text: str, rounds: int,
               title: str, *, limit: int | None = None, since: str = "", pool: Iterable[int] = (),
               max_active: int = 0) -> Job:
        """With a limit, raises QuotaExceeded if the user, together with the
        users in `pool` (their firm), already has that many counted cases
        since `since` (see used). With max_active, raises QueueFull if the
        user already has that many cases queued or running."""
        job_id = uuid.uuid4().hex
        now = datetime.now().isoformat(timespec="seconds")
        # One lock around the check and the insert, so two submissions at
        # once can't both take the last case of an allowance.
        with self._lock:
            if max_active > 0 and self._active(user_id) >= max_active:
                raise QueueFull()
            if limit is not None and self._used({user_id, *pool}, since) >= limit:
                raise QuotaExceeded()
            self._dir(job_id).mkdir()
            (self._dir(job_id) / "input.md").write_text(text, encoding="utf-8")
            with self._db:
                self._db.execute(
                    "INSERT INTO cases (id, created_at, state_code, input_mode, rounds, title, status, user_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, now, state_code, input_mode, rounds, title, QUEUED, user_id),
                )
                self._db.execute(
                    "INSERT INTO usage (case_id, user_id, created_at, state_code, input_mode, rounds) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (job_id, user_id, now, state_code, input_mode, rounds),
                )
        return self.get(job_id)

    def _active(self, user_id: int) -> int:
        return self._db.execute("SELECT COUNT(*) FROM cases WHERE user_id = ? AND status IN (?, ?)",
                                (user_id, QUEUED, RUNNING)).fetchone()[0]

    def used(self, user_ids: Iterable[int], since: str = "") -> int:
        """Cases these users (one, or a firm's) have started at or after
        `since` (an ISO time; "" for ever) that count against their
        allowance: queued, running and finished ones, including any deleted
        since."""
        with self._lock:
            return self._used(set(user_ids), since)

    def _used(self, user_ids: set[int], since: str) -> int:
        return self._db.execute(
            f"SELECT COUNT(*) FROM usage WHERE user_id IN ({', '.join('?' * len(user_ids))}) "
            "AND counted = 1 AND created_at >= ?", (*user_ids, since)
        ).fetchone()[0]

    def usage_rows(self, user_id: int, limit: int = 100) -> list[tuple[str, str, str, int, bool]]:
        """A user's cases as the usage record has them, newest first:
        (started, state, input mode, rounds, counted). No titles or text."""
        with self._lock:
            rows = self._db.execute(
                "SELECT created_at, state_code, input_mode, rounds, counted FROM usage "
                "WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", (user_id, limit)
            ).fetchall()
        return [(*r[:4], bool(r[4])) for r in rows]

    def usage_by_user(self, since: str = "", until: str = "9999") -> dict[int, tuple[int, int]]:
        """Per user id: (cases counted, cases not counted) started from
        `since` up to but not including `until`."""
        with self._lock:
            rows = self._db.execute(
                "SELECT user_id, SUM(counted), SUM(1 - counted) FROM usage "
                "WHERE created_at >= ? AND created_at < ? GROUP BY user_id", (since, until)
            ).fetchall()
        return {user_id: (counted, uncounted) for user_id, counted, uncounted in rows}

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._db.execute(f"SELECT {_FIELDS} FROM cases WHERE id = ?", (job_id,)).fetchone()
        return Job(*row) if row else None

    def list(self, user_id: int | None = None) -> list[Job]:
        """One user's cases, or every case (for the worker) when user_id is None."""
        where, args = ("WHERE user_id = ?", (user_id,)) if user_id is not None else ("", ())
        with self._lock:
            rows = self._db.execute(
                # rowid breaks ties: created_at only goes down to the second.
                f"SELECT {_FIELDS} FROM cases {where} ORDER BY created_at DESC, rowid DESC", args
            ).fetchall()
        return [Job(*r) for r in rows]

    def update(self, job_id: str, *, status: str | None = None, stage: str | None = None,
               error: str | None = None) -> None:
        sets, values = [], []
        for column, value in (("status", status), ("stage", stage), ("error", error)):
            if value is not None:
                sets.append(f"{column} = ?")
                values.append(value)
        if status in (DONE, FAILED):
            sets.append("finished_at = ?")
            values.append(datetime.now().isoformat(timespec="seconds"))
        with self._lock, self._db:
            self._db.execute(f"UPDATE cases SET {', '.join(sets)} WHERE id = ?", (*values, job_id))
            if status == FAILED:  # a run that failed doesn't use up the allowance
                self._db.execute("UPDATE usage SET counted = 0 WHERE case_id = ?", (job_id,))

    def queue_position(self, job: Job) -> int:
        """1 = next to run. Counts queued jobs submitted before this one."""
        with self._lock:
            (n,) = self._db.execute(
                # rowid breaks ties: created_at only goes down to the second.
                "SELECT COUNT(*) FROM cases WHERE status = ? AND (created_at < ? OR (created_at = ? "
                "AND rowid < (SELECT rowid FROM cases WHERE id = ?)))",
                (QUEUED, job.created_at, job.created_at, job.id),
            ).fetchone()
        return n + 1

    def delete(self, job_id: str) -> None:
        """Remove a case's row and every file it has. Irreversible."""
        with self._lock, self._db:
            # Deleted while still waiting: it never ran, so it isn't counted.
            self._db.execute(
                "UPDATE usage SET counted = 0 WHERE case_id = ? "
                "AND (SELECT status FROM cases WHERE id = ?) = ?", (job_id, job_id, QUEUED))
            self._db.execute("DELETE FROM cases WHERE id = ?", (job_id,))
        shutil.rmtree(self._dir(job_id), ignore_errors=True)

    def expired(self, retention_days: int, now: datetime | None = None) -> list[Job]:
        """Finished or failed cases created more than retention_days ago.
        Queued and running cases never expire. 0 days means keep forever."""
        if retention_days <= 0:
            return []
        cutoff = ((now or datetime.now()) - timedelta(days=retention_days)).isoformat(timespec="seconds")
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_FIELDS} FROM cases WHERE status IN (?, ?) AND created_at < ?",
                (DONE, FAILED, cutoff),
            ).fetchall()
        return [Job(*r) for r in rows]

    def purge_expired(self, retention_days: int, now: datetime | None = None) -> int:
        jobs = self.expired(retention_days, now)
        for job in jobs:
            self.delete(job.id)
        return len(jobs)

    def input_text(self, job_id: str) -> str:
        return (self._dir(job_id) / "input.md").read_text(encoding="utf-8")

    def save_result(self, job_id: str, run: CaseRun, markdown: str) -> None:
        data = asdict(run)
        data["state"] = run.state
        (self._dir(job_id) / "result.json").write_text(json.dumps(data), encoding="utf-8")
        (self._dir(job_id) / "result.md").write_text(markdown, encoding="utf-8")

    def result(self, job_id: str) -> dict | None:
        path = self._dir(job_id) / "result.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def result_markdown_path(self, job_id: str) -> Path:
        return self._dir(job_id) / "result.md"

    def close(self) -> None:
        self._db.close()


_FIELDS = ", ".join(Job.__dataclass_fields__)


class Progress:
    """In-memory event log per job, for the live progress page.

    Draft tokens arrive one at a time, so they're buffered and sent in
    chunks. A page that opens late replays the log from the start.
    """

    FLUSH_CHARS = 120

    def __init__(self):
        self._events: list[dict] = []
        self._buffer = ""
        self._lock = threading.Lock()

    def stage(self, title: str, role: str) -> None:
        with self._lock:
            self._flush()
            self._events.append({"type": "stage", "title": title, "role": role})

    def token(self, piece: str) -> None:
        with self._lock:
            self._buffer += piece
            if len(self._buffer) >= self.FLUSH_CHARS or "\n" in piece:
                self._flush()

    def finish(self, status: str, error: str = "") -> None:
        with self._lock:
            self._flush()
            self._events.append({"type": status, "error": error})

    def since(self, index: int) -> list[dict]:
        with self._lock:
            self._flush()
            return self._events[index:]

    def _flush(self) -> None:
        if self._buffer:
            self._events.append({"type": "text", "text": self._buffer})
            self._buffer = ""


# runner(job, input_text, on_stage, on_token) -> (the run, its Markdown report)
Runner = Callable[[Job, str, Callable[[str, str], None], Callable[[str], None]], tuple[CaseRun, str]]


class Worker:
    """Runs queued jobs on background threads, `workers` at a time, oldest first."""

    def __init__(self, store: JobStore, runner: Runner, workers: int = 1):
        self.store = store
        self.runner = runner
        self.workers = workers
        self._queue: queue.Queue[str] = queue.Queue()
        self._progress: dict[str, Progress] = {}
        self._threads = [threading.Thread(target=self._loop, name=f"battle-test-worker-{n}", daemon=True)
                         for n in range(1, workers + 1)]

    def start(self) -> None:
        # A restart loses whatever was running. Say so, and resume the queue.
        for job in reversed(self.store.list()):
            if job.status == RUNNING:
                self.store.update(job.id, status=FAILED, error="Interrupted: the server restarted during this run.")
            elif job.status == QUEUED:
                self._queue.put(job.id)
        for thread in self._threads:
            thread.start()

    def submit(self, job_id: str) -> None:
        self._progress[job_id] = Progress()
        self._queue.put(job_id)

    def progress(self, job_id: str) -> Progress | None:
        return self._progress.get(job_id)

    def forget(self, job_id: str) -> None:
        """Drop a deleted case's in-memory progress log."""
        self._progress.pop(job_id, None)

    def wait_idle(self, timeout: float = 30) -> bool:
        """For tests: wait until the queue is empty and nothing is running."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.02)
        return False

    def _loop(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                self._run(job_id)
            finally:
                self._queue.task_done()

    def _run(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job is None or job.status != QUEUED:
            return
        progress = self._progress.setdefault(job_id, Progress())
        self.store.update(job_id, status=RUNNING)

        def on_stage(title: str, role: str) -> None:
            self.store.update(job_id, stage=f"{title} ({role})")
            progress.stage(title, role)

        try:
            run, markdown = self.runner(job, self.store.input_text(job_id), on_stage, progress.token)
            self.store.save_result(job_id, run, markdown)
            self.store.update(job_id, status=DONE, stage="")
            progress.finish(DONE)
        except Exception as e:  # noqa: BLE001 - any failure should mark the job, not kill the worker
            message = f"{type(e).__name__}: {e}"
            self.store.update(job_id, status=FAILED, error=message)
            progress.finish(FAILED, message)
