"""Guard the source-bound gameplay architecture register against status drift."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER_PATH = ROOT / "designs" / "gameplay-systems-register.json"


class GameplayRegister(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.register = json.loads(REGISTER_PATH.read_text(encoding="utf-8"))

    def test_schema_and_feature_states_are_complete(self):
        self.assertEqual(self.register["schema"], "arena-gameplay-systems-register/1")
        states = set(self.register["state_vocabulary"])
        systems = self.register["systems"]
        self.assertGreaterEqual(len(systems), 15)
        ids = [item["id"] for item in systems]
        self.assertEqual(len(ids), len(set(ids)))
        required = {"owner", "dependencies", "player_loop", "source_evidence",
                    "manifest_id_needs", "implementation_state", "outstanding_tests",
                    "risks", "next_milestone"}
        for item in systems:
            self.assertTrue(required <= set(item), item["id"])
            self.assertIn(item["implementation_state"], states, item["id"])
            for key in ("dependencies", "source_evidence", "manifest_id_needs",
                        "outstanding_tests", "risks"):
                self.assertIsInstance(item[key], list, (item["id"], key))
                self.assertTrue(item[key], (item["id"], key))

    def test_local_source_evidence_paths_exist_and_external_gates_are_explicit(self):
        for system in self.register["systems"]:
            for evidence in system["source_evidence"]:
                if evidence.startswith("Directive requirements"):
                    continue
                self.assertTrue((ROOT / evidence).is_file(), (system["id"], evidence))
        world = next(item for item in self.register["systems"]
                     if item["id"] == "foundry-world-ownership")
        self.assertEqual(world["implementation_state"], "SYNTHETIC")
        self.assertTrue(any("exact pinned" in test.lower() or "pinned" in test.lower()
                            for test in world["outstanding_tests"]))
        party = next(item for item in self.register["systems"]
                     if item["id"] == "party-build-architect")
        self.assertTrue(any("private" in risk.lower() for risk in party["risks"]))

    def test_backlog_keeps_fix_before_gameplay_and_optimization(self):
        priorities = [item["id"] for item in self.register["prioritized_prs"]]
        self.assertEqual(priorities[:5], ["P0-01a", "P0-01b", "P0-02", "P0-03", "P0-04"])
        self.assertIn("P1-02", priorities)
        self.assertIn("P1-03", priorities)
        for item in self.register["prioritized_prs"]:
            self.assertTrue(item["acceptance"], item["id"])


if __name__ == "__main__":
    unittest.main()
