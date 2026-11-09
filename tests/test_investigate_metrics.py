"""Ch.4.3 binary investigation + metrics summary tests."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unified.adapters import AdapterContext, register_adapters
from unified.broker import CapabilityBroker
from unified.contracts import TaskState
from unified.engine import Engine
from unified.evidence import EvidenceStore
from unified.ledger import Ledger

GRANTS = ["web.read.public", "data.market.read", "repo.read",
          "workspace.write.sandbox", "binary.inspect.static", "model.infer"]


class InvestigateMetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.ws = os.path.join(d, "ws")
        os.makedirs(self.ws)
        with open(os.path.join(self.ws, "sample.bin"), "wb") as f:
            f.write(b"\x7fELF" + b"\x00" * 40 + b"visit http://evil.example.com/x from 10.0.0.1 now")
        with open(os.path.join(self.ws, "m.py"), "w") as f:
            f.write("def f():\n    pass\n")
        self.db_path = os.path.join(d, "ledger.db")
        self.ledger = Ledger(self.db_path)
        self.evidence = EvidenceStore(os.path.join(d, "art"))
        self.broker = CapabilityBroker(self.evidence, workspace_root=self.ws)
        register_adapters(self.broker, AdapterContext(
            evidence=self.evidence, workspace_root=self.ws,
            graph_db=os.path.join(d, "g.db")))
        self.engine = Engine(self.ledger, self.broker, self.evidence)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ch4_3_investigate_static_only(self):
        out = self.engine.submit("p", "inv-1",
                                 {"intent": "investigate", "path": "sample.bin", "commit": "c1"},
                                 ["binary.inspect.static", "repo.read"], GRANTS)
        r = self.engine.run(out["task_id"], GRANTS)
        self.assertEqual(r["state"], TaskState.COMPLETED.value)
        # rea.inspect step carried static-only triage with IOCs
        from unified.delivery import evidence_with_citations
        cites = evidence_with_citations(self.ledger.get_receipts(out["task_id"]))
        self.assertTrue(any("sha256:" in c["locator"] for c in cites))

    def test_rea_iocs_extracted(self):
        from unified.policy import PolicyEngine
        r = self.broker.call("T", "S", "rea.inspect", {"path": "sample.bin"},
                             ["binary.inspect.static"], PolicyEngine(set(GRANTS)))
        self.assertEqual(r.status, "succeeded")
        self.assertEqual(r.data["mode"], "static-only")
        self.assertIn("http://evil.example.com/x", r.data["iocs"]["urls"])
        self.assertIn("10.0.0.1", r.data["iocs"]["ips"])
        self.assertGreater(r.data["entropy"], 0)

    def test_metrics_summary_shape(self):
        for i in range(3):
            out = self.engine.submit("p", f"m-{i}", {"intent": "echo", "instructions": "x"}, [], GRANTS)
            self.engine.run(out["task_id"], GRANTS)
        m = self.engine.metrics
        lat = sorted(m["latencies_ms"])
        self.assertEqual(len(lat), 3)
        self.assertEqual(m["tasks_by_state"].get("COMPLETED"), 3)
        self.assertEqual(m["tool_calls"], 3)

    def test_durable_accounting_survives_engine_restart(self):
        out = self.engine.submit("durable", "usage-1",
                                 {"intent": "echo", "instructions": "usage"}, [], GRANTS,
                                 budgets={"memory_mb": 256})
        task_id = out["task_id"]
        result = self.engine.run(task_id, GRANTS)
        self.assertEqual(result["state"], TaskState.COMPLETED.value)
        receipt = self.ledger.get_receipts(task_id)[0]
        self.assertIn("elapsed_ms", receipt.usage)
        self.assertIn("cpu_seconds", receipt.usage)
        self.assertIn("peak_rss_bytes", receipt.usage)
        self.assertIn("memory_limit_bytes", receipt.usage)
        self.assertEqual(receipt.usage["memory_limit_bytes"], 256 * 1024 * 1024)
        self.assertGreaterEqual(self.ledger.usage_metrics("durable")["tool_calls"], 1)
        restarted_ledger = Ledger(self.db_path)
        restarted = Engine(restarted_ledger, self.broker, self.evidence)
        metrics = restarted.metrics_for_project("durable")
        restarted_ledger.close()
        self.assertEqual(metrics["tool_calls"], 1)
        self.assertEqual(metrics["receipt_count"], 1)
        self.assertGreaterEqual(metrics["cpu_seconds"], 0)
        self.assertGreaterEqual(metrics["max_worker_peak_rss_bytes"],
                                receipt.usage["peak_rss_bytes"])


if __name__ == "__main__":
    unittest.main()
