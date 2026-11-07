"""Tenant scoping + ch.4.2 repo-change workflow tests."""
import os
import sys
import tempfile
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unified.adapters import AdapterContext, register_adapters
from unified.broker import CapabilityBroker
from unified.contracts import TaskState
from unified.engine import Engine
from unified.evidence import EvidenceStore
from unified.gateway import serve
from unified.ledger import Ledger

GRANTS = ["web.read.public", "data.market.read", "repo.read",
          "workspace.write.sandbox", "binary.inspect.static", "model.infer"]


class TenantAndRepoChangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.ws = os.path.join(d, "ws")
        os.makedirs(self.ws)
        with open(os.path.join(self.ws, "svc.py"), "w") as f:
            f.write("def entry():\n    pass\n")
        self.ledger = Ledger()
        self.evidence = EvidenceStore(os.path.join(d, "art"))
        self.broker = CapabilityBroker(self.evidence, workspace_root=self.ws)
        register_adapters(self.broker, AdapterContext(
            evidence=self.evidence, workspace_root=self.ws,
            graph_db=os.path.join(d, "g.db")))
        self.engine = Engine(self.ledger, self.broker, self.evidence)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ch4_2_repo_change_flow(self):
        """Manual 4.2: index -> impact -> sandboxed write -> verify (write
        pauses for approval, then completes exactly once)."""
        out = self.engine.submit("proj", "rc-1",
                                 {"intent": "repo_change", "commit": "c1", "symbol": "entry",
                                  "path": "svc.py", "content": "def entry():\n    return 1\n"},
                                 ["repo.read", "workspace.write.sandbox"], GRANTS)
        r = self.engine.run(out["task_id"], GRANTS)
        self.assertEqual(r["state"], TaskState.WAITING_APPROVAL.value)
        r2 = self.engine.approve(out["task_id"], "approved", GRANTS)
        res = r2["result"]
        self.assertEqual(res["state"], TaskState.COMPLETED.value)
        self.assertTrue(res["rubric"]["every_claim_has_evidence"])
        with open(os.path.join(self.ws, "svc.py")) as f:
            self.assertIn("return 1", f.read())

    def test_cross_project_access_denied(self):
        import threading
        server = serve(self.engine, "127.0.0.1", 8124)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            out = self.engine.submit("proj-a", "iso-1", {"intent": "echo", "instructions": "x"},
                                     [], GRANTS)
            self.engine.run(out["task_id"], GRANTS)
            tid = out["task_id"]
            # caller from proj-b must not read proj-a's task
            r = urllib.request.Request(f"http://127.0.0.1:8124/v1/tasks/{tid}",
                                       headers={"X-Project-Id": "proj-b"})
            try:
                urllib.request.urlopen(r)
                code = 200
            except urllib.error.HTTPError as e:
                code = e.code
            self.assertEqual(code, 403)
            # caller from proj-a can
            r2 = urllib.request.Request(f"http://127.0.0.1:8124/v1/tasks/{tid}",
                                        headers={"X-Project-Id": "proj-a"})
            self.assertEqual(urllib.request.urlopen(r2).status, 200)
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
