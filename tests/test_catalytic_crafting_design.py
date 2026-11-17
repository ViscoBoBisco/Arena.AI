"""Guard the catalytic-crafting exploration against implementation/evidence drift."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESIGN = json.loads((ROOT / "designs" / "catalytic-crafting.json").read_text(encoding="utf-8"))
DOC = (ROOT / "docs" / "15-catalytic-crafting.md").read_text(encoding="utf-8")


class CatalyticCraftingDesign(unittest.TestCase):
    def test_artifact_is_design_only_and_not_a_foundry_spec(self):
        self.assertEqual(DESIGN["schema"], "arena-feature-design/1")
        self.assertEqual(DESIGN["maturity"], "DESIGN_ONLY_NOT_A_FOUNDRY_SPEC")
        self.assertIn("no generator, module, SQL or gameplay implementation claimed",
                      DESIGN["recommended_first_slice"]["status"])

    def test_source_revision_and_qualification_are_recorded(self):
        snapshot = DESIGN["upstream_source_snapshot"]
        self.assertRegex(snapshot["revision"], r"^[0-9a-f]{40}$")
        self.assertIn(snapshot["revision"], DOC)
        self.assertIn("not a Campaign Party pin", snapshot["qualification"])
        self.assertIn("module compile", snapshot["qualification"])

    def test_contract_is_server_authoritative_and_idempotent(self):
        contract = DESIGN["augmentation_contract"]
        self.assertEqual(contract["request"]["fields"],
                         ["recipe_spell_id", "augmentation_key", "request_id"])
        self.assertIn("at most one output transaction", contract["request"]["idempotency"])
        self.assertTrue({"ADMITTED", "RESERVED", "COMMITTED", "REFUNDED", "REJECTED"}
                        <= set(contract["transaction_states"]))
        self.assertIn("AzerothCore validates", contract["compatibility_boundary"])

    def test_examples_cover_batch_and_catalyst_without_claiming_real_ids(self):
        patterns = {pattern["key"]: pattern for pattern in DESIGN["design_patterns"]}
        self.assertTrue({"batch-yield", "moonstone-imprint"} <= set(patterns))
        self.assertIn("deterministic", patterns["batch-yield"]["player_choice"])
        self.assertIn("Moonstone", patterns["moonstone-imprint"]["player_choice"])
        self.assertIn("verified", patterns["moonstone-imprint"]["output"])
        forbidden = {"display_id", "model_id", "visual_id", "icon_id", "spell_id"}
        stack = [DESIGN]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                self.assertFalse(forbidden & set(value), "design must not fabricate stock asset IDs")
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)

    def test_first_slice_requires_negative_paths_and_preserves_normal_craft(self):
        acceptance = " ".join(DESIGN["recommended_first_slice"]["acceptance"]).lower()
        for required in ("base recipe still works", "inventory full", "interruption",
                         "server restart", "existing specialization bonuses"):
            self.assertIn(required, acceptance)

    def test_document_distinguishes_hooks_from_qualification(self):
        for phrase in ("not proof", "transaction", "client", "DESIGN_ONLY", "rollback",
                       "stock inventory escrow primitive", "before it is stored",
                       "joint atomic commit"):
            self.assertIn(phrase.lower(), DOC.lower())
        self.assertIn("before storage", DESIGN["core_mechanics"][4]["design_effect"])
        self.assertRegex(DOC, re.compile(r"\| C\+\+ module compiled.*\|\s*`ARENA-COMPILE` pending\s*\|"))


if __name__ == "__main__":
    unittest.main()
