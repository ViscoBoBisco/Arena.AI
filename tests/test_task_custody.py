"""Custody invariants: immutable plan snapshots, CAS state transitions and worker fencing."""
import multiprocessing
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unified.broker import CapabilityBroker, ToolSpec
from unified.contracts import Step, StepState, TaskEnvelope, TaskState, ToolReceipt
from unified.engine import Engine
from unified.evidence import EvidenceStore
from unified.ledger import Ledger, LedgerError
from unified.planner import PLANNER_VERSION, plan


class TaskCustodyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "ledger.db")
        self.ledger = Ledger(self.db_path)
        self.evidence = EvidenceStore(os.path.join(self.tmp.name, "artifacts"))
        self.workspace = os.path.join(self.tmp.name, "workspace")
        os.makedirs(self.workspace)
        self.broker = CapabilityBroker(self.evidence, workspace_root=self.workspace)
        self.engine = Engine(self.ledger, self.broker, self.evidence)

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def _task(self, key="custody"):
        return self.engine.submit("custody-project", key,
                                  {"intent": "echo", "instructions": "custody"}, [], [])

    def test_task_identity_is_server_generated_and_collision_resistant(self):
        task = self._task()
        prefix, identifier = task["task_id"].split("-", 1)
        self.assertEqual(prefix, "T")
        self.assertEqual(len(identifier), 32)
        self.assertTrue(all(char in "0123456789ABCDEF" for char in identifier))
        saved = self.ledger.get_task(task["task_id"])
        self.assertEqual(saved["envelope"]["task_id"], task["task_id"])
        self.assertEqual(saved["envelope"]["envelope_version"], 1)

    def test_accepted_admitted_tasks_are_recovered(self):
        task_id = self._task("accepted-recovery")["task_id"]
        self.assertEqual(self.ledger.get_task(task_id)["state"], TaskState.ADMITTED.value)
        self.assertIn(task_id, [row["task_id"]
                                for row in self.ledger.list_recoverable_tasks()])
        self.assertIn(task_id, self.engine.recover([]))
        self.assertEqual(self.ledger.get_task(task_id)["state"], TaskState.COMPLETED.value)

    def test_plan_snapshot_is_versioned_hashed_and_immutable_on_resume(self):
        task = self._task("immutable-plan")
        task_id = task["task_id"]
        env = TaskEnvelope(**self.ledger.get_task(task_id)["envelope"])
        original = plan(env)
        first = self.ledger.persist_plan(task_id, original, PLANNER_VERSION)
        self.assertEqual(first["version"], 1)
        self.assertEqual(len(first["plan_hash"]), 64)
        self.assertEqual(first["planner_version"], PLANNER_VERSION)

        changed = [
            Step("S-INVALID-A", "native.echo", {}, depends_on=["S-INVALID-B"]),
            Step("S-INVALID-B", "native.echo", {}, depends_on=["S-INVALID-A"]),
        ]
        resumed = self.ledger.persist_plan(task_id, changed, "future-planner-v2")
        self.assertEqual(resumed["version"], first["version"])
        self.assertEqual(resumed["plan_hash"], first["plan_hash"])
        self.assertEqual(resumed["planner_version"], PLANNER_VERSION)
        self.assertEqual(resumed["steps"][0].args, original[0].args)
        self.assertEqual(self.ledger.get_task(task_id)["current_plan_version"], 1)

    def test_plan_validation_rejects_cycles_duplicate_edges_and_empty_plans(self):
        task_id = self._task("invalid-dag")["task_id"]
        cycle = [
            Step("S-A", "native.echo", {}, depends_on=["S-B"]),
            Step("S-B", "native.echo", {}, depends_on=["S-A"]),
        ]
        with self.assertRaisesRegex(LedgerError, "cycle"):
            self.ledger.persist_plan(task_id, cycle, PLANNER_VERSION)
        with self.assertRaisesRegex(LedgerError, "duplicate dependencies"):
            self.ledger.persist_plan(task_id, [
                Step("S-A", "native.echo", {}, depends_on=["S-B", "S-B"]),
                Step("S-B", "native.echo", {}),
            ], PLANNER_VERSION)
        with self.assertRaisesRegex(LedgerError, "invalid dependency"):
            self.ledger.persist_plan(task_id, [
                Step("S-ORPHAN", "native.echo", {}, depends_on=["MISSING"]),
            ], PLANNER_VERSION)
        with self.assertRaisesRegex(LedgerError, "at least one step"):
            self.ledger.persist_plan(task_id, [], PLANNER_VERSION)
        self.assertIsNone(self.ledger.get_plan(task_id))

    def test_engine_does_not_complete_a_cancelled_or_incomplete_step(self):
        task_id = self._task("incomplete-dag")["task_id"]
        step = Step("S-INCOMPLETE", "native.echo", {})
        import unified.engine as engine_module
        original_plan = engine_module.plan
        original_call = self.broker.call
        engine_module.plan = lambda _env: [step]

        def cancelled_receipt(task_id, step_id, tool, args, permissions, policy,
                              deadline_ms=45000, attempt_id=None, idempotency_key=None):
            return ToolReceipt(task_id, step_id, attempt_id, "cancelled",
                               "cancelled.v1", "adapter stopped before completion")

        self.broker.call = cancelled_receipt
        try:
            result = self.engine.run(task_id, [])
            self.assertEqual(result["state"], TaskState.CANCELLED.value)
            self.assertIn(step.step_id, result["cancelled_steps"])
            self.assertEqual(self.ledger.get_task(task_id)["state"], TaskState.CANCELLED.value)
        finally:
            engine_module.plan = original_plan
            self.broker.call = original_call

    def test_succeeded_step_state_without_receipt_is_not_completion_authority(self):
        task_id = self._task("missing-success-receipt")["task_id"]
        step = Step("S-NO-RECEIPT", "native.echo", {})
        self.ledger.persist_plan(task_id, [step], PLANNER_VERSION)
        self.ledger.set_step_state(task_id, step.step_id, StepState.SUCCEEDED)
        import unified.engine as engine_module
        original_plan = engine_module.plan
        engine_module.plan = lambda _env: [step]
        try:
            result = self.engine.run(task_id, [])
            self.assertEqual(result["state"], TaskState.FAILED.value)
            self.assertEqual(result["incomplete_steps"], [step.step_id])
            self.assertEqual(self.ledger.get_receipts(task_id), [])
        finally:
            engine_module.plan = original_plan

    def test_failed_dependency_prevents_false_task_completion(self):
        task_id = self._task("failed-dependency")["task_id"]
        dependent_dispatches = []
        self.broker.register(ToolSpec(
            "test.failed", [], "read_only",
            lambda _args: {"errors": [{"type": "expected_failure"}],
                           "summary": "failed"}))
        self.broker.register(ToolSpec(
            "test.dependent", [], "read_only",
            lambda _args: dependent_dispatches.append(True) or {
                "summary": "must not run"}))
        first = Step("S-FAIL", "test.failed", {})
        second = Step("S-AFTER-FAIL", "test.dependent", {},
                      depends_on=[first.step_id])
        import unified.engine as engine_module
        original_plan = engine_module.plan
        engine_module.plan = lambda _env: [first, second]
        try:
            result = self.engine.run(task_id, [])
            self.assertEqual(result["state"], TaskState.FAILED.value)
            self.assertEqual(result["incomplete_steps"], [second.step_id])
            self.assertEqual(dependent_dispatches, [])
            self.assertEqual(self.ledger.get_task(task_id)["state"], TaskState.FAILED.value)
        finally:
            engine_module.plan = original_plan

    def test_compare_and_set_state_transition_serializes_multiple_connections(self):
        task_id = self._task("cas-transition")["task_id"]
        second_ledger = Ledger(self.db_path)
        barrier = threading.Barrier(2)

        def transition(ledger):
            barrier.wait()
            try:
                ledger.set_state(task_id, TaskState.PLANNED,
                                 expected_state=TaskState.ADMITTED)
                return "won"
            except LedgerError:
                return "lost"

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(transition, (self.ledger, second_ledger)))
            self.assertCountEqual(outcomes, ["won", "lost"])
            self.assertEqual(self.ledger.get_task(task_id)["state"], TaskState.PLANNED.value)
        finally:
            second_ledger.close()

    def test_worker_cpu_accounting_survives_ledger_restart(self):
        def burn(_args):
            deadline = time.process_time() + 0.03
            while time.process_time() < deadline:
                pass
            return {"summary": "cpu measured"}
        self.broker.register(ToolSpec("test.cpu-persistent", [], "read_only", burn))
        task_id = self._task("cpu-persistence")["task_id"]
        import unified.engine as engine_module
        original_plan = engine_module.plan
        engine_module.plan = lambda _env: [Step("S-CPU-PERSIST", "test.cpu-persistent", {})]
        try:
            result = self.engine.run(task_id, [])
        finally:
            engine_module.plan = original_plan
        self.assertEqual(result["state"], TaskState.COMPLETED.value)
        before_restart = self.ledger.task_cpu_seconds_used(task_id)
        self.assertGreater(before_restart, 0)
        self.ledger.close()
        self.ledger = Ledger(self.db_path)
        self.engine = Engine(self.ledger, self.broker, self.evidence)
        self.assertAlmostEqual(self.ledger.task_cpu_seconds_used(task_id),
                               before_restart, places=6)

    def test_attempt_identity_is_durable_before_dispatch_and_completed_atomically(self):
        task_id = self._task("attempt-record")["task_id"]
        token = self.ledger.claim_run(task_id)
        env = TaskEnvelope(**self.ledger.get_task(task_id)["envelope"])
        snapshot = self.ledger.persist_plan(task_id, plan(env), PLANNER_VERSION,
                                            run_token=token)
        self.ledger.set_state(task_id, TaskState.PLANNED,
                              expected_state=TaskState.ADMITTED, run_token=token)
        self.ledger.set_state(task_id, TaskState.RUNNING,
                              expected_state=TaskState.PLANNED, run_token=token)
        step = snapshot["steps"][0]
        attempt_id = self.ledger.start_attempt(task_id, step.step_id,
                                               snapshot["version"], token)
        active = self.ledger.get_attempts(task_id)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["attempt_id"], attempt_id)
        self.assertEqual(active[0]["state"], "RUNNING")
        self.assertEqual(self.ledger.get_steps(task_id)[0].state, StepState.RUNNING)

        receipt = ToolReceipt(task_id, step.step_id, attempt_id, "succeeded",
                              "native.echo.v1", "echoed", data={"text": "custody"})
        self.ledger.save_receipt(receipt, committed=True, run_token=token)
        self.assertEqual(self.ledger.get_attempts(task_id)[0]["state"], "SUCCEEDED")
        self.assertEqual(self.ledger.get_steps(task_id)[0].state, StepState.SUCCEEDED)
        self.assertEqual(len(self.ledger.get_receipts(task_id)), 1)
        self.ledger.release_run_claim(task_id, token)

    def test_mid_execution_cancellation_reaches_cooperative_tool(self):
        entered = multiprocessing.get_context("fork").Event()

        def cooperative(args):
            entered.set()
            while not args["_cancelled"]():
                time.sleep(0.005)
            return {"result_type": "cancelled.v1", "summary": "cancelled in handler",
                    "errors": [{"type": "cancelled"}]}

        self.broker.register(ToolSpec("test.cooperative", [], "read_only", cooperative,
                                      supports_cancellation=True))
        task_id = self._task("mid-execution-cancel")["task_id"]
        import unified.engine as engine_module
        original_plan = engine_module.plan
        engine_module.plan = lambda _env: [Step("S-COOPERATIVE", "test.cooperative", {})]
        result = {}
        try:
            worker = threading.Thread(
                target=lambda: result.update(self.engine.run(task_id, [])), daemon=True)
            worker.start()
            self.assertTrue(entered.wait(2), "cooperative tool did not start")
            self.assertTrue(self.engine.cancel(task_id))
            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(result["state"], TaskState.CANCELLED.value)
            receipts = self.ledger.get_receipts(task_id)
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0].status, "cancelled")
        finally:
            engine_module.plan = original_plan

    def test_killed_side_effect_worker_stays_unknown_and_is_not_replayed(self):
        side_effect_path = os.path.join(self.tmp.name, "side-effect-marker")
        effect_applied = multiprocessing.get_context("fork").Event()

        def effect_then_wait(_args):
            burn_until = time.process_time() + 0.03
            while time.process_time() < burn_until:
                pass
            with open(side_effect_path, "w") as stream:
                stream.write("applied")
            effect_applied.set()
            while True:
                time.sleep(0.01)

        self.broker.register(ToolSpec("test.kill-during-effect", [], "external_effect",
                                      effect_then_wait))
        task_id = self._task("kill-during-effect")["task_id"]
        import unified.engine as engine_module
        original_plan = engine_module.plan
        engine_module.plan = lambda _env: [
            Step("S-KILLED-EFFECT", "test.kill-during-effect", {}, read_only=False)]
        result = {}
        try:
            waiting = self.engine.run(task_id, [])
            self.assertEqual(waiting["state"], TaskState.WAITING_APPROVAL.value)
            runner = threading.Thread(
                target=lambda: result.update(self.engine.approve(task_id, "approved", [])),
                daemon=True)
            runner.start()
            self.assertTrue(effect_applied.wait(3), "external side effect did not occur")
            self.assertTrue(self.engine.cancel(task_id))
            runner.join(3)
            self.assertFalse(runner.is_alive())
            execution_result = result["result"]
            self.assertEqual(execution_result["state"], TaskState.RECOVERY_REQUIRED.value)
            self.assertTrue(execution_result["cancellation_requested"])
            self.assertEqual(self.ledger.get_receipts(task_id), [])
            attempts = self.ledger.get_attempts(task_id)
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0]["state"], "UNKNOWN")
            self.assertGreater(self.ledger.task_cpu_seconds_used(task_id), 0)
            with open(side_effect_path) as stream:
                self.assertEqual(stream.read(), "applied")
            self.engine.recover([])
            self.assertEqual(len(self.ledger.get_attempts(task_id)), 1)
            with open(side_effect_path) as stream:
                self.assertEqual(stream.read(), "applied")
        finally:
            engine_module.plan = original_plan

    def test_killed_billable_read_is_not_automatically_retried(self):
        marker = os.path.join(self.tmp.name, "billable-model-call")
        began = multiprocessing.get_context("fork").Event()

        def billable_call(_args):
            with open(marker, "a") as stream:
                stream.write("charged\n")
            began.set()
            while True:
                time.sleep(0.01)

        self.broker.register(ToolSpec(
            "model.complete", ["model.infer"], "read_only", billable_call,
            resource_effectful=True))
        submitted = self.engine.submit(
            "custody-project", "killed-billable-model",
            {"intent": "model_complete", "instructions": "x"},
            ["model.infer"], ["model.infer"])
        task_id = submitted["task_id"]
        import unified.engine as engine_module
        original_plan = engine_module.plan
        engine_module.plan = lambda _env: [Step(
            "S-BILLABLE-MODEL", "model.complete", {"prompt": "x"},
            permissions_used=["model.infer"])]
        result = {}
        try:
            runner = threading.Thread(
                target=lambda: result.update(self.engine.run(task_id, ["model.infer"])),
                daemon=True)
            runner.start()
            self.assertTrue(began.wait(3), "billable model invocation did not start")
            self.assertTrue(self.engine.cancel(task_id))
            runner.join(3)
            self.assertFalse(runner.is_alive())
            self.assertEqual(result["state"], TaskState.RECOVERY_REQUIRED.value)
            self.engine.recover(["model.infer"])
            attempts = self.ledger.get_attempts(task_id)
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0]["state"], "UNKNOWN")
            with open(marker) as stream:
                self.assertEqual(stream.readlines(), ["charged\n"])
        finally:
            engine_module.plan = original_plan

    def test_concurrent_engines_cannot_dispatch_the_same_task(self):
        second_ledger = Ledger(self.db_path)
        second_engine = Engine(second_ledger, self.broker, self.evidence)
        entered = multiprocessing.get_context("fork").Event()
        release = multiprocessing.get_context("fork").Event()
        calls = multiprocessing.get_context("fork").Value("i", 0)

        def slow_tool(_args):
            with calls.get_lock():
                calls.value += 1
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test tool was not released")
            return {"result_type": "test.slow.v1", "summary": "done"}

        self.broker.register(ToolSpec("test.custody", [], "read_only", slow_tool))
        task_id = self._task("single-worker")["task_id"]
        import unified.engine as engine_module
        original_plan = engine_module.plan
        engine_module.plan = lambda _env: [Step("S-CUSTODY", "test.custody", {})]
        result = {}
        try:
            worker = threading.Thread(
                target=lambda: result.update(self.engine.run(task_id, [])), daemon=True)
            worker.start()
            self.assertTrue(entered.wait(2), "first worker did not reach tool dispatch")
            second = second_engine.run(task_id, [])
            self.assertTrue(second["already_running"])
            self.assertEqual(second["state"], TaskState.RUNNING.value)
            release.set()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(result["state"], TaskState.COMPLETED.value)
            self.assertEqual(calls.value, 1)
            attempts = self.ledger.get_attempts(task_id)
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0]["state"], "SUCCEEDED")
        finally:
            release.set()
            engine_module.plan = original_plan
            second_ledger.close()

    def test_recovery_marks_in_flight_attempt_unknown_instead_of_reexecuting(self):
        task_id = self._task("interrupted-attempt")["task_id"]
        token = self.ledger.claim_run(task_id)
        env = TaskEnvelope(**self.ledger.get_task(task_id)["envelope"])
        snapshot = self.ledger.persist_plan(task_id, plan(env), PLANNER_VERSION,
                                            run_token=token)
        self.ledger.set_state(task_id, TaskState.PLANNED,
                              expected_state=TaskState.ADMITTED, run_token=token)
        self.ledger.set_state(task_id, TaskState.RUNNING,
                              expected_state=TaskState.PLANNED, run_token=token)
        step = snapshot["steps"][0]
        attempt_id = self.ledger.start_attempt(task_id, step.step_id,
                                               snapshot["version"], token)
        # Simulate a dead worker: its lease has expired, but dispatch outcome is unknown.
        with self.ledger._db:
            self.ledger._db.execute(
                "UPDATE run_claims SET lease_until=0 WHERE task_id=?", (task_id,))
        second_ledger = Ledger(self.db_path)
        second_engine = Engine(second_ledger, self.broker, self.evidence)
        try:
            result = second_engine.run(task_id, [])
            self.assertEqual(result["state"], TaskState.RECOVERY_REQUIRED.value)
            self.assertEqual(len(result["unresolved_attempts"]), 1)
            self.assertEqual(result["unresolved_attempts"][0]["attempt_id"], attempt_id)
            self.assertEqual(second_ledger.get_attempts(task_id)[0]["state"], "UNKNOWN")
            self.assertEqual(second_ledger.get_steps(task_id)[0].state, StepState.INTERRUPTED)
            self.assertEqual(second_ledger.get_task(task_id)["state"],
                             TaskState.RECOVERY_REQUIRED.value)
        finally:
            second_ledger.close()

    def test_unknown_idempotent_operation_replays_with_same_key(self):
        task_id = self._task("idempotent-recovery")["task_id"]
        seen_keys = set()
        side_effects = []

        def idempotent_tool(args):
            key = args["_idempotency_key"]
            if key not in seen_keys:
                seen_keys.add(key)
                side_effects.append(key)
            return {"result_type": "test.idempotent.v1", "summary": "deduplicated"}

        self.broker.register(ToolSpec("test.idempotent", [], "external_effect",
                                      idempotent_tool, supports_idempotency=True))
        step = Step("S-IDEMPOTENT", "test.idempotent", {}, read_only=False)
        import unified.engine as engine_module
        original_plan = engine_module.plan
        engine_module.plan = lambda _env: [step]
        token = self.ledger.claim_run(task_id)
        try:
            snapshot = self.ledger.persist_plan(task_id, [step], PLANNER_VERSION,
                                                run_token=token)
            self.ledger.set_state(task_id, TaskState.PLANNED,
                                  expected_state=TaskState.ADMITTED, run_token=token)
            self.ledger.set_state(task_id, TaskState.RUNNING,
                                  expected_state=TaskState.PLANNED, run_token=token)
            self.ledger.save_approval(task_id, step.step_id, "approved")
            first_attempt = self.ledger.start_attempt(task_id, step.step_id,
                                                      snapshot["version"], token)
            first_key = self.ledger.attempt_operation_key(first_attempt)
            # The remote side effect succeeded, but the worker died before receipt commit.
            self.broker.call(task_id, step.step_id, step.tool, {}, [],
                             __import__("unified.policy", fromlist=["PolicyEngine"]).PolicyEngine(set()),
                             attempt_id=first_attempt, idempotency_key=first_key)
            self.ledger.release_run_claim(task_id, token)
            with self.ledger._db:
                self.ledger._db.execute(
                    "UPDATE run_claims SET lease_until=0 WHERE task_id=?", (task_id,))
            recovered = self.engine.recover([])
            self.assertIn(task_id, recovered)
            self.assertEqual(side_effects, [first_key])
            attempts = self.ledger.get_attempts(task_id)
            self.assertEqual(len(attempts), 2,
                             f"task={self.ledger.get_task(task_id)} attempts={attempts}")
            self.assertEqual(attempts[0]["state"], "RETRYABLE")
            self.assertEqual(attempts[0]["operation_key"], attempts[1]["operation_key"])
            self.assertEqual(attempts[1]["state"], "SUCCEEDED")
            self.assertEqual(self.ledger.get_task(task_id)["state"], TaskState.COMPLETED.value)
        finally:
            engine_module.plan = original_plan

    def test_unknown_non_idempotent_side_effect_remains_parked(self):
        task_id = self._task("park-unknown-effect")["task_id"]
        dispatches = []
        self.broker.register(ToolSpec(
            "test.ambiguous", [], "external_effect",
            lambda _args: dispatches.append(True) or {
                "result_type": "test.ambiguous.v1", "summary": "should not rerun"}))
        step = Step("S-AMBIGUOUS", "test.ambiguous", {}, read_only=False)
        import unified.engine as engine_module
        original_plan = engine_module.plan
        engine_module.plan = lambda _env: [step]
        token = self.ledger.claim_run(task_id)
        try:
            snapshot = self.ledger.persist_plan(task_id, [step], PLANNER_VERSION,
                                                run_token=token)
            self.ledger.set_state(task_id, TaskState.PLANNED,
                                  expected_state=TaskState.ADMITTED, run_token=token)
            self.ledger.set_state(task_id, TaskState.RUNNING,
                                  expected_state=TaskState.PLANNED, run_token=token)
            self.ledger.save_approval(task_id, step.step_id, "approved")
            self.ledger.start_attempt(task_id, step.step_id, snapshot["version"], token)
            self.ledger.release_run_claim(task_id, token)
            recovered = self.engine.recover([])
            self.assertIn(task_id, recovered)
            self.assertEqual(dispatches, [])
            self.assertEqual(len(self.ledger.get_attempts(task_id)), 1)
            self.assertEqual(self.ledger.get_attempts(task_id)[0]["state"], "UNKNOWN")
            self.assertEqual(self.ledger.get_task(task_id)["state"],
                             TaskState.RECOVERY_REQUIRED.value)
            self.assertTrue(self.engine.cancel(task_id))
            parked = self.ledger.get_task(task_id)
            self.assertEqual(parked["state"], TaskState.RECOVERY_REQUIRED.value)
            self.assertTrue(parked["result"]["cancellation_requested"])
            reconciled = self.engine.reconcile_attempt(
                task_id, self.ledger.get_attempts(task_id)[0]["attempt_id"],
                "not_applied", grants=[])
            self.assertEqual(reconciled["state"], TaskState.CANCELLED.value)
            self.assertEqual(dispatches, [])
            self.assertEqual(self.ledger.get_attempts(task_id)[0]["state"], "RETRYABLE")
        finally:
            engine_module.plan = original_plan

    def test_recovery_callback_commits_applied_outcome_without_dispatch(self):
        task_id = self._task("reconcile-applied")["task_id"]
        dispatched = []
        reconciled = []

        def should_not_dispatch(_args):
            dispatched.append(True)
            raise AssertionError("reconciled operation must not be dispatched again")

        def reconcile(_args, operation_key):
            reconciled.append(operation_key)
            return {"outcome": "applied", "result": {
                "result_type": "test.reconciled.v1", "summary": "found committed result",
                "data": {"operation_key": operation_key}}}

        self.broker.register(ToolSpec("test.reconcile", [], "external_effect",
                                      should_not_dispatch, reconcile=reconcile))
        step = Step("S-RECONCILE", "test.reconcile", {}, read_only=False)
        import unified.engine as engine_module
        original_plan = engine_module.plan
        engine_module.plan = lambda _env: [step]
        token = self.ledger.claim_run(task_id)
        try:
            snapshot = self.ledger.persist_plan(task_id, [step], PLANNER_VERSION,
                                                run_token=token)
            self.ledger.set_state(task_id, TaskState.PLANNED,
                                  expected_state=TaskState.ADMITTED, run_token=token)
            self.ledger.set_state(task_id, TaskState.RUNNING,
                                  expected_state=TaskState.PLANNED, run_token=token)
            attempt_id = self.ledger.start_attempt(task_id, step.step_id,
                                                   snapshot["version"], token)
            operation_key = self.ledger.attempt_operation_key(attempt_id)
            self.ledger.release_run_claim(task_id, token)
            with self.ledger._db:
                self.ledger._db.execute(
                    "UPDATE run_claims SET lease_until=0 WHERE task_id=?", (task_id,))
            recovered = self.engine.recover([])
            self.assertIn(task_id, recovered)
            self.assertEqual(reconciled, [operation_key])
            self.assertEqual(dispatched, [])
            self.assertEqual(self.ledger.get_task(task_id)["state"], TaskState.COMPLETED.value)
            receipts = self.ledger.get_receipts(task_id)
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0].attempt_id, attempt_id)
            self.assertEqual(receipts[0].data["operation_key"], operation_key)
        finally:
            engine_module.plan = original_plan


if __name__ == "__main__":
    unittest.main()
