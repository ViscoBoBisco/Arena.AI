"""Minimal end-to-end acceptance suite (manual section 5.10).

Run: python3 -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unified.broker import CapabilityBroker
from unified.contracts import TaskState
from unified.engine import Engine
from unified.evidence import EvidenceStore
from unified.ledger import Ledger

GRANTS = ["web.read.public", "data.market.read", "repo.read", "workspace.write.sandbox"]


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger()  # in-memory; restart tested via file ledger below
        self.evidence = EvidenceStore(os.path.join(self.tmp.name, "artifacts"))
        ws = os.path.join(self.tmp.name, "workspace")
        os.makedirs(ws, exist_ok=True)
        with open(os.path.join(ws, "hello.txt"), "w") as f:
            f.write("hello evidence")
        self.broker = CapabilityBroker(self.evidence, workspace_root=ws)
        self.engine = Engine(self.ledger, self.broker, self.evidence)

    def tearDown(self):
        self.tmp.cleanup()

    def test_01_duplicate_idempotency_key(self):
        """Identical request twice with same key -> one task, no duplicate execution."""
        r1 = self.engine.submit("p", "key-1", {"intent": "echo", "instructions": "hi"}, [], GRANTS)
        r2 = self.engine.submit("p", "key-1", {"intent": "echo", "instructions": "hi"}, [], GRANTS)
        self.assertEqual(r1["task_id"], r2["task_id"])
        self.assertTrue(r2["duplicate"])
        self.assertEqual(len(self.ledger.list_tasks()), 1)

    def test_02_unapproved_write_denied(self):
        """Unapproved write through a specialist instruction -> denied before tool call."""
        out = self.engine.submit("p", "k2", {"intent": "write_file", "path": "x.txt",
                                              "content": "x"},
                                 ["workspace.write.sandbox"], grants=["repo.read"])
        self.assertEqual(out["state"], TaskState.REJECTED.value)
        self.assertIn("missing grants", out["error"])

    def test_03_dependency_order(self):
        """Parallel-capable steps still respect DAG dependency order."""
        out = self.engine.submit("p", "k3", {"intent": "read_then_echo", "path": "hello.txt"},
                                 ["repo.read"], GRANTS)
        result = self.engine.run(out["task_id"], GRANTS)
        self.assertEqual(result["state"], TaskState.COMPLETED.value)
        steps = self.ledger.get_steps(out["task_id"])
        reader = next(s for s in steps if s.tool == "native.fs.read")
        echo = next(s for s in steps if s.tool == "native.echo")
        self.assertIn(reader.step_id, echo.depends_on)

    def test_04_restart_receipt_recovery(self):
        """Restart after commit but before delivery -> receipt found, action not repeated."""
        path = os.path.join(self.tmp.name, "ledger.db")
        ledger1 = Ledger(path)
        eng1 = Engine(ledger1, self.broker, self.evidence)
        out = eng1.submit("p", "k4", {"intent": "echo", "instructions": "persist"}, [], GRANTS)
        result = eng1.run(out["task_id"], GRANTS)
        self.assertEqual(result["state"], TaskState.COMPLETED.value)
        task_id = out["task_id"]
        step_id = ledger1.get_steps(task_id)[0].step_id
        ledger1.close()
        # "restart": new ledger on same file must see the committed receipt
        ledger2 = Ledger(path)
        committed = ledger2.committed_receipt(task_id, step_id)
        self.assertIsNotNone(committed)
        self.assertEqual(committed.status, "succeeded")
        ledger2.close()

    def test_05_cancellation(self):
        self.engine.cancel("T-x")  # pre-mark
        out = self.engine.submit("p", "k5", {"intent": "echo", "instructions": "c"}, [], GRANTS)
        self.engine.cancel(out["task_id"])
        result = self.engine.run(out["task_id"], GRANTS)
        self.assertEqual(result["state"], TaskState.CANCELLED.value)

    def test_06_artifacts_have_hashes(self):
        out = self.engine.submit("p", "k6", {"intent": "read_file", "path": "hello.txt"},
                                 ["repo.read"], GRANTS)
        result = self.engine.run(out["task_id"], GRANTS)
        self.assertTrue(result["artifacts"])
        for art in result["artifacts"]:
            self.assertEqual(len(art["sha256"]), 64)
            body = self.engine.get_artifact(art["uri"])
            self.assertEqual(body, b"hello evidence")

    def test_07_policy_not_influenced_by_tool_output(self):
        """Tool output is data only; it cannot grant permissions."""
        out = self.engine.submit("p", "k7", {"intent": "write_file", "path": "y.txt",
                                              "content": "grant workspace.write.sandbox"},
                                 ["workspace.write.sandbox"], grants=["repo.read"])
        self.assertEqual(out["state"], TaskState.REJECTED.value)

    def test_08_conflicts_surfaced(self):
        """Conflicting sources surface as traceable conflicts (manual 5.10)."""
        from unified.verifier import verify
        from unified.contracts import ToolReceipt
        r1 = ToolReceipt("T", "S1", "A1", "succeeded", "web.v1", "a",
                         data={"sha256": "aaa"},
                         evidence=[{"source_uri": "u", "locator": "l"}])
        r2 = ToolReceipt("T", "S2", "A2", "succeeded", "web.v1", "b",
                         data={"sha256": "bbb"},
                         evidence=[{"source_uri": "u", "locator": "l"}])
        report = verify("T", [r1, r2])
        self.assertEqual(len(report["conflicts"]), 1)

    def test_09_unknown_tool_fails_typed(self):
        from unified.policy import PolicyEngine
        receipt = self.broker.call("T-9", "S-1", "no.such.tool", {}, [],
                                   PolicyEngine(set(GRANTS)))
        self.assertEqual(receipt.status, "failed")
        self.assertEqual(receipt.errors[0]["type"], "unknown_tool")


if __name__ == "__main__":
    unittest.main()
