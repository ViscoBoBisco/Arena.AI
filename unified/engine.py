"""Engine: ties gateway-facing operations to ledger, policy, planner,
scheduler, broker and verifier. Executes the canonical request path from
manual 1.4: gateway -> ledger -> plan -> broker -> evidence -> verifier.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading

from .broker import CapabilityBroker
from .contracts import (
    Step,
    StepState,
    TaskEnvelope,
    TaskState,
    ToolReceipt,
    new_id,
)
from .evidence import EvidenceStore
from .ledger import Ledger, LedgerError
from .planner import plan
from .policy import PolicyEngine, PolicyViolation
from .verifier import verify


def _new_metrics() -> dict:
    return {"tasks_by_state": {}, "tool_calls": 0, "tool_failures": 0,
            "model_tokens": 0, "errors_by_tool": {}, "latencies_ms": []}


def _request_fingerprint(env: TaskEnvelope) -> str:
    """Canonical identity for one project-scoped idempotent submission."""
    canonical = json.dumps({
        "request": env.request,
        "inputs": env.inputs,
        "permissions": env.permissions,
        "budgets": env.budgets,
        "delivery": env.delivery,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Engine:
    def __init__(self, ledger: Ledger, broker: CapabilityBroker, evidence: EvidenceStore,
                 model_router=None, roles=None, automation=None, memory=None,
                 delivery=None):
        self.ledger = ledger
        self.broker = broker
        self.evidence = evidence
        self.model_router = model_router
        self.roles = roles
        self.automation = automation
        self.memory = memory
        self.delivery = delivery
        self._cancel = set()
        self._lock = threading.RLock()
        self.metrics = _new_metrics()  # aggregate, internal/operator use
        self._project_metrics: dict[str, dict] = {}

    def metrics_for_project(self, project_id: str) -> dict:
        """Return only the requesting project’s in-process metrics."""
        with self._lock:
            return copy.deepcopy(self._project_metrics.get(project_id, _new_metrics()))

    def _project_metric(self, project_id: str) -> dict:
        return self._project_metrics.setdefault(project_id, _new_metrics())

    # ---- admission (manual 3.1 / 3.3) --------------------------------
    def _existing_submission(self, task_id: str, fingerprint: str) -> dict:
        task = self.ledger.get_task(task_id)
        prior = TaskEnvelope(**task["envelope"])
        if _request_fingerprint(prior) != fingerprint:
            return {"task_id": task_id, "duplicate": False, "conflict": True,
                    "state": task["state"],
                    "error": "idempotency key already used with a different request"}
        return {"task_id": task_id, "duplicate": True, "conflict": False,
                "state": task["state"]}

    def submit(self, project_id: str, idempotency_key: str, request: dict,
               permissions: list[str], grants: list[str],
               inputs: list | None = None, budgets: dict | None = None) -> dict:
        """POST /v1/tasks. Duplicate idempotency key returns the same task ID
        without duplicate execution (manual 5.10, test 1)."""
        env = TaskEnvelope(task_id=new_id("T"), idempotency_key=idempotency_key,
                           project_id=project_id, request=request,
                           inputs=inputs or [], permissions=permissions,
                           budgets=budgets or {"model_calls": 12, "tool_calls": 30,
                                              "time_seconds": 600})
        fingerprint = _request_fingerprint(env)
        existing = self.ledger.find_by_idempotency(project_id, idempotency_key)
        if existing:
            return self._existing_submission(existing, fingerprint)
        try:
            self.ledger.create_task(env)
        except LedgerError:
            # A concurrent process may have claimed the unique project/key
            # pair after the lookup; re-read and classify it rather than 500.
            existing = self.ledger.find_by_idempotency(project_id, idempotency_key)
            if existing:
                return self._existing_submission(existing, fingerprint)
            raise
        policy = PolicyEngine(set(grants))
        try:
            policy.admit(permissions)  # caller grants checked at admission
            required = {permission for step in plan(env)
                        for permission in step.permissions_used}
            omitted = sorted(required - set(permissions))
            if omitted:
                raise PolicyViolation(
                    f"request omitted required permissions: {omitted}")
        except PolicyViolation as e:
            self.ledger.set_state(env.task_id, TaskState.REJECTED,
                                  expected_state=TaskState.CREATED)
            self.ledger.set_result(env.task_id, {"error": str(e)})
            return {"task_id": env.task_id, "duplicate": False,
                    "state": TaskState.REJECTED.value, "error": str(e)}
        self.ledger.set_state(env.task_id, TaskState.ADMITTED,
                              expected_state=TaskState.CREATED)
        return {"task_id": env.task_id, "duplicate": False,
                "state": TaskState.ADMITTED.value}

    # ---- execution -----------------------------------------------------
    def run(self, task_id: str, grants: list[str], *, reconcile_recovery: bool = False) -> dict:
        """Run a task under a durable, lease-based single-worker claim.

        Recovery-required tasks are deliberately inert unless the caller enters
        through the explicit startup recovery path.
        """
        task = self.ledger.get_task(task_id)
        if task is None:
            raise LedgerError(f"unknown task {task_id}")
        state = TaskState(task["state"])
        if state in {TaskState.REJECTED, TaskState.FAILED, TaskState.CANCELLED,
                     TaskState.COMPLETED} or (
                     state == TaskState.RECOVERY_REQUIRED and not reconcile_recovery):
            return task.get("result") or {"task_id": task_id, "state": state.value}
        run_token = self.ledger.claim_run(task_id)
        if run_token is None:
            current = self.ledger.get_task(task_id)
            return {"task_id": task_id, "state": current["state"],
                    "already_running": True}
        stop_heartbeat = threading.Event()
        lost_claim = threading.Event()

        def heartbeat():
            while not stop_heartbeat.wait(10.0):
                try:
                    if not self.ledger.renew_run_claim(task_id, run_token):
                        lost_claim.set()
                        return
                except Exception:
                    lost_claim.set()
                    return

        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        try:
            return self._run_claimed(task_id, grants, run_token, lost_claim,
                                    reconcile_recovery=reconcile_recovery)
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=1)
            self.ledger.release_run_claim(task_id, run_token)

    def _run_claimed(self, task_id: str, grants: list[str], run_token: str,
                     lost_claim: threading.Event, *,
                     reconcile_recovery: bool = False) -> dict:
        """Execute only while this worker retains the durable task claim."""
        task = self.ledger.get_task(task_id)
        if task is None:
            raise LedgerError(f"unknown task {task_id}")
        if task["state"] == TaskState.REJECTED.value:
            return task["result"]
        if (task["state"] == TaskState.RECOVERY_REQUIRED.value
                and not reconcile_recovery):
            return task.get("result") or {
                "task_id": task_id, "state": TaskState.RECOVERY_REQUIRED.value}
        env = TaskEnvelope(**task["envelope"])
        # Admission validated these requested capabilities against the caller's
        # grants. Persisted task permissions are the attenuated execution scope;
        # a later approver need not inherit the original tool authority.
        policy = PolicyEngine(set(env.permissions))
        from .planner import PLANNER_VERSION
        persisted_plan = self.ledger.persist_plan(
            task_id, plan(env), planner_version=PLANNER_VERSION, run_token=run_token)
        steps = persisted_plan["steps"]
        cancellation_was_requested = task["state"] == TaskState.CANCELLING.value
        interrupted = self.ledger.mark_interrupted_attempts(task_id, run_token)
        if interrupted:
            result = {"task_id": task_id,
                      "state": TaskState.RECOVERY_REQUIRED.value,
                      "reason": "worker stopped during a tool attempt; outcome requires reconciliation",
                      "cancellation_requested": cancellation_was_requested,
                      "unresolved_attempts": interrupted}
            self.ledger.set_result(task_id, result, run_token=run_token)
            if not reconcile_recovery:
                return result
            task = self.ledger.get_task(task_id)
        plan_version = persisted_plan["version"]
        cur = TaskState(task["state"])
        recovered_from_recovery = cur == TaskState.RECOVERY_REQUIRED
        if recovered_from_recovery:
            if not reconcile_recovery:
                return task.get("result") or {
                    "task_id": task_id, "state": TaskState.RECOVERY_REQUIRED.value}
            for attempt in self.ledger.unresolved_attempts(task_id):
                spec = self.broker.get(attempt["tool"])
                attempt_args = json.loads(attempt["args"])
                attempt_args["_project_id"] = env.project_id
                permissions = json.loads(attempt["permissions_used"])
                operation_key = attempt.get("operation_key")
                if not operation_key:
                    continue
                outcome, receipt = self.broker.reconcile(
                    task_id, attempt["step_id"], attempt["attempt_id"],
                    attempt["tool"], attempt_args, permissions, policy, operation_key)
                if outcome == "applied":
                    self.ledger.resolve_unknown_attempt(
                        attempt["attempt_id"], "applied", run_token, receipt)
                elif outcome == "not_applied":
                    self.ledger.resolve_unknown_attempt(
                        attempt["attempt_id"], "retryable", run_token)
                elif spec and (spec.side_effect_class == "read_only"
                               or spec.supports_idempotency):
                    # Reads are repeatable; keyed adapters can safely replay the
                    # exact persisted operation key to obtain the original result.
                    self.ledger.resolve_unknown_attempt(
                        attempt["attempt_id"], "retryable", run_token)
            still_unknown = self.ledger.unresolved_attempts(task_id)
            if still_unknown:
                result = {"task_id": task_id,
                          "state": TaskState.RECOVERY_REQUIRED.value,
                          "reason": "one or more tool outcomes remain unresolved",
                          "unresolved_attempts": [
                              {"attempt_id": a["attempt_id"], "step_id": a["step_id"],
                               "tool": a["tool"]} for a in still_unknown]}
                self.ledger.set_result(task_id, result, run_token=run_token)
                return result
            prior_result = task.get("result") or {}
            if prior_result.get("cancellation_requested"):
                self.ledger.set_state(task_id, TaskState.CANCELLED,
                                      expected_state=TaskState.RECOVERY_REQUIRED,
                                      run_token=run_token)
                result = {"task_id": task_id, "state": TaskState.CANCELLED.value,
                          "reason": "cancelled after reconciling the in-flight operation",
                          "receipts": [r.to_dict() for r in self.ledger.get_receipts(task_id)]}
                self.ledger.set_result(task_id, result, run_token=run_token)
                return result
            self.ledger.set_state(task_id, TaskState.RUNNING,
                                  expected_state=TaskState.RECOVERY_REQUIRED,
                                  run_token=run_token)
            cur = TaskState.RUNNING
        if cur == TaskState.CANCELLING:
            self.ledger.set_state(task_id, TaskState.CANCELLED, run_token=run_token)
            result = {"task_id": task_id, "state": TaskState.CANCELLED.value,
                      "reason": "recovered cancellation"}
            self.ledger.set_result(task_id, result, run_token=run_token)
            with self._lock:
                self._cancel.discard(task_id)
            if self.model_router and hasattr(self.model_router, "forget_task"):
                self.model_router.forget_task(task_id)
            return result
        if cur == TaskState.ADMITTED:
            self.ledger.set_state(task_id, TaskState.PLANNED,
                                  expected_state=TaskState.ADMITTED, run_token=run_token)
            self.ledger.set_state(task_id, TaskState.RUNNING,
                                  expected_state=TaskState.PLANNED, run_token=run_token)
        elif cur in {TaskState.PLANNED, TaskState.WAITING_APPROVAL}:
            self.ledger.set_state(task_id, TaskState.RUNNING,
                                  expected_state=cur, run_token=run_token)
        elif cur not in {TaskState.RUNNING, TaskState.VERIFYING}:
            raise LedgerError(f"task {task_id} cannot run from state {cur.value}")

        # Resumable: steps already SUCCEEDED (after approval pause or restart)
        # are not re-executed.
        done: set[str] = {s.step_id for s in self.ledger.get_steps(task_id)
                          if s.state == StepState.SUCCEEDED}
        receipts: list[ToolReceipt] = self.ledger.get_receipts(task_id)
        if self.model_router and hasattr(self.model_router, "restore_task_usage"):
            persisted_tokens = sum(int(r.usage.get("model_tokens", 0)) for r in receipts)
            self.model_router.restore_task_usage(task_id, persisted_tokens)
        import time as _time
        from concurrent.futures import ThreadPoolExecutor
        started = _time.monotonic()
        budgets = env.budgets or {}
        resolved_steps = {receipt.step_id for receipt in receipts}
        remaining = ([] if cur == TaskState.VERIFYING else
                     [s for s in steps if s.step_id not in done
                      and s.step_id not in resolved_steps])
        # Wave-based DAG execution (manual 4.5): independent ready steps run
        # in parallel (tool parallelism); model calls serialize on the shared
        # router queue. Approval pauses break the wave loop sequentially.
        while remaining:
            if lost_claim.is_set():
                raise LedgerError(f"worker lost execution claim for {task_id}")
            ready = [s for s in remaining
                     if all(dep in done for dep in s.depends_on)]
            if not ready:
                break
            wave_receipts = []
            pause = None
            for step in ready:
                spec = self.broker.get(step.tool)
                if spec and spec.side_effect_class != "read_only":
                    decision = self.ledger.get_approval(task_id, step.step_id)
                    if decision is None:
                        self.ledger.save_approval(task_id, step.step_id, "pending")
                    if decision == "rejected":
                        self.ledger.set_state(task_id, TaskState.CANCELLING, run_token=run_token)
                        self.ledger.set_state(task_id, TaskState.CANCELLED, run_token=run_token)
                        result = {"task_id": task_id, "state": TaskState.CANCELLED.value,
                                  "reason": "approval rejected", "pending_step": step.step_id}
                        self.ledger.set_result(task_id, result, run_token=run_token)
                        return result
                    if decision != "approved":
                        pause = step
                        break
            if pause is not None:
                with self._lock:
                    if task_id in self._cancel:
                        self.ledger.set_state(task_id, TaskState.CANCELLING, run_token=run_token)
                        self.ledger.set_state(task_id, TaskState.CANCELLED, run_token=run_token)
                        result = {"task_id": task_id, "state": TaskState.CANCELLED.value,
                                  "reason": "cancelled before approval"}
                        for approval in self.ledger.pending_approvals(task_id):
                            self.ledger.save_approval(task_id, approval["step_id"], "cancelled")
                        self.ledger.set_result(task_id, result, run_token=run_token)
                        self._cancel.discard(task_id)
                        if self.model_router and hasattr(self.model_router, "forget_task"):
                            self.model_router.forget_task(task_id)
                        return result
                    self.ledger.set_state(task_id, TaskState.WAITING_APPROVAL, run_token=run_token)
                    result = {"task_id": task_id, "state": TaskState.WAITING_APPROVAL.value,
                              "pending_step": pause.step_id, "tool": pause.tool}
                    self.ledger.set_result(task_id, result, run_token=run_token)
                    return result
            budget_hit = None
            if _time.monotonic() - started > budgets.get("time_seconds", 600):
                budget_hit = "time_seconds"
            elif len(receipts) + len(wave_receipts) >= budgets.get("tool_calls", 30):
                budget_hit = "tool_calls"
            if task_id in self._cancel or budget_hit:
                reason = "cancelled" if task_id in self._cancel else "budget_exceeded"
                for step in ready:
                    attempt_id = self.ledger.start_attempt(
                        task_id, step.step_id, plan_version, run_token)
                    receipt = ToolReceipt(task_id, step.step_id, attempt_id,
                                          "failed" if budget_hit else "cancelled",
                                          "error.budget.v1" if budget_hit else "cancelled.v1",
                                          reason,
                                          errors=[] if not budget_hit else
                                          [{"type": "budget_exceeded", "budget": budget_hit}])
                    self.ledger.save_receipt(receipt, committed=False, run_token=run_token)
                    receipts.append(receipt)
                break
            with ThreadPoolExecutor(max_workers=4) as pool:
                futs = {}
                for step in ready:
                    committed = self.ledger.committed_receipt(task_id, step.step_id)
                    if committed is not None:
                        receipts.append(committed)
                        done.add(step.step_id)
                        continue
                    attempt_id = self.ledger.start_attempt(
                        task_id, step.step_id, plan_version, run_token)
                    operation_key = self.ledger.attempt_operation_key(attempt_id)
                    call_args = dict(step.args)
                    call_args["_project_id"] = env.project_id
                    futs[pool.submit(self.broker.call, task_id, step.step_id, step.tool,
                                     call_args, step.permissions_used, policy,
                                     attempt_id=attempt_id,
                                     idempotency_key=operation_key)] = step
                for fut in futs:
                    step = futs[fut]
                    receipt = fut.result()
                    self.ledger.save_receipt(receipt, committed=(receipt.status == "succeeded"), run_token=run_token)
                    receipts.append(receipt)
                    with self._lock:
                        for metrics in (self.metrics, self._project_metric(env.project_id)):
                            metrics["tool_calls"] += 1
                            if receipt.status == "failed":
                                metrics["tool_failures"] += 1
                                metrics["errors_by_tool"][step.tool] = \
                                    metrics["errors_by_tool"].get(step.tool, 0) + 1
                            metrics["model_tokens"] += receipt.usage.get("model_tokens", 0)
                    if receipt.status == "succeeded":
                        done.add(step.step_id)
            resolved = {receipt.step_id for receipt in receipts}
            remaining = [s for s in remaining
                         if s.step_id not in done and s.step_id not in resolved]

        with self._lock:
            current = TaskState(self.ledger.get_task(task_id)["state"])
            if task_id in self._cancel or current == TaskState.CANCELLING:
                if current != TaskState.CANCELLING:
                    self.ledger.set_state(task_id, TaskState.CANCELLING, run_token=run_token)
                self.ledger.set_state(task_id, TaskState.CANCELLED, run_token=run_token)
                result = {"task_id": task_id, "state": TaskState.CANCELLED.value,
                          "receipts": [r.to_dict() for r in receipts]}
            elif any(r.status == "failed" for r in receipts):
                self.ledger.set_state(task_id, TaskState.FAILED, run_token=run_token)
                result = verify(task_id, receipts)
                result["state"] = TaskState.FAILED.value
            else:
                if current != TaskState.VERIFYING:
                    self.ledger.set_state(task_id, TaskState.VERIFYING, run_token=run_token)
                result = verify(task_id, receipts)
                result["state"] = TaskState.COMPLETED.value
                self.ledger.set_state(task_id, TaskState.COMPLETED, run_token=run_token)
            self.ledger.set_result(task_id, result, run_token=run_token)
            self._cancel.discard(task_id)

        # Keep network delivery outside the engine lock; terminal state is
        # durable before potentially slow callback attempts begin.
        if result.get("state") == TaskState.COMPLETED.value and self.delivery \
                and env.delivery.get("webhook_url"):
            result["delivery"] = self.delivery.deliver(env.delivery["webhook_url"], result)
            self.ledger.set_result(task_id, result, run_token=run_token)
        if self.model_router and hasattr(self.model_router, "forget_task"):
            self.model_router.forget_task(task_id)
        with self._lock:
            state = result.get("state", task["state"])
            elapsed_ms = int((_time.monotonic() - started) * 1000)
            for metrics in (self.metrics, self._project_metric(env.project_id)):
                metrics["latencies_ms"].append(elapsed_ms)
                metrics["tasks_by_state"][state] = \
                    metrics["tasks_by_state"].get(state, 0) + 1
        return result

    def submit_async(self, task_id: str, grants: list[str]) -> threading.Thread:
        """Run a task on a background worker thread (202 Accepted pattern)."""
        t = threading.Thread(target=self.run, args=(task_id, grants), daemon=True)
        t.start()
        return t

    def recover(self, grants: list[str]) -> list:
        """Recover interrupted tasks without replaying ambiguous side effects.

        Committed receipts are reused. Unknown outcomes require an adapter
        reconciliation callback, a read-only operation, or a guaranteed
        idempotency key; otherwise the task stays recovery-required.
        """
        recovered = []
        for task in self.ledger.list_recoverable_tasks():
            try:
                self.run(task["task_id"], grants, reconcile_recovery=True)
                recovered.append(task["task_id"])
            except Exception:
                pass
        return recovered

    def cancel(self, task_id: str) -> bool:
        """Request cancellation, or immediately cancel a paused approval task.

        Returns False for unknown or terminal tasks instead of retaining stale
        cancellation markers indefinitely.
        """
        with self._lock:
            task = self.ledger.get_task(task_id)
            if task is None:
                return False
            state = TaskState(task["state"])
            if state in {TaskState.COMPLETED, TaskState.FAILED,
                         TaskState.CANCELLED, TaskState.REJECTED}:
                self._cancel.discard(task_id)
                return False
            if state == TaskState.WAITING_APPROVAL:
                self.ledger.set_state(task_id, TaskState.CANCELLED,
                                      expected_state=TaskState.WAITING_APPROVAL)
                for approval in self.ledger.pending_approvals(task_id):
                    self.ledger.save_approval(task_id, approval["step_id"], "cancelled")
                result = {"task_id": task_id, "state": TaskState.CANCELLED.value,
                          "reason": "cancelled by caller"}
                self.ledger.set_result(task_id, result)
                self._cancel.discard(task_id)
                if self.model_router and hasattr(self.model_router, "forget_task"):
                    self.model_router.forget_task(task_id)
                return True
            if state == TaskState.RECOVERY_REQUIRED:
                self.ledger.set_state(task_id, TaskState.CANCELLED,
                                      expected_state=TaskState.RECOVERY_REQUIRED)
                result = copy.deepcopy(task.get("result") or {})
                result.update({"state": TaskState.CANCELLED.value,
                               "reason": "cancelled with unresolved tool outcomes"})
                self.ledger.set_result(task_id, result)
                return True
            self._cancel.add(task_id)
            return True

    def approve(self, task_id: str, decision: str = "approved", grants: list | None = None) -> dict:
        """Decide a pending approval durably, then resume the paused run from
        its persisted state — the action executes exactly once."""
        if decision not in ("approved", "rejected"):
            raise ValueError("decision must be approved or rejected")
        task = self.ledger.get_task(task_id)
        if not task:
            raise LedgerError(f"unknown task {task_id}")
        if task["state"] == TaskState.WAITING_APPROVAL.value:
            target = TaskState.RUNNING if decision == "approved" else TaskState.CANCELLED
            try:
                self.ledger.set_state(task_id, target,
                                      expected_state=TaskState.WAITING_APPROVAL)
            except LedgerError:
                current = self.ledger.get_task(task_id)
                return {"task_id": task_id, "decision": decision,
                        "state": current["state"] if current else "UNKNOWN",
                        "already_resolved": True}
            for approval in self.ledger.pending_approvals(task_id):
                self.ledger.save_approval(task_id, approval["step_id"], decision)
            if decision == "approved":
                result = self.run(task_id, grants or [])
                return {"task_id": task_id, "decision": decision, "result": result}
            result = {"task_id": task_id, "state": TaskState.CANCELLED.value,
                      "reason": "approval rejected"}
            self.ledger.set_result(task_id, result)
            return {"task_id": task_id, "decision": decision,
                    "state": TaskState.CANCELLED.value}
        return {"task_id": task_id, "decision": decision, "state": task["state"]}

    # ---- read paths ----------------------------------------------------
    def get_task(self, task_id: str) -> dict | None:
        return self.ledger.get_task(task_id)

    def get_events(self, task_id: str, after_id: int = 0,
                   limit: int | None = None) -> list:
        return self.ledger.events(task_id, after_id=after_id, limit=limit)

    def get_artifact(self, uri: str) -> bytes | None:
        return self.evidence.get_artifact(uri)
