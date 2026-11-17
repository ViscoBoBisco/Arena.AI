"""Guard the source-backed frontier design against scope/evidence drift."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESIGN = json.loads((ROOT / "designs" / "azeroth-unbound.json").read_text(encoding="utf-8"))
DOC = (ROOT / "docs" / "14-world-atlas-frontier.md").read_text(encoding="utf-8")


class FrontierDesign(unittest.TestCase):
    def test_design_is_explicitly_not_a_built_feature(self):
        self.assertEqual(DESIGN["schema"], "arena-frontier-design/1")
        self.assertEqual(DESIGN["maturity"], "DESIGN_ONLY_NOT_A_FOUNDRY_SPEC")
        self.assertEqual(DESIGN["flagship_campaign"]["status"],
                         "DESIGN_ONLY; not valid Foundry spec; no generated gameplay claims")

    def test_user_requested_frontiers_are_explicit(self):
        ids = {card["id"] for card in DESIGN["capability_assessments"]}
        self.assertTrue({"old-world-flight-charter", "kingfall-regalia",
                         "world-level-weave", "field-vocation"} <= ids)

    def test_each_assessment_names_cost_limits_and_unverified_evidence(self):
        required = {"id", "fantasy", "existing_335_mechanics", "earliest_delivery",
                    "client_patch", "source_symbols", "hard_limits", "patch_cost", "evidence"}
        for card in DESIGN["capability_assessments"]:
            self.assertTrue(required <= set(card), card.get("id"))
            self.assertTrue(card["hard_limits"], card["id"])
            self.assertTrue(card["evidence"], card["id"])
            self.assertIn("NOT_ASSESSED", card["evidence"], card["id"])
            self.assertIn("no new art", card["patch_cost"].lower(), card["id"])

    def test_source_revision_is_a_full_commit_hash(self):
        self.assertRegex(DESIGN["source_snapshot"]["revision"], r"^[0-9a-f]{40}$")
        self.assertIn(DESIGN["source_snapshot"]["revision"], DOC)

    def test_no_unverified_client_asset_ids_are_invented(self):
        stack = [DESIGN]
        forbidden = {"display_id", "model_id", "visual_id", "icon_id", "spell_id"}
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                self.assertFalse(forbidden & set(value), "design must not fabricate stock asset IDs")
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)

    def test_flight_boundary_documents_required_dbc_and_policy_hook(self):
        flight = next(card for card in DESIGN["capability_assessments"]
                      if card["id"] == "old-world-flight-charter")
        self.assertIn("AreaTable.dbc", flight["earliest_delivery"])
        self.assertIn("OnPlayerCanFlyInZone", " ".join(flight["source_symbols"]))
        self.assertTrue(any("cannot grant" in limit.lower() for limit in flight["hard_limits"]))
        self.assertIn("AREA_FLAG_NO_FLY_ZONE", DOC)

    def test_doc_keeps_existing_custom_patch_out_of_scope(self):
        self.assertIn("Never assume ownership of or overwrite an existing custom MPQ.",
                      DESIGN["non_negotiable_constraints"])
        self.assertIn("cannot yet generate this AreaTable change", DOC)


if __name__ == "__main__":
    unittest.main()
