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
    side_effect_class: str  # read_only | sandbox_write | external_effect
    handler: Callable
    description: str = ""
    read_only: bool = True


class CapabilityBroker:
    def __init__(self, evidence: EvidenceStore, workspace_root: Optional[str] = None):
        self.evidence = evidence
        self.workspace_root = os.path.abspath(workspace_root or os.getcwd())
        self._tools: dict[str, ToolSpec] = {}
        self._register_native_tools()

    # ---- registry ----------------------------------------------------
    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def list_tools(self) -> list[dict]:
        return [{"name": t.name, "permissions": t.required_permissions,
                 "side_effect_class": t.side_effect_class,
                 "read_only": t.read_only, "description": t.description}
                for t in self._tools.values()]

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    # ---- dispatch ----------------------------------------------------
    def call(self, task_id: str, step_id: str, tool: str, args: dict,
             permissions_used: list[str], policy: PolicyEngine,
             deadline_ms: int = 45000) -> ToolReceipt:
        """Execute one typed tool call (manual 5.2 envelope)."""
        attempt_id = new_id("A")
        spec = self._tools.get(tool)
        if spec is None:
            return ToolReceipt(task_id, step_id, attempt_id, "failed",
                               "error.v1", f"unknown tool: {tool}",
                               errors=[{"type": "unknown_tool", "tool": tool}])
        # Manual 3.3: re-evaluate policy immediately before execution.
        try:
            policy.authorize_tool(spec.required_permissions)
        except PolicyViolation as e:
            return ToolReceipt(task_id, step_id, attempt_id, "failed",
                               "error.policy.v1", str(e),
                               errors=[{"type": "policy_denied", "detail": str(e)}])
        start = time.monotonic()
        try:
            result = spec.handler(args)
            receipt = ToolReceipt(
                task_id=task_id, step_id=step_id, attempt_id=attempt_id,
                status="succeeded", result_type=result.get("result_type", "native.v1"),
                summary=result.get("summary", ""), data=result.get("data", {}),
                artifacts=result.get("artifacts", []), evidence=result.get("evidence", []),
                errors=result.get("errors", []),
                usage={"elapsed_ms": int((time.monotonic() - start) * 1000),
                       "model_tokens": 0})
            return receipt
        except Exception as e:  # typed failure, never fabricated findings (manual 2.7)
            return ToolReceipt(task_id, step_id, attempt_id, "failed",
                               "error.v1", f"tool {tool} failed: {e}",
                               errors=[{"type": "tool_error", "detail": str(e)}])

    # ---- native tools --------------------------------------------------
    def _sandbox_path(self, rel: str) -> str:
        """Resolve rel inside the workspace root; reject traversal (manual 5.8)."""
        root = self.workspace_root
        path = os.path.abspath(os.path.join(root, rel))
        if path != root and not path.startswith(root + os.sep):
            raise PermissionError(f"path escapes workspace: {rel}")
        return path

    def _register_native_tools(self) -> None:
        def echo(args):
            return {"result_type": "native.echo.v1",
                    "summary": "echoed input",
                    "data": {"text": args.get("text", "")}}

        def fs_read(args):
            path = self._sandbox_path(args["path"])
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
            path = self._sandbox_path(args["path"])
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
                               read_only=False))
