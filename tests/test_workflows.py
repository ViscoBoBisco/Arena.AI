"""End-to-end workflow tests: manual chapter 4 flows + budget enforcement."""
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
from unified.model_router import ModelRouter
from unified.policy import PolicyEngine

GRANTS = ["web.read.public", "data.market.read", "repo.read",
          "workspace.write.sandbox", "binary.inspect.static", "model.infer"]


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.ws = os.path.join(d, "workspace")
        self.fix = os.path.join(d, "fixtures")
        os.makedirs(self.ws); os.makedirs(self.fix)
        with open(os.path.join(self.fix, "demo.html"), "w") as f:
            f.write("<html><title>Market News</title><main>Stocks rallied today.</main></html>")
        with open(os.path.join(self.ws, "a.py"), "w") as f:
            f.write("from b import helper\n\ndef use():\n    return helper()\n")
        with open(os.path.join(self.ws, "b.py"), "w") as f:
            f.write("def helper():\n    return 42\n")
        self.ledger = Ledger()
        self.evidence = EvidenceStore(os.path.join(d, "artifacts"))
        self.broker = CapabilityBroker(self.evidence, workspace_root=self.ws)
        register_adapters(self.broker, AdapterContext(
            evidence=self.evidence, workspace_root=self.ws, fixture_dir=self.fix,
            graph_db=os.path.join(d, "graph.db")))
        self.broker.register_model_tool(ModelRouter(max_concurrent=1))
        self.engine = Engine(self.ledger, self.broker, self.evidence)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, key, request, perms, budgets=None):
        out = self.engine.submit("p", key, request, perms, GRANTS, budgets=budgets)
        return self.engine.run(out["task_id"], GRANTS)

    def test_ch4_research_workflow(self):
        """Manual 4.1: web evidence -> model synthesis -> verified report."""
        r = self._run("wf-research", {"intent": "research", "name": "demo.html",
                                      "instructions": "one-line summary"},
                      ["web.read.public", "model.infer"])
        self.assertEqual(r["state"], TaskState.COMPLETED.value)
        self.assertTrue(r["observed_evidence"])          # observed page evidence
        self.assertTrue(r["artifacts"])                  # hashed artifact
        self.assertEqual(r["observed_evidence"][0]["source_uri"], "fixture://demo.html")

    def test_budget_tool_calls_hard_limit(self):
        r = self._run("wf-budget", {"intent": "research", "name": "demo.html",
                                    "instructions": "x"},
                      ["web.read.public", "model.infer"],
                      budgets={"tool_calls": 1, "time_seconds": 600, "model_calls": 12})
        self.assertEqual(r["state"], TaskState.FAILED.value)
        self.assertEqual(r["failures"][-1]["errors"][0]["type"], "budget_exceeded")

    def test_budget_time_limit(self):
        r = self._run("wf-time", {"intent": "echo", "instructions": "x"}, [],
                      budgets={"tool_calls": 30, "time_seconds": -1, "model_calls": 12})
        self.assertEqual(r["state"], TaskState.FAILED.value)
        self.assertEqual(r["failures"][0]["errors"][0]["budget"], "time_seconds")
        self.assertEqual(r["budget_exceeded"][0]["budget"], "time_seconds")

    def test_budget_model_call_limit(self):
        r = self._run("wf-model-call-budget", {"intent": "research", "name": "demo.html",
                                               "instructions": "summarize"},
                      ["web.read.public", "model.infer"],
                      budgets={"model_calls": 0})
        self.assertEqual(r["state"], TaskState.FAILED.value)
        self.assertEqual(r["budget_exceeded"][0]["budget"], "model_calls")

    def test_budget_model_token_limit(self):
        r = self._run("wf-model-token-budget", {"intent": "research", "name": "demo.html",
                                                "instructions": "summarize"},
                      ["web.read.public", "model.infer"],
                      budgets={"model_tokens": 0})
        self.assertEqual(r["state"], TaskState.FAILED.value)
        self.assertEqual(r["budget_exceeded"][0]["budget"], "model_tokens")

    def test_cpu_and_memory_budgets_are_not_silently_ignored(self):
        with self.assertRaisesRegex(ValueError, "isolated worker enforcement"):
            self.engine.submit("p", "wf-cpu-unavailable",
                               {"intent": "echo", "instructions": "x"}, [], GRANTS,
                               budgets={"cpu_seconds": 1})
        with self.assertRaisesRegex(ValueError, "isolated worker enforcement"):
            self.engine.submit("p", "wf-memory-unavailable",
                               {"intent": "echo", "instructions": "x"}, [], GRANTS,
                               budgets={"memory_mb": 128})

    def test_budget_external_request_limit(self):
        spec = self.broker.get("scrapling.fetch_fixture")
        old = spec.external_request
        spec.external_request = True
        try:
            r = self._run("wf-external-budget", {"intent": "research", "name": "demo.html",
                                                  "instructions": "summarize"},
                          ["web.read.public", "model.infer"],
                          budgets={"external_requests": 0})
        finally:
            spec.external_request = old
        self.assertEqual(r["state"], TaskState.FAILED.value)
        self.assertEqual(r["budget_exceeded"][0]["budget"], "external_requests")

    def test_impact_trace(self):
        pol = PolicyEngine(set(GRANTS))
        self.broker.call("T", "S", "codegraph.index", {"root": ".", "commit": "c1"},
                         ["repo.read"], pol)
        r = self.broker.call("T", "S", "codegraph.impact", {"symbol": "use"},
                             ["repo.read"], pol)
        self.assertEqual(r.status, "succeeded")
        self.assertIn("a.py", r.data["impacted_files"])
        self.assertNotIn("b.py", r.data["impacted_files"])


if __name__ == "__main__":
    unittest.main()
