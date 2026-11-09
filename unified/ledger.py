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
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional

from .contracts import (
    TaskEnvelope,
    TaskState,
    Step,
    StepState,
    ToolReceipt,
    can_transition,
    new_id,
    utcnow,
)

def _empty_usage_metrics() -> dict:
    return {
        "tasks_by_state": {}, "cpu_seconds": 0.0,
        "max_worker_peak_rss_bytes": 0,
        "max_worker_address_space_limit_bytes": 0,
        "task_memory_limit_mb": 0,
        "execution_time_seconds": 0.0, "uncertain_execution_seconds": 0.0,
        "attempts": 0, "retries": 0,
        "tool_calls": 0, "model_calls": 0, "provider_calls": 0,
        "provider_reported_tokens": 0, "estimated_model_tokens": 0,
        "unclassified_model_tokens": 0, "model_tokens": 0,
        "input_tokens": 0, "output_tokens": 0,
        "cost_usd": 0.0, "provider_reported_cost_usd": 0.0,
        "rate_card_estimated_cost_usd": 0.0,
        "priced_provider_calls": 0, "unpriced_provider_calls": 0,
        "unknown_attempts": 0,
        "unknown_resource_effectful_calls": 0, "uncertain_provider_calls": 0,
        "uncertain_token_consumption_calls": 0,
        "uncertain_cost_consumption_calls": 0,
        "uncertain_external_effects": 0, "receipt_count": 0,
        "elapsed_ms": 0, "latencies_ms": [], "tool_failures": 0,
        "cancelled_calls": 0, "budget_exceeded": 0,
        "calls_by_tool": {}, "errors_by_tool": {},
    }


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
    current_plan_version INTEGER NOT NULL DEFAULT 0,
    execution_started_at REAL,
    cpu_seconds_used REAL NOT NULL DEFAULT 0,
    max_worker_peak_rss_bytes INTEGER NOT NULL DEFAULT 0,
    UNIQUE (project_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS plans (
    task_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    planner_version TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_id, plan_version),
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
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
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    attempt_number INTEGER NOT NULL,
    operation_key TEXT,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    dispatched_at TEXT,
    finished_at TEXT,
    receipt_json TEXT,
    UNIQUE (task_id, step_id, attempt_number),
    FOREIGN KEY (task_id, step_id) REFERENCES steps(task_id, step_id)
);
CREATE TABLE IF NOT EXISTS run_claims (
    task_id TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    generation INTEGER NOT NULL,
    lease_until REAL NOT NULL,
    heartbeat_at REAL NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
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
CREATE TABLE IF NOT EXISTS recovery_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recovery_id TEXT NOT NULL,
    task_id TEXT,
    outcome TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS recovery_reports_task_idx
    ON recovery_reports(task_id, id DESC);
"""


class LedgerError(Exception):
    pass


class RunClaimLost(LedgerError):
    """Raised when a worker no longer owns the durable task execution claim."""


class Ledger:
    def __init__(self, path: str = ":memory:"):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        with self._db:
            self._db.executescript(_SCHEMA)
            task_columns = {row["name"] for row in self._db.execute("PRAGMA table_info(tasks)")}
            if "current_plan_version" not in task_columns:
                self._db.execute(
                    "ALTER TABLE tasks ADD COLUMN current_plan_version INTEGER NOT NULL DEFAULT 0")
            if "execution_started_at" not in task_columns:
                self._db.execute("ALTER TABLE tasks ADD COLUMN execution_started_at REAL")
            if "cpu_seconds_used" not in task_columns:
                self._db.execute(
                    "ALTER TABLE tasks ADD COLUMN cpu_seconds_used REAL NOT NULL DEFAULT 0")
            if "max_worker_peak_rss_bytes" not in task_columns:
                self._db.execute(
                    "ALTER TABLE tasks ADD COLUMN max_worker_peak_rss_bytes INTEGER NOT NULL DEFAULT 0")
            attempt_columns = {row["name"] for row in self._db.execute("PRAGMA table_info(attempts)")}
            if "operation_key" not in attempt_columns:
                self._db.execute("ALTER TABLE attempts ADD COLUMN operation_key TEXT")
            if "dispatched_at" not in attempt_columns:
                self._db.execute("ALTER TABLE attempts ADD COLUMN dispatched_at TEXT")
            for col in ("prev_hash", "entry_hash"):
                try:
                    self._db.execute(f"ALTER TABLE events ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass

    @contextmanager
    def _write_transaction(self):
        """Serialize read-check-write sequences across SQLite connections."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._db.rollback()
                raise
            else:
                self._db.commit()

    def _assert_run_claim_locked(self, task_id: str, token: str) -> None:
        row = self._db.execute(
            "SELECT token,lease_until FROM run_claims WHERE task_id=?", (task_id,)
        ).fetchone()
        if not row or row["token"] != token or row["lease_until"] <= time.time():
            raise RunClaimLost(f"worker no longer owns task {task_id}")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def ping(self) -> None:
        """Cheap, read-only database probe for readiness checks."""
        with self._lock:
            self._db.execute("SELECT 1").fetchone()

    # ---- execution ownership -----------------------------------------
    def claim_run(self, task_id: str, lease_seconds: float = 60.0) -> str | None:
        """Claim a task for one worker; an unexpired claim excludes peers."""
        token = new_id("W")
        now = time.time()
        with self._write_transaction():
            if not self._db.execute(
                    "SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone():
                raise LedgerError(f"unknown task {task_id}")
            current = self._db.execute(
                "SELECT generation,lease_until FROM run_claims WHERE task_id=?",
                (task_id,)).fetchone()
            if current and current["lease_until"] > now:
                return None
            generation = (current["generation"] + 1) if current else 1
            self._db.execute(
                "INSERT INTO run_claims(task_id,token,generation,lease_until,heartbeat_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
                "token=excluded.token,generation=excluded.generation,"
                "lease_until=excluded.lease_until,heartbeat_at=excluded.heartbeat_at",
                (task_id, token, generation, now + max(5.0, lease_seconds), now))
        return token

    def renew_run_claim(self, task_id: str, token: str,
                        lease_seconds: float = 60.0) -> bool:
        now = time.time()
        with self._write_transaction():
            cursor = self._db.execute(
                "UPDATE run_claims SET lease_until=?,heartbeat_at=? "
                "WHERE task_id=? AND token=? AND lease_until>?",
                (now + max(5.0, lease_seconds), now, task_id, token, now))
            return cursor.rowcount == 1

    def release_run_claim(self, task_id: str, token: str) -> None:
        with self._write_transaction():
            self._db.execute(
                "DELETE FROM run_claims WHERE task_id=? AND token=?", (task_id, token))

    def begin_execution(self, task_id: str, run_token: str) -> float:
        """Return a durable task execution start time, initialized once."""
        with self._write_transaction():
            self._assert_run_claim_locked(task_id, run_token)
            now = time.time()
            self._db.execute(
                "UPDATE tasks SET execution_started_at=COALESCE(execution_started_at,?) "
                "WHERE task_id=?", (now, task_id))
            row = self._db.execute(
                "SELECT execution_started_at FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if not row or row["execution_started_at"] is None:
                raise LedgerError(f"could not initialize execution time for {task_id}")
            return float(row["execution_started_at"])

    def task_cpu_seconds_used(self, task_id: str) -> float:
        with self._lock:
            row = self._db.execute(
                "SELECT cpu_seconds_used FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if not row:
                raise LedgerError(f"unknown task {task_id}")
            return float(row["cpu_seconds_used"])

    def add_task_cpu_seconds(self, task_id: str, run_token: str, seconds: float) -> None:
        """Persist measured worker CPU so restarts cannot reset the CPU budget."""
        self.add_task_worker_usage(task_id, run_token, seconds, 0)

    def add_task_worker_usage(self, task_id: str, run_token: str,
                              cpu_seconds: float, peak_rss_bytes: int) -> None:
        """Persist cumulative CPU and the maximum per-worker RSS observation."""
        cpu_seconds = max(0.0, float(cpu_seconds))
        peak_rss_bytes = max(0, int(peak_rss_bytes))
        with self._write_transaction():
            self._assert_run_claim_locked(task_id, run_token)
            self._db.execute(
                "UPDATE tasks SET cpu_seconds_used=cpu_seconds_used+?, "
                "max_worker_peak_rss_bytes=MAX(max_worker_peak_rss_bytes,?),updated_at=? "
                "WHERE task_id=?", (cpu_seconds, peak_rss_bytes, utcnow(), task_id))
            self._log(task_id, "task.worker_usage", {
                "cpu_seconds": cpu_seconds,
                "worker_peak_rss_bytes": peak_rss_bytes})

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
        task_identifier = env.task_id[2:] if isinstance(env.task_id, str) else ""
        if (not isinstance(env.task_id, str) or not env.task_id.startswith("T-")
                or len(task_identifier) != 32
                or any(char not in "0123456789ABCDEF" for char in task_identifier)):
            raise LedgerError("task_id must be a server-generated UUID identity")
        if (not isinstance(env.project_id, str) or not env.project_id.strip()
                or len(env.project_id) > 128):
            raise LedgerError("project_id must be a 1–128 character string")
        if (not isinstance(env.idempotency_key, str)
                or not 1 <= len(env.idempotency_key) <= 200):
            raise LedgerError("idempotency_key must be a 1–200 character string")
        if env.envelope_version != 1:
            raise LedgerError(f"unsupported task envelope version {env.envelope_version}")
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
            d["attempts"] = self.get_attempts(task_id)
            plan = self.get_plan(task_id)
            if plan:
                plan["steps"] = [step.to_dict() for step in plan["steps"]]
            d["plan"] = plan
            d["pending_approvals"] = self.pending_approvals(task_id)
            return d

    def set_state(self, task_id: str, dst: TaskState, *,
                  expected_state: TaskState | None = None,
                  run_token: str | None = None) -> None:
        """Atomically validate and apply one legal state transition.

        ``BEGIN IMMEDIATE`` makes the read/validate/write sequence a single
        cross-connection operation, not merely protected by this instance's
        Python lock. Supplying ``expected_state`` additionally makes the
        caller's transition a compare-and-set.
        """
        with self._write_transaction():
            row = self._db.execute(
                "SELECT state FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not row:
                raise LedgerError(f"unknown task {task_id}")
            src = TaskState(row["state"])
            if expected_state is not None and src != expected_state:
                raise LedgerError(
                    f"concurrent task state transition for {task_id}: expected "
                    f"{expected_state.value}, found {src.value}")
            if not can_transition(src, dst):
                raise LedgerError(f"illegal transition {src.value} -> {dst.value}")
            if run_token is not None:
                self._assert_run_claim_locked(task_id, run_token)
            cursor = self._db.execute(
                "UPDATE tasks SET state=?, updated_at=? WHERE task_id=? AND state=?",
                (dst.value, utcnow(), task_id, src.value),
            )
            if cursor.rowcount != 1:
                raise LedgerError(f"concurrent task state transition for {task_id}")
            self._log(task_id, "task.state", {"from": src.value, "to": dst.value})

    def set_result(self, task_id: str, result: dict,
                   run_token: str | None = None) -> None:
        with self._write_transaction():
            if run_token is not None:
                self._assert_run_claim_locked(task_id, run_token)
            cursor = self._db.execute(
                "UPDATE tasks SET result=?, updated_at=? WHERE task_id=?",
                (json.dumps(result), utcnow(), task_id),
            )
            if cursor.rowcount != 1:
                raise LedgerError(f"unknown task {task_id}")

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

    def list_recoverable_tasks(self) -> list[dict]:
        """Fetch only interrupted tasks for recovery, not the full task history."""
        states = (TaskState.ADMITTED.value, TaskState.PLANNED.value,
                  TaskState.RUNNING.value, TaskState.VERIFYING.value,
                  TaskState.CANCELLING.value, TaskState.RECOVERY_REQUIRED.value)
        placeholders = ",".join("?" for _ in states)
        with self._lock:
            rows = self._db.execute(
                f"SELECT task_id,state FROM tasks WHERE state IN ({placeholders}) ORDER BY rowid",
                states).fetchall()
            return [dict(row) for row in rows]

    def usage_metrics(self, project_id: str,
                      resource_effectful_tools: set[str] | None = None,
                      billable_tools: set[str] | None = None,
                      external_effect_tools: set[str] | None = None) -> dict:
        """Rebuild project usage and reliability metrics from durable rows."""
        with self._lock:
            tasks = self._db.execute(
                "SELECT task_id,state,envelope,created_at,updated_at,execution_started_at,"
                "cpu_seconds_used,max_worker_peak_rss_bytes FROM tasks WHERE project_id=?",
                (project_id,)).fetchall()
            if not tasks:
                return _empty_usage_metrics()
            attempts = self._db.execute(
                "SELECT a.*,s.tool FROM attempts a JOIN steps s "
                "ON s.task_id=a.task_id AND s.step_id=a.step_id "
                "JOIN tasks t ON t.task_id=a.task_id WHERE t.project_id=?",
                (project_id,)).fetchall()
            receipts = self._db.execute(
                "SELECT r.receipt,s.tool FROM receipts r JOIN steps s "
                "ON s.task_id=r.task_id AND s.step_id=r.step_id "
                "JOIN tasks t ON t.task_id=r.task_id WHERE t.project_id=?",
                (project_id,)).fetchall()

        metrics = _empty_usage_metrics()
        metrics["tasks_by_state"] = {}
        metrics["cpu_seconds"] = sum(float(row["cpu_seconds_used"] or 0) for row in tasks)
        metrics["max_worker_peak_rss_bytes"] = max(
            (int(row["max_worker_peak_rss_bytes"] or 0) for row in tasks), default=0)
        metrics["task_memory_limit_mb"] = max(
            (int(json.loads(row["envelope"]).get("budgets", {}).get("memory_mb", 0))
             for row in tasks), default=0)
        for row in tasks:
            state = row["state"]
            metrics["tasks_by_state"][state] = metrics["tasks_by_state"].get(state, 0) + 1

        unknown_resource = set(resource_effectful_tools or ())
        unknown_billable = set(billable_tools or ())
        unknown_external = set(external_effect_tools or ())
        for attempt in attempts:
            tool = attempt["tool"]
            metrics["attempts"] += 1
            metrics["retries"] += max(0, int(attempt["attempt_number"]) - 1)
            if attempt["dispatched_at"]:
                metrics["tool_calls"] += 1
                metrics["calls_by_tool"][tool] = metrics["calls_by_tool"].get(tool, 0) + 1
                if tool == "model.complete":
                    metrics["model_calls"] += 1
            if attempt["state"] == "UNKNOWN":
                metrics["unknown_attempts"] += 1
                if attempt["dispatched_at"] and tool in unknown_resource:
                    metrics["unknown_resource_effectful_calls"] += 1
                if attempt["dispatched_at"] and tool in unknown_billable:
                    metrics["uncertain_provider_calls"] += 1
                if attempt["dispatched_at"] and tool in unknown_external:
                    metrics["uncertain_external_effects"] += 1
                if attempt["dispatched_at"]:
                    try:
                        dispatched = datetime.strptime(
                            attempt["dispatched_at"], "%Y-%m-%dT%H:%M:%SZ"
                        ).replace(tzinfo=timezone.utc)
                        metrics["uncertain_execution_seconds"] += max(
                            0.0, (datetime.now(timezone.utc) - dispatched).total_seconds())
                    except (ValueError, TypeError):
                        pass

        for row in receipts:
            try:
                receipt = json.loads(row["receipt"])
            except (ValueError, TypeError):
                continue
            tool = row["tool"]
            status = receipt.get("status")
            usage = receipt.get("usage") or {}
            metrics["receipt_count"] += 1
            metrics["max_worker_peak_rss_bytes"] = max(
                metrics["max_worker_peak_rss_bytes"],
                max(0, int(usage.get("peak_rss_bytes", 0) or 0)))
            metrics["max_worker_address_space_limit_bytes"] = max(
                metrics["max_worker_address_space_limit_bytes"],
                max(0, int(usage.get("memory_limit_bytes", 0) or 0)))
            elapsed_ms = max(0, int(usage.get("elapsed_ms", 0) or 0))
            metrics["elapsed_ms"] += elapsed_ms
            metrics["execution_time_seconds"] += elapsed_ms / 1000.0
            metrics["latencies_ms"].append(elapsed_ms)
            if status == "failed":
                metrics["tool_failures"] += 1
                metrics["errors_by_tool"][tool] = metrics["errors_by_tool"].get(tool, 0) + 1
            elif status == "cancelled":
                metrics["cancelled_calls"] += 1
            errors = receipt.get("errors") or []
            if any(isinstance(error, dict) and error.get("type") == "budget_exceeded"
                   for error in errors):
                metrics["budget_exceeded"] += 1
            token_count = max(0, int(usage.get("model_tokens", 0) or 0))
            metrics["model_tokens"] += token_count
            source = usage.get("usage_source", "unclassified")
            if source == "provider":
                metrics["provider_reported_tokens"] += token_count
            elif source == "estimated":
                metrics["estimated_model_tokens"] += token_count
            elif token_count:
                metrics["unclassified_model_tokens"] += token_count
            metrics["input_tokens"] += max(0, int(usage.get("input_tokens", 0) or 0))
            metrics["output_tokens"] += max(0, int(usage.get("output_tokens", 0) or 0))
            calls = max(0, int(usage.get("provider_calls", 0) or 0))
            metrics["provider_calls"] += calls
            metrics["uncertain_token_consumption_calls"] += int(bool(
                usage.get("token_consumption_uncertain")))
            metrics["uncertain_cost_consumption_calls"] += int(bool(
                usage.get("cost_consumption_uncertain")))
            metrics["uncertain_provider_calls"] += max(0, int(
                usage.get("uncertain_provider_calls", 0) or 0))
            cost = usage.get("cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                amount = max(0.0, float(cost))
                metrics["cost_usd"] += amount
                metrics["priced_provider_calls"] += calls
                if usage.get("cost_source") == "provider_reported":
                    metrics["provider_reported_cost_usd"] += amount
                elif usage.get("cost_source") == "configured_rate_card":
                    metrics["rate_card_estimated_cost_usd"] += amount
            elif calls:
                metrics["unpriced_provider_calls"] += calls
        metrics["latencies_ms"].sort()
        return metrics

    def record_recovery_outcome(self, recovery_id: str, task_id: str | None,
                                outcome: str, detail: dict) -> dict:
        """Persist a structured recovery outcome and audit task-scoped reports."""
        if outcome not in {"completed", "resumed", "still_unresolved",
                           "already_running", "recovery_failed"}:
            raise ValueError(f"unsupported recovery outcome: {outcome}")
        payload = json.dumps(detail, sort_keys=True)
        created_at = utcnow()
        with self._write_transaction():
            cursor = self._db.execute(
                "INSERT INTO recovery_reports(recovery_id,task_id,outcome,detail,created_at) "
                "VALUES(?,?,?,?,?)", (recovery_id, task_id, outcome, payload, created_at))
            if task_id:
                self._log(task_id, "recovery.outcome", {
                    "recovery_id": recovery_id, "outcome": outcome, **detail})
            row = self._db.execute(
                "SELECT * FROM recovery_reports WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
        return {**dict(row), "detail": json.loads(row["detail"])}

    def recovery_outcomes(self, recovery_id: str | None = None,
                          task_id: str | None = None, limit: int = 100,
                          project_id: str | None = None) -> list[dict]:
        clauses, params = [], []
        if recovery_id is not None:
            clauses.append("r.recovery_id=?")
            params.append(recovery_id)
        if task_id is not None:
            clauses.append("r.task_id=?")
            params.append(task_id)
        if project_id is not None:
            clauses.append("t.project_id=?")
            params.append(project_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(int(limit), 1000)))
        with self._lock:
            rows = self._db.execute(
                "SELECT r.* FROM recovery_reports r LEFT JOIN tasks t "
                "ON t.task_id=r.task_id" + where +
                " ORDER BY r.id DESC LIMIT ?", params).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["detail"] = json.loads(item["detail"])
            out.append(item)
        return out

    # ---- plans and steps ---------------------------------------------
    @staticmethod
    def _step_definition(step: Step | dict) -> dict:
        if isinstance(step, Step):
            row = step.to_dict()
        elif isinstance(step, dict):
            row = dict(step)
        else:
            raise LedgerError("plan steps must be Step objects or dictionaries")
        required = ("step_id", "tool", "args", "depends_on", "read_only",
                    "permissions_used")
        if any(key not in row for key in required):
            raise LedgerError("plan step is missing required fields")
        return {key: row[key] for key in required}

    @staticmethod
    def _plan_digest(definitions: list[dict]) -> tuple[str, str]:
        canonical = json.dumps(definitions, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False)
        return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_plan_definitions(definitions: list[dict]) -> None:
        """Validate a complete DAG before it can become executable state."""
        if not isinstance(definitions, list) or not definitions:
            raise LedgerError("plan must contain at least one step")
        ids = []
        for step in definitions:
            if not isinstance(step, dict):
                raise LedgerError("plan steps must be objects")
            required = {"step_id", "tool", "args", "depends_on",
                        "read_only", "permissions_used"}
            if not required.issubset(step):
                raise LedgerError("plan step is missing required fields")
            if not isinstance(step["step_id"], str) or not step["step_id"].strip():
                raise LedgerError("step_id must be a non-empty string")
            if not isinstance(step["tool"], str) or not step["tool"].strip():
                raise LedgerError(f"step {step['step_id']} has an invalid tool name")
            if not isinstance(step["args"], dict):
                raise LedgerError(f"step {step['step_id']} args must be an object")
            if not isinstance(step["read_only"], bool):
                raise LedgerError(f"step {step['step_id']} read_only must be boolean")
            if (not isinstance(step["permissions_used"], list)
                    or any(not isinstance(p, str) or not p
                           for p in step["permissions_used"])):
                raise LedgerError(f"step {step['step_id']} permissions_used must be strings")
            if (not isinstance(step["depends_on"], list)
                    or any(not isinstance(dep, str) or not dep
                           for dep in step["depends_on"])):
                raise LedgerError(f"step {step['step_id']} dependencies must be step IDs")
            if len(step["depends_on"]) != len(set(step["depends_on"])):
                raise LedgerError(f"step {step['step_id']} has duplicate dependencies")
            ids.append(step["step_id"])
        if len(ids) != len(set(ids)):
            raise LedgerError("plan contains duplicate step IDs")
        known = set(ids)
        dependents = {step_id: [] for step_id in ids}
        indegree = {step_id: 0 for step_id in ids}
        for step in definitions:
            step_id = step["step_id"]
            for dep in step["depends_on"]:
                if dep not in known or dep == step_id:
                    raise LedgerError(f"plan has invalid dependency {dep!r} for {step_id}")
                dependents[dep].append(step_id)
                indegree[step_id] += 1
        ready = [step_id for step_id, degree in indegree.items() if degree == 0]
        visited = 0
        while ready:
            current = ready.pop()
            visited += 1
            for child in dependents[current]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        if visited != len(ids):
            raise LedgerError("plan dependencies contain a cycle")

    def persist_plan(self, task_id: str, steps: list[Step],
                     planner_version: str = "rules-v1",
                     run_token: str | None = None) -> dict:
        """Persist the first validated DAG and never replace it implicitly."""
        if not isinstance(steps, list):
            raise LedgerError("plan must be a list of steps")
        proposed = [self._step_definition(step) for step in steps]
        with self._write_transaction():
            task = self._db.execute(
                "SELECT current_plan_version FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if not task:
                raise LedgerError(f"unknown task {task_id}")
            if run_token is not None:
                self._assert_run_claim_locked(task_id, run_token)
            version = int(task["current_plan_version"])
            if version:
                existing = self._db.execute(
                    "SELECT plan_hash,steps_json FROM plans WHERE task_id=? AND plan_version=?",
                    (task_id, version)).fetchone()
                if not existing:
                    raise LedgerError(f"task {task_id} references a missing plan")
                definitions = json.loads(existing["steps_json"])
                _, stored_digest = self._plan_digest(definitions)
                if stored_digest != existing["plan_hash"]:
                    raise LedgerError(f"persisted plan {version} for {task_id} is corrupt")
                self._validate_plan_definitions(definitions)
                # On resume the newly generated plan is intentionally ignored;
                # only the original, validated snapshot is executable.
            else:
                definitions = proposed
                self._validate_plan_definitions(definitions)
                serialized, digest = self._plan_digest(definitions)
                version = 1
                existing_steps = self._db.execute(
                    "SELECT step_id,tool,args,depends_on,read_only,permissions_used "
                    "FROM steps WHERE task_id=? ORDER BY rowid", (task_id,)).fetchall()
                if existing_steps:
                    stored_definitions = [{
                        "step_id": row["step_id"], "tool": row["tool"],
                        "args": json.loads(row["args"]),
                        "depends_on": json.loads(row["depends_on"]),
                        "read_only": bool(row["read_only"]),
                        "permissions_used": json.loads(row["permissions_used"]),
                    } for row in existing_steps]
                    if stored_definitions != definitions:
                        raise LedgerError("persisted steps do not match the initial plan")
                else:
                    for step in definitions:
                        self._insert_step_locked(task_id, step)
                self._db.execute(
                    "INSERT INTO plans(task_id,plan_version,planner_version,plan_hash,steps_json,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (task_id, version, planner_version, digest, serialized, utcnow()))
                self._db.execute(
                    "UPDATE tasks SET current_plan_version=? WHERE task_id=? AND current_plan_version=0",
                    (version, task_id))
                self._log(task_id, "plan.persisted", {
                    "plan_version": version, "planner_version": planner_version,
                    "plan_hash": digest, "step_count": len(definitions)})
        result = self.get_plan(task_id)
        if result is None:
            raise LedgerError(f"could not load persisted plan for {task_id}")
        return result

    def get_plan(self, task_id: str) -> dict | None:
        with self._lock:
            task = self._db.execute(
                "SELECT current_plan_version FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if not task or not task["current_plan_version"]:
                return None
            version = int(task["current_plan_version"])
            row = self._db.execute(
                "SELECT planner_version,plan_hash,steps_json,created_at FROM plans "
                "WHERE task_id=? AND plan_version=?", (task_id, version)).fetchone()
            if not row:
                raise LedgerError(f"task {task_id} references a missing plan")
            definitions = json.loads(row["steps_json"])
            self._validate_plan_definitions(definitions)
            _, digest = self._plan_digest(definitions)
            if digest != row["plan_hash"]:
                raise LedgerError(f"persisted plan {version} for {task_id} is corrupt")
            loaded_steps = self.get_steps(task_id)
            by_id = {step.step_id: step for step in loaded_steps}
            if len(by_id) != len(definitions) or any(
                    step_id not in by_id or self._step_definition(by_id[step_id]) != definition
                    for step_id, definition in ((d["step_id"], d) for d in definitions)):
                raise LedgerError(f"runtime steps diverge from persisted plan {version}")
            runtime_steps = [by_id[definition["step_id"]] for definition in definitions]
            return {"task_id": task_id, "version": version,
                    "planner_version": row["planner_version"],
                    "plan_hash": row["plan_hash"], "created_at": row["created_at"],
                    "steps": runtime_steps}

    # ---- steps -------------------------------------------------------
    def _insert_step_locked(self, task_id: str, definition: dict) -> None:
        self._db.execute(
            "INSERT INTO steps(task_id,step_id,tool,args,depends_on,read_only,permissions_used,state) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (task_id, definition["step_id"], definition["tool"],
             json.dumps(definition["args"], sort_keys=True),
             json.dumps(definition["depends_on"]), int(definition["read_only"]),
             json.dumps(definition["permissions_used"]), StepState.PENDING.value))

    def save_steps(self, task_id: str, steps: list[Step]) -> None:
        """Persist step definitions without replacing states or prior work."""
        with self._write_transaction():
            if not self._db.execute(
                    "SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone():
                raise LedgerError(f"unknown task {task_id}")
            for step in steps:
                definition = self._step_definition(step)
                row = self._db.execute(
                    "SELECT step_id,tool,args,depends_on,read_only,permissions_used "
                    "FROM steps WHERE task_id=? AND step_id=?",
                    (task_id, step.step_id)).fetchone()
                if row:
                    stored = {"step_id": row["step_id"], "tool": row["tool"],
                              "args": json.loads(row["args"]),
                              "depends_on": json.loads(row["depends_on"]),
                              "read_only": bool(row["read_only"]),
                              "permissions_used": json.loads(row["permissions_used"])}
                    if stored != definition:
                        raise LedgerError(f"step {step.step_id} is immutable once persisted")
                    continue
                if self._db.execute(
                        "SELECT 1 FROM plans WHERE task_id=?", (task_id,)).fetchone():
                    raise LedgerError("cannot add steps to an already versioned plan")
                self._insert_step_locked(task_id, definition)

    def get_steps(self, task_id: str) -> list[Step]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM steps WHERE task_id=? ORDER BY rowid", (task_id,)).fetchall()
            return [Step(
                step_id=r["step_id"], tool=r["tool"], args=json.loads(r["args"]),
                depends_on=json.loads(r["depends_on"]), read_only=bool(r["read_only"]),
                permissions_used=json.loads(r["permissions_used"]),
                state=StepState(r["state"])) for r in rows]

    def set_step_state(self, task_id: str, step_id: str, state: StepState, *,
                       expected_state: StepState | None = None,
                       run_token: str | None = None) -> None:
        allowed = {
            StepState.PENDING: {StepState.READY, StepState.RUNNING,
                                StepState.SUCCEEDED, StepState.FAILED,
                                StepState.CANCELLED},
            StepState.READY: {StepState.RUNNING, StepState.CANCELLED},
            StepState.RUNNING: {StepState.SUCCEEDED, StepState.FAILED,
                                StepState.CANCELLED, StepState.INTERRUPTED},
            StepState.FAILED: {StepState.READY, StepState.RUNNING},
            StepState.INTERRUPTED: set(),
            StepState.SUCCEEDED: set(),
            StepState.CANCELLED: set(),
        }
        with self._write_transaction():
            if run_token is not None:
                self._assert_run_claim_locked(task_id, run_token)
            row = self._db.execute(
                "SELECT state FROM steps WHERE task_id=? AND step_id=?",
                (task_id, step_id)).fetchone()
            if not row:
                raise LedgerError(f"unknown step {step_id} for task {task_id}")
            current = StepState(row["state"])
            if expected_state is not None and current != expected_state:
                raise LedgerError(f"concurrent step state transition for {step_id}")
            if current == state:
                return
            if state not in allowed[current]:
                raise LedgerError(f"illegal step state transition {current.value} -> {state.value}")
            self._db.execute(
                "UPDATE steps SET state=? WHERE task_id=? AND step_id=? AND state=?",
                (state.value, task_id, step_id, current.value))

    # ---- durable attempts --------------------------------------------
    def start_attempt(self, task_id: str, step_id: str, plan_version: int,
                      run_token: str) -> str:
        """Record attempt identity and RUNNING state before tool dispatch."""
        attempt_id = new_id("A")
        now = utcnow()
        with self._write_transaction():
            self._assert_run_claim_locked(task_id, run_token)
            task = self._db.execute(
                "SELECT current_plan_version,state FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if (not task or int(task["current_plan_version"]) != int(plan_version)
                    or task["state"] != TaskState.RUNNING.value):
                raise LedgerError("attempt does not belong to a running task plan")
            step = self._db.execute(
                "SELECT state FROM steps WHERE task_id=? AND step_id=?",
                (task_id, step_id)).fetchone()
            if not step:
                raise LedgerError(f"unknown step {step_id} for task {task_id}")
            step_state = StepState(step["state"])
            if step_state in {StepState.RUNNING, StepState.SUCCEEDED,
                              StepState.CANCELLED, StepState.INTERRUPTED}:
                raise LedgerError(f"step {step_id} cannot start from {step_state.value}")
            active = self._db.execute(
                "SELECT 1 FROM attempts WHERE task_id=? AND step_id=? AND state='RUNNING'",
                (task_id, step_id)).fetchone()
            if active:
                raise LedgerError(f"step {step_id} already has an active attempt")
            row = self._db.execute(
                "SELECT COALESCE(MAX(attempt_number),0)+1 AS next_attempt FROM attempts "
                "WHERE task_id=? AND step_id=?", (task_id, step_id)).fetchone()
            operation = self._db.execute(
                "SELECT operation_key FROM attempts WHERE task_id=? AND step_id=? "
                "AND plan_version=? AND operation_key IS NOT NULL ORDER BY attempt_number LIMIT 1",
                (task_id, step_id, plan_version)).fetchone()
            operation_key = operation["operation_key"] if operation else new_id("O")
            self._db.execute(
                "INSERT INTO attempts(attempt_id,task_id,step_id,plan_version,attempt_number,operation_key,state,started_at) "
                "VALUES(?,?,?,?,?,?,'RUNNING',?)",
                (attempt_id, task_id, step_id, plan_version, row["next_attempt"],
                 operation_key, now))
            self._db.execute(
                "UPDATE steps SET state=? WHERE task_id=? AND step_id=?",
                (StepState.RUNNING.value, task_id, step_id))
            self._log(task_id, "attempt.started", {
                "step_id": step_id, "attempt_id": attempt_id,
                "attempt_number": row["next_attempt"], "plan_version": plan_version,
                "operation_key": operation_key})
        return attempt_id

    def mark_attempt_dispatched(self, attempt_id: str, task_id: str,
                                run_token: str) -> None:
        """Persist the dispatch boundary before a worker can call a provider."""
        with self._write_transaction():
            self._assert_run_claim_locked(task_id, run_token)
            cursor = self._db.execute(
                "UPDATE attempts SET dispatched_at=COALESCE(dispatched_at,?) "
                "WHERE attempt_id=? AND task_id=? AND state='RUNNING'",
                (utcnow(), attempt_id, task_id))
            if cursor.rowcount != 1:
                raise LedgerError(f"attempt {attempt_id} is not dispatchable")
            self._log(task_id, "attempt.dispatched", {"attempt_id": attempt_id})

    def get_attempts(self, task_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM attempts WHERE task_id=? ORDER BY started_at,attempt_number",
                (task_id,)).fetchall()
            return [dict(row) for row in rows]

    def attempt_operation_key(self, attempt_id: str) -> str:
        with self._lock:
            row = self._db.execute(
                "SELECT operation_key FROM attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if not row:
                raise LedgerError(f"unknown attempt {attempt_id}")
            if not row["operation_key"]:
                raise LedgerError(f"attempt {attempt_id} has no durable operation key")
            return row["operation_key"]

    def unresolved_attempts(self, task_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT a.*,s.tool,s.args,s.permissions_used FROM attempts a "
                "JOIN steps s ON s.task_id=a.task_id AND s.step_id=a.step_id "
                "WHERE a.task_id=? AND a.state='UNKNOWN' ORDER BY a.started_at,a.attempt_number",
                (task_id,)).fetchall()
            return [dict(row) for row in rows]

    def request_recovery_cancel(self, task_id: str) -> dict:
        """Persist cancellation intent without hiding unresolved side effects."""
        with self._write_transaction():
            task = self._db.execute(
                "SELECT state,result FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not task:
                raise LedgerError(f"unknown task {task_id}")
            if task["state"] != TaskState.RECOVERY_REQUIRED.value:
                raise LedgerError("task is not recovery-required")
            result = json.loads(task["result"]) if task["result"] else {}
            unresolved = self._db.execute(
                "SELECT COUNT(*) AS n FROM attempts WHERE task_id=? AND state='UNKNOWN'",
                (task_id,)).fetchone()["n"]
            if unresolved:
                result["cancellation_requested"] = True
                result["reason"] = "cancellation requested; unresolved tool outcomes still require reconciliation"
                self._db.execute("UPDATE tasks SET result=?,updated_at=? WHERE task_id=?",
                                 (json.dumps(result), utcnow(), task_id))
                self._log(task_id, "task.cancellation_requested", {
                    "state": TaskState.RECOVERY_REQUIRED.value,
                    "unresolved_attempts": int(unresolved)})
                return {"state": TaskState.RECOVERY_REQUIRED.value,
                        "cancel_requested": True, "unresolved_attempts": int(unresolved)}
            if not can_transition(TaskState.RECOVERY_REQUIRED, TaskState.CANCELLED):
                raise LedgerError("invalid cancellation transition")
            result.update({"state": TaskState.CANCELLED.value,
                           "cancellation_requested": True,
                           "reason": "cancelled after all tool outcomes were reconciled"})
            self._db.execute("UPDATE tasks SET state=?,result=?,updated_at=? WHERE task_id=?",
                             (TaskState.CANCELLED.value, json.dumps(result), utcnow(), task_id))
            self._log(task_id, "task.state", {"from": TaskState.RECOVERY_REQUIRED.value,
                                               "to": TaskState.CANCELLED.value})
            return {"state": TaskState.CANCELLED.value, "cancel_requested": True,
                    "unresolved_attempts": 0}

    def set_recovery_result(self, task_id: str, result: dict, run_token: str) -> None:
        """Update unresolved diagnostics without losing a concurrent cancel request."""
        with self._write_transaction():
            self._assert_run_claim_locked(task_id, run_token)
            row = self._db.execute(
                "SELECT state,result FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not row or row["state"] != TaskState.RECOVERY_REQUIRED.value:
                raise LedgerError("task is no longer recovery-required")
            previous = json.loads(row["result"]) if row["result"] else {}
            if previous.get("cancellation_requested"):
                result["cancellation_requested"] = True
            self._db.execute("UPDATE tasks SET result=?,updated_at=? WHERE task_id=?",
                             (json.dumps(result), utcnow(), task_id))

    def finish_recovery(self, task_id: str, run_token: str) -> str:
        """Atomically choose resume versus cancel once ambiguity is removed."""
        with self._write_transaction():
            self._assert_run_claim_locked(task_id, run_token)
            task = self._db.execute(
                "SELECT state,result FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not task or task["state"] != TaskState.RECOVERY_REQUIRED.value:
                raise LedgerError("task is no longer recovery-required")
            unresolved = self._db.execute(
                "SELECT COUNT(*) AS n FROM attempts WHERE task_id=? AND state='UNKNOWN'",
                (task_id,)).fetchone()["n"]
            if unresolved:
                return "unresolved"
            result = json.loads(task["result"]) if task["result"] else {}
            target = (TaskState.CANCELLED if result.get("cancellation_requested")
                      else TaskState.RUNNING)
            if not can_transition(TaskState.RECOVERY_REQUIRED, target):
                raise LedgerError("invalid recovery completion transition")
            result["state"] = target.value
            if target == TaskState.CANCELLED:
                result["reason"] = "cancelled after all tool outcomes were reconciled"
            self._db.execute(
                "UPDATE tasks SET state=?,result=?,updated_at=? WHERE task_id=? AND state=?",
                (target.value, json.dumps(result), utcnow(), task_id,
                 TaskState.RECOVERY_REQUIRED.value))
            self._log(task_id, "task.state", {"from": TaskState.RECOVERY_REQUIRED.value,
                                               "to": target.value})
            return "cancelled" if target == TaskState.CANCELLED else "running"

    def resolve_unknown_attempt(self, attempt_id: str, outcome: str,
                                run_token: str, receipt: ToolReceipt | None = None) -> None:
        """Atomically record reconciliation or mark a safe keyed retry."""
        if outcome not in {"applied", "retryable"}:
            raise LedgerError(f"unsupported reconciliation outcome {outcome!r}")
        if outcome == "applied" and receipt is None:
            raise LedgerError("an applied reconciliation requires a receipt")
        with self._write_transaction():
            row = self._db.execute(
                "SELECT task_id,step_id,state FROM attempts WHERE attempt_id=?",
                (attempt_id,)).fetchone()
            if not row or row["state"] != "UNKNOWN":
                raise LedgerError(f"attempt {attempt_id} is not unresolved")
            task_id = row["task_id"]
            self._assert_run_claim_locked(task_id, run_token)
            if outcome == "retryable":
                self._db.execute(
                    "UPDATE attempts SET state='RETRYABLE' WHERE attempt_id=? AND state='UNKNOWN'",
                    (attempt_id,))
                self._db.execute(
                    "UPDATE steps SET state=? WHERE task_id=? AND step_id=?",
                    (StepState.READY.value, task_id, row["step_id"]))
                self._log(task_id, "attempt.retryable", {"attempt_id": attempt_id,
                                                           "step_id": row["step_id"]})
                return
            if (receipt.task_id != task_id or receipt.step_id != row["step_id"]
                    or receipt.attempt_id != attempt_id):
                raise LedgerError("reconciled receipt identity does not match its attempt")
            state_map = {"succeeded": StepState.SUCCEEDED, "failed": StepState.FAILED,
                         "cancelled": StepState.CANCELLED}
            if receipt.status not in {"succeeded", "failed", "cancelled"}:
                raise LedgerError(f"invalid reconciled receipt status {receipt.status!r}")
            receipt_json = json.dumps(receipt.to_dict(), sort_keys=True)
            self._db.execute(
                "UPDATE attempts SET state=?,finished_at=?,receipt_json=? "
                "WHERE attempt_id=? AND state='UNKNOWN'",
                (state_map[receipt.status].value, utcnow(), receipt_json, attempt_id))
            self._db.execute(
                "INSERT INTO receipts(task_id,step_id,attempt_id,receipt,committed,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (task_id, row["step_id"], attempt_id, receipt_json,
                 int(receipt.status == "succeeded"), utcnow()))
            self._db.execute(
                "UPDATE steps SET state=? WHERE task_id=? AND step_id=?",
                (state_map[receipt.status].value, task_id, row["step_id"]))
            self._log(task_id, "attempt.reconciled", {"attempt_id": attempt_id,
                                                        "step_id": row["step_id"],
                                                        "status": receipt.status})

    def mark_interrupted_attempts(self, task_id: str, run_token: str) -> list[dict]:
        """Fence off outcomes left ambiguous by a worker that disappeared."""
        with self._write_transaction():
            self._assert_run_claim_locked(task_id, run_token)
            rows = self._db.execute(
                "SELECT attempt_id,step_id,plan_version FROM attempts "
                "WHERE task_id=? AND state='RUNNING' ORDER BY started_at",
                (task_id,)).fetchall()
            for row in rows:
                self._db.execute(
                    "UPDATE attempts SET state='UNKNOWN',finished_at=? "
                    "WHERE attempt_id=? AND state='RUNNING'",
                    (utcnow(), row["attempt_id"]))
                self._db.execute(
                    "UPDATE steps SET state=? WHERE task_id=? AND step_id=?",
                    (StepState.INTERRUPTED.value, task_id, row["step_id"]))
                self._log(task_id, "attempt.interrupted", {
                    "step_id": row["step_id"], "attempt_id": row["attempt_id"],
                    "plan_version": row["plan_version"]})
            if rows:
                task = self._db.execute(
                    "SELECT state FROM tasks WHERE task_id=?", (task_id,)).fetchone()
                if task and task["state"] in {
                        TaskState.RUNNING.value, TaskState.CANCELLING.value}:
                    previous = TaskState(task["state"])
                    if not can_transition(previous, TaskState.RECOVERY_REQUIRED):
                        raise LedgerError("invalid recovery-required transition")
                    self._db.execute(
                        "UPDATE tasks SET state=?,updated_at=? WHERE task_id=? AND state=?",
                        (TaskState.RECOVERY_REQUIRED.value, utcnow(), task_id,
                         previous.value))
                    self._log(task_id, "task.state", {
                        "from": previous.value,
                        "to": TaskState.RECOVERY_REQUIRED.value})
            return [dict(row) for row in rows]

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

    def save_receipt(self, receipt: ToolReceipt, committed: bool,
                     run_token: str | None = None) -> None:
        """Atomically persist a receipt, finish its attempt, and update its step."""
        receipt_json = json.dumps(receipt.to_dict(), sort_keys=True, separators=(",", ":"))
        state_map = {"succeeded": "SUCCEEDED", "failed": "FAILED", "cancelled": "CANCELLED"}
        if receipt.status not in state_map:
            raise LedgerError(f"invalid receipt status {receipt.status!r}")
        with self._write_transaction():
            if run_token is not None:
                self._assert_run_claim_locked(receipt.task_id, run_token)
            attempt = self._db.execute(
                "SELECT task_id,step_id,state FROM attempts WHERE attempt_id=?",
                (receipt.attempt_id,)).fetchone()
            if attempt:
                if attempt["task_id"] != receipt.task_id or attempt["step_id"] != receipt.step_id:
                    raise LedgerError("receipt identity does not match its durable attempt")
                if attempt["state"] not in {"RUNNING", state_map[receipt.status]}:
                    raise LedgerError(f"attempt {receipt.attempt_id} is already resolved")
            elif run_token is not None:
                raise LedgerError("cannot commit a receipt without its pre-dispatch attempt")
            else:
                task = self._db.execute(
                    "SELECT current_plan_version FROM tasks WHERE task_id=?",
                    (receipt.task_id,)).fetchone()
                if not task:
                    raise LedgerError(f"unknown task {receipt.task_id}")
                if not self._db.execute(
                        "SELECT 1 FROM steps WHERE task_id=? AND step_id=?",
                        (receipt.task_id, receipt.step_id)).fetchone():
                    raise LedgerError(f"unknown step {receipt.step_id} for task {receipt.task_id}")
                row = self._db.execute(
                    "SELECT COALESCE(MAX(attempt_number),0)+1 AS next_attempt FROM attempts "
                    "WHERE task_id=? AND step_id=?", (receipt.task_id, receipt.step_id)).fetchone()
                now = utcnow()
                self._db.execute(
                    "INSERT INTO attempts(attempt_id,task_id,step_id,plan_version,attempt_number,state,"
                    "started_at,finished_at,receipt_json) VALUES(?,?,?,?,?,?,?,?,?)",
                    (receipt.attempt_id, receipt.task_id, receipt.step_id,
                     int(task["current_plan_version"]), row["next_attempt"],
                     state_map[receipt.status], now, now, receipt_json))
            existing = self._db.execute(
                "SELECT receipt,committed FROM receipts WHERE task_id=? AND step_id=? AND attempt_id=?",
                (receipt.task_id, receipt.step_id, receipt.attempt_id)).fetchone()
            if existing and (existing["receipt"] != json.dumps(receipt.to_dict())
                             or bool(existing["committed"]) != bool(committed)):
                raise LedgerError(f"attempt {receipt.attempt_id} already has a different receipt")
            if not existing:
                self._db.execute(
                    "INSERT INTO receipts(task_id,step_id,attempt_id,receipt,committed,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (receipt.task_id, receipt.step_id, receipt.attempt_id,
                     json.dumps(receipt.to_dict()), int(committed), utcnow()))
            self._db.execute(
                "UPDATE attempts SET state=?,finished_at=COALESCE(finished_at,?),receipt_json=? "
                "WHERE attempt_id=?",
                (state_map[receipt.status], utcnow(), receipt_json, receipt.attempt_id))
            self._db.execute(
                "UPDATE steps SET state=? WHERE task_id=? AND step_id=?",
                (StepState(state_map[receipt.status]).value, receipt.task_id, receipt.step_id))
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

    def events(self, task_id: str, after_id: int = 0,
               limit: int | None = None) -> list:
        query = "SELECT id,at,kind,detail FROM events WHERE task_id=? AND id>? ORDER BY id"
        params: list = [task_id, min(2**63 - 1, max(0, int(after_id)))]
        if limit is not None:
            query += " LIMIT ?"
            params.append(max(0, int(limit)))
        with self._lock:
            return [dict(row) for row in self._db.execute(query, params)]
