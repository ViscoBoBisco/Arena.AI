"""Evidence and artifact store (manual 3.7, 5.5).

Stores immutable artifacts keyed by content hash, with source locators.
Retrieval returns short excerpts plus source handles; observed data is always
distinguishable from model inference.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Optional

from .contracts import utcnow


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class EvidenceStore:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(self.root, exist_ok=True)
        self._index_path = os.path.join(self.root, "index.jsonl")

    def put_artifact(self, task_id: str, step_id: str, name: str, data: bytes) -> dict:
        """Store bytes immutably; returns an artifact reference with hash."""
        digest = sha256_bytes(data)
        rel = f"{task_id}/{step_id}/{name}"
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(data)
        uri = f"artifact://{rel}"
        record = {"uri": uri, "sha256": digest, "bytes": len(data)}
        with open(self._index_path, "a") as f:
            f.write(json.dumps({"at": utcnow(), "task_id": task_id,
                                "step_id": step_id, **record}) + "\n")
        return record

    def get_artifact(self, uri: str) -> Optional[bytes]:
        if not uri.startswith("artifact://"):
            return None
        path = os.path.join(self.root, uri[len("artifact://"):])
        # path traversal guard (manual 5.8)
        root = os.path.abspath(self.root)
        try:
            if os.path.commonpath((root, os.path.abspath(path))) != root:
                return None
        except ValueError:
            return None
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
        return None

    @staticmethod
    def evidence_ref(source_uri: str, locator: str) -> dict:
        return {"source_uri": source_uri, "locator": locator, "captured_at": utcnow()}
