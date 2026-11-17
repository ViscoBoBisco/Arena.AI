"""Campaign Party adapter contract: admission, idempotency, gates."""

from __future__ import annotations

import sys
import unittest

sys.path.insert(0, str(helpers.REPO)) if False else None
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import helpers  # noqa: E402

sys.path.insert(0, str(helpers.REPO))
from foundry.adapter import FakeHost  # noqa: E402


def intent(**over):
    base = {"idempotency_key": "pkg-1.0.0", "kind": "install_package",
            "payload": {"patch_cost": "none", "ids": {"item_template": [901000]},
                        "schema_version": "1.0.0", "unverified": []}}
    base.update(over)
    return base


class Admission(unittest.TestCase):
    def test_clean_intent_is_admitted_and_applied(self):
        host = FakeHost()
        self.assertEqual(host.admit(intent())["status"], "admitted")
        self.assertEqual(host.apply(intent())["status"], "applied")
        self.assertIn(901000, host.snapshot()["reserved_ids"]["item_template"])

    def test_replay_returns_original_receipt(self):
        host = FakeHost()
        first = host.apply(intent())
        second = host.apply(intent())
        self.assertTrue(second["replayed"])
        self.assertEqual(first["idempotency_key"], second["idempotency_key"])

    def test_collision_is_refused(self):
        host = FakeHost(reserved_ids={"item_template": {901000}})
        receipt = host.admit(intent())
        self.assertEqual(receipt["code"], "ID_COLLISION")

    def test_dbc_patch_without_capability_is_refused(self):
        host = FakeHost()  # mpq_patch defaults False (patch-4.MPQ is owned)
        bad = intent(payload={"patch_cost": "dbc-rows", "ids": {},
                              "schema_version": "1.0.0", "unverified": []})
        self.assertEqual(host.admit(bad)["code"], "CAPABILITY_MISSING")

    def test_unverified_assets_are_not_admitted_silently(self):
        host = FakeHost()
        bad = intent(payload={"patch_cost": "none", "ids": {},
                              "schema_version": "1.0.0", "unverified": ["x.display_id=1"]})
        self.assertEqual(host.admit(bad)["code"], "NOT_ASSESSED")
        ok = intent(allow_unverified=True,
                    payload={"patch_cost": "none", "ids": {},
                             "schema_version": "1.0.0", "unverified": ["x.display_id=1"]})
        self.assertEqual(host.admit(ok)["status"], "admitted")

    def test_schema_mismatch_is_refused(self):
        host = FakeHost(schema_version="2.0.0")
        self.assertEqual(host.admit(intent())["code"], "SCHEMA_MISMATCH")

    def test_missing_idempotency_key_is_refused(self):
        host = FakeHost()
        bad = intent()
        del bad["idempotency_key"]
        self.assertEqual(host.admit(bad)["code"], "VALIDATION_FAILED")

    def test_unknown_kind_is_refused(self):
        host = FakeHost()
        self.assertEqual(host.admit(intent(kind="teleport"))["code"], "UNKNOWN_INTENT")

    def test_rollback_frees_ids(self):
        host = FakeHost()
        host.apply(intent())
        host.apply(intent(idempotency_key="pkg-1.0.0-rollback",
                          kind="rollback_package"))
        self.assertNotIn(901000, host.snapshot()["reserved_ids"].get("item_template", []))


if __name__ == "__main__":
    unittest.main()
