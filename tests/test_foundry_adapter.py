"""Campaign Party adapter contract: admission, idempotency, gates."""

from __future__ import annotations

import sys
import unittest

sys.path.insert(0, str(helpers.REPO)) if False else None
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import helpers  # noqa: E402

sys.path.insert(0, str(helpers.REPO))
from foundry.adapter import FakeHost  # noqa: E402
from foundry.spec import delivery_requirements  # noqa: E402


def intent(**over):
    payload_overrides = over.pop("payload", {})
    feature_spec = {"spec_version": 1, "version": "1.0.0",
                    "items": [{"key": "sample-item", "name": "Sample item"}]}
    base = {"idempotency_key": "pkg-1.0.0", "kind": "install_package",
            "payload": {"patch_cost": "none", "ids": {"item_template": [901000]},
                        "schema_version": "1.0.0", "unverified": [],
                        "feature_spec": feature_spec,
                        "delivery_requirements": delivery_requirements(feature_spec),
                        "available_artifacts": [{"kind": "world_sql"},
                                                 {"kind": "data_migration"}]}}
    base["payload"].update(payload_overrides)
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

    def test_mixed_package_without_mpq_merge_capability_is_refused(self):
        requirements = delivery_requirements({"spec_version": 1,
                                              "addon": {"title": "UI"},
                                              "items": [{"key": "blade", "dbc_row": True}]})
        caps = {key: value for key, value in FakeHost().capabilities().items()
                if key != "adapter_version"}
        caps["client_server_dbc_parity"] = True
        host = FakeHost(capabilities=caps,
                        dbc_hashes={"client": {"Item.dbc": "a" * 64},
                                    "server": {"Item.dbc": "a" * 64}})
        bad = intent(payload={"patch_cost": "client-files", "ids": {},
                              "schema_version": "1.0.0", "unverified": [],
                              "feature_spec": {"spec_version": 1,
                                               "addon": {"title": "UI"},
                                               "items": [{"key": "blade", "dbc_row": True}]},
                              "delivery_requirements": requirements,
                              "available_artifacts": []})
        result = host.admit(bad)
        self.assertEqual(result["code"], "CAPABILITY_MISSING")
        self.assertIn("mpq_patch", result["message"])

    def test_mixed_package_with_capabilities_but_no_mpq_artifact_is_refused(self):
        requirements = delivery_requirements({"spec_version": 1,
                                              "addon": {"title": "UI"},
                                              "items": [{"key": "blade", "dbc_row": True}]})
        caps = {key: value for key, value in FakeHost().capabilities().items()
                if key != "adapter_version"}
        caps["client_server_dbc_parity"] = True
        caps["mpq_patch"] = True
        host = FakeHost(capabilities=caps,
                        dbc_hashes={"client": {"Item.dbc": "a" * 64},
                                    "server": {"Item.dbc": "a" * 64}})
        supplied = [{"kind": entry["kind"], "table": entry.get("table")}
                    for entry in requirements["artifacts"] if entry["kind"] != "mpq"]
        bad = intent(payload={"patch_cost": "client-files", "ids": {},
                              "schema_version": "1.0.0", "unverified": [],
                              "feature_spec": {"spec_version": 1,
                                               "addon": {"title": "UI"},
                                               "items": [{"key": "blade", "dbc_row": True}]},
                              "delivery_requirements": requirements,
                              "available_artifacts": supplied})
        result = host.admit(bad)
        self.assertEqual(result["code"], "ARTIFACT_MISSING")
        self.assertIn("mpq", result["message"])

    def test_missing_or_different_client_server_hashes_fail_parity(self):
        requirements = delivery_requirements({"spec_version": 1,
                                              "items": [{"key": "blade", "dbc_row": True}]})
        caps = {key: value for key, value in FakeHost().capabilities().items()
                if key != "adapter_version"}
        caps["client_server_dbc_parity"] = True
        caps["mpq_patch"] = True
        bad = intent(payload={"patch_cost": "none", "ids": {},
                              "schema_version": "1.0.0", "unverified": [],
                              "feature_spec": {"spec_version": 1,
                                               "items": [{"key": "blade", "dbc_row": True}]},
                              "delivery_requirements": requirements,
                              "available_artifacts": []})
        for hashes in (
                {"client": {"Item.dbc": "a" * 64},
                 "server": {"Item.dbc": "b" * 64}},
                {"client": {"Item.dbc": "a" * 64}, "server": {}}):
            with self.subTest(hashes=hashes):
                host = FakeHost(capabilities=caps, dbc_hashes=hashes)
                self.assertEqual(host.admit(bad)["code"], "DBC_PARITY_MISSING")

    def test_requirements_must_match_embedded_feature_spec(self):
        host = FakeHost()
        feature_spec = {"spec_version": 1, "addon": {"title": "UI"},
                        "items": [{"key": "blade", "dbc_row": True}]}
        bad = intent(payload={"feature_spec": feature_spec, "patch_cost": "none"})
        self.assertEqual(host.admit(bad)["code"], "REQUIREMENTS_INVALID")

    def test_missing_structured_requirements_fail_closed(self):
        host = FakeHost()
        bad = intent()
        del bad["payload"]["delivery_requirements"]
        self.assertEqual(host.admit(bad)["code"], "REQUIREMENTS_INVALID")

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
