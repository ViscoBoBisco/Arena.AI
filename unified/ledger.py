"""Durable task ledger (manual 3.2, 5.4).

SQLite-backed, transactional, independent of any upstream store.
Tracks tasks, plan steps, attempts and committed receipts. The key invariant
from the manual: once a side-effecting step has a committed success receipt,
retries return the existing receipt rather than repeating the action.
"""
from __future__ import annotations

import hashlib
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
    detail TEXT NOT NULL,
    prev_hash TEXT,
    entry_hash TEXT
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
            for col in ("prev_hash", "entry_hash"):
                try:
                    self._db.execute(f"ALTER TABLE events ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass

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
        """Insert task; raises LedgerError on duplicate idempotency key.

        The unique index is the cross-connection/process arbiter; translate its
        race-time IntegrityError back to the same duplicate contract.
        """
        try:
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
        except sqlite3.IntegrityError as e:
            existing = self.find_by_idempotency(env.project_id, env.idempotency_key)
            if existing:
                raise LedgerError(f"duplicate idempotency key -> {existing}") from e
            raise

    def get_task(self, task_id: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["envelope"] = json.loads(d["envelope"])
            if d["result"]:
                d["result"] = json.loads(d["result"])
            d["steps"] = [s.to_dict() for s in self.get_steps(task_id)]
            d["receipts"] = [r.to_dict() for r in self.get_receipts(task_id)]
            d["pending_approvals"] = self.pending_approvals(task_id)
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

    def list_tasks(self, project_id: str | None = None, limit: int | None = None,
                   offset: int = 0) -> list:
        """List tasks in newest-first order; optional project and SQL pagination."""
        query = (
            "SELECT task_id,idempotency_key,state,project_id,envelope,result,created_at,updated_at"
            " FROM tasks"
        )
        params = []
        if project_id is not None:
            query += " WHERE project_id=?"
            params.append(project_id)
        query += " ORDER BY rowid DESC"
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([max(0, int(limit)), max(0, int(offset))])
        elif offset:
            query += " LIMIT -1 OFFSET ?"
            params.append(max(0, int(offset)))
        with self._lock:
            rows = self._db.execute(query, params).fetchall()
            out = []
            for r in rows:
                env = json.loads(r["envelope"]) if r["envelope"] else {}
                res = json.loads(r["result"]) if r["result"] else {}
                req = env.get("request", {}) if isinstance(env, dict) else {}
                summary = ""
                if isinstance(res, dict):
                    summary = res.get("answer") or res.get("reason") or res.get("error") or ""
                out.append({
                    "task_id": r["task_id"],
                    "idempotency_key": r["idempotency_key"],
                    "state": r["state"],
                    "project_id": r["project_id"],
                    "intent": req.get("intent", "echo"),
                    "summary": summary,
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                })
            return out

    def count_tasks(self, project_id: str | None = None) -> int:
        with self._lock:
            if project_id is None:
                row = self._db.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()
            else:
                row = self._db.execute(
                    "SELECT COUNT(*) AS n FROM tasks WHERE project_id=?", (project_id,)
                ).fetchone()
            return int(row["n"])

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
        at = utcnow()
        payload = json.dumps(detail, sort_keys=True)
        prev = self._db.execute(
            "SELECT entry_hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
        prev_hash = prev["entry_hash"] if (prev and prev["entry_hash"]) else "GENESIS"
        entry_hash = hashlib.sha256(
            f"{prev_hash}|{task_id}|{at}|{kind}|{payload}".encode()).hexdigest()
        self._db.execute(
            "INSERT INTO events(task_id,at,kind,detail,prev_hash,entry_hash) VALUES(?,?,?,?,?,?)",
            (task_id, at, kind, payload, prev_hash, entry_hash))

    def verify_audit_chain(self) -> dict:
        """Recompute the hash chain over all audit events; detects any
        retroactive edit or deletion (tamper-evident audit log best practice)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id,task_id,at,kind,detail,prev_hash,entry_hash FROM events ORDER BY id"
            ).fetchall()
        expected_prev = "GENESIS"
        checked = 0
        for r in rows:
            if not r["entry_hash"]:
                continue  # pre-chain legacy rows
            calc = hashlib.sha256(
                f"{r['prev_hash']}|{r['task_id']}|{r['at']}|{r['kind']}|{r['detail']}".encode()
            ).hexdigest()
            if r["prev_hash"] != expected_prev or r["entry_hash"] != calc:
                return {"intact": False, "broken_at_id": r["id"], "checked": checked}
            expected_prev = r["entry_hash"]
            checked += 1
        return {"intact": True, "checked": checked, "head": expected_prev}

    def events(self, task_id: str) -> list:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT at,kind,detail FROM events WHERE task_id=? ORDER BY id", (task_id,))]
