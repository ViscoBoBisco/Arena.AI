"""Engine: ties gateway-facing operations to ledger, policy, planner,
scheduler, broker and verifier. Executes the canonical request path from
manual 1.4: gateway -> ledger -> plan -> broker -> evidence -> verifier.
"""
from __future__ import annotations

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


class Engine:
    def __init__(self, ledger: Ledger, broker: CapabilityBroker, evidence: EvidenceStore,
                 model_router=None, roles=None, automation=None, memory=None):
        self.ledger = ledger
        self.broker = broker
        self.evidence = evidence
        self.model_router = model_router
        self.roles = roles
        self.automation = automation
        self.memory = memory
        self._cancel = set()
        self._lock = threading.RLock()
        self.metrics = {"tasks_by_state": {}, "tool_calls": 0,
                        "tool_failures": 0, "model_tokens": 0}

    # ---- admission (manual 3.1 / 3.3) --------------------------------
    def submit(self, project_id: str, idempotency_key: str, request: dict,
               permissions: list[str], grants: list[str],
               inputs: list | None = None, budgets: dict | None = None) -> dict:
        """POST /v1/tasks. Duplicate idempotency key returns the same task ID
        without duplicate execution (manual 5.10, test 1)."""
        existing = self.ledger.find_by_idempotency(project_id, idempotency_key)
        if existing:
            return {"task_id": existing, "duplicate": True,
                    "state": self.ledger.get_task(existing)["state"]}
        env = TaskEnvelope(task_id=new_id("T"), idempotency_key=idempotency_key,
                           project_id=project_id, request=request,
                           inputs=inputs or [], permissions=permissions,
                           budgets=budgets or {"model_calls": 12, "tool_calls": 30,
                                              "time_seconds": 600})
        self.ledger.create_task(env)
        policy = PolicyEngine(set(grants))
        try:
            policy.admit(permissions)  # evaluate at planning (manual 3.3)
        except PolicyViolation as e:
            self.ledger.set_state(env.task_id, TaskState.REJECTED)
            self.ledger.set_result(env.task_id, {"error": str(e)})
            return {"task_id": env.task_id, "duplicate": False,
                    "state": TaskState.REJECTED.value, "error": str(e)}
        self.ledger.set_state(env.task_id, TaskState.ADMITTED)
        return {"task_id": env.task_id, "duplicate": False,
                "state": TaskState.ADMITTED.value}

    # ---- execution -----------------------------------------------------
    def run(self, task_id: str, grants: list[str]) -> dict:
        """Plan and execute a task synchronously through the canonical path."""
        task = self.ledger.get_task(task_id)
        if task is None:
            raise LedgerError(f"unknown task {task_id}")
        if task["state"] == TaskState.REJECTED.value:
            return task["result"]
        env = TaskEnvelope(**task["envelope"])
        policy = PolicyEngine(set(grants))
        steps = plan(env)
        self.ledger.save_steps(task_id, steps)
        cur = TaskState(task["state"])
        if cur == TaskState.ADMITTED:
            self.ledger.set_state(task_id, TaskState.PLANNED)
            self.ledger.set_state(task_id, TaskState.RUNNING)
        elif cur == TaskState.WAITING_APPROVAL:
            self.ledger.set_state(task_id, TaskState.RUNNING)  # resumed after approval

        # Resumable: steps already SUCCEEDED (after approval pause or restart)
        # are not re-executed.
        done: set[str] = {s.step_id for s in self.ledger.get_steps(task_id)
                          if s.state == StepState.SUCCEEDED}
        receipts: list[ToolReceipt] = self.ledger.get_receipts(task_id)
        import time as _time
        started = _time.monotonic()
        budgets = env.budgets or {}
        for step in steps:  # simple topological order (planner emits ordered DAGs)
            if step.step_id in done or any(dep not in done for dep in step.depends_on):
                continue
            # Hard per-task budgets: enforced at step level with graceful
            # termination and a typed receipt (never a silent overrun).
            if len(receipts) >= budgets.get("tool_calls", 30):
                receipt = ToolReceipt(task_id, step.step_id, new_id("A"), "failed",
                                      "error.budget.v1", "tool_calls budget exhausted",
                                      errors=[{"type": "budget_exceeded", "budget": "tool_calls"}])
                self.ledger.save_receipt(receipt, committed=False)
                self.ledger.set_step_state(task_id, step.step_id, StepState.FAILED)
                receipts.append(receipt)
                break
            if _time.monotonic() - started > budgets.get("time_seconds", 600):
                receipt = ToolReceipt(task_id, step.step_id, new_id("A"), "failed",
                                      "error.budget.v1", "time_seconds budget exhausted",
                                      errors=[{"type": "budget_exceeded", "budget": "time_seconds"}])
                self.ledger.save_receipt(receipt, committed=False)
                self.ledger.set_step_state(task_id, step.step_id, StepState.FAILED)
                receipts.append(receipt)
                break
            # Durable human-in-the-loop: side-effecting steps require an
            # approved record; the run pauses (not the process) and resumes
            # from persisted state on approval (manual 5.4).
            spec = self.broker.get(step.tool)
            if spec and spec.side_effect_class != "read_only":
                decision = self.ledger.get_approval(task_id, step.step_id)
                if decision is None:
                    self.ledger.save_approval(task_id, step.step_id, "pending")
                if decision == "rejected":
                    self.ledger.set_state(task_id, TaskState.CANCELLING)
                    self.ledger.set_state(task_id, TaskState.CANCELLED)
                    result = {"task_id": task_id, "state": TaskState.CANCELLED.value,
                              "reason": "approval rejected", "pending_step": step.step_id}
                    self.ledger.set_result(task_id, result)
                    return result
                if decision != "approved":
                    self.ledger.set_state(task_id, TaskState.WAITING_APPROVAL)
                    result = {"task_id": task_id, "state": TaskState.WAITING_APPROVAL.value,
                              "pending_step": step.step_id, "tool": step.tool}
                    self.ledger.set_result(task_id, result)
                    return result
            if task_id in self._cancel:
                self.ledger.set_step_state(task_id, step.step_id, StepState.CANCELLED)
                receipts.append(ToolReceipt(task_id, step.step_id, new_id("A"),
                                            "cancelled", "cancelled.v1", "cancelled"))
                continue
            # Manual 3.2 invariant: committed receipt is reused, never repeated.
            committed = self.ledger.committed_receipt(task_id, step.step_id)
            if committed is not None:
                receipts.append(committed)
                done.add(step.step_id)
                continue
            self.ledger.set_step_state(task_id, step.step_id, StepState.RUNNING)
            receipt = self.broker.call(task_id, step.step_id, step.tool, step.args,
                                       step.permissions_used, policy)
            self.ledger.save_receipt(receipt, committed=(receipt.status == "succeeded"))
            self.ledger.set_step_state(
                task_id, step.step_id,
                StepState.SUCCEEDED if receipt.status == "succeeded" else StepState.FAILED)
            receipts.append(receipt)
            with self._lock:
                self.metrics["tool_calls"] += 1
                if receipt.status == "failed":
                    self.metrics["tool_failures"] += 1
                self.metrics["model_tokens"] += receipt.usage.get("model_tokens", 0)
            if receipt.status == "succeeded":
                done.add(step.step_id)

        if task_id in self._cancel:
            self.ledger.set_state(task_id, TaskState.CANCELLING)
            self.ledger.set_state(task_id, TaskState.CANCELLED)
            result = {"task_id": task_id, "state": TaskState.CANCELLED.value,
                      "receipts": [r.to_dict() for r in receipts]}
        elif any(r.status == "failed" for r in receipts):
            self.ledger.set_state(task_id, TaskState.FAILED)
            result = verify(task_id, receipts)
            result["state"] = TaskState.FAILED.value
        else:
            self.ledger.set_state(task_id, TaskState.VERIFYING)
            result = verify(task_id, receipts)
            result["state"] = TaskState.COMPLETED.value
            self.ledger.set_state(task_id, TaskState.COMPLETED)
        self.ledger.set_result(task_id, result)
        with self._lock:
            st = task["state"] if result.get("state") is None else result.get("state")
            self.metrics["tasks_by_state"][result.get("state", st)] = \
                self.metrics["tasks_by_state"].get(result.get("state", st), 0) + 1
            self._cancel.discard(task_id)
        return result

    def cancel(self, task_id: str) -> None:
        with self._lock:
            self._cancel.add(task_id)

    def approve(self, task_id: str, decision: str = "approved", grants: list | None = None) -> dict:
        """Decide a pending approval durably, then resume the paused run from
        its persisted state — the action executes exactly once."""
        task = self.ledger.get_task(task_id)
        if not task:
            raise LedgerError(f"unknown task {task_id}")
        for ap in self.ledger.pending_approvals(task_id):
            self.ledger.save_approval(task_id, ap["step_id"], decision)
        if task["state"] == TaskState.WAITING_APPROVAL.value:
            if decision == "approved":
                self.ledger.set_state(task_id, TaskState.RUNNING)
                result = self.run(task_id, grants or [])
                return {"task_id": task_id, "decision": decision, "result": result}
            self.ledger.set_state(task_id, TaskState.CANCELLED)  # legal per 5.4
            return {"task_id": task_id, "decision": decision,
                    "state": TaskState.CANCELLED.value}
        return {"task_id": task_id, "decision": decision, "state": task["state"]}

    # ---- read paths ----------------------------------------------------
    def get_task(self, task_id: str) -> dict | None:
        return self.ledger.get_task(task_id)

    def get_events(self, task_id: str) -> list:
        return self.ledger.events(task_id)

    def get_artifact(self, uri: str) -> bytes | None:
        return self.evidence.get_artifact(uri)
