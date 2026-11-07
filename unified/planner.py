"""Planner adapter (manual 3.5 / AgenticSeek stand-in).

Produces a StepDAG from a task envelope. This is a deterministic rule-based
planner standing in for the AgenticSeek adapter; the contract
plan(TaskEnvelope) -> StepDAG is preserved.
"""
from __future__ import annotations

import hashlib

from .contracts import Step, TaskEnvelope


def _sid(env: TaskEnvelope, n: int) -> str:
    """Deterministic step ID: stable across restarts/resumes of the same task
    so committed receipts and approvals keep matching (manual 3.2)."""
    return "S-" + hashlib.md5(f"{env.task_id}:{n}".encode()).hexdigest()[:10].upper()


def plan(env: TaskEnvelope) -> list[Step]:
    """Decompose the request intent into a DAG of typed tool steps."""
    intent = env.request.get("intent", "general")
    instructions = env.request.get("instructions", "")
    steps: list[Step] = []
    n = 0

    def sid() -> str:
        nonlocal n
        n += 1
        return _sid(env, n)

    if intent == "echo":
        steps.append(Step(sid(), "native.echo",
                          {"text": instructions}, read_only=True))
    elif intent == "read_file":
        steps.append(Step(sid(), "native.fs.read",
                          {"path": env.request.get("path", ""),
                           "_task_id": env.task_id},
                          read_only=True, permissions_used=["repo.read"]))
    elif intent == "write_file":
        s = Step(sid(), "native.fs.write",
                 {"path": env.request.get("path", ""),
                  "content": env.request.get("content", ""),
                  "_task_id": env.task_id},
                 read_only=False, permissions_used=["workspace.write.sandbox"])
        steps.append(s)
    elif intent == "read_then_echo":
        s1 = Step(sid(), "native.fs.read",
                  {"path": env.request.get("path", ""), "_task_id": env.task_id},
                  read_only=True, permissions_used=["repo.read"])
        s2 = Step(sid(), "native.echo", {"text": "done"},
                  depends_on=[s1.step_id], read_only=True)
        steps.extend([s1, s2])
    elif intent == "fetch_fixture":
        steps.append(Step(sid(), "scrapling.fetch_fixture",
                          {"name": env.request.get("name", ""), "_task_id": env.task_id},
                          read_only=True, permissions_used=["web.read.public"]))
    elif intent == "index_repo":
        steps.append(Step(sid(), "codegraph.index",
                          {"root": env.request.get("root", "."), "commit": env.request.get("commit", "unknown"),
                           "_task_id": env.task_id},
                          read_only=True, permissions_used=["repo.read"]))
    elif intent == "find_symbol":
        steps.append(Step(sid(), "codegraph.find",
                          {"query": env.request.get("query", "")},
                          read_only=True, permissions_used=["repo.read"]))
    elif intent == "inspect_binary":
        steps.append(Step(sid(), "rea.inspect",
                          {"path": env.request.get("path", ""), "_task_id": env.task_id},
                          read_only=True, permissions_used=["binary.inspect.static"]))
    elif intent == "repo_change":
        # Manual ch. 4.2: graph -> impact hypothesis -> sandboxed write -> verify
        s1 = Step(sid(), "codegraph.index",
                  {"root": env.request.get("root", "."), "commit": env.request.get("commit", "unknown"),
                   "_task_id": env.task_id},
                  read_only=True, permissions_used=["repo.read"])
        s2 = Step(sid(), "codegraph.impact",
                  {"symbol": env.request.get("symbol", "")},
                  depends_on=[s1.step_id], read_only=True, permissions_used=["repo.read"])
        s3 = Step(sid(), "native.fs.write",
                  {"path": env.request.get("path", ""), "content": env.request.get("content", ""),
                   "_task_id": env.task_id},
                  depends_on=[s2.step_id], read_only=False,
                  permissions_used=["workspace.write.sandbox"])
        steps.extend([s1, s2, s3])
    elif intent == "investigate":
        # Manual ch. 4.3: authorized static triage + optional source-graph
        # correlation; runtime observation stays separate and unauthorized.
        s1 = Step(sid(), "rea.inspect",
                  {"path": env.request.get("path", ""), "_task_id": env.task_id},
                  read_only=True, permissions_used=["binary.inspect.static"])
        s2 = Step(sid(), "codegraph.index",
                  {"root": ".", "commit": env.request.get("commit", "unknown"),
                   "_task_id": env.task_id},
                  depends_on=[s1.step_id], read_only=True, permissions_used=["repo.read"])
        steps.extend([s1, s2])
    elif intent == "trace":
        steps.append(Step(sid(), "codegraph.index",
                          {"root": ".", "commit": env.request.get("commit", "unknown"),
                           "_task_id": env.task_id},
                          read_only=True, permissions_used=["repo.read"]))
        s2 = Step(sid(), "codegraph.trace",
                  {"symbol": env.request.get("symbol", "")},
                  depends_on=[steps[0].step_id], read_only=True, permissions_used=["repo.read"])
        steps.append(s2)
    elif intent == "query_data":
        steps.append(Step(sid(), "openbb.query",
                          {"dataset_uri": env.request.get("dataset_uri", ""),
                           "unit": env.request.get("unit"), "provider": env.request.get("provider")},
                          read_only=True, permissions_used=["data.market.read"]))
    elif intent == "freshness":
        steps.append(Step(sid(), "codegraph.freshness",
                          {"commit": env.request.get("commit", "unknown")},
                          read_only=True, permissions_used=["repo.read"]))
    elif intent == "impact":
        steps.append(Step(sid(), "codegraph.impact",
                          {"symbol": env.request.get("symbol", "")},
                          read_only=True, permissions_used=["repo.read"]))
    elif intent == "research":
        # Manual ch. 4.1 vertical slice: acquire evidence, then synthesize.
        s1 = Step(sid(), "scrapling.fetch_fixture",
                  {"name": env.request.get("name", "demo.html"), "_task_id": env.task_id},
                  read_only=True, permissions_used=["web.read.public"])
        s2 = Step(sid(), "model.complete",
                  {"prompt": f"Summarize the captured page: {instructions}",
                   "budget_tokens": env.budgets.get("model_tokens", 4096)},
                  depends_on=[s1.step_id], read_only=True, permissions_used=["model.infer"])
        steps.extend([s1, s2])
    elif intent == "model_complete":
        steps.append(Step(sid(), "model.complete",
                          {"prompt": instructions, "budget_tokens": env.budgets.get("model_tokens", 4096)},
                          read_only=True, permissions_used=["model.infer"]))
    else:
        steps.append(Step(sid(), "native.echo",
                          {"text": instructions or intent}, read_only=True))
    return steps
