"""Foundry spec validation: the fail-closed gate and its negative tests."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

sys.path.insert(0, str(helpers.REPO))
from foundry.assets import ingest_dbc_dir  # noqa: E402
from foundry.spec import validate_spec, load_spec  # noqa: E402

SPEC = json.loads((helpers.REPO / "specs" / "frost-armory.json").read_text(encoding="utf-8"))
WORLD_TABLES = {ln.strip() for ln in
                (helpers.REPO / "catalog" / "world_tables.txt").read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")}


def validate(spec, **kw):
    kw.setdefault("world_tables", WORLD_TABLES)
    return validate_spec(spec, **kw)


class TheGateIsFailClosed(unittest.TestCase):
    def test_unverified_assets_block_the_build(self):
        result = validate(copy.deepcopy(SPEC))
        self.assertFalse(result.ok)
        self.assertEqual(result.verdicts["asset-verification"], "NOT_ASSESSED")

    def test_explicit_allowance_is_recorded_not_hidden(self):
        result = validate(copy.deepcopy(SPEC), allow_unverified=True)
        self.assertEqual(result.verdicts["asset-verification"], "SKIPPED")
        self.assertFalse(result.ok, "a skipped essential check is not a pass")

    def test_verified_true_without_source_evidence_is_not_proof(self):
        spec = copy.deepcopy(SPEC)
        for key in ("items", "spells", "creatures"):
            for obj in spec[key]:
                for field in ("display_id", "visual_id", "icon_id"):
                    if isinstance(obj.get(field), dict):
                        obj[field]["verified"] = True
        result = validate(spec)
        self.assertFalse(result.ok)
        self.assertEqual(result.verdicts["asset-verification"], "NOT_ASSESSED")

    def test_matching_local_dbc_provenance_passes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture_tables = {
                "ItemDisplayInfo.dbc": (25, [(i, *([0] * 24)) for i in
                                               (41179, 41180, 41181, 41182)]),
                "SpellVisual.dbc": (2, [(1, 0)]),
                "SpellIcon.dbc": (2, [(1, 0)]),
                "CreatureDisplayInfo.dbc": (2, [(24954, 0)]),
            }
            for table, (fields, rows) in fixture_tables.items():
                helpers.write_dbc(root / table, fields, rows)
            asset_index = ingest_dbc_dir(root)
            spec = copy.deepcopy(SPEC)
            refs = (
                ("items", "display_id", "item_display"),
                ("spells", "visual_id", "spell_visual"),
                ("spells", "icon_id", "spell_icon"),
                ("creatures", "display_id", "creature_display"),
            )
            for collection, field, asset_kind in refs:
                for obj in spec[collection]:
                    ref = obj[field]
                    ref["verified"] = True  # claim alone is ignored; evidence is checked
                    ref["evidence"] = asset_index[asset_kind][str(ref["value"])]["evidence"]
            result = validate(spec, asset_index=asset_index)
            self.assertTrue(result.ok, result.to_dict())
            self.assertEqual(result.verdicts["asset-verification"], "PASS")

    def test_wrong_hash_and_stale_dbc_are_rejected_even_with_allowance(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helpers.write_dbc(root / "ItemDisplayInfo.dbc", 1,
                              [(41179,), (41180,), (41181,), (41182,)])
            helpers.write_dbc(root / "SpellVisual.dbc", 2, [(1, 0)])
            helpers.write_dbc(root / "SpellIcon.dbc", 2, [(1, 0)])
            helpers.write_dbc(root / "CreatureDisplayInfo.dbc", 2, [(24954, 0)])
            asset_index = ingest_dbc_dir(root)
            spec = copy.deepcopy(SPEC)
            for obj in spec["items"]:
                ref = obj["display_id"]
                ref["evidence"] = asset_index["item_display"][str(ref["value"])]["evidence"]
            # A spec-embedded claim without a parser-built local index is not proof.
            result = validate(spec)
            self.assertEqual(result.verdicts["asset-verification"], "FAIL")
            # One evidence hash is altered; --allow-unverified must not waive tampering.
            spec["items"][0]["display_id"]["evidence"]["sha256"] = "0" * 64
            result = validate(spec, asset_index=asset_index, allow_unverified=True)
            self.assertEqual(result.verdicts["asset-verification"], "FAIL")

            # A previously indexed source changing after ingest also invalidates its evidence.
            spec = copy.deepcopy(SPEC)
            for obj in spec["items"]:
                ref = obj["display_id"]
                ref["evidence"] = asset_index["item_display"][str(ref["value"])]["evidence"]
            helpers.write_dbc(root / "ItemDisplayInfo.dbc", 1,
                              [(41179,), (41180,), (41181,), (41182,), (99999,)])
            result = validate(spec, asset_index=asset_index)
            self.assertEqual(result.verdicts["asset-verification"], "FAIL")


class NegativeTests(unittest.TestCase):
    def test_non_object_root_fails_without_exception(self):
        result = validate(["not", "a", "spec"], allow_unverified=True)
        self.assertEqual(result.verdicts["schema"], "FAIL")

    def test_malformed_nested_shapes_fail_without_exception(self):
        spec = copy.deepcopy(SPEC)
        spec["id_range"] = []
        spec["vendors"] = [{"key": "bad", "creature_key": "x", "rows": [None]}]
        spec["recipes"] = [{"key": "bad-recipe", "result_key": "x", "ingredients": [None]}]
        result = validate(spec, allow_unverified=True)
        self.assertEqual(result.verdicts["schema"], "FAIL")

    def test_malformed_asset_reference_fails_closed(self):
        spec = copy.deepcopy(SPEC)
        spec["items"][0]["display_id"] = {"value": "not-an-id", "verified": True}
        result = validate(spec, allow_unverified=True)
        self.assertEqual(result.verdicts["references"], "FAIL")

    def test_unknown_top_level_key_fails(self):
        spec = copy.deepcopy(SPEC)
        spec["invented"] = 1
        result = validate(spec, allow_unverified=True)
        self.assertEqual(result.verdicts["schema"], "FAIL")

    def test_dangling_reference_fails(self):
        spec = copy.deepcopy(SPEC)
        spec["vendors"][0]["creature_key"] = "nobody"
        result = validate(spec, allow_unverified=True)
        self.assertEqual(result.verdicts["references"], "FAIL")

    def test_duplicate_key_fails(self):
        spec = copy.deepcopy(SPEC)
        spec["items"].append(dict(spec["items"][0]))
        result = validate(spec, allow_unverified=True)
        self.assertEqual(result.verdicts["references"], "FAIL")

    def test_game_event_id_above_tinyint_fails(self):
        spec = copy.deepcopy(SPEC)
        spec["events"][0]["id"] = 901500
        result = validate(spec, allow_unverified=True)
        self.assertEqual(result.verdicts["ids"], "FAIL")

    def test_id_outside_spec_range_fails(self):
        spec = copy.deepcopy(SPEC)
        spec["items"][0]["id"] = 123
        result = validate(spec, allow_unverified=True)
        self.assertEqual(result.verdicts["ids"], "FAIL")

    def test_counter_terminal_target_is_bounded_by_addon_integer_decoder(self):
        spec = json.loads((helpers.REPO / "specs" / "vault-terminal.json").read_text())
        spec["addon_protocol"]["target"] = 2147483648
        result = validate(spec)
        self.assertEqual(result.verdicts["interaction"], "FAIL")

    def test_counter_table_identifier_reserves_space_for_safe_migration_names(self):
        spec = json.loads((helpers.REPO / "specs" / "vault-terminal.json").read_text())
        table = "a" * 54
        spec["addon_protocol"]["claims_table"] = table
        spec["delivery"]["characters_sql_tables"] = [table]
        accepted = validate(spec)
        self.assertEqual(accepted.verdicts["interaction"], "PASS")

        spec["addon_protocol"]["claims_table"] = table + "a"
        spec["delivery"]["characters_sql_tables"] = [table + "a"]
        rejected = validate(spec)
        self.assertEqual(rejected.verdicts["interaction"], "FAIL")

    def test_too_few_acceptance_checks_fails(self):
        spec = copy.deepcopy(SPEC)
        spec["acceptance"] = spec["acceptance"][:1]
        result = validate(spec, allow_unverified=True)
        self.assertEqual(result.verdicts["acceptance"], "FAIL")

    def test_baseline_collision_fails(self):
        spec = copy.deepcopy(SPEC)
        spec["items"][0]["id"] = 901000
        result = validate(spec, allow_unverified=True,
                          reserved={"item_template": {901000}})
        self.assertEqual(result.verdicts["ids"], "FAIL")

    def test_wrong_spec_version_fails(self):
        spec = copy.deepcopy(SPEC)
        spec["spec_version"] = 99
        result = validate(spec, allow_unverified=True)
        self.assertEqual(result.verdicts["schema"], "FAIL")


class Loading(unittest.TestCase):
    def test_missing_file_is_a_spec_error(self):
        with self.assertRaises(Exception):
            load_spec(Path("/nonexistent/spec.json"))

    def test_non_json_is_a_spec_error(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write("{not json")
            name = fh.name
        with self.assertRaises(Exception):
            load_spec(Path(name))


if __name__ == "__main__":
    unittest.main()
