"""Model router with GPU/inference admission (manual 3.6).

One inference entry point; bounds simultaneous heavyweight generations
(default: 1) and enforces per-task token budgets. Swarm I/O work proceeds in
parallel while model generations queue.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class ModelRouter:
    max_concurrent: int = 1
    def __post_init__(self):
        self._sem = threading.BoundedSemaphore(self.max_concurrent)
        self._lock = threading.Lock()
        self._tokens_used = 0
        self._task_tokens: dict[str, int] = {}

    def complete(self, prompt: str, budget_tokens: int = 4096,
                 task_id: str | None = None) -> dict:
        """Deterministic local stand-in for a model generation. Queues on the
        semaphore and fails gracefully with a resource receipt when the budget
        is exhausted (manual 5.10, test 9)."""
        if not self._sem.acquire(timeout=30):
            return {"ok": False, "error": "model queue saturated",
                    "usage": {"queued_seconds": 30}}
        try:
            # The default identity keeps direct/legacy calls in one budget
            # scope; production tool calls always pass their durable task ID.
            budget_scope = task_id or "_unscoped"
            # deterministic mock completion: echo + length stats
            completion = f"[model.complete] {prompt[:200]}"
            used = len(prompt.split()) + len(completion.split())
            with self._lock:
                task_used = self._task_tokens.get(budget_scope, 0)
                if task_used + used > max(0, budget_tokens):
                    return {"ok": False, "error": "token budget exhausted",
                            "usage": {"tokens_used": task_used,
                                      "budget_tokens": max(0, budget_tokens)}}
                task_used += used
                self._task_tokens[budget_scope] = task_used
                self._tokens_used += used
                total_used = self._tokens_used
            return {"ok": True, "completion": completion,
                    "usage": {"elapsed_ms": 1, "model_tokens": used,
                              "tokens_used": task_used,
                              "tokens_used_total": total_used}}
        finally:
            self._sem.release()
