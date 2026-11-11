"""The app's own record of runs (SQLite): the queue, each run's status, which steps are finished,
where results were saved. It survives closing the app and a power cut (SYS-5, spec 6.3, spec 8).

Statuses: queued -> running -> finished, or from running: failed, cancelled, interrupted (the app
stopped while the run was going). A failed, cancelled or interrupted run can be queued again.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Optional

from . import steps

QUEUED, RUNNING, FINISHED, FAILED, CANCELLED, INTERRUPTED = (
    "queued", "running", "finished", "failed", "cancelled", "interrupted")
STATUSES = (QUEUED, RUNNING, FINISHED, FAILED, CANCELLED, INTERRUPTED)
_ALLOWED = {
    QUEUED: {RUNNING, CANCELLED},                 # cancelled = taken out of the queue (RUN-9)
    RUNNING: {FINISHED, FAILED, CANCELLED, INTERRUPTED},
    FAILED: {QUEUED},
    CANCELLED: {QUEUED},
    INTERRUPTED: {QUEUED},
    FINISHED: set(),
}
_FIELDS = ("site", "capture_date", "run_by", "input_path", "input_size", "input_mtime", "input_points",
           "local_input", "sha256", "workdir", "max_points", "started_at", "finished_at", "error_kind",
           "error_message", "error_detail", "trees", "project_path")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
    site TEXT NOT NULL, capture_date TEXT, run_by TEXT NOT NULL, input_path TEXT NOT NULL, input_size INTEGER,
    input_mtime REAL, input_points INTEGER, local_input TEXT, sha256 TEXT, workdir TEXT NOT NULL,
    max_points INTEGER NOT NULL, started_at TEXT, finished_at TEXT, error_kind TEXT, error_message TEXT,
    error_detail TEXT, trees INTEGER, project_path TEXT);
CREATE TABLE IF NOT EXISTS steps (
    run_id TEXT NOT NULL, step TEXT NOT NULL, seconds REAL, info TEXT, finished_at TEXT,
    PRIMARY KEY (run_id, step));
CREATE TABLE IF NOT EXISTS saves (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, saved_at TEXT NOT NULL, folder TEXT NOT NULL,
    files TEXT);
"""


class InvalidTransition(Exception):
    pass


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------------ runs
    def add_run(self, *, site: str, run_by: str, input_path, workdir, capture_date: Optional[str] = None,
                max_points: int = steps.DEFAULT_MAX_POINTS, input_size: Optional[int] = None,
                input_mtime: Optional[float] = None) -> str:
        """A new run, waiting in the queue. Returns its id."""
        if not site.strip() or not run_by.strip():
            raise ValueError("site and run_by are required")           # NEW-4, NEW-5
        run_id = uuid.uuid4().hex
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO runs (id, status, created_at, site, capture_date, run_by, input_path, input_size, "
                "input_mtime, workdir, max_points) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, QUEUED, now(), site.strip(), capture_date, run_by.strip(), str(input_path), input_size,
                 input_mtime, str(workdir), int(max_points)))
        return run_id

    def get(self, run_id: str) -> dict:
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return dict(row)

    def list_runs(self) -> list:
        """Newest first (HOME-1)."""
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM runs ORDER BY seq DESC")]

    def next_queued(self) -> Optional[dict]:
        """The run that has waited longest, or None."""
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE status = ? ORDER BY seq LIMIT 1", (QUEUED,)).fetchone()
        return dict(row) if row else None

    def update(self, run_id: str, **fields) -> None:
        bad = [k for k in fields if k not in _FIELDS]
        if bad:
            raise ValueError(f"unknown field(s): {bad}")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._lock, self._db:
            self._db.execute(f"UPDATE runs SET {sets} WHERE id = ?", (*fields.values(), run_id))

    def set_status(self, run_id: str, status: str, **fields) -> None:
        """Move a run to another status; only the moves of spec 8 are allowed."""
        with self._lock:
            current = self.get(run_id)["status"]
            if status not in _ALLOWED.get(current, ()):
                raise InvalidTransition(f"a {current} run cannot become {status}")
            with self._db:
                self._db.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
            if status == QUEUED:                       # a new attempt: forget the last error
                fields = {"error_kind": None, "error_message": None, "error_detail": None, "finished_at": None,
                          **fields}
            self.update(run_id, **fields)

    def delete_run(self, run_id: str) -> None:
        with self._lock:
            if self.get(run_id)["status"] == RUNNING:
                raise InvalidTransition("cancel the run before deleting it")
            with self._db:
                for table, col in (("runs", "id"), ("steps", "run_id"), ("saves", "run_id")):
                    self._db.execute(f"DELETE FROM {table} WHERE {col} = ?", (run_id,))

    def mark_interrupted(self) -> list:
        """Call once when the app starts: a run still marked running was cut off (SYS-5)."""
        with self._lock:
            ids = [r["id"] for r in self._db.execute("SELECT id FROM runs WHERE status = ?", (RUNNING,))]
            for run_id in ids:
                self.set_status(run_id, INTERRUPTED, finished_at=now())
        return ids

    def known_names(self) -> list:
        """Names used in 'Run by' before, most recent first (NEW-5)."""
        with self._lock:
            rows = self._db.execute("SELECT run_by, MAX(seq) AS last FROM runs GROUP BY run_by ORDER BY last DESC")
            return [r["run_by"] for r in rows]

    def finished_run_for(self, input_path, input_size, input_mtime) -> Optional[dict]:
        """NEW-3: a finished run of the very same file, if there is one."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM runs WHERE status = ? AND input_path = ? AND input_size IS ? AND input_mtime IS ? "
                "ORDER BY seq DESC LIMIT 1", (FINISHED, str(input_path), input_size, input_mtime)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ steps
    def step_done(self, run_id: str, step: str, seconds: float, info: Optional[dict] = None) -> None:
        if step not in steps.STEP_ORDER:
            raise ValueError(step)
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO steps (run_id, step, seconds, info, finished_at) VALUES (?,?,?,?,?)",
                             (run_id, step, float(seconds), json.dumps(info or {}), now()))

    def steps_done(self, run_id: str) -> dict:
        """step -> {'seconds', 'info'} for every finished step."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM steps WHERE run_id = ?", (run_id,)).fetchall()
        return {r["step"]: {"seconds": r["seconds"], "info": json.loads(r["info"] or "{}")} for r in rows}

    def forget_steps(self, run_id: str, from_step: str) -> None:
        """Mark a step and every later one as not done (their outputs will be made again)."""
        later = steps.STEP_ORDER[steps.STEP_ORDER.index(from_step):]
        with self._lock, self._db:
            self._db.executemany("DELETE FROM steps WHERE run_id = ? AND step = ?", [(run_id, s) for s in later])

    # ------------------------------------------------------------------ saves
    def add_save(self, run_id: str, folder, files: dict) -> None:
        with self._lock, self._db:                                         # SAVE-8
            self._db.execute("INSERT INTO saves (run_id, saved_at, folder, files) VALUES (?,?,?,?)",
                             (run_id, now(), str(folder), json.dumps(files)))

    def saves(self, run_id: str) -> list:
        with self._lock:
            rows = self._db.execute("SELECT * FROM saves WHERE run_id = ? ORDER BY seq", (run_id,)).fetchall()
        return [{"saved_at": r["saved_at"], "folder": r["folder"], "files": json.loads(r["files"] or "{}")}
                for r in rows]
