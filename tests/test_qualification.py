"""Phase 6 qualification: all four manual chapter-4 workflows end-to-end."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unified.adapters import AdapterContext, register_adapters
from unified.automation import AutomationConnector
from unified.broker import CapabilityBroker
from unified.contracts import TaskState
from unified.engine import Engine
from unified.evidence import EvidenceStore
from unified.ledger import Ledger
from unified.model_router import ModelRouter

GRANTS = ["web.read.public", "data.market.read", "repo.read",
          "workspace.write.sandbox", "binary.inspect.static", "model.infer"]


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.ws = os.path.join(d, "ws")
        self.fix = os.path.join(d, "fix")
        os.makedirs(self.ws); os.makedirs(self.fix)
        with open(os.path.join(self.fix, "news.html"), "w") as f:
            f.write("<main>Markets up 2%.</main>")
        with open(os.path.join(self.ws, "b.py"), "w") as f:
            f.write("def helper():\n    return 1\n")
        with open(os.path.join(self.ws, "a.py"), "w") as f:
            f.write("from b import helper\n\ndef use():\n    return helper()\n")
        with open(os.path.join(self.ws, "sample.bin"), "wb") as f:
            f.write(b"\x7fELF" + b"\x00" * 32 + b"contact http://c2.example.net")
        self.ledger = Ledger()
        self.evidence = EvidenceStore(os.path.join(d, "art"))
        self.broker = CapabilityBroker(self.evidence, workspace_root=self.ws)
        register_adapters(self.broker, AdapterContext(
            evidence=self.evidence, workspace_root=self.ws, fixture_dir=self.fix,
            graph_db=os.path.join(d, "g.db")))
        self.broker.register_model_tool(ModelRouter(max_concurrent=1))
        self.engine = Engine(self.ledger, self.broker, self.evidence)
        self.engine.automation = AutomationConnector(self.engine, "q-secret")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, key, request, perms):
        out = self.engine.submit("q", key, request, perms, GRANTS)
        return self.engine.run(out["task_id"], GRANTS)

    def test_all_four_chapter4_workflows(self):
        # 4.1 research + structured data
        r = self._run("q-41", {"intent": "research", "name": "news.html",
                               "instructions": "summarize"},
                      ["web.read.public", "model.infer"])
        self.assertEqual(r["state"], TaskState.COMPLETED.value)
        self.assertTrue(r["observed_evidence"])
        # 4.2 repo change (approval-gated write)
        out = self.engine.submit("q", "q-42", {"intent": "repo_change", "commit": "c1",
                                                "symbol": "helper", "path": "a.py",
                                                "content": "from b import helper\n"},
                                 ["repo.read", "workspace.write.sandbox"], GRANTS)
        self.engine.run(out["task_id"], GRANTS)
        r = self.engine.approve(out["task_id"], "approved", GRANTS)["result"]
        self.assertEqual(r["state"], TaskState.COMPLETED.value)
        # 4.3 binary investigation
        r = self._run("q-43", {"intent": "investigate", "path": "sample.bin", "commit": "c1"},
                      ["binary.inspect.static", "repo.read"])
        self.assertEqual(r["state"], TaskState.COMPLETED.value)
        # 4.4 scheduled monitoring via automation trigger (idempotent)
        t1 = self.engine.automation.trigger("wf", "e1", {"intent": "echo", "instructions": "m"}, [])
        t2 = self.engine.automation.trigger("wf", "e1", {"intent": "echo", "instructions": "m"}, [])
        self.assertEqual(t1["task_id"], t2["task_id"])
        self.assertTrue(t2["duplicate"])
        # trace + typed dataset tools
        from unified.policy import PolicyEngine
        pol = PolicyEngine(set(GRANTS))
        self.broker.call("T", "S", "codegraph.index", {"root": ".", "commit": "c1"}, ["repo.read"], pol)
        tr = self.broker.call("T", "S", "codegraph.trace", {"symbol": "helper"}, ["repo.read"], pol)
        self.assertEqual(tr.status, "succeeded")
        self.assertIn("a.py", tr.data["transitive_callers"])
        art = self.evidence.put_artifact("q", "s", "ds.json", b'[{"date":"2026-11-06","close":1.0}]')
        q = self.broker.call("T", "S", "openbb.query",
                             {"dataset_uri": art["uri"], "unit": "USD"}, ["data.market.read"], pol)
        self.assertEqual(q.status, "succeeded")
        self.assertEqual(q.data["unit"], "USD")
        self.assertEqual(q.data["rows"], 1)


if __name__ == "__main__":
    unittest.main()
