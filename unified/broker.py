"""Unified capability broker and adapter registry (manual 3.4).

Inventories tools, validates the typed call envelope, enforces per-tool
permissions immediately before execution, and normalizes results into typed
receipts with evidence/artifact/failure channels. Untrusted tool output is
data only — it never alters policy.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .contracts import ToolReceipt, new_id, utcnow
from .evidence import EvidenceStore, sha256_bytes
from .policy import PolicyEngine, PolicyViolation


@dataclass
class ToolSpec:
    name: str
    required_permissions: list
    side_effect_class: str  # read_only | internal_write | sandbox_write | external_effect
    handler: Callable
    description: str = ""
    read_only: bool = True
    supports_idempotency: bool = False
    reconcile: Optional[Callable] = None
    external_request: bool = False
    supports_cancellation: bool = False
    resource_effectful: bool = False
    repeatable: bool = False
    billable: bool = False
    requires_approval: bool | None = None
    # Recovery trusts replay safety only when the adapter's *service* contract
    # has been qualified, not merely because the broker forwards a key.
    idempotency_qualified: bool = False
    idempotency_evidence: str | None = None


class CapabilityBroker:
    def __init__(self, evidence: EvidenceStore, workspace_root: Optional[str] = None,
                 project_isolation: bool = False):
        self.evidence = evidence
        self.workspace_root = os.path.abspath(workspace_root or os.getcwd())
        self.project_isolation = project_isolation
        self._tools: dict[str, ToolSpec] = {}
        self._register_native_tools()

    # ---- registry ----------------------------------------------------
    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def list_tools(self) -> list[dict]:
        return [{"name": t.name, "permissions": t.required_permissions,
                 "side_effect_class": t.side_effect_class,
                 "read_only": t.read_only,
                 "external_request": t.external_request,
                 "supports_cancellation": t.supports_cancellation,
                 "idempotency_qualified": t.idempotency_qualified,
                 "idempotency_evidence": t.idempotency_evidence,
                 "resource_effectful": t.resource_effectful,
                 "repeatable": t.repeatable,
                 "billable": t.billable,
                 "requires_approval": t.requires_approval,
                 "description": t.description}
                for t in self._tools.values()]

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    # ---- dispatch ----------------------------------------------------
    def call(self, task_id: str, step_id: str, tool: str, args: dict,
             permissions_used: list[str], policy: PolicyEngine,
             deadline_ms: int = 45000, attempt_id: str | None = None,
             idempotency_key: str | None = None,
             deadline_at: float | None = None,
             cancel_check: Callable[[], bool] | None = None) -> ToolReceipt:
        """Execute one typed tool call (manual 5.2 envelope)."""
        attempt_id = attempt_id or new_id("A")
        spec = self._tools.get(tool)
        if spec is None:
            return ToolReceipt(task_id, step_id, attempt_id, "failed",
                               "error.v1", f"unknown tool: {tool}",
                               errors=[{"type": "unknown_tool", "tool": tool}])
        if cancel_check and cancel_check():
            return ToolReceipt(task_id, step_id, attempt_id, "cancelled",
                               "cancelled.v1", "cancelled before dispatch")
        now_wall = time.time()
        effective_deadline = min(deadline_at or now_wall + deadline_ms / 1000,
                                 now_wall + deadline_ms / 1000)
        if effective_deadline <= now_wall:
            return ToolReceipt(task_id, step_id, attempt_id, "failed",
                               "error.budget.v1", "execution time budget exceeded",
                               errors=[{"type": "budget_exceeded", "budget": "time_seconds"}])
        # Manual 3.3: re-evaluate policy immediately before execution.
        try:
            policy.authorize_tool(spec.required_permissions)
        except PolicyViolation as e:
            return ToolReceipt(task_id, step_id, attempt_id, "failed",
                               "error.policy.v1", str(e),
                               errors=[{"type": "policy_denied", "detail": str(e)}])
        start = time.monotonic()
        call_args = dict(args)
        if spec.supports_idempotency and idempotency_key:
            call_args["_idempotency_key"] = idempotency_key
        if spec.supports_cancellation:
            call_args["_cancelled"] = cancel_check or (lambda: False)
            call_args["_deadline_at"] = effective_deadline
        try:
            result = spec.handler(call_args)
            # Adapters signal failure through the typed error channel; a
            # transport-successful response with errors is NOT a success
            # (manual 5.3: receipt status reflects the tool, not the transport).
            errors = result.get("errors", [])
            cancelled = (result.get("result_type") == "cancelled.v1"
                         or any(error.get("type") == "cancelled"
                                for error in errors if isinstance(error, dict)))
            status = "cancelled" if cancelled else "failed" if errors else "succeeded"
            usage = dict(result.get("usage", {}))
            usage.setdefault("elapsed_ms", int((time.monotonic() - start) * 1000))
            usage.setdefault("model_tokens", 0)
            receipt = ToolReceipt(
                task_id=task_id, step_id=step_id, attempt_id=attempt_id,
                status=status, result_type=result.get("result_type", "native.v1"),
                summary=result.get("summary", ""), data=result.get("data", {}),
                artifacts=result.get("artifacts", []), evidence=result.get("evidence", []),
                errors=result.get("errors", []), usage=usage)
            return receipt
        except MemoryError:
            return ToolReceipt(task_id, step_id, attempt_id, "failed",
                               "error.budget.v1", f"tool {tool} exceeded its memory budget",
                               errors=[{"type": "budget_exceeded", "budget": "memory_mb"}])
        except Exception as e:  # typed failure, never fabricated findings (manual 2.7)
            return ToolReceipt(task_id, step_id, attempt_id, "failed",
                               "error.v1", f"tool {tool} failed: {e}",
                               errors=[{"type": "tool_error", "detail": str(e)}])

    def reconcile(self, task_id: str, step_id: str, attempt_id: str, tool: str,
                  args: dict, permissions_used: list[str], policy: PolicyEngine,
                  idempotency_key: str) -> tuple[str, ToolReceipt | None, dict | None]:
        """Ask an adapter for a durable outcome; errors never imply retry safety.

        Reconciliation callbacks return ``{"outcome": "applied", "result": ...}``,
        ``{"outcome": "not_applied"}``, or ``{"outcome": "unknown"}``. The
        result for an applied operation uses the same normalized payload as a
        normal tool handler.
        """
        spec = self._tools.get(tool)
        if spec is None or spec.reconcile is None:
            return "unknown", None, None
        try:
            policy.authorize_tool(spec.required_permissions)
            reconciled = spec.reconcile(dict(args), idempotency_key)
        except Exception as exc:
            return "unknown", None, {
                "type": type(exc).__name__,
                "message": "adapter reconciliation callback raised an exception",
            }
        if not isinstance(reconciled, dict):
            return "unknown", None, {
                "type": "invalid_reconciliation_response",
                "message": "adapter reconciliation callback returned a non-object",
            }
        outcome = reconciled.get("outcome")
        if outcome == "not_applied":
            return "not_applied", None, None
        if outcome != "applied" or not isinstance(reconciled.get("result"), dict):
            return "unknown", None, {
                "type": "unresolved_reconciliation",
                "message": "adapter did not establish an applied or not-applied outcome",
            }
        result = reconciled["result"]
        receipt = ToolReceipt(
            task_id=task_id, step_id=step_id, attempt_id=attempt_id,
            status="failed" if result.get("errors") else "succeeded",
            result_type=result.get("result_type", "native.v1"),
            summary=result.get("summary", ""), data=result.get("data", {}),
            artifacts=result.get("artifacts", []), evidence=result.get("evidence", []),
            errors=result.get("errors", []), usage=result.get("usage", {}))
        return "applied", receipt, None

    def register_model_tool(self, router) -> None:
        def model_complete(args):
            task_id = args.get("_task_id")
            out = router.complete(args.get("prompt", ""),
                                  budget_tokens=args.get("budget_tokens", 4096),
                                  task_id=task_id,
                                  deadline_at=args.get("_deadline_at"),
                                  cancel_check=args.get("_cancelled"),
                                  supervised=bool(args.get("_supervised_model")))
            if not out["ok"]:
                error_type = ("budget_exceeded" if out.get("budget") else
                              "cancelled" if out.get("cancelled") else "resource")
                error = {"type": error_type, "detail": out["error"]}
                if out.get("budget"):
                    error["budget"] = out["budget"]
                result_type = ("cancelled.v1" if out.get("cancelled") else
                               "error.budget.v1" if out.get("budget") else
                               "error.resource.v1")
                return {"result_type": result_type, "summary": out["error"],
                        "errors": [error], "usage": out.get("usage", {})}
            return {"result_type": "model.complete.v1",
                    "summary": "model completion",
                    "data": {"completion": out["completion"]},
                    "usage": out["usage"]}

        self.register(ToolSpec("model.complete", ["model.infer"], "read_only",
                               model_complete, "Shared model router (bounded queue).",
                               read_only=True, supports_cancellation=True,
                               resource_effectful=True,
                               billable=bool(getattr(router.provider, "billable", False))))

    # ---- native tools --------------------------------------------------
    def workspace_for_project(self, project_id: str | None = None) -> str:
        """Return a tenant workspace; production namespaces projects by digest."""
        if not self.project_isolation or not project_id:
            return self.workspace_root
        import hashlib
        return os.path.join(self.workspace_root,
                            hashlib.sha256(project_id.encode("utf-8")).hexdigest())

    def _sandbox_path(self, rel: str, project_id: str | None = None) -> str:
        """Resolve within the caller project root and reject traversal/symlinks."""
        root = os.path.realpath(self.workspace_for_project(project_id))
        path = os.path.realpath(os.path.join(root, rel))
        try:
            if os.path.commonpath((root, path)) != root:
                raise PermissionError(f"path escapes workspace: {rel}")
        except ValueError as exc:
            raise PermissionError(f"path escapes workspace: {rel}") from exc
        return path

    def _register_native_tools(self) -> None:
        def echo(args):
            return {"result_type": "native.echo.v1",
                    "summary": "echoed input",
                    "data": {"text": args.get("text", "")}}

        def fs_read(args):
            path = self._sandbox_path(args["path"], args.get("_project_id"))
            with open(path, "rb") as f:
                data = f.read()
            art = self.evidence.put_artifact(args.get("_task_id", "adhoc"),
                                             args.get("_step_id", "adhoc"),
                                             os.path.basename(path), data)
            return {"result_type": "native.fs.read.v1",
                    "summary": f"read {len(data)} bytes",
                    "data": {"path": args["path"], "sha256": sha256_bytes(data),
                             "text": data.decode("utf-8", "replace")[:4000]},
                    "artifacts": [art],
                    "evidence": [self.evidence.evidence_ref(
                        f"file://{path}", f"bytes:0-{len(data)}")]}

        def fs_write(args):
            path = self._sandbox_path(args["path"], args.get("_project_id"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = args.get("content", "").encode()
            with open(path, "wb") as f:
                f.write(data)
            art = self.evidence.put_artifact(args.get("_task_id", "adhoc"),
                                             args.get("_step_id", "adhoc"),
                                             os.path.basename(path) + ".written", data)
            return {"result_type": "native.fs.write.v1",
                    "summary": f"wrote {len(data)} bytes",
                    "data": {"path": args["path"], "sha256": sha256_bytes(data)},
                    "artifacts": [art]}

        self.register(ToolSpec("native.echo", [], "read_only", echo,
                               "Echo input (mock planning tool)."))
        self.register(ToolSpec("native.fs.read", ["repo.read"], "read_only", fs_read,
                               "Read a file inside the sandboxed workspace."))
        self.register(ToolSpec("native.fs.write", ["workspace.write.sandbox"],
                               "sandbox_write", fs_write,
                               "Write a file inside the sandboxed workspace.",
                               read_only=False, repeatable=True))
