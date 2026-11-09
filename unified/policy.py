"""Policy and approval service (manual 3.3).

Grants are defined by caller identity, project scope, target resources and
action type. Grants are never inferred from agent role text or fetched content.
Policy is evaluated at planning time AND immediately before a tool executes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Example grant vocabulary from manual 3.3.
KNOWN_GRANTS = {
    "web.read.public", "data.market.read", "repo.read",
    "workspace.write.sandbox", "binary.inspect.static",
    "binary.execute.authorized", "external.notification.send",
    "external.account.modify", "model.infer", "task.approve", "task.cancel",
    "task.reconcile", "ops.read",
    "memory.read", "memory.write",
}


class PolicyViolation(Exception):
    pass


@dataclass
class PolicyEngine:
    """Holds the caller's granted permissions for a task scope."""
    grants: set = field(default_factory=set)

    def admit(self, requested_permissions: list[str]) -> None:
        """Admission check (manual 3.3: evaluate at planning)."""
        missing = [p for p in requested_permissions if p not in self.grants]
        if missing:
            raise PolicyViolation(f"admission denied; missing grants: {missing}")

    def authorize_tool(self, tool_permissions: list[str]) -> None:
        """Pre-execution check (manual 3.3: re-evaluate immediately before
        a tool executes; a blocked operation cannot be re-enabled through an
        agent prompt because this engine only consults its grant set)."""
        missing = [p for p in tool_permissions if p not in self.grants]
        if missing:
            raise PolicyViolation(f"tool execution denied; missing grants: {missing}")
