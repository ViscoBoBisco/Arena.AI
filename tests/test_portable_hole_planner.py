"""Pure contract checks for read-only profession/Campaign Party preparation planning."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server/mod-portable-hole/harness"))
import project_planner as planner  # noqa: E402


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, query, params=()):
        self.calls.append((query, params))

    def fetchall(self):
        return self.rows


class PortableHolePreparationPlanner(unittest.TestCase):
    def contract(self):
        return {
            "schema": planner.SCHEMA,
            "request_id": "party-prep-25",
            "profession": {"recipe_id": 17187},
            "requirements": [
                {"item_entry": 4304, "amount": 8,
                 "project_owner_account_id": 100, "project_key": "raid-leather"},
                {"item_entry": 4304, "amount": 4,
                 "project_owner_account_id": 100, "project_key": "raid-leather"},
            ],
        }

    def test_authorized_snapshot_plans_held_budget_and_shortfall_without_mutations(self):
        cursor = Cursor([("raid-leather", 4304, 20, 7, "OPEN")])
        result = planner.plan_preparation(cursor, 200, self.contract())
        self.assertEqual(result["schema"], "arena-preparation-plan/1")
        self.assertEqual(result["request_id"], "party-prep-25")
        self.assertEqual(result["recipe_id"], 17187)
        self.assertFalse(result["mutation_performed"])
        self.assertFalse(result["physical_inventory_accessed"])
        self.assertEqual(result["lines"], [{
            "project_owner_account_id": 100, "project_key": "raid-leather",
            "item_entry": 4304, "required_amount": 12, "held_amount": 7,
            "shortfall_amount": 5, "project_capacity_remaining": 13,
            "project_status": "OPEN",
        }])
        query, params = cursor.calls[0]
        self.assertIn("portable_hole_project_access", query)
        self.assertIn("role IN ('VIEW','CONTRIBUTE')", query)
        self.assertNotIn("UPDATE", query.upper())
        self.assertNotIn("INSERT", query.upper())
        self.assertIn(200, params)

    def test_missing_acl_or_project_fails_closed(self):
        with self.assertRaisesRegex(PermissionError, "not authorized"):
            planner.plan_preparation(Cursor([]), 999, self.contract())

    def test_unauthenticated_caller_and_forged_requirement_are_rejected(self):
        with self.assertRaises(PermissionError):
            planner.plan_preparation(Cursor([]), 0, self.contract())
        invalid = self.contract()
        invalid["requirements"][0]["amount"] = True
        with self.assertRaisesRegex(ValueError, "bounded integer"):
            planner.validate_contract(invalid)

    def test_item_mismatch_and_closed_budget_are_reported_without_overclaiming_capacity(self):
        with self.assertRaisesRegex(ValueError, "different commodity"):
            planner.plan_preparation(Cursor([("raid-leather", 9999, 20, 7, "OPEN")]),
                                     200, self.contract())
        closed = self.contract()
        closed["requirements"] = [closed["requirements"][0]]
        result = planner.plan_preparation(
            Cursor([("raid-leather", 4304, 20, 3, "CLOSED")]), 100, closed)
        self.assertEqual(result["lines"][0]["project_capacity_remaining"], 0)
        self.assertEqual(result["lines"][0]["shortfall_amount"], 5)


if __name__ == "__main__":
    unittest.main()
