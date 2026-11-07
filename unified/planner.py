"""Planner adapter (manual 3.5 / AgenticSeek stand-in).

Produces a StepDAG from a task envelope. This is a deterministic rule-based
planner standing in for the AgenticSeek adapter; the contract
plan(TaskEnvelope) -> StepDAG is preserved.
"""
from __future__ import annotations

from .contracts import Step, TaskEnvelope, new_id


def plan(env: TaskEnvelope) -> list[Step]:
    """Decompose the request intent into a DAG of typed tool steps."""
    intent = env.request.get("intent", "general")
    instructions = env.request.get("instructions", "")
    steps: list[Step] = []

    if intent == "echo":
        steps.append(Step(new_id("S"), "native.echo",
                          {"text": instructions}, read_only=True))
    elif intent == "read_file":
        steps.append(Step(new_id("S"), "native.fs.read",
                          {"path": env.request.get("path", ""),
                           "_task_id": env.task_id},
                          read_only=True, permissions_used=["repo.read"]))
    elif intent == "write_file":
        s = Step(new_id("S"), "native.fs.write",
                 {"path": env.request.get("path", ""),
                  "content": env.request.get("content", ""),
                  "_task_id": env.task_id},
                 read_only=False, permissions_used=["workspace.write.sandbox"])
        steps.append(s)
    elif intent == "read_then_echo":
        s1 = Step(new_id("S"), "native.fs.read",
                  {"path": env.request.get("path", ""), "_task_id": env.task_id},
                  read_only=True, permissions_used=["repo.read"])
        s2 = Step(new_id("S"), "native.echo", {"text": "done"},
                  depends_on=[s1.step_id], read_only=True)
        steps.extend([s1, s2])
    elif intent == "fetch_fixture":
        steps.append(Step(new_id("S"), "scrapling.fetch_fixture",
                          {"name": env.request.get("name", ""), "_task_id": env.task_id},
                          read_only=True, permissions_used=["web.read.public"]))
    elif intent == "index_repo":
        steps.append(Step(new_id("S"), "codegraph.index",
                          {"root": env.request.get("root", "."), "commit": env.request.get("commit", "unknown"),
                           "_task_id": env.task_id},
                          read_only=True, permissions_used=["repo.read"]))
    elif intent == "find_symbol":
        steps.append(Step(new_id("S"), "codegraph.find",
                          {"query": env.request.get("query", "")},
                          read_only=True, permissions_used=["repo.read"]))
    elif intent == "inspect_binary":
        steps.append(Step(new_id("S"), "rea.inspect",
                          {"path": env.request.get("path", ""), "_task_id": env.task_id},
                          read_only=True, permissions_used=["binary.inspect.static"]))
    elif intent == "model_complete":
        steps.append(Step(new_id("S"), "model.complete",
                          {"prompt": instructions, "budget_tokens": env.budgets.get("model_tokens", 4096)},
                          read_only=True, permissions_used=["model.infer"]))
    else:
        steps.append(Step(new_id("S"), "native.echo",
                          {"text": instructions or intent}, read_only=True))
    return steps
