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

    def test_fully_verified_spec_passes(self):
        spec = copy.deepcopy(SPEC)
        for key in ("items", "spells", "creatures"):
            for obj in spec[key]:
                for field in ("display_id", "visual_id", "icon_id"):
                    if isinstance(obj.get(field), dict):
                        obj[field]["verified"] = True
        result = validate(spec)
        self.assertTrue(result.ok, result.to_dict())


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

    def test_too_few_acceptance_checks_fails(self):
        spec = copy.deepcopy(SPEC)
        spec["acceptance"] = spec["acceptance"][:1]
        result = validate(spec, allow_unverified=True)
        self.assertEqual(result.verdicts["acceptance"], "FAIL")

    def test_baseline_collision_fails(self):
        spec = copy.deepcopy(SPEC)
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
