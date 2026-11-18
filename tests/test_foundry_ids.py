"""Per-table allocation and baseline collision contract."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

sys.path.insert(0, str(helpers.REPO))
from foundry.spec import allocate_ids, validate_spec  # noqa: E402

SPEC = json.loads((helpers.REPO / "specs" / "frost-armory.json").read_text(encoding="utf-8"))
FOUNDRY = helpers.REPO / "tools" / "foundry.py"
SUPPORTED_SPEC = helpers.foundry_spec_fixture("frost-armory.json", omit=("recipes",))


class Allocation(unittest.TestCase):
    def test_implicit_id_skips_later_explicit_id_in_same_table(self):
        spec = copy.deepcopy(SPEC)
        spec["items"][0]["id"] = 901001
        ids = allocate_ids(spec)
        self.assertEqual(ids["frostbrand-blade"], 901001)
        self.assertEqual(ids["rimeplate-chest"], 901000)
        self.assertNotEqual(ids["frostbrand-blade"], ids["rimeplate-chest"])

    def test_duplicate_explicit_ids_in_same_table_are_rejected(self):
        spec = copy.deepcopy(SPEC)
        spec["items"][0]["id"] = 901000
        spec["items"][1]["id"] = 901000
        with self.assertRaisesRegex(ValueError, "duplicate explicit IDs.*item_template"):
            allocate_ids(spec)
        result = validate_spec(spec, allow_unverified=True)
        self.assertEqual(result.verdicts["ids"], "FAIL")

    def test_per_table_integer_domain_allocation(self):
        spec = copy.deepcopy(SPEC)
        spec["events"] = [
            {"key": "event-a", "name": "Event A"},
            {"key": "event-b", "name": "Event B", "id": 1},
        ]
        ids = allocate_ids(spec)
        self.assertEqual(ids["event-a"], 2)
        self.assertLessEqual(ids["event-a"], 255)

    def test_reserved_baseline_is_skipped_for_automatic_ids(self):
        spec = copy.deepcopy(SPEC)
        ids = allocate_ids(spec, reserved={"item_template": {901000, 901001}})
        self.assertEqual(ids["frostbrand-blade"], 901002)

    def test_exhausted_range_fails_instead_of_reusing_id(self):
        spec = copy.deepcopy(SPEC)
        spec["id_range"] = {"start": 901000, "end": 901000}
        spec["items"] = spec["items"][:2]
        with self.assertRaisesRegex(ValueError, "no free IDs remain for item_template"):
            allocate_ids(spec)

    def test_relationship_objects_do_not_get_phantom_primary_ids(self):
        ids = allocate_ids(copy.deepcopy(SPEC))
        self.assertNotIn("armory-vendor", ids)
        self.assertNotIn("recipe-blade", ids)
        self.assertNotIn("recipe-chest", ids)

    def test_relationship_objects_reject_explicit_ids(self):
        spec = copy.deepcopy(SPEC)
        spec["vendors"][0]["id"] = 901100
        result = validate_spec(spec, allow_unverified=True)
        self.assertEqual(result.verdicts["ids"], "FAIL")


class BaselineCli(unittest.TestCase):
    def test_plan_refuses_malformed_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp) / "baseline.json"
            baseline.write_text(json.dumps({"schema": "wrong", "reserved_ids": {}}))
            result = helpers.run_tool(FOUNDRY, "plan", SUPPORTED_SPEC,
                                      "--baseline", baseline)
            self.assertEqual(result.returncode, 2)
            self.assertIn("arena-id-baseline/1", result.stderr)

    def test_plan_uses_external_reserved_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp) / "baseline.json"
            baseline.write_text(json.dumps({
                "schema": "arena-id-baseline/1",
                "reserved_ids": {"item_template": [901000, 901001]},
            }))
            result = helpers.run_tool(FOUNDRY, "plan", SUPPORTED_SPEC,
                                      "--baseline", baseline)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual(plan["ids"]["frostbrand-blade"], 901002)
            self.assertEqual(plan["validation"]["verdicts"]["ids"], "PASS")

    def test_explicit_baseline_collision_fails_build_without_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp) / "baseline.json"
            baseline.write_text(json.dumps({
                "schema": "arena-id-baseline/1",
                "reserved_ids": {"item_template": [901000]},
            }))
            spec_path = Path(tmp) / "spec.json"
            spec = copy.deepcopy(SPEC)
            spec["items"][0]["id"] = 901000
            spec_path.write_text(json.dumps(spec))
            out = Path(tmp) / "out"
            result = helpers.run_tool(FOUNDRY, "build", spec_path,
                                      "--baseline", baseline, "--out", out, "--allow-unverified")
            self.assertEqual(result.returncode, 1)
            self.assertFalse(out.exists())

    def test_package_records_baseline_aware_allocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp) / "baseline.json"
            baseline.write_text(json.dumps({
                "schema": "arena-id-baseline/1",
                "reserved_ids": {"item_template": [901000]},
            }))
            out = Path(tmp) / "out"
            result = helpers.run_tool(FOUNDRY, "build", SUPPORTED_SPEC,
                                      "--baseline", baseline, "--out", out, "--allow-unverified")
            self.assertEqual(result.returncode, 0, result.stderr)
            allocation = json.loads((out / "id-allocation.json").read_text())
            self.assertEqual(allocation["allocations"]["frostbrand-blade"], 901001)
            baseline_payload = json.loads((out / "id-baseline.json").read_text())
            self.assertEqual(baseline_payload["reserved_ids"]["item_template"], [901000])
            self.assertEqual(allocation["baseline_sha256"],
                             __import__("hashlib").sha256((out / "id-baseline.json").read_bytes()).hexdigest())
            sql = (out / "sql/01_world.sql").read_text()
            self.assertIn("INSERT INTO item_template (entry, name, description, VerifiedBuild) VALUES (901001,", sql)
            self.assertNotIn("INSERT INTO item_template (entry, name, description, VerifiedBuild) VALUES (901000,", sql)


if __name__ == "__main__":
    unittest.main()
