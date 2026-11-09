"""Scheduler tests: wave parallelism and crash recovery (manual 4.5, 5.4)."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unified.broker import CapabilityBroker, ToolSpec
from unified.contracts import Step, TaskState
from unified.engine import Engine
from unified.evidence import EvidenceStore
from unified.ledger import Ledger

GRANTS = ["repo.read"]


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.ws = os.path.join(d, "ws")
        os.makedirs(self.ws)
        self.ledger = Ledger()
        self.evidence = EvidenceStore(os.path.join(d, "art"))
        self.broker = CapabilityBroker(self.evidence, workspace_root=self.ws)

        def slow(args):
            time.sleep(args.get("ms", 300) / 1000)
            return {"result_type": "test.slow.v1", "summary": "slept",
                    "data": {"ms": args.get("ms", 300)}}
        self.broker.register(ToolSpec("test.slow", [], "read_only", slow, "sleep tool"))

    def tearDown(self):
        self.tmp.cleanup()

    def _run_steps(self, task_id, steps):
        from unified.planner import plan  # noqa
        self.ledger.save_steps(task_id, steps)
        self.ledger.set_state(task_id, TaskState.PLANNED)
        self.ledger.set_state(task_id, TaskState.RUNNING)
        return self._engine_run(task_id)

    def _engine_run(self, task_id):
        # minimal run path: reuse Engine.run but with pre-saved steps
        eng = Engine(self.ledger, self.broker, self.evidence)
        # bypass planner by monkeypatching plan via env intent fallback
        import unified.engine as eng_mod
        orig = eng_mod.plan
        eng_mod.plan = lambda env: self.ledger.get_steps(env.task_id)
        try:
            return eng.run(task_id, GRANTS)
        finally:
            eng_mod.plan = orig

    def test_parallel_wave_faster_than_serial(self):
        eng = Engine(self.ledger, self.broker, self.evidence)
        out = eng.submit("p", "par-1", {"intent": "echo", "instructions": "x"}, [], GRANTS)
        tid = out["task_id"]
        steps = [Step(f"S-{i}", "test.slow", {"ms": 300}) for i in range(3)]
        self.ledger.save_steps(tid, steps)
        self.ledger.set_state(tid, TaskState.PLANNED)
        self.ledger.set_state(tid, TaskState.RUNNING)
        import unified.engine as eng_mod
        orig = eng_mod.plan
        eng_mod.plan = lambda env: steps
        t0 = time.monotonic()
        try:
            r = eng.run(tid, GRANTS)
        finally:
            eng_mod.plan = orig
        elapsed = time.monotonic() - t0
        self.assertEqual(r["state"], TaskState.COMPLETED.value)
        self.assertLess(elapsed, 0.85)  # 3x300ms serial would be >=0.9s

    def test_crash_recovery_reuses_receipts(self):
        d = self.tmp.name
        path = os.path.join(d, "led.db")
        # "crash": task left RUNNING with one committed receipt, no result
        l1 = Ledger(path)
        eng1 = Engine(l1, self.broker, self.evidence)
        out = eng1.submit("p", "crash-1", {"intent": "echo", "instructions": "x"}, [], GRANTS)
        tid = out["task_id"]
        from unified.planner import plan
        from unified.contracts import TaskEnvelope
        env = TaskEnvelope(**l1.get_task(tid)["envelope"])
        l1.save_steps(tid, plan(env))
        l1.set_state(tid, TaskState.PLANNED)
        l1.set_state(tid, TaskState.RUNNING)
        step = l1.get_steps(tid)[0]
        from unified.contracts import ToolReceipt
        l1.save_receipt(ToolReceipt(tid, step.step_id, "A-1", "succeeded",
                                    "native.echo.v1", "echoed input",
                                    data={"text": "x"}), committed=True)
        l1.close()
        # "restart": new engine on same ledger, recover
        l2 = Ledger(path)
        eng2 = Engine(l2, self.broker, self.evidence)
        recovered = eng2.recover(GRANTS)
        self.assertEqual(recovered[0]["task_id"], tid)
        self.assertEqual(recovered[0]["outcome"], "completed")
        task = l2.get_task(tid)
        self.assertEqual(task["state"], TaskState.COMPLETED.value)
        # exactly one committed receipt for the step (no re-execution)
        self.assertEqual(len(l2.get_receipts(tid)), 1)
        l2.close()


if __name__ == "__main__":
    unittest.main()
