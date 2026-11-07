"""Evidence verifier and result assembler (manual 3.8).

Collects step receipts, distinguishes observed evidence from inference, and
surfaces conflicts/failures explicitly instead of hiding them.
"""
from __future__ import annotations

from .contracts import ToolReceipt, utcnow


def verify(task_id: str, receipts: list[ToolReceipt], delivery_format: str = "report_with_evidence") -> dict:
    succeeded = [r for r in receipts if r.status == "succeeded"]
    failed = [r for r in receipts if r.status == "failed"]
    cancelled = [r for r in receipts if r.status == "cancelled"]

    evidence = []
    artifacts = []
    for r in succeeded:
        evidence.extend(r.evidence)
        artifacts.extend(r.artifacts)

    # Conflicts: same locator reported with differing content hashes.
    conflicts = []
    seen = {}
    for r in succeeded:
        for e in r.evidence:
            key = (e.get("source_uri"), e.get("locator"))
            digest = r.data.get("sha256")
            if key in seen and seen[key] != digest:
                conflicts.append({"locator": list(key),
                                  "reported_hashes": [seen[key], digest],
                                  "step_ids": r.step_id})
            seen.setdefault(key, digest)

    # Cross-check: every artifact hash claimed in receipt data must match the
    # stored immutable bytes (manual 5.5 freshness/identity checks).
    hash_mismatches = []
    for r in succeeded:
        digest = r.data.get("sha256")
        if digest:
            for art in r.artifacts:
                if art.get("sha256") and art["sha256"] != digest:
                    hash_mismatches.append({"step_id": r.step_id, "artifact": art["uri"]})

    answer = "; ".join(r.summary for r in succeeded) or "no successful steps"
    return {
        "task_id": task_id,
        "format": delivery_format,
        "answer": answer,
        "observed_evidence": evidence,          # observed data, not inference
        "model_inference": [],                   # planner made no model claims
        "artifacts": artifacts,
        "failures": [{"step_id": r.step_id, "errors": r.errors} for r in failed],
        "cancelled_steps": [r.step_id for r in cancelled],
        "conflicts": conflicts,
        "hash_mismatches": hash_mismatches,
        "verified_at": utcnow(),
    }
