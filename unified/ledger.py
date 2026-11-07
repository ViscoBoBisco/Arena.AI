"""Durable task ledger (manual 3.2, 5.4).

SQLite-backed, transactional, independent of any upstream store.
Tracks tasks, plan steps, attempts and committed receipts. The key invariant
from the manual: once a side-effecting step has a committed success receipt,
retries return the existing receipt rather than repeating the action.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Optional

from .contracts import (
    TaskEnvelope,
    TaskState,
    Step,
    StepState,
    ToolReceipt,
    can_transition,
    utcnow,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    project_id TEXT NOT NULL,
    envelope TEXT NOT NULL,
    state TEXT NOT NULL,
    result TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS steps (
    task_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    args TEXT NOT NULL,
    depends_on TEXT NOT NULL,
    read_only INTEGER NOT NULL,
    permissions_used TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY (task_id, step_id)
);
CREATE TABLE IF NOT EXISTS receipts (
    task_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    receipt TEXT NOT NULL,
    committed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_id, step_id, attempt_id)
);
CREATE TABLE IF NOT EXISTS approvals (
    task_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    status TEXT NOT NULL,  -- pending | approved | rejected
    decided_at TEXT,
    PRIMARY KEY (task_id, step_id)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


class LedgerError(Exception):
    pass


class Ledger:
    def __init__(self, path: str = ":memory:"):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        with self._db:
            self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- tasks -------------------------------------------------------
    def find_by_idempotency(self, project_id: str, key: str) -> Optional[str]:
        with self._lock:
            row = self._db.execute(
                "SELECT task_id FROM tasks WHERE project_id=? AND idempotency_key=?",
                (project_id, key),
            ).fetchone()
            return row["task_id"] if row else None

    def create_task(self, env: TaskEnvelope) -> str:
        """Insert task; raises LedgerError on duplicate idempotency key."""
        with self._lock, self._db:
            existing = self.find_by_idempotency(env.project_id, env.idempotency_key)
            if existing:
                raise LedgerError(f"duplicate idempotency key -> {existing}")
            now = utcnow()
            self._db.execute(
                "INSERT INTO tasks(task_id,idempotency_key,project_id,envelope,state,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (env.task_id, env.idempotency_key, env.project_id,
                 json.dumps(env.__dict__), TaskState.CREATED.value, now, now),
            )
            self._log(env.task_id, "task.created", {"state": TaskState.CREATED.value})
            return env.task_id

    def get_task(self, task_id: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["envelope"] = json.loads(d["envelope"])
            if d["result"]:
                d["result"] = json.loads(d["result"])
            return d

    def set_state(self, task_id: str, dst: TaskState) -> None:
        with self._lock, self._db:
            row = self._db.execute("SELECT state FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not row:
                raise LedgerError(f"unknown task {task_id}")
            src = TaskState(row["state"])
            if not can_transition(src, dst):
                raise LedgerError(f"illegal transition {src.value} -> {dst.value}")
            self._db.execute(
                "UPDATE tasks SET state=?, updated_at=? WHERE task_id=?",
                (dst.value, utcnow(), task_id),
            )
            self._log(task_id, "task.state", {"from": src.value, "to": dst.value})

    def set_result(self, task_id: str, result: dict) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE tasks SET result=?, updated_at=? WHERE task_id=?",
                (json.dumps(result), utcnow(), task_id),
            )

    def list_tasks(self) -> list:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT task_id,state,project_id FROM tasks")]

    # ---- steps -------------------------------------------------------
    def save_steps(self, task_id: str, steps: list[Step]) -> None:
        with self._lock, self._db:
            for s in steps:
                self._db.execute(
                    "INSERT OR REPLACE INTO steps(task_id,step_id,tool,args,depends_on,read_only,permissions_used,state)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (task_id, s.step_id, s.tool, json.dumps(s.args),
                     json.dumps(s.depends_on), int(s.read_only),
                     json.dumps(s.permissions_used), s.state.value),
                )

    def get_steps(self, task_id: str) -> list[Step]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM steps WHERE task_id=?", (task_id,)).fetchall()
            return [Step(
                step_id=r["step_id"], tool=r["tool"], args=json.loads(r["args"]),
                depends_on=json.loads(r["depends_on"]), read_only=bool(r["read_only"]),
                permissions_used=json.loads(r["permissions_used"]),
                state=StepState(r["state"])) for r in rows]

    def set_step_state(self, task_id: str, step_id: str, state: StepState) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE steps SET state=? WHERE task_id=? AND step_id=?",
                (state.value, task_id, step_id),
            )

    # ---- receipts ----------------------------------------------------
    def committed_receipt(self, task_id: str, step_id: str) -> Optional[ToolReceipt]:
        """Manual 3.2 invariant: a committed success receipt is returned as-is."""
        with self._lock:
            row = self._db.execute(
                "SELECT receipt FROM receipts WHERE task_id=? AND step_id=? AND committed=1"
                " ORDER BY created_at LIMIT 1",
                (task_id, step_id),
            ).fetchone()
            if row:
                d = json.loads(row["receipt"])
                return ToolReceipt(**d)
            return None

    def save_receipt(self, receipt: ToolReceipt, committed: bool) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO receipts(task_id,step_id,attempt_id,receipt,committed,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (receipt.task_id, receipt.step_id, receipt.attempt_id,
                 json.dumps(receipt.to_dict()), int(committed), utcnow()),
            )
            self._log(receipt.task_id, "receipt", {
                "step_id": receipt.step_id, "attempt_id": receipt.attempt_id,
                "status": receipt.status, "committed": committed})

    def get_receipts(self, task_id: str) -> list[ToolReceipt]:
        with self._lock:
            rows = self._db.execute(
                "SELECT receipt FROM receipts WHERE task_id=? ORDER BY created_at", (task_id,))
            return [ToolReceipt(**json.loads(r["receipt"])) for r in rows]

    # ---- approvals (durable human-in-the-loop records) -----------------
    def save_approval(self, task_id: str, step_id: str, status: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO approvals(task_id,step_id,status,decided_at) VALUES(?,?,?,?)",
                (task_id, step_id, status, utcnow() if status != "pending" else None))
            self._log(task_id, "approval", {"step_id": step_id, "status": status})

    def get_approval(self, task_id: str, step_id: str) -> Optional[str]:
        with self._lock:
            row = self._db.execute(
                "SELECT status FROM approvals WHERE task_id=? AND step_id=?",
                (task_id, step_id)).fetchone()
            return row["status"] if row else None

    def pending_approvals(self, task_id: str) -> list:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT step_id,status FROM approvals WHERE task_id=? AND status='pending'",
                (task_id,))]

    # ---- events ------------------------------------------------------
    def _log(self, task_id: str, kind: str, detail: dict) -> None:
        self._db.execute(
            "INSERT INTO events(task_id,at,kind,detail) VALUES(?,?,?,?)",
            (task_id, utcnow(), kind, json.dumps(detail)))

    def events(self, task_id: str) -> list:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT at,kind,detail FROM events WHERE task_id=? ORDER BY id", (task_id,))]
