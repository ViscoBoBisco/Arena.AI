"""Delivery service (manual 3.8 / 4.4): signed webhook delivery of completed
results, plus citation-numbered evidence retrieval (manual 3.7: short excerpts
+ source handles)."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.request

from .contracts import utcnow


class DeliveryService:
    def __init__(self, secret: str):
        self.secret = secret.encode()

    def sign(self, raw: bytes) -> tuple[str, str]:
        ts = str(int(time.time()))
        sig = "sha256=" + hmac.new(self.secret, raw, hashlib.sha256).hexdigest()
        return sig, ts

    def deliver(self, url: str, payload: dict) -> dict:
        """POST a signed result payload to a registered callback URL."""
        raw = json.dumps(payload).encode()
        sig, ts = self.sign(raw)
        req = urllib.request.Request(url, data=raw, method="POST", headers={
            "Content-Type": "application/json",
            "X-Signature-256": sig, "X-Timestamp": ts})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return {"delivered": True, "status": resp.status}
        except Exception as e:
            return {"delivered": False, "error": str(e)}


def evidence_with_citations(receipts: list) -> list[dict]:
    """Numbered citation handles over observed evidence; excerpts are data,
    never instructions (quarantine per prompt-injection defense)."""
    out = []
    for i, r in enumerate(receipts, 1):
        for e in r.evidence:
            out.append({"citation": f"[{i}]", "step_id": r.step_id,
                        "source_uri": e.get("source_uri"),
                        "locator": e.get("locator"),
                        "captured_at": e.get("captured_at"),
                        "trust": "untrusted-data"})
    return out
