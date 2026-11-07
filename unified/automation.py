"""Automation connector: signed n8n-style callbacks and triggers (manual 3.1, 4.4).

Security per current webhook best practice: HMAC-SHA256 over the RAW body,
timestamp skew window, timing-safe comparison, and replay detection via
stored event IDs. Duplicate triggers map to the same gateway task through a
stable idempotency key — no duplicate task, no duplicate external action.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

from .engine import Engine

SKEW_SECONDS = 300


class AutomationConnector:
    def __init__(self, engine: Engine, secret: str):
        self.engine = engine
        self.secret = secret.encode()
        self._seen_events: set[str] = set()

    def _verify(self, raw_body: bytes, signature: str, timestamp: str) -> tuple[bool, str]:
        try:
            ts = int(timestamp)
        except (TypeError, ValueError):
            return False, "bad timestamp"
        if abs(time.time() - ts) > SKEW_SECONDS:
            return False, "stale timestamp"
        expected = hmac.new(self.secret, raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, (signature or "").replace("sha256=", "")):
            return False, "bad signature"
        return True, "ok"

    def callback(self, raw_body: bytes, signature: str, timestamp: str) -> dict:
        ok, reason = self._verify(raw_body, signature, timestamp)
        if not ok:
            return {"accepted": False, "reason": reason}
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"accepted": False, "reason": "invalid JSON payload"}
        if not isinstance(payload, dict):
            return {"accepted": False, "reason": "payload must be a JSON object"}
        event_id = payload.get("event_id", "")
        if event_id in self._seen_events:
            return {"accepted": True, "replay": True, "event_id": event_id}
        self._seen_events.add(event_id)
        # Completion callbacks are recorded against the gateway task; the
        # gateway remains the single task authority (manual 1.1).
        task_id = payload.get("task_id")
        if task_id:
            task = self.engine.get_task(task_id)
            if task is None:
                return {"accepted": False, "reason": "unknown task"}
        return {"accepted": True, "replay": False, "event_id": event_id,
                "task_id": task_id}

    def trigger(self, workflow_id: str, execution_id: str, request: dict,
                permissions: list[str], project_id: str = "automation") -> dict:
        """n8n workflow submits a task with a stable idempotency key derived
        from workflow + execution (manual 4.4)."""
        key = f"n8n:{workflow_id}:{execution_id}"
        return self.engine.submit(project_id=project_id, idempotency_key=key,
                                  request=request, permissions=permissions,
                                  grants=["web.read.public", "data.market.read",
                                          "repo.read", "workspace.write.sandbox",
                                          "binary.inspect.static"])
