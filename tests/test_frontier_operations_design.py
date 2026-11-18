"""Guard the Frontier Operations slice against scope and evidence drift."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESIGN = json.loads((ROOT / "designs" / "frontier-operations.json").read_text(encoding="utf-8"))
DOC = (ROOT / "docs" / "16-frontier-operations.md").read_text(encoding="utf-8")


class FrontierOperationsDesignTests(unittest.TestCase):
    def test_design_is_not_misrepresented_as_installable_gameplay(self):
        self.assertEqual(DESIGN["maturity"], "DESIGN_ONLY_NOT_A_FOUNDRY_SPEC")
        self.assertIn("no AzerothCore content", DESIGN["status"])
        self.assertEqual(DESIGN["qualification"]["reference_model"], "SYNTHETIC_ONLY")
        self.assertEqual(DESIGN["qualification"]["live_gameplay"], "NOT_ASSESSED")
        self.assertIn("Neither artifact is a Foundry spec or installable gameplay", DOC)

    def test_operation_has_bounded_varied_gameplay_and_no_live_clock_gate(self):
        loop = " ".join(DESIGN["vertical"]["player_loop"]).lower()
        for phrase in ("three field sites", "rescue", "two field beacons", "deterministic regional outcome"):
            self.assertIn(phrase, loop)
        self.assertIn("no real-time waiting", loop)
        self.assertIn("no random reward roll", " ".join(DESIGN["vertical"]["reward_policy"]).lower())

    def test_authority_boundaries_are_explicit_and_no_api_is_invented(self):
        authority = DESIGN["authority_and_integration"]
        self.assertIn("Must own", authority["azerothcore"])
        self.assertIn("bot navigation", authority["playerbots"])
        self.assertIn("No party API or schema is assumed", authority["campaign_party"])
        self.assertIn("untrusted", authority["addon"])

    def test_no_candidate_client_asset_ids_are_asserted(self):
        forbidden = {"display_id", "model_id", "visual_id", "icon_id", "spell_id", "gameobject_id"}
        stack = [DESIGN]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                self.assertFalse(forbidden & set(value), "design must not invent a client/game asset ID")
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)

    def test_docs_include_reused_event_followup_and_production_gates(self):
        for phrase in ("Darkmoon", "Midsummer", "CAMPAIGN-ADMISSION", "NOT_ASSESSED",
                       "user-owned MPQ", "rollback", "synthetic"):
            self.assertIn(phrase.lower(), DOC.lower())


if __name__ == "__main__":
    unittest.main()
