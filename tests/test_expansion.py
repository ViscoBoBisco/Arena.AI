"""Tests for the Phase 3-5 expansion: adapters, model router, roles, automation."""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unified.adapters import AdapterContext, register_adapters
from unified.automation import AutomationConnector
from unified.broker import CapabilityBroker, ToolSpec
from unified.engine import Engine
from unified.evidence import EvidenceStore
from unified.ledger import Ledger
from unified.model_router import ModelRouter
from unified.policy import PolicyEngine
from unified.role_registry import RoleRegistry

GRANTS = ["web.read.public", "data.market.read", "repo.read",
          "workspace.write.sandbox", "binary.inspect.static", "model.infer"]


class ExpansionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.ws = os.path.join(d, "workspace")
        self.fix = os.path.join(d, "fixtures")
        os.makedirs(self.ws); os.makedirs(self.fix)
        with open(os.path.join(self.fix, "page1.html"), "w") as f:
            f.write("<html><title>Demo</title><main>body text</main></html>")
        with open(os.path.join(self.ws, "mod.py"), "w") as f:
            f.write("def alpha():\n    pass\nclass Beta:\n    pass\n")
        with open(os.path.join(self.ws, "blob.bin"), "wb") as f:
            f.write(b"\x7fELF" + b"\x00" * 60 + b"hello_world_string")
        self.ledger = Ledger()
        self.evidence = EvidenceStore(os.path.join(d, "artifacts"))
        self.broker = CapabilityBroker(self.evidence, workspace_root=self.ws)
        register_adapters(self.broker, AdapterContext(
            evidence=self.evidence, workspace_root=self.ws,
            web_allowlist=(), fixture_dir=self.fix,
            graph_db=os.path.join(d, "graph.db")))
        self.router = ModelRouter(max_concurrent=1)
        self.broker.register_model_tool(self.router)
        self.roles = RoleRegistry()
        self.engine = Engine(self.ledger, self.broker, self.evidence,
                             model_router=self.router, roles=self.roles)
        self.engine.automation = AutomationConnector(self.engine, "test-secret")
        self.policy = PolicyEngine(set(GRANTS))

    def tearDown(self):
        self.tmp.cleanup()

    def _call(self, tool, args, perms):
        return self.broker.call("T", "S", tool, args, perms, self.policy)

    def test_web_allowlist_fails_closed(self):
        r = self._call("scrapling.fetch", {"url": "https://example.org/x"}, ["web.read.public"])
        self.assertEqual(r.status, "failed")
        self.assertEqual(r.errors[0]["type"], "capability_unavailable")

    def test_web_fetch_rejects_plain_http_and_does_not_follow_redirects(self):
        from unified.adapters import _NoRedirectHandler
        r = self._call("scrapling.fetch", {"url": "http://example.org/x"}, ["web.read.public"])
        self.assertEqual(r.status, "failed")
        self.assertEqual(r.errors[0]["type"], "policy_denied")
        handler = _NoRedirectHandler()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {},
                                                   "https://allowed.example/redirected"))

    def test_fixture_fetch_with_evidence(self):
        r = self._call("scrapling.fetch_fixture", {"name": "page1.html"}, ["web.read.public"])
        self.assertEqual(r.status, "succeeded")
        self.assertEqual(r.data["title"], "Demo")
        self.assertEqual(len(r.artifacts[0]["sha256"]), 64)

    def test_fixture_path_traversal_rejected(self):
        r = self._call("scrapling.fetch_fixture", {"name": "../workspace/mod.py"}, ["web.read.public"])
        self.assertEqual(r.errors[0]["type"], "policy_denied")

    def test_openbb_honest_capability_error(self):
        r = self._call("openbb.query", {"command": "equity.price", "provider": "yfinance"},
                       ["data.market.read"])
        self.assertEqual(r.status, "failed")
        self.assertEqual(r.errors[0]["type"], "capability_unavailable")

    def test_codegen_index_and_find(self):
        r = self._call("codegraph.index", {"root": ".", "commit": "abc123"}, ["repo.read"])
        self.assertEqual(r.data["symbols"], 2)
        r2 = self._call("codegraph.find", {"query": "alpha"}, ["repo.read"])
        self.assertEqual(r2.data["matches"][0]["file"], "mod.py")
        self.assertEqual(r2.data["matches"][0]["commit"], "abc123")

    def test_rea_static_inspect(self):
        r = self._call("rea.inspect", {"path": "blob.bin"}, ["binary.inspect.static"])
        self.assertEqual(r.status, "succeeded")
        self.assertEqual(r.data["format"], "ELF")
        with open(os.path.join(self.ws, "blob.bin"), "rb") as f:
            expected_sha256 = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(r.data["sha256"], expected_sha256)
        self.assertIn("hello_world_string", r.data["strings_sample"])

    def test_rea_requires_grant(self):
        r = self.broker.call("T", "S", "rea.inspect", {"path": "blob.bin"},
                             ["binary.inspect.static"], PolicyEngine(set()))
        self.assertEqual(r.status, "failed")
        self.assertEqual(r.errors[0]["type"], "policy_denied")

    def test_model_router_budget_and_queue(self):
        ok = self.router.complete("hello world", budget_tokens=100)
        self.assertTrue(ok["ok"])
        exhausted = self.router.complete("more text here", budget_tokens=1)
        self.assertFalse(exhausted["ok"])

    def test_model_router_queue_wait_propagates_cancellation_and_deadline(self):
        router = ModelRouter(max_concurrent=1)
        self.assertTrue(router._sem.acquire(blocking=False))
        cancelled = threading.Event()
        result = {}
        worker = threading.Thread(target=lambda: result.setdefault(
            "cancel", router.complete("hello", task_id="cancel-test",
                                       cancel_check=cancelled.is_set)))
        worker.start()
        time.sleep(0.03)
        cancelled.set()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertTrue(result["cancel"]["cancelled"])

        timed = router.complete("hello", task_id="deadline-test",
                                deadline_at=time.time() + 0.03)
        self.assertEqual(timed.get("budget"), "time_seconds")
        router._sem.release()

    def test_model_router_budgets_are_isolated_by_task(self):
        router = ModelRouter(max_concurrent=1)
        first = router.complete("hi", budget_tokens=5, task_id="task-a")
        other = router.complete("hi", budget_tokens=5, task_id="task-b")
        repeated = router.complete("hi", budget_tokens=5, task_id="task-a")
        self.assertTrue(first["ok"])
        self.assertTrue(other["ok"])
        self.assertEqual(first["usage"]["model_tokens"], other["usage"]["model_tokens"])
        self.assertFalse(repeated["ok"])
        self.assertEqual(repeated["usage"]["tokens_used"], first["usage"]["model_tokens"])
        router.forget_task("task-a")
        self.assertTrue(router.complete("hi", budget_tokens=5, task_id="task-a")["ok"])
        router.restore_task_usage("task-recovered", 3)
        self.assertFalse(router.complete("hi", budget_tokens=5,
                                         task_id="task-recovered")["ok"])

    def test_model_tool(self):
        r = self._call("model.complete", {"prompt": "summarize", "budget_tokens": 100},
                       ["model.infer"])
        self.assertEqual(r.status, "succeeded")
        self.assertIn("model_tokens", r.usage)

    def test_broker_normalizes_cooperative_cancel_receipt(self):
        self.broker.register(ToolSpec(
            "test.cancelled", [], "read_only",
            lambda _args: {"result_type": "cancelled.v1", "summary": "stopped",
                           "errors": [{"type": "cancelled"}]},
            supports_cancellation=True))
        receipt = self._call("test.cancelled", {}, [])
        self.assertEqual(receipt.status, "cancelled")
        self.assertEqual(receipt.result_type, "cancelled.v1")

    def test_roles_immutable_profiles(self):
        names = [r["name"] for r in self.roles.list()]
        self.assertIn("verifier", names)
        self.assertIsNone(self.roles.get("nonexistent"))

    def test_signed_callback_and_replay(self):
        body = json.dumps({"event_id": "evt-1", "task_id": None}).encode()
        ts = str(int(time.time()))
        sig = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
        r1 = self.engine.automation.callback(body, sig, ts)
        r2 = self.engine.automation.callback(body, sig, ts)
        self.assertTrue(r1["accepted"] and not r1["replay"])
        self.assertTrue(r2["replay"])
        bad = self.engine.automation.callback(body, "sha256=deadbeef", ts)
        self.assertFalse(bad["accepted"])
        stale = self.engine.automation.callback(body, sig, str(int(time.time()) - 9999))
        self.assertFalse(stale["accepted"])

    def test_automation_trigger_idempotent(self):
        req = {"intent": "echo", "instructions": "monitor"}
        r1 = self.engine.automation.trigger("wf-1", "exec-1", req, [])
        r2 = self.engine.automation.trigger("wf-1", "exec-1", req, [])
        self.assertEqual(r1["task_id"], r2["task_id"])
        self.assertTrue(r2["duplicate"])

    def test_full_task_with_adapters(self):
        out = self.engine.submit("p", "k-full",
                                 {"intent": "fetch_fixture", "name": "page1.html"},
                                 ["web.read.public"], GRANTS)
        result = self.engine.run(out["task_id"], GRANTS)
        self.assertEqual(result["state"], "COMPLETED")
        self.assertTrue(result["observed_evidence"])
        self.assertTrue(result["artifacts"])


if __name__ == "__main__":
    unittest.main()
