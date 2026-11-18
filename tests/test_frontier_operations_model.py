"""Synthetic behavioral tests for the Frontier Operations reference model."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT / "prototypes" / "frontier-operations" / "model.py"
MODEL_SPEC = importlib.util.spec_from_file_location("frontier_operations_model", MODEL_PATH)
assert MODEL_SPEC and MODEL_SPEC.loader
MODEL = importlib.util.module_from_spec(MODEL_SPEC)
sys.modules[MODEL_SPEC.name] = MODEL
MODEL_SPEC.loader.exec_module(MODEL)
FrontierOperation = MODEL.FrontierOperation
OperationError = MODEL.OperationError


class FrontierOperationModelTests(unittest.TestCase):
    def setUp(self):
        self.op = FrontierOperation()

    def apply(self, request_id, action, argument=None):
        return self.op.apply(
            request_id=request_id,
            expected_revision=self.op.revision,
            action=action,
            argument=argument,
        )

    def complete_survey(self):
        # Deliberately non-canonical order: region traversal is not a sequence gate.
        for key, site in (("survey-c", "site-c"), ("survey-a", "site-a"), ("survey-b", "site-b")):
            self.apply(key, "record_site", site)

    def complete_defense(self):
        self.complete_survey()
        self.apply("rescue", "rescue_surveyor")
        self.apply("beacon-b", "activate_beacon", "beacon-b")
        self.apply("beacon-a", "activate_beacon", "beacon-a")

    def test_any_survey_order_advances_only_after_three_distinct_sites(self):
        self.apply("one", "record_site", "site-c")
        self.apply("two", "record_site", "site-a")
        self.assertEqual(self.op.state, "SURVEY")
        self.apply("three", "record_site", "site-b")
        self.assertEqual(self.op.state, "RESCUE")
        self.assertEqual(len(self.op.sites), 3)

    def test_prerequisites_block_out_of_order_progress(self):
        with self.assertRaisesRegex(OperationError, "survey clues"):
            self.apply("early-rescue", "rescue_surveyor")
        with self.assertRaisesRegex(OperationError, "unknown operation action"):
            self.apply("kill-quest", "kill_one_hundred_mobs")
        self.assertEqual(self.op.revision, 0)
        self.assertEqual(self.op.state, "SURVEY")

    def test_duplicate_site_and_unknown_site_do_not_mutate_state(self):
        self.apply("site", "record_site", "site-a")
        with self.assertRaisesRegex(OperationError, "already recorded"):
            self.apply("site-again", "record_site", "site-a")
        with self.assertRaisesRegex(OperationError, "unknown survey site"):
            self.apply("bad-site", "record_site", "invented-gameobject-id")
        self.assertEqual(self.op.revision, 1)
        self.assertEqual(self.op.sites, {"site-a"})

    def test_rescue_and_defense_are_finite_objective_steps(self):
        self.complete_survey()
        self.apply("rescue", "rescue_surveyor")
        self.assertEqual(self.op.state, "DEFENSE")
        self.apply("beacon-a", "activate_beacon", "beacon-a")
        self.assertEqual(self.op.state, "DEFENSE")
        self.apply("beacon-b", "activate_beacon", "beacon-b")
        self.assertEqual(self.op.state, "OUTCOME")
        with self.assertRaisesRegex(OperationError, "required before defense"):
            self.apply("beacon-a-again", "activate_beacon", "beacon-a")

    def test_outcome_is_deterministic_and_immutable(self):
        self.complete_defense()
        receipt = self.apply("choice", "choose_outcome", "restore-route")
        self.assertEqual(receipt.state, "COMPLETE")
        self.assertEqual(self.op.outcome, "restore-route")
        with self.assertRaisesRegex(OperationError, "already finalized"):
            self.apply("second-choice", "choose_outcome", "archive-findings")
        self.assertEqual(self.op.outcome, "restore-route")

    def test_successful_retry_replays_receipt_without_a_second_mutation(self):
        receipt = self.op.apply(
            request_id="retry-me", expected_revision=0,
            action="record_site", argument="site-b",
        )
        retry = self.op.apply(
            request_id="retry-me", expected_revision=0,
            action="record_site", argument="site-b",
        )
        self.assertEqual(retry, receipt)
        self.assertEqual(self.op.revision, 1)
        self.assertEqual(self.op.sites, {"site-b"})

    def test_request_id_cannot_be_replayed_for_different_intent(self):
        self.apply("shared-id", "record_site", "site-a")
        with self.assertRaisesRegex(OperationError, "different intent"):
            self.op.apply(
                request_id="shared-id", expected_revision=0,
                action="record_site", argument="site-b",
            )
        self.assertEqual(self.op.sites, {"site-a"})
        self.assertEqual(self.op.revision, 1)

    def test_stale_revision_is_rejected_without_mutation(self):
        self.apply("first", "record_site", "site-a")
        with self.assertRaisesRegex(OperationError, "stale"):
            self.op.apply(
                request_id="stale", expected_revision=0,
                action="record_site", argument="site-b",
            )
        self.assertEqual(self.op.sites, {"site-a"})
        self.assertEqual(self.op.revision, 1)

    def test_snapshot_restore_preserves_progress_and_retry_receipts(self):
        original = self.apply("first-site", "record_site", "site-b")
        restored = FrontierOperation.restore(self.op.snapshot())
        self.assertEqual(restored.snapshot(), self.op.snapshot())
        replay = restored.apply(
            request_id="first-site", expected_revision=0,
            action="record_site", argument="site-b",
        )
        self.assertEqual(replay, original)
        self.assertEqual(restored.revision, 1)
        restored.apply(
            request_id="second-site", expected_revision=1,
            action="record_site", argument="site-a",
        )
        self.assertEqual(restored.sites, {"site-a", "site-b"})
        self.assertEqual(restored.revision, 2)

    def test_restore_rejects_tampered_summary_and_event_log(self):
        self.apply("one", "record_site", "site-a")
        summary_tamper = self.op.snapshot()
        summary_tamper["state"] = "COMPLETE"
        with self.assertRaisesRegex(OperationError, "summary does not match"):
            FrontierOperation.restore(summary_tamper)

        log_tamper = self.op.snapshot()
        log_tamper["receipts"][0]["action"] = "choose_outcome"
        with self.assertRaises(OperationError):
            FrontierOperation.restore(log_tamper)

    def test_missing_request_id_is_rejected(self):
        with self.assertRaisesRegex(OperationError, "request_id is required"):
            self.op.apply(
                request_id="", expected_revision=0,
                action="record_site", argument="site-a",
            )
        self.assertEqual(self.op.revision, 0)


if __name__ == "__main__":
    unittest.main()
