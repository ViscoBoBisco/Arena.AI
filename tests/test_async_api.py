"""Async submission (202) and artifact download endpoint tests."""
import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unified.broker import CapabilityBroker
from unified.engine import Engine
from unified.evidence import EvidenceStore
from unified.gateway import serve
from unified.ledger import Ledger

GRANTS = ["web.read.public", "repo.read", "workspace.write.sandbox", "model.infer"]


class AsyncApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        ws = os.path.join(d, "ws")
        os.makedirs(ws)
        with open(os.path.join(ws, "f.txt"), "w") as f:
            f.write("artifact body")
        ledger = Ledger()
        evidence = EvidenceStore(os.path.join(d, "art"))
        broker = CapabilityBroker(evidence, workspace_root=ws)
        cls.engine = Engine(ledger, broker, evidence)
        cls.server = serve(cls.engine, "127.0.0.1", 8123)
        import threading
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmp.cleanup()

    def _req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"X-Project-Id": "t", "Authorization": "Bearer dev-key"}
        if data:
            headers["Content-Type"] = "application/json"
        r = urllib.request.Request("http://127.0.0.1:8123" + path, data=data, method=method,
                                   headers=headers)
        try:
            resp = urllib.request.urlopen(r)
            return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_202_async_then_poll(self):
        code, out = self._req("POST", "/v1/tasks", {
            "project_id": "t", "idempotency_key": "async-1",
            "request": {"intent": "echo", "instructions": "async"}, "permissions": []})
        self.assertEqual(code, 202)
        self.assertIn("status_url", out)
        tid = out["task_id"]
        for _ in range(50):
            _, task = self._req("GET", f"/v1/tasks/{tid}")
            if task["state"] in ("COMPLETED", "FAILED", "REJECTED"):
                break
            time.sleep(0.1)
        self.assertEqual(task["state"], "COMPLETED")

    def test_sync_mode(self):
        code, out = self._req("POST", "/v1/tasks?sync=1", {
            "project_id": "t", "idempotency_key": "sync-1",
            "request": {"intent": "echo", "instructions": "sync"}, "permissions": []})
        self.assertEqual(code, 201)
        self.assertEqual(out["result"]["state"], "COMPLETED")

    def test_artifact_download_hash(self):
        code, out = self._req("POST", "/v1/tasks?sync=1", {
            "project_id": "t", "idempotency_key": "art-1",
            "request": {"intent": "read_file", "path": "f.txt"},
            "permissions": ["repo.read"]})
        uri = out["result"]["artifacts"][0]["uri"]
        path = "/v1/" + uri.replace("artifact://", "artifacts/")
        resp = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8123" + path, headers={"Authorization": "Bearer dev-key"}))
        body = resp.read()
        self.assertEqual(body, b"artifact body")
        self.assertEqual(resp.headers["X-Sha256"], hashlib.sha256(body).hexdigest())


if __name__ == "__main__":
    unittest.main()
