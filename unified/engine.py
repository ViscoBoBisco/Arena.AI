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
    def __init__(self, ledger: Ledger, broker: CapabilityBroker, evidence: EvidenceStore):
        self.ledger = ledger
        self.broker = broker
        self.evidence = evidence
        self._cancel = set()
        self._lock = threading.RLock()

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
        self.ledger.set_state(task_id, TaskState.PLANNED)
        self.ledger.set_state(task_id, TaskState.RUNNING)

        by_id = {s.step_id: s for s in steps}
        done: set[str] = set()
        receipts: list[ToolReceipt] = []
        for step in steps:  # simple topological order (planner emits ordered DAGs)
            if any(dep not in done for dep in step.depends_on):
                continue
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
            self._cancel.discard(task_id)
        return result

    def cancel(self, task_id: str) -> None:
        with self._lock:
            self._cancel.add(task_id)

    def approve(self, task_id: str) -> dict:
        task = self.ledger.get_task(task_id)
        if task and task["state"] == TaskState.WAITING_APPROVAL.value:
            self.ledger.set_state(task_id, TaskState.RUNNING)
        return {"task_id": task_id, "approved": True}

    # ---- read paths ----------------------------------------------------
    def get_task(self, task_id: str) -> dict | None:
        return self.ledger.get_task(task_id)

    def get_events(self, task_id: str) -> list:
        return self.ledger.events(task_id)

    def get_artifact(self, uri: str) -> bytes | None:
        return self.evidence.get_artifact(uri)
