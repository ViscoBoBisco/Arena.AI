"""Qualification tests for isolated, supervised capability workers."""
import os
import tempfile
import time
import unittest

from unified.broker import CapabilityBroker, ToolSpec
from unified.evidence import EvidenceStore
from unified.policy import PolicyEngine
from unified.workers import dispatch_isolated


class IsolatedWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.evidence = EvidenceStore(os.path.join(self.tmp.name, "artifacts"))
        self.broker = CapabilityBroker(self.evidence,
                                       workspace_root=os.path.join(self.tmp.name, "ws"))
        os.makedirs(self.broker.workspace_root, exist_ok=True)
        self.policy = PolicyEngine(set())

    def tearDown(self):
        self.tmp.cleanup()

    def _dispatch(self, name, deadline=2, cpu=1, memory=128, mutating=False):
        spec = self.broker.get(name)
        return dispatch_isolated(
            self.broker,
            ("T-WORKER", "S-WORKER", name, {}, [], self.policy),
            {"attempt_id": "A-WORKER", "deadline_ms": int(deadline * 1000),
             "deadline_at": time.time() + deadline,
             "cancel_check": lambda: False},
            deadline_at=time.time() + deadline,
            cancel_check=lambda: False, cpu_seconds=cpu, memory_mb=memory,
            mutating=mutating or bool(spec and spec.side_effect_class != "read_only"))

    def test_wall_deadline_kills_noncooperative_worker(self):
        def stuck(_args):
            while True:
                pass
        self.broker.register(ToolSpec("test.stuck", [], "read_only", stuck))
        started = time.monotonic()
        outcome = self._dispatch("test.stuck", deadline=0.15, cpu=5)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertIsNotNone(outcome.receipt)
        self.assertEqual(outcome.receipt.errors[0]["budget"], "time_seconds")

    def test_cpu_limit_kills_cpu_bound_worker_and_accounts_usage(self):
        def burn_cpu(_args):
            while True:
                pass
        self.broker.register(ToolSpec("test.cpu", [], "read_only", burn_cpu))
        outcome = self._dispatch("test.cpu", deadline=2, cpu=0.12)
        self.assertIsNotNone(outcome.receipt)
        self.assertEqual(outcome.receipt.errors[0]["budget"], "cpu_seconds")
        self.assertGreater(outcome.cpu_seconds, 0)

    def test_address_space_limit_reports_memory_budget_exceeded(self):
        def allocate(_args):
            return {"summary": len(bytearray(128 * 1024 * 1024))}
        self.broker.register(ToolSpec("test.memory", [], "read_only", allocate))
        outcome = self._dispatch("test.memory", deadline=2, cpu=2, memory=48)
        self.assertIsNotNone(outcome.receipt)
        self.assertEqual(outcome.receipt.errors[0]["budget"], "memory_mb")

    def test_mutating_worker_termination_is_marked_ambiguous(self):
        def effect_then_stall(_args):
            path = os.path.join(self.tmp.name, "external-effect")
            with open(path, "w") as stream:
                stream.write("applied")
            while True:
                pass
        # Closure accesses temp path inherited through fork, simulating an
        # external durable effect that completes before its worker is killed.
        self.broker.register(ToolSpec("test.effect", [], "external_effect", effect_then_stall))
        outcome = self._dispatch("test.effect", deadline=0.2, cpu=5, mutating=True)
        self.assertTrue(outcome.ambiguous)
        self.assertIsNone(outcome.receipt)
        with open(os.path.join(self.tmp.name, "external-effect")) as stream:
            self.assertEqual(stream.read(), "applied")


if __name__ == "__main__":
    unittest.main()
