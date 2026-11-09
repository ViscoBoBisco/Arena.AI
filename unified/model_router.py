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

    def restore_task_usage(self, task_id: str, tokens_used: int) -> None:
        """Reconcile in-memory accounting from durable committed receipts."""
        with self._lock:
            self._task_tokens[task_id] = max(
                self._task_tokens.get(task_id, 0), max(0, int(tokens_used)))

    def forget_task(self, task_id: str) -> None:
        """Release per-task budget state after a durable terminal outcome."""
        with self._lock:
            self._task_tokens.pop(task_id, None)

    def complete(self, prompt: str, budget_tokens: int = 4096,
                 task_id: str | None = None, deadline_at: float | None = None,
                 cancel_check=None) -> dict:
        """Run under task cancellation, deadline, and cumulative token limits."""
        queued_at = time.monotonic()
        wait_deadline = min(deadline_at or (time.time() + 30), time.time() + 30)
        while not self._sem.acquire(timeout=min(0.05, max(0.0, wait_deadline - time.time()))):
            if cancel_check and cancel_check():
                return {"ok": False, "cancelled": True, "error": "model call cancelled",
                        "usage": {"queued_seconds": time.monotonic() - queued_at}}
            if time.time() >= wait_deadline:
                return {"ok": False, "budget": "time_seconds",
                        "error": "model queue/deadline budget exceeded",
                        "usage": {"queued_seconds": time.monotonic() - queued_at}}
        try:
            if cancel_check and cancel_check():
                return {"ok": False, "cancelled": True, "error": "model call cancelled",
                        "usage": {"queued_seconds": 0}}
            if deadline_at is not None and time.time() >= deadline_at:
                return {"ok": False, "budget": "time_seconds",
                        "error": "task time budget exceeded", "usage": {"queued_seconds": 0}}
            # The default identity keeps direct/legacy calls in one budget
            # scope; production tool calls always pass their durable task ID.
            budget_scope = task_id or "_unscoped"
            # deterministic mock completion: echo + length stats
            completion = f"[model.complete] {prompt[:200]}"
            used = len(prompt.split()) + len(completion.split())
            with self._lock:
                task_used = self._task_tokens.get(budget_scope, 0)
                if task_used + used > max(0, budget_tokens):
                    return {"ok": False, "budget": "model_tokens",
                            "error": "model token budget exhausted",
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
