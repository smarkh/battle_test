"""Case jobs for the web UI: storage, live progress, and a one-at-a-time worker.

There's one GPU, so runs execute one at a time on a single worker thread and
later submissions wait in the queue. Each case's input and results live in
their own folder under the web data dir (inside gitignored cases/).
"""

import json
import queue
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

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
"""


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
               title: str) -> Job:
        job_id = uuid.uuid4().hex
        self._dir(job_id).mkdir()
        (self._dir(job_id) / "input.md").write_text(text, encoding="utf-8")
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO cases (id, created_at, state_code, input_mode, rounds, title, status, user_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, datetime.now().isoformat(timespec="seconds"), state_code, input_mode,
                 rounds, title, QUEUED, user_id),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._db.execute(f"SELECT {_FIELDS} FROM cases WHERE id = ?", (job_id,)).fetchone()
        return Job(*row) if row else None

    def list(self, user_id: int | None = None) -> list[Job]:
        """One user's cases, or every case (for the worker) when user_id is None."""
        where, args = ("WHERE user_id = ?", (user_id,)) if user_id is not None else ("", ())
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_FIELDS} FROM cases {where} ORDER BY created_at DESC", args
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

    def queue_position(self, job: Job) -> int:
        """1 = next to run. Counts queued jobs submitted no later than this one."""
        with self._lock:
            (n,) = self._db.execute(
                "SELECT COUNT(*) FROM cases WHERE status = ? AND created_at <= ? AND id != ?",
                (QUEUED, job.created_at, job.id),
            ).fetchone()
        return n + 1

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
    """Runs queued jobs one at a time on a background thread."""

    def __init__(self, store: JobStore, runner: Runner):
        self.store = store
        self.runner = runner
        self._queue: queue.Queue[str] = queue.Queue()
        self._progress: dict[str, Progress] = {}
        self._thread = threading.Thread(target=self._loop, name="battle-test-worker", daemon=True)

    def start(self) -> None:
        # A restart loses whatever was running. Say so, and resume the queue.
        for job in reversed(self.store.list()):
            if job.status == RUNNING:
                self.store.update(job.id, status=FAILED, error="Interrupted: the server restarted during this run.")
            elif job.status == QUEUED:
                self._queue.put(job.id)
        self._thread.start()

    def submit(self, job_id: str) -> None:
        self._progress[job_id] = Progress()
        self._queue.put(job_id)

    def progress(self, job_id: str) -> Progress | None:
        return self._progress.get(job_id)

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
