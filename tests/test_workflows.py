"""End-to-end workflow tests: manual chapter 4 flows + budget enforcement."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unified.adapters import AdapterContext, register_adapters
from unified.broker import CapabilityBroker
from unified.contracts import Step, TaskState
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
        self.router = ModelRouter(max_concurrent=1)
        self.broker.register_model_tool(self.router)
        self.engine = Engine(self.ledger, self.broker, self.evidence,
                             model_router=self.router)

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

    def test_worker_cpu_usage_is_persisted_with_task(self):
        import unified.engine as engine_module
        from unified.broker import ToolSpec
        def consume_cpu(_args):
            deadline = time.process_time() + 0.05
            while time.process_time() < deadline:
                pass
            return {"summary": "measured"}
        self.broker.register(ToolSpec("test.cpu-account", [], "read_only", consume_cpu))
        original_plan = engine_module.plan
        engine_module.plan = lambda _env: [Step("S-CPU-ACCOUNT", "test.cpu-account", {})]
        try:
            submitted = self.engine.submit(
                "p", "wf-cpu-accounting", {"intent": "echo", "instructions": "measure"},
                [], GRANTS, budgets={"memory_mb": 2048})
            result = self.engine.run(submitted["task_id"], GRANTS)
        finally:
            engine_module.plan = original_plan
        self.assertEqual(result["state"], TaskState.COMPLETED.value,
                         msg=f"{result}; receipts={self.ledger.get_receipts(submitted['task_id'])}")
        task_cpu = self.ledger.task_cpu_seconds_used(submitted["task_id"])
        receipt_cpu = sum(r.usage.get("cpu_seconds", 0)
                          for r in self.ledger.get_receipts(submitted["task_id"]))
        self.assertGreater(task_cpu, 0)
        self.assertAlmostEqual(task_cpu, receipt_cpu, places=3)

    def test_engine_worker_memory_limit_returns_budget_result(self):
        import unified.engine as engine_module
        from unified.broker import ToolSpec
        self.broker.register(ToolSpec(
            "test.engine-memory", [], "read_only",
            lambda _args: {"summary": len(bytearray(128 * 1024 * 1024))}))
        original_plan = engine_module.plan
        engine_module.plan = lambda _env: [Step("S-ENGINE-MEM", "test.engine-memory", {})]
        try:
            submitted = self.engine.submit(
                "p", "wf-engine-memory-budget",
                {"intent": "echo", "instructions": "memory"}, [], GRANTS,
                budgets={"memory_mb": 48})
            result = self.engine.run(submitted["task_id"], GRANTS)
        finally:
            engine_module.plan = original_plan
        self.assertEqual(result["state"], TaskState.FAILED.value)
        self.assertIn({"step_id": "S-ENGINE-MEM", "budget": "memory_mb"},
                      result["budget_exceeded"])

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

    def test_repeated_model_calls_share_one_cumulative_token_ceiling(self):
        import unified.engine as engine_module
        original_plan = engine_module.plan
        model_steps = [
            Step("S-MODEL-1", "model.complete", {"prompt": "hi"},
                 permissions_used=["model.infer"]),
            Step("S-MODEL-2", "model.complete", {"prompt": "hi"},
                 depends_on=["S-MODEL-1"], permissions_used=["model.infer"]),
        ]
        engine_module.plan = lambda _env: model_steps
        try:
            submitted = self.engine.submit(
                "p", "wf-repeated-model-budget",
                {"intent": "echo", "instructions": "two model calls"},
                ["model.infer"], GRANTS, budgets={"model_tokens": 8})
            result = self.engine.run(submitted["task_id"], GRANTS)
        finally:
            engine_module.plan = original_plan
        self.assertEqual(result["state"], TaskState.COMPLETED.value)
        receipts = self.ledger.get_receipts(submitted["task_id"])
        self.assertEqual(sum(r.usage["model_tokens"] for r in receipts), 6)
        self.assertEqual(self.router._tokens_used, 6)

    def test_cpu_and_memory_budgets_are_enforced_or_validated(self):
        submitted = self.engine.submit(
            "p", "wf-cpu-budget-zero", {"intent": "echo", "instructions": "x"},
            [], GRANTS, budgets={"cpu_seconds": 0})
        result = self.engine.run(submitted["task_id"], GRANTS)
        self.assertEqual(result["state"], TaskState.FAILED.value)
        self.assertEqual(result["budget_exceeded"][0]["budget"], "cpu_seconds")
        with self.assertRaisesRegex(ValueError, "memory_mb must be greater than zero"):
            self.engine.submit("p", "wf-memory-zero",
                               {"intent": "echo", "instructions": "x"}, [], GRANTS,
                               budgets={"memory_mb": 0})

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
