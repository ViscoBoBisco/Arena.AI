"""Tests for durable approvals, SSE streaming, memory, metrics, hash checks."""
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unified.broker import CapabilityBroker
from unified.contracts import TaskState
from unified.engine import Engine
from unified.evidence import EvidenceStore
from unified.ledger import Ledger
from unified.memory import ConversationMemory

GRANTS = ["web.read.public", "data.market.read", "repo.read",
          "workspace.write.sandbox", "binary.inspect.static", "model.infer"]


class ApprovalFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.ws = os.path.join(d, "workspace")
        os.makedirs(self.ws)
        self.ledger = Ledger()
        self.evidence = EvidenceStore(os.path.join(d, "artifacts"))
        self.broker = CapabilityBroker(self.evidence, workspace_root=self.ws)
        self.engine = Engine(self.ledger, self.broker, self.evidence,
                             memory=ConversationMemory())

    def tearDown(self):
        self.tmp.cleanup()

    def test_side_effect_waits_for_approval_then_completes_once(self):
        out = self.engine.submit("p", "ap-1",
                                 {"intent": "write_file", "path": "a.txt", "content": "data"},
                                 ["workspace.write.sandbox"], GRANTS)
        r1 = self.engine.run(out["task_id"], GRANTS)
        self.assertEqual(r1["state"], TaskState.WAITING_APPROVAL.value)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "a.txt")))
        # resume via durable approval; action executes exactly once
        r2 = self.engine.approve(out["task_id"], "approved", GRANTS)
        self.assertEqual(r2["result"]["state"], TaskState.COMPLETED.value)
        self.assertTrue(os.path.exists(os.path.join(self.ws, "a.txt")))
        # re-approving must not re-execute the side effect
        r3 = self.engine.approve(out["task_id"], "approved", GRANTS)
        self.assertNotEqual(r3.get("result", {}).get("state"), TaskState.RUNNING.value)

    def test_cancel_waiting_approval_finishes_and_clears_pending_request(self):
        out = self.engine.submit("p", "ap-cancel",
                                 {"intent": "write_file", "path": "cancelled.txt", "content": "x"},
                                 ["workspace.write.sandbox"], GRANTS)
        waiting = self.engine.run(out["task_id"], GRANTS)
        self.assertEqual(waiting["state"], TaskState.WAITING_APPROVAL.value)
        self.assertTrue(self.engine.cancel(out["task_id"]))
        task = self.engine.get_task(out["task_id"])
        self.assertEqual(task["state"], TaskState.CANCELLED.value)
        self.assertEqual(task["pending_approvals"], [])
        self.assertFalse(os.path.exists(os.path.join(self.ws, "cancelled.txt")))
        self.assertFalse(self.engine.cancel(out["task_id"]))

    def test_cancel_during_active_tool_finishes_as_cancelled(self):
        started = threading.Event()
        release = threading.Event()
        spec = self.broker.get("native.echo")

        def slow_echo(args):
            started.set()
            self.assertTrue(release.wait(2))
            return {"result_type": "native.echo.v1", "summary": "finished",
                    "data": {"text": args.get("text", "")}}

        spec.handler = slow_echo
        out = self.engine.submit("p", "ap-running-cancel",
                                 {"intent": "echo", "instructions": "work"}, [], GRANTS)
        result_box = {}
        worker = threading.Thread(
            target=lambda: result_box.update(self.engine.run(out["task_id"], GRANTS)))
        worker.start()
        self.assertTrue(started.wait(2))
        self.assertTrue(self.engine.cancel(out["task_id"]))
        release.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result_box["state"], TaskState.CANCELLED.value)
        self.assertEqual(self.engine.get_task(out["task_id"])["state"], TaskState.CANCELLED.value)

    def test_rejection_cancels(self):
        out = self.engine.submit("p", "ap-2",
                                 {"intent": "write_file", "path": "b.txt", "content": "x"},
                                 ["workspace.write.sandbox"], GRANTS)
        self.engine.run(out["task_id"], GRANTS)
        r = self.engine.approve(out["task_id"], "rejected", GRANTS)
        self.assertEqual(r["state"], TaskState.CANCELLED.value)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "b.txt")))

    def test_read_only_needs_no_approval(self):
        out = self.engine.submit("p", "ap-3", {"intent": "echo", "instructions": "hi"},
                                 [], GRANTS)
        r = self.engine.run(out["task_id"], GRANTS)
        self.assertEqual(r["state"], TaskState.COMPLETED.value)

    def test_memory_scoped(self):
        self.engine.memory.put("proj-a", "pref", {"tone": "formal"})
        self.assertEqual(self.engine.memory.get("proj-a", "pref"), {"tone": "formal"})
        self.assertIsNone(self.engine.memory.get("proj-b", "pref"))

    def test_metrics_accumulate(self):
        self.engine.submit("p", "m-1", {"intent": "echo", "instructions": "x"}, [], GRANTS)
        out = self.engine.submit("p", "m-2", {"intent": "echo", "instructions": "y"}, [], GRANTS)
        self.engine.run(out["task_id"], GRANTS)
        m = self.engine.metrics
        self.assertGreaterEqual(m["tool_calls"], 1)
        self.assertIn("COMPLETED", m["tasks_by_state"])

    def test_verifier_hash_mismatch_detected(self):
        from unified.verifier import verify
        from unified.contracts import ToolReceipt
        r = ToolReceipt("T", "S", "A", "succeeded", "x.v1", "s",
                        data={"sha256": "aaa"},
                        artifacts=[{"uri": "artifact://x", "sha256": "bbb"}])
        report = verify("T", [r])
        self.assertEqual(len(report["hash_mismatches"]), 1)


if __name__ == "__main__":
    unittest.main()
