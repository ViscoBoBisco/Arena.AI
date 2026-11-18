"""Faction-operation identity and authority separation contracts."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from foundry.faction_ops import (  # noqa: E402
    AccordPolicy,
    HostilityTemplateRef,
    ReputationFactionRef,
    argent_foundation,
)


class FactionOperationsFoundation(unittest.TestCase):
    def test_foundation_has_unresolved_stock_ids_and_no_playable_claim(self):
        payload = argent_foundation().to_dict()
        self.assertEqual(payload["status"], "DESIGN_ONLY_NOT_PLAYABLE")
        self.assertEqual(payload["identity"]["reputation"], {
            "table": "Faction.dbc", "value": None, "status": "UNRESOLVED",
            "evidence_sha256": None,
        })
        hostility = payload["identity"]["hostility_templates"][0]
        self.assertEqual(hostility["table"], "FactionTemplate.dbc")
        self.assertIsNone(hostility["value"])
        self.assertEqual(hostility["status"], "UNRESOLVED")
        self.assertFalse(payload["authority"]["global_hostility_mutation"])
        self.assertFalse(payload["authority"]["npc_player_group_emulation"])
        self.assertTrue(all(level == "NOT_ASSESSED" for level in
                            payload["evidence_levels"].values()
                            if level.endswith("NOT_ASSESSED")))

    def test_reputation_and_hostility_tables_cannot_be_substituted(self):
        policy = argent_foundation()
        with self.assertRaisesRegex(ValueError, "Faction.dbc"):
            AccordPolicy(
                key=policy.key,
                reputation_faction=ReputationFactionRef(table="FactionTemplate.dbc"),
                hostility_templates=policy.hostility_templates,
            ).validate()
        with self.assertRaisesRegex(ValueError, "FactionTemplate.dbc"):
            AccordPolicy(
                key=policy.key,
                reputation_faction=policy.reputation_faction,
                hostility_templates=(HostilityTemplateRef(table="Faction.dbc"),),
            ).validate()

    def test_personal_accord_cannot_mutate_global_hostility_or_fake_party_membership(self):
        policy = argent_foundation()
        with self.assertRaisesRegex(ValueError, "globally rewrite"):
            AccordPolicy(**{**policy.__dict__, "global_hostility_mutation": True}).validate()
        with self.assertRaisesRegex(ValueError, "not implicitly player-group"):
            AccordPolicy(**{**policy.__dict__,
                            "player_group_membership_emulated_for_npcs": True}).validate()

    def test_design_artifact_matches_typed_foundation(self):
        path = Path(__file__).resolve().parent.parent / "designs" / \
            "argent-faction-operations-foundation.json"
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                         argent_foundation().to_dict())


if __name__ == "__main__":
    unittest.main()
