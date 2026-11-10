"""Canonical contracts from the architecture manual (sections 5.1-5.4).

Task states follow the state machine in manual section 5.4 exactly.
"""
from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field


DEFAULT_BUDGETS = {
    "tool_calls": 30,
    "model_calls": 12,
    "model_tokens": 4096,
    "time_seconds": 600,
    "concurrency": 4,
    "external_requests": 10,
    "cpu_seconds": 60,
    "memory_mb": 512,
}


def new_id(prefix: str) -> str:
    """Create a collision-resistant, server-owned identifier."""
    return f"{prefix}-{uuid.uuid4().hex.upper()}"


class TaskState(str, enum.Enum):
    CREATED = "CREATED"
    ADMITTED = "ADMITTED"
    REJECTED = "REJECTED"
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    COMPLETED = "COMPLETED"


class StepState(str, enum.Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


# Allowed transitions per manual 5.4. No undocumented transition may move a
# failed run directly to completed.
ALLOWED_TRANSITIONS = {
    TaskState.CREATED: {TaskState.ADMITTED, TaskState.REJECTED},
    TaskState.ADMITTED: {TaskState.PLANNED},
    TaskState.PLANNED: {TaskState.RUNNING},
    TaskState.RUNNING: {
        TaskState.VERIFYING,
        TaskState.WAITING_APPROVAL,
        TaskState.CANCELLING,
        TaskState.FAILED,
        TaskState.RECOVERY_REQUIRED,
    },
    TaskState.WAITING_APPROVAL: {TaskState.RUNNING, TaskState.CANCELLED},
    TaskState.CANCELLING: {TaskState.CANCELLED, TaskState.RECOVERY_REQUIRED},
    TaskState.RECOVERY_REQUIRED: {TaskState.RUNNING, TaskState.CANCELLED},
    TaskState.VERIFYING: {TaskState.COMPLETED, TaskState.FAILED},
}
for _t in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED, TaskState.REJECTED):
    ALLOWED_TRANSITIONS[_t] = set()


def can_transition(src: TaskState, dst: TaskState) -> bool:
    return dst in ALLOWED_TRANSITIONS.get(src, set())


def utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class TaskEnvelope:
    """Canonical, versioned task envelope (manual 5.1)."""
    task_id: str
    idempotency_key: str
    project_id: str
    request: dict
    inputs: list = field(default_factory=list)
    permissions: list = field(default_factory=list)
    budgets: dict = field(default_factory=lambda: dict(DEFAULT_BUDGETS))
    delivery: dict = field(default_factory=lambda: {"format": "report_with_evidence"})
    envelope_version: int = 1


@dataclass
class Step:
    step_id: str
    tool: str
    args: dict
    depends_on: list = field(default_factory=list)
    read_only: bool = True
    permissions_used: list = field(default_factory=list)
    state: StepState = StepState.PENDING

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "tool": self.tool,
            "args": self.args,
            "depends_on": self.depends_on,
            "read_only": self.read_only,
            "permissions_used": self.permissions_used,
            "state": self.state.value if isinstance(self.state, StepState) else self.state,
        }


@dataclass
class ToolReceipt:
    """Typed result with evidence, artifact and failure channels (manual 5.3)."""
    task_id: str
    step_id: str
    attempt_id: str
    status: str  # succeeded | failed | cancelled
    result_type: str
    summary: str
    data: dict = field(default_factory=dict)
    artifacts: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "step_id": self.step_id,
            "attempt_id": self.attempt_id,
            "status": self.status,
            "result_type": self.result_type,
            "summary": self.summary,
            "data": self.data,
            "artifacts": self.artifacts,
            "evidence": self.evidence,
            "errors": self.errors,
            "usage": self.usage,
        }
