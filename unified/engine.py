"""Engine: ties gateway-facing operations to ledger, policy, planner,
scheduler, broker and verifier. Executes the canonical request path from
manual 1.4: gateway -> ledger -> plan -> broker -> evidence -> verifier.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import math
import multiprocessing
import threading
import time

from .broker import CapabilityBroker
from .contracts import (
    DEFAULT_BUDGETS,
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
from .workers import WorkerDispatch, dispatch_isolated


def _new_metrics() -> dict:
    return {"tasks_by_state": {}, "tool_calls": 0, "tool_failures": 0,
            "model_tokens": 0, "errors_by_tool": {}, "latencies_ms": []}


def _normalize_budgets(budgets: dict | None) -> dict:
    if budgets is not None and not isinstance(budgets, dict):
        raise ValueError("budgets must be an object")
    unknown = set(budgets or {}) - set(DEFAULT_BUDGETS)
    if unknown:
        raise ValueError(f"unknown budget(s): {', '.join(sorted(unknown))}")
    out = {**DEFAULT_BUDGETS, **(budgets or {})}
    for key in DEFAULT_BUDGETS:
        value = out[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)):
            raise ValueError(f"budget {key} must be a finite number")
        if key != "time_seconds" and int(value) != value:
            raise ValueError(f"budget {key} must be an integer")
        if key != "time_seconds":
            out[key] = int(value)
        if key == "memory_mb" and value <= 0:
            raise ValueError("budget memory_mb must be greater than zero")
        if key != "time_seconds" and key != "cpu_seconds" and value < 0:
            raise ValueError(f"budget {key} must not be negative")
        if key == "cpu_seconds" and value < 0:
            raise ValueError("budget cpu_seconds must not be negative")
    return out


def _request_fingerprint(env: TaskEnvelope) -> str:
    """Canonical identity for one project-scoped idempotent submission."""
    canonical = json.dumps({
        "request": env.request,
        "inputs": env.inputs,
        "permissions": env.permissions,
        "budgets": _normalize_budgets(env.budgets),
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
        self._cancel_events: dict[str, object] = {}
        # Per-task concurrency is also capped by a process-wide dispatch gate,
        # so many concurrently running tasks cannot multiply the configured
        # worker pool without bound.
        self._tool_slots = threading.BoundedSemaphore(DEFAULT_BUDGETS["concurrency"])
        self._model_slots = threading.BoundedSemaphore(
            max(1, int(getattr(model_router, "max_concurrent", 1))))
        self._lock = threading.RLock()
        self.metrics = _new_metrics()  # aggregate, internal/operator use
        self._project_metrics: dict[str, dict] = {}

    def _cancel_event(self, task_id: str):
        with self._lock:
            event = self._cancel_events.get(task_id)
            if event is None:
                event = multiprocessing.get_context("fork").Event()
                self._cancel_events[task_id] = event
            return event

    def _clear_cancel_signal(self, task_id: str) -> None:
        with self._lock:
            self._cancel.discard(task_id)
            event = self._cancel_events.pop(task_id, None)
            if event is not None:
                event.clear()

    def metrics_for_project(self, project_id: str) -> dict:
        """Return project usage reconstructed from durable attempts and receipts."""
        tools = self.broker.list_tools()
        resource_effectful = {item["name"] for item in tools
                              if item.get("resource_effectful")}
        billable = {item["name"] for item in tools if item.get("billable")}
        external = {item["name"] for item in tools
                    if item.get("side_effect_class") == "external_effect"}
        return self.ledger.usage_metrics(project_id, resource_effectful,
                                         billable, external)

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
                           budgets=_normalize_budgets(budgets))
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
            reconciliation_errors = []
            for attempt in self.ledger.unresolved_attempts(task_id):
                spec = self.broker.get(attempt["tool"])
                attempt_args = json.loads(attempt["args"])
                attempt_args["_project_id"] = env.project_id
                permissions = json.loads(attempt["permissions_used"])
                operation_key = attempt.get("operation_key")
                if not operation_key:
                    continue
                outcome, receipt, reconciliation_error = self.broker.reconcile(
                    task_id, attempt["step_id"], attempt["attempt_id"],
                    attempt["tool"], attempt_args, permissions, policy, operation_key)
                if reconciliation_error:
                    reconciliation_errors.append({
                        "attempt_id": attempt["attempt_id"],
                        "tool": attempt["tool"],
                        "error": reconciliation_error,
                    })
                if outcome == "applied":
                    self.ledger.resolve_unknown_attempt(
                        attempt["attempt_id"], "applied", run_token, receipt)
                elif outcome == "not_applied":
                    self.ledger.resolve_unknown_attempt(
                        attempt["attempt_id"], "retryable", run_token)
                elif spec and (spec.repeatable or
                               (spec.side_effect_class == "read_only"
                                and not spec.resource_effectful and not spec.billable)
                               or (spec.supports_idempotency
                                   and spec.idempotency_qualified
                                   and bool(spec.idempotency_evidence))):
                    # Key propagation alone is not replay qualification; require
                    # a declared service-side contract plus evidence reference.
                    self.ledger.resolve_unknown_attempt(
                        attempt["attempt_id"], "retryable", run_token)
            still_unknown = self.ledger.unresolved_attempts(task_id)
            if still_unknown:
                prior_result = task.get("result") or {}
                result = {"task_id": task_id,
                          "state": TaskState.RECOVERY_REQUIRED.value,
                          "reason": "one or more tool outcomes remain unresolved",
                          "cancellation_requested": bool(
                              prior_result.get("cancellation_requested")),
                          "unresolved_attempts": [
                              {"attempt_id": a["attempt_id"], "step_id": a["step_id"],
                               "tool": a["tool"],
                               "operation_key": a.get("operation_key")}
                              for a in still_unknown]}
                if reconciliation_errors:
                    result["reconciliation_errors"] = reconciliation_errors
                self.ledger.set_recovery_result(task_id, result, run_token)
                return self.ledger.get_task(task_id)["result"]
            try:
                recovery_action = self.ledger.finish_recovery(task_id, run_token)
            except LedgerError:
                latest = self.ledger.get_task(task_id)
                if latest and latest["state"] == TaskState.CANCELLED.value:
                    return latest.get("result") or {
                        "task_id": task_id, "state": TaskState.CANCELLED.value}
                raise
            if recovery_action == "unresolved":
                latest = self.ledger.get_task(task_id)
                return latest.get("result") or {
                    "task_id": task_id, "state": TaskState.RECOVERY_REQUIRED.value}
            if recovery_action == "cancelled":
                result = {"task_id": task_id, "state": TaskState.CANCELLED.value,
                          "reason": "cancelled after reconciling the in-flight operation",
                          "receipts": [r.to_dict() for r in self.ledger.get_receipts(task_id)]}
                self.ledger.set_result(task_id, result, run_token=run_token)
                return result
            cur = TaskState.RUNNING
        if cur == TaskState.CANCELLING:
            self.ledger.set_state(task_id, TaskState.CANCELLED, run_token=run_token)
            result = {"task_id": task_id, "state": TaskState.CANCELLED.value,
                      "reason": "recovered cancellation"}
            self.ledger.set_result(task_id, result, run_token=run_token)
            self._clear_cancel_signal(task_id)
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

        budgets = _normalize_budgets(env.budgets)
        execution_started_at = self.ledger.begin_execution(task_id, run_token)
        deadline_at = execution_started_at + float(budgets["time_seconds"])

        # Receipts are the durable success authority; step state alone cannot
        # certify completion. Reconcile legacy receipt rows to their runtime state.
        receipts: list[ToolReceipt] = self.ledger.get_receipts(task_id)
        successful_receipt_ids = {r.step_id for r in receipts if r.status == "succeeded"}
        for stored_step in self.ledger.get_steps(task_id):
            if (stored_step.step_id in successful_receipt_ids
                    and stored_step.state in {StepState.PENDING, StepState.READY,
                                              StepState.RUNNING}):
                self.ledger.set_step_state(task_id, stored_step.step_id,
                                           StepState.SUCCEEDED, run_token=run_token)
        steps = self.ledger.get_plan(task_id)["steps"]
        done: set[str] = set(successful_receipt_ids)
        if self.model_router and hasattr(self.model_router, "restore_task_usage"):
            persisted_tokens = sum(int(r.usage.get("model_tokens", 0)) for r in receipts)
            self.model_router.restore_task_usage(task_id, persisted_tokens)
        import time as _time
        from concurrent.futures import ThreadPoolExecutor
        started = _time.monotonic()
        step_tools = {step.step_id: step.tool for step in self.ledger.get_steps(task_id)}
        previous_attempts = self.ledger.get_attempts(task_id)
        tool_calls_used = len(previous_attempts)
        model_calls_used = sum(step_tools.get(a["step_id"]) == "model.complete"
                               for a in previous_attempts)
        external_requests_used = sum(
            bool(self.broker.get(step_tools.get(a["step_id"], ""))
                 and self.broker.get(step_tools.get(a["step_id"], "")).external_request)
            for a in previous_attempts)
        cpu_seconds_used = self.ledger.task_cpu_seconds_used(task_id)
        max_concurrency = min(4, budgets["concurrency"],
                              max(1, budgets["memory_mb"] // 64))
        resolved_steps = {receipt.step_id for receipt in receipts}
        remaining = ([] if cur == TaskState.VERIFYING else
                     [s for s in steps if s.step_id not in done
                      and s.step_id not in resolved_steps
                      and s.state != StepState.SUCCEEDED])
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
                requires_approval = bool(spec and (
                    spec.requires_approval if spec.requires_approval is not None else
                    spec.side_effect_class in {"sandbox_write", "external_effect"}))
                if requires_approval:
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
                        self._clear_cancel_signal(task_id)
                        if self.model_router and hasattr(self.model_router, "forget_task"):
                            self.model_router.forget_task(task_id)
                        return result
                    self.ledger.set_state(task_id, TaskState.WAITING_APPROVAL, run_token=run_token)
                    result = {"task_id": task_id, "state": TaskState.WAITING_APPROVAL.value,
                              "pending_step": pause.step_id, "tool": pause.tool}
                    self.ledger.set_result(task_id, result, run_token=run_token)
                    return result
            # A previously committed step is free: reuse its receipt before
            # reserving any new tool-call budget.
            for step in ready:
                committed = self.ledger.committed_receipt(task_id, step.step_id)
                if committed is not None:
                    receipts.append(committed)
                    done.add(step.step_id)
            ready = [step for step in ready if step.step_id not in done]
            if not ready:
                remaining = [s for s in remaining if s.step_id not in done]
                continue

            def record_budget_failure(step, budget_name):
                attempt_id = self.ledger.start_attempt(
                    task_id, step.step_id, plan_version, run_token)
                receipt = ToolReceipt(
                    task_id, step.step_id, attempt_id, "failed", "error.budget.v1",
                    f"{budget_name} budget exceeded",
                    errors=[{"type": "budget_exceeded", "budget": budget_name}])
                self.ledger.save_receipt(receipt, committed=False, run_token=run_token)
                receipts.append(receipt)

            if task_id in self._cancel:
                for step in ready:
                    attempt_id = self.ledger.start_attempt(
                        task_id, step.step_id, plan_version, run_token)
                    receipt = ToolReceipt(task_id, step.step_id, attempt_id,
                                          "cancelled", "cancelled.v1", "cancelled before dispatch")
                    self.ledger.save_receipt(receipt, committed=False, run_token=run_token)
                    receipts.append(receipt)
                break
            if time.time() >= deadline_at:
                record_budget_failure(ready[0], "time_seconds")
                break
            if max_concurrency <= 0:
                record_budget_failure(ready[0], "concurrency")
                break
            cpu_remaining = budgets["cpu_seconds"] - cpu_seconds_used
            if cpu_remaining <= 0:
                record_budget_failure(ready[0], "cpu_seconds")
                break

            dispatch_steps = []
            blocked_budget = None
            model_reservations = 0
            external_reservations = 0
            for step in ready:
                if tool_calls_used + len(dispatch_steps) >= budgets["tool_calls"]:
                    blocked_budget = (step, "tool_calls")
                    break
                spec = self.broker.get(step.tool)
                if (step.tool == "model.complete"
                        and model_calls_used + model_reservations >= budgets["model_calls"]):
                    blocked_budget = (step, "model_calls")
                    continue
                if (spec and spec.external_request
                        and external_requests_used + external_reservations
                        >= budgets["external_requests"]):
                    blocked_budget = (step, "external_requests")
                    continue
                dispatch_steps.append(step)
                model_reservations += int(step.tool == "model.complete")
                external_reservations += int(bool(spec and spec.external_request))
                # Reserve one model call per wave so the next call receives the
                # exact cumulative token balance from the preceding receipt.
                if step.tool == "model.complete" or len(dispatch_steps) >= max_concurrency:
                    break
            if not dispatch_steps:
                if blocked_budget:
                    record_budget_failure(*blocked_budget)
                else:
                    break
                break

            cpu_per_worker = cpu_remaining / len(dispatch_steps)
            memory_per_worker = max(1, budgets["memory_mb"] // len(dispatch_steps))
            cancel_signal = self._cancel_event(task_id)

            def call_with_global_slot(*args, deadline_at, cancel_check,
                                      worker_cpu_seconds, memory_mb, **kwargs):
                while not self._tool_slots.acquire(timeout=0.05):
                    if cancel_check():
                        return WorkerDispatch(ToolReceipt(
                            args[0], args[1], kwargs.get("attempt_id", ""),
                            "cancelled", "cancelled.v1",
                            "cancelled while waiting for a tool slot"), 0.0)
                    if time.time() >= deadline_at:
                        return WorkerDispatch(ToolReceipt(
                            args[0], args[1], kwargs.get("attempt_id", ""), "failed",
                            "error.budget.v1", "execution time budget exceeded",
                            errors=[{"type": "budget_exceeded", "budget": "time_seconds"}]), 0.0)
                model_slot_acquired = False
                try:
                    if cancel_check():
                        return WorkerDispatch(ToolReceipt(
                            args[0], args[1], kwargs.get("attempt_id", ""),
                            "cancelled", "cancelled.v1",
                            "cancelled before tool dispatch"), 0.0)
                    if time.time() >= deadline_at:
                        return WorkerDispatch(ToolReceipt(
                            args[0], args[1], kwargs.get("attempt_id", ""), "failed",
                            "error.budget.v1", "execution time budget exceeded",
                            errors=[{"type": "budget_exceeded", "budget": "time_seconds"}]), 0.0)
                    if args[2] == "model.complete":
                        while not model_slot_acquired:
                            model_slot_acquired = self._model_slots.acquire(timeout=0.05)
                            if model_slot_acquired:
                                break
                            if cancel_check():
                                return WorkerDispatch(ToolReceipt(
                                    args[0], args[1], kwargs.get("attempt_id", ""),
                                    "cancelled", "cancelled.v1",
                                    "cancelled while waiting for model capacity"), 0.0)
                            if time.time() >= deadline_at:
                                return WorkerDispatch(ToolReceipt(
                                    args[0], args[1], kwargs.get("attempt_id", ""),
                                    "failed", "error.budget.v1",
                                    "model capacity deadline exceeded",
                                    errors=[{"type": "budget_exceeded",
                                             "budget": "time_seconds"}]), 0.0)
                    call = self.broker.call
                    parameters = inspect.signature(call).parameters.values()
                    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD
                                         for p in parameters)
                    names = {p.name for p in parameters}
                    extra = {}
                    if accepts_kwargs or "deadline_at" in names:
                        extra["deadline_at"] = deadline_at
                    if accepts_kwargs or "cancel_check" in names:
                        extra["cancel_check"] = cancel_check
                    call_kwargs = {**kwargs, **extra}
                    spec = self.broker.get(args[2])
                    return dispatch_isolated(
                        self.broker, args, call_kwargs, deadline_at=deadline_at,
                        cancel_check=cancel_check, cpu_seconds=worker_cpu_seconds,
                        memory_mb=memory_mb,
                        mutating=bool(spec and (spec.side_effect_class != "read_only"
                                                or spec.resource_effectful)),
                        on_dispatch=lambda: self.ledger.mark_attempt_dispatched(
                            kwargs["attempt_id"], args[0], run_token))
                finally:
                    if model_slot_acquired:
                        self._model_slots.release()
                    self._tool_slots.release()

            with ThreadPoolExecutor(max_workers=min(max_concurrency, len(dispatch_steps))) as pool:
                futs = {}
                for step in dispatch_steps:
                    attempt_id = self.ledger.start_attempt(
                        task_id, step.step_id, plan_version, run_token)
                    operation_key = self.ledger.attempt_operation_key(attempt_id)
                    call_args = dict(step.args)
                    call_args["_project_id"] = env.project_id
                    call_args.setdefault("_task_id", task_id)
                    if step.tool == "model.complete":
                        # Each supervised model call has a fresh child copy of
                        # the router. Engine receipts are the durable cumulative
                        # counter, so pass this invocation's remaining balance.
                        persisted_tokens = sum(
                            int(r.usage.get("model_tokens", 0)) for r in receipts)
                        call_args["budget_tokens"] = max(
                            0, budgets["model_tokens"] - persisted_tokens)
                        call_args["_supervised_model"] = True
                    remaining_seconds = max(0.0, deadline_at - time.time())
                    tool_deadline = min(deadline_at, time.time() + 45.0)
                    cancel_check = lambda tid=task_id, signal=cancel_signal: (
                        tid in self._cancel or signal.is_set())
                    futs[pool.submit(call_with_global_slot, task_id, step.step_id, step.tool,
                                     call_args, step.permissions_used, policy,
                                     deadline_ms=max(1, int(remaining_seconds * 1000)),
                                     attempt_id=attempt_id, idempotency_key=operation_key,
                                     deadline_at=tool_deadline, cancel_check=cancel_check,
                                     worker_cpu_seconds=cpu_per_worker,
                                     memory_mb=memory_per_worker)] = step
                    tool_calls_used += 1
                    model_calls_used += int(step.tool == "model.complete")
                    spec = self.broker.get(step.tool)
                    external_requests_used += int(bool(spec and spec.external_request))
                ambiguous_worker_exit = False
                for fut in futs:
                    step = futs[fut]
                    dispatch = fut.result()
                    if isinstance(dispatch, ToolReceipt):  # compatibility for patched brokers
                        dispatch = WorkerDispatch(dispatch, 0.0)
                    if dispatch.cpu_seconds or dispatch.peak_rss_bytes:
                        self.ledger.add_task_worker_usage(
                            task_id, run_token, dispatch.cpu_seconds,
                            dispatch.peak_rss_bytes)
                        cpu_seconds_used += dispatch.cpu_seconds
                    if dispatch.receipt is None:
                        ambiguous_worker_exit = ambiguous_worker_exit or dispatch.ambiguous
                        continue
                    receipt = dispatch.receipt
                    self.ledger.save_receipt(receipt, committed=(receipt.status == "succeeded"), run_token=run_token)
                    receipts.append(receipt)
                    if (step.tool == "model.complete" and self.model_router
                            and hasattr(self.model_router, "restore_task_usage")):
                        self.model_router.restore_task_usage(
                            task_id, sum(int(r.usage.get("model_tokens", 0)) for r in receipts))
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
                if ambiguous_worker_exit:
                    cancellation_requested = task_id in self._cancel or cancel_signal.is_set()
                    unresolved = self.ledger.mark_interrupted_attempts(task_id, run_token)
                    result = {"task_id": task_id,
                              "state": TaskState.RECOVERY_REQUIRED.value,
                              "reason": "isolated worker terminated during an operation",
                              "cancellation_requested": cancellation_requested,
                              "unresolved_attempts": unresolved}
                    self.ledger.set_result(task_id, result, run_token=run_token)
                    return result
            resolved = {receipt.step_id for receipt in receipts}
            remaining = [s for s in remaining
                         if s.step_id not in done and s.step_id not in resolved]

        with self._lock:
            current = TaskState(self.ledger.get_task(task_id)["state"])
            receipt_cancelled = any(receipt.status == "cancelled" for receipt in receipts)
            if (task_id in self._cancel or current == TaskState.CANCELLING
                    or receipt_cancelled):
                if current != TaskState.CANCELLING:
                    self.ledger.set_state(task_id, TaskState.CANCELLING, run_token=run_token)
                self.ledger.set_state(task_id, TaskState.CANCELLED, run_token=run_token)
                result = {"task_id": task_id, "state": TaskState.CANCELLED.value,
                          "receipts": [r.to_dict() for r in receipts],
                          "cancelled_steps": [r.step_id for r in receipts
                                              if r.status == "cancelled"]}
            else:
                step_states = {step.step_id: step.state
                               for step in self.ledger.get_steps(task_id)}
                covered_by_failure = {r.step_id for r in receipts
                                      if r.status in {"failed", "cancelled"}}
                successful_receipt_ids = {r.step_id for r in receipts
                                          if r.status == "succeeded"}
                incomplete = [step.step_id for step in steps
                              if (step_states.get(step.step_id) != StepState.SUCCEEDED
                                  or step.step_id not in successful_receipt_ids)
                              and step.step_id not in covered_by_failure]
                failed_receipt = bool(covered_by_failure)
                if incomplete or failed_receipt:
                    self.ledger.set_state(task_id, TaskState.FAILED, run_token=run_token)
                    result = verify(task_id, receipts)
                    result["state"] = TaskState.FAILED.value
                    if incomplete:
                        result["incomplete_steps"] = incomplete
                        result.setdefault("failures", []).extend({
                            "step_id": step_id,
                            "errors": [{"type": "incomplete_step",
                                        "detail": "step did not reach SUCCEEDED"}],
                        } for step_id in incomplete)
                else:
                    if current != TaskState.VERIFYING:
                        self.ledger.set_state(task_id, TaskState.VERIFYING, run_token=run_token)
                    result = verify(task_id, receipts)
                    result["state"] = TaskState.COMPLETED.value
                    self.ledger.set_state(task_id, TaskState.COMPLETED, run_token=run_token)
            self.ledger.set_result(task_id, result, run_token=run_token)
            self._clear_cancel_signal(task_id)

        exceeded = []
        for receipt in receipts:
            for error in receipt.errors:
                if error.get("type") == "budget_exceeded":
                    item = {"step_id": receipt.step_id,
                            "budget": error.get("budget", "unknown")}
                    if item not in exceeded:
                        exceeded.append(item)
        if exceeded:
            result["budget_exceeded"] = exceeded
            self.ledger.set_result(task_id, result, run_token=run_token)

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

    def recover(self, grants: list[str]) -> list[dict]:
        """Recover tasks and durably report every outcome and failure.

        A successful terminal recovery is ``completed``; a task that was
        resumed but remains active or pauses for approval is ``resumed``.
        Unknown operations remain ``still_unresolved`` and failures retain
        their exception type/message in the recovery ledger.
        """
        recovery_id = new_id("R")
        outcomes = []
        try:
            tasks = self.ledger.list_recoverable_tasks()
        except Exception as exc:
            outcome = {
                "recovery_id": recovery_id, "task_id": None,
                "outcome": "recovery_failed", "state": None,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
            self.ledger.record_recovery_outcome(
                recovery_id, None, outcome["outcome"], {"error": outcome["error"]})
            return [outcome]

        for candidate in tasks:
            task_id = candidate["task_id"]
            outcome = {
                "recovery_id": recovery_id,
                "task_id": task_id,
                "initial_state": candidate.get("state"),
                "state": None,
                "outcome": "recovery_failed",
            }
            try:
                result = self.run(task_id, grants, reconcile_recovery=True)
                current = self.ledger.get_task(task_id)
                final_state = current["state"] if current else result.get("state")
                outcome["state"] = final_state
                if result.get("already_running"):
                    outcome["outcome"] = "already_running"
                elif final_state == TaskState.RECOVERY_REQUIRED.value:
                    outcome["outcome"] = "still_unresolved"
                    outcome["unresolved_attempts"] = result.get(
                        "unresolved_attempts", self.ledger.unresolved_attempts(task_id))
                    if result.get("reason"):
                        outcome["reason"] = result["reason"]
                elif final_state in {TaskState.COMPLETED.value, TaskState.CANCELLED.value}:
                    outcome["outcome"] = "completed"
                elif final_state == TaskState.FAILED.value:
                    outcome["outcome"] = "recovery_failed"
                    outcome["error"] = {
                        "type": "task_failed_during_recovery",
                        "message": (result.get("reason") or
                                    result.get("error") or "task ended in FAILED state"),
                    }
                else:
                    outcome["outcome"] = "resumed"
                outcome["result"] = result
            except Exception as exc:
                current = self.ledger.get_task(task_id)
                outcome["state"] = current["state"] if current else None
                outcome["error"] = {"type": type(exc).__name__, "message": str(exc)}
            self.ledger.record_recovery_outcome(
                recovery_id, task_id, outcome["outcome"],
                {key: value for key, value in outcome.items()
                 if key not in {"recovery_id", "task_id", "outcome"}})
            outcomes.append(outcome)
        return outcomes

    def reconcile_attempt(self, task_id: str, attempt_id: str, outcome: str,
                          receipt_data: dict | None = None,
                          grants: list[str] | None = None) -> dict:
        """Apply an explicit operator decision to one unresolved attempt.

        ``applied`` requires a concise operator-supplied receipt; ``not_applied``
        authorizes a retry after a human has verified no side effect occurred.
        """
        if outcome not in {"applied", "not_applied"}:
            raise ValueError("outcome must be applied or not_applied")
        task = self.ledger.get_task(task_id)
        if task is None:
            raise LedgerError(f"unknown task {task_id}")
        if task["state"] != TaskState.RECOVERY_REQUIRED.value:
            raise LedgerError("task is not recovery-required")
        run_token = self.ledger.claim_run(task_id)
        if run_token is None:
            current = self.ledger.get_task(task_id)
            return {"task_id": task_id, "state": current["state"],
                    "already_running": True}
        try:
            unresolved = {attempt["attempt_id"]: attempt
                          for attempt in self.ledger.unresolved_attempts(task_id)}
            attempt = unresolved.get(attempt_id)
            if attempt is None:
                raise LedgerError(f"attempt {attempt_id} is not unresolved for {task_id}")
            if outcome == "not_applied":
                self.ledger.resolve_unknown_attempt(attempt_id, "retryable", run_token)
            else:
                data = receipt_data or {}
                status = data.get("status")
                result_type = data.get("result_type")
                summary = data.get("summary")
                if status not in {"succeeded", "failed"}:
                    raise ValueError("applied receipt status must be succeeded or failed")
                if not isinstance(result_type, str) or not result_type.strip():
                    raise ValueError("applied receipt requires result_type")
                if not isinstance(summary, str):
                    raise ValueError("applied receipt requires a summary string")
                for field_name in ("data", "usage"):
                    if field_name in data and not isinstance(data[field_name], dict):
                        raise ValueError(f"receipt {field_name} must be an object")
                for field_name in ("artifacts", "evidence", "errors"):
                    if field_name in data and not isinstance(data[field_name], list):
                        raise ValueError(f"receipt {field_name} must be an array")
                receipt = ToolReceipt(
                    task_id=task_id, step_id=attempt["step_id"],
                    attempt_id=attempt_id, status=status, result_type=result_type,
                    summary=summary, data=data.get("data", {}),
                    artifacts=data.get("artifacts", []),
                    evidence=data.get("evidence", []), errors=data.get("errors", []),
                    usage=data.get("usage", {}))
                self.ledger.resolve_unknown_attempt(
                    attempt_id, "applied", run_token, receipt)
        finally:
            self.ledger.release_run_claim(task_id, run_token)
        return self.run(task_id, grants or [], reconcile_recovery=True)

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
                self._clear_cancel_signal(task_id)
                return False
            if state == TaskState.WAITING_APPROVAL:
                self.ledger.set_state(task_id, TaskState.CANCELLED,
                                      expected_state=TaskState.WAITING_APPROVAL)
                for approval in self.ledger.pending_approvals(task_id):
                    self.ledger.save_approval(task_id, approval["step_id"], "cancelled")
                result = {"task_id": task_id, "state": TaskState.CANCELLED.value,
                          "reason": "cancelled by caller"}
                self.ledger.set_result(task_id, result)
                self._clear_cancel_signal(task_id)
                if self.model_router and hasattr(self.model_router, "forget_task"):
                    self.model_router.forget_task(task_id)
                return True
            if state == TaskState.RECOVERY_REQUIRED:
                try:
                    self.ledger.request_recovery_cancel(task_id)
                    return True
                except LedgerError:
                    latest = self.ledger.get_task(task_id)
                    if not latest or latest["state"] in {
                            TaskState.COMPLETED.value, TaskState.FAILED.value,
                            TaskState.CANCELLED.value, TaskState.REJECTED.value}:
                        return False
                    self._cancel.add(task_id)
                    self._cancel_event(task_id).set()
                    return True
            self._cancel.add(task_id)
            self._cancel_event(task_id).set()
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
