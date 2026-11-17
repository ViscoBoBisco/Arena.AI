"""Structured delivery requirements must not be inferred from summary labels."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

sys.path.insert(0, str(helpers.REPO))
from foundry.spec import delivery_requirements, patch_cost, validate_spec  # noqa: E402


class DeliveryRequirements(unittest.TestCase):
    def test_addon_and_item_dbc_rows_require_all_delivery_surfaces(self):
        spec = {"spec_version": 1, "addon": {"title": "UI"},
                "items": [{"key": "blade", "dbc_row": True}]}
        requirements = delivery_requirements(spec)
        artifacts = requirements["artifacts"]
        kinds = {entry["kind"] for entry in artifacts}
        self.assertTrue({"addon_files", "client_dbc", "server_dbc", "mpq"} <= kinds)
        self.assertTrue({"mpq_patch", "client_server_dbc_parity", "addon_channel"}
                        <= {entry["name"] for entry in requirements["capabilities"]})
        self.assertEqual(patch_cost(spec),
                         "mixed delivery (addon-files + dbc-rows; MPQ patch required)")

    def test_addon_and_spell_dbc_rows_are_not_hidden_by_addon_precedence(self):
        requirements = delivery_requirements({
            "spec_version": 1, "addon": {"title": "UI"},
            "spells": [{"key": "spell", "dbc_row": True}],
        })
        table_pairs = {(entry["kind"], entry.get("table"))
                       for entry in requirements["artifacts"]}
        self.assertIn(("client_dbc", "Spell.dbc"), table_pairs)
        self.assertIn(("server_dbc", "Spell.dbc"), table_pairs)
        self.assertIn(("addon_files", None), table_pairs)

    def test_multiple_client_tables_each_require_server_parity(self):
        requirements = delivery_requirements({
            "spec_version": 1,
            "delivery": {"client_dbc_tables": ["Item.dbc", "Spell.dbc"],
                         "server_dbc_tables": ["Item.dbc", "Spell.dbc"]},
        })
        table_pairs = {(entry["kind"], entry.get("table"))
                       for entry in requirements["artifacts"]}
        for table in ("Item.dbc", "Spell.dbc"):
            self.assertIn(("client_dbc", table), table_pairs)
            self.assertIn(("server_dbc", table), table_pairs)

    def test_mixed_sql_and_mpq_requirements_are_independent(self):
        requirements = delivery_requirements({
            "spec_version": 1,
            "items": [{"key": "blade", "dbc_row": True}],
        })
        kinds = {entry["kind"] for entry in requirements["artifacts"]}
        self.assertTrue({"world_sql", "data_migration", "client_dbc", "server_dbc", "mpq"}
                        <= kinds)
        self.assertIn("world_db", {entry["name"] for entry in requirements["capabilities"]})

    def test_dbc_row_opt_in_must_be_boolean(self):
        result = validate_spec({"spec_version": 1,
                                "items": [{"key": "blade", "name": "Blade",
                                           "dbc_row": "yes"}]})
        self.assertEqual(result.verdicts["schema"], "FAIL")

    def test_client_server_table_declarations_must_match(self):
        spec = {"spec_version": 1, "delivery": {
            "client_dbc_tables": ["Item.dbc", "Spell.dbc"],
            "server_dbc_tables": ["Item.dbc"],
        }}
        result = validate_spec(spec)
        self.assertEqual(result.verdicts["delivery"], "FAIL")

    def test_delivery_declaration_rejects_unknown_and_malformed_fields(self):
        result = validate_spec({"spec_version": 1,
                                "delivery": {"client_dbc_tables": [None], "mpq": True}})
        self.assertEqual(result.verdicts["schema"], "FAIL")


if __name__ == "__main__":
    unittest.main()
