"""Delivery service (manual 3.8 / 4.4): signed webhook delivery of completed
results, SSRF destination validation, retry with dead-letter recording, and
citation-numbered evidence retrieval (manual 3.7: short excerpts + handles).

Security / reliability (research):
- SSRF defense: allow https:// only (unless allow_private=True for local test),
  resolve hostname and reject private/loopback/link-local/reserved IPs
  (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, ::1).
- Retry / DLQ: 4xx is terminal (routed straight to DLQ without wasting retries);
  5xx / network errors retry up to max_attempts before moving to DLQ.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse


def check_callback_url(url: str, allow_private: bool = False) -> tuple[bool, str]:
    """Validate callback URL against SSRF rules (OWASP Webhook Security)."""
    p = urlparse(url)
    if p.scheme not in (("https", "http") if allow_private else ("https",)):
        return False, f"disallowed scheme: {p.scheme}"
    if not p.hostname:
        return False, "missing hostname"
    if not allow_private:
        try:
            infos = socket.getaddrinfo(p.hostname, p.port or 443)
        except socket.gaierror:
            return False, "unresolvable host"
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False, f"non-public destination blocked: {ip}"
    return True, "ok"


class DeliveryService:
    def __init__(self, secret: str, allow_private: bool = False, max_attempts: int = 3):
        self.secret = secret.encode()
        self.allow_private = allow_private
        self.max_attempts = max_attempts
        self.dlq: list[dict] = []

    def sign(self, raw: bytes) -> tuple[str, str]:
        ts = str(int(time.time()))
        sig = "sha256=" + hmac.new(self.secret, raw, hashlib.sha256).hexdigest()
        return sig, ts

    def deliver(self, url: str, payload: dict) -> dict:
        """POST a signed result payload with SSRF check, retry, and DLQ."""
        ok, reason = check_callback_url(url, allow_private=self.allow_private)
        if not ok:
            rec = {"url": url, "reason": f"ssrf_blocked: {reason}", "attempts": 0}
            self.dlq.append(rec)
            return {"delivered": False, "dlq": True, **rec}
        raw = json.dumps(payload).encode()
        last_err = ""
        for attempt in range(1, self.max_attempts + 1):
            sig, ts = self.sign(raw)
            req = urllib.request.Request(url, data=raw, method="POST", headers={
                "Content-Type": "application/json",
                "X-Signature-256": sig, "X-Timestamp": ts})
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return {"delivered": True, "status": resp.status, "attempts": attempt}
            except urllib.error.HTTPError as e:
                last_err = f"http {e.code}"
                if 400 <= e.code < 500:  # 4xx is terminal — straight to DLQ
                    rec = {"url": url, "reason": last_err, "attempts": attempt}
                    self.dlq.append(rec)
                    return {"delivered": False, "dlq": True, **rec}
            except Exception as e:
                last_err = str(e)
        rec = {"url": url, "reason": last_err, "attempts": self.max_attempts}
        self.dlq.append(rec)
        return {"delivered": False, "dlq": True, **rec}


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
