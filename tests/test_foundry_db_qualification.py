"""Database qualification harness: safety, identity checks and cleanup behavior."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

sys.path.insert(0, str(helpers.REPO))
from foundry.db_qualification import (  # noqa: E402
    _count_query,
    _parse_predicate,
    expected_world_rows,
    inspect_generated_sql,
    qualify_database,
    validate_generated_sql,
    write_attestations,
)
from foundry.qualification import _valid_database_attestation, integrity_check  # noqa: E402

PACKAGE = helpers.REPO / "packages" / "frost-armory"


class MemoryDatabase:
    def __init__(self, *, initial_rows=None, fail_first_migration=False):
        spec = json.loads((PACKAGE / "spec.json").read_text(encoding="utf-8"))
        allocation = json.loads((PACKAGE / "id-allocation.json").read_text(encoding="utf-8"))
        self.expected = expected_world_rows(spec, allocation)
        self.migration = (PACKAGE / "sql/01_world.sql").read_text(encoding="utf-8")
        self.rollback = (PACKAGE / "sql/99_rollback.sql").read_text(encoding="utf-8")
        self.rows = set(initial_rows or ())
        self.count_queries = {_count_query(row): row for row in self.expected}
        self.migration_count = 0
        self.rollback_count = 0
        self.fail_first_migration = fail_first_migration

    def __call__(self, sql: str) -> str:
        if sql == "SELECT DATABASE();":
            return "arena_foundry_disposable_test\n"
        if sql == "SELECT VERSION();":
            return "MariaDB test double\n"
        if sql in self.count_queries:
            return "1\n" if self.count_queries[sql] in self.rows else "0\n"
        if sql == self.migration:
            self.migration_count += 1
            self.rows.update(self.expected)
            if self.fail_first_migration and self.migration_count == 1:
                raise RuntimeError("injected migration interruption")
            return ""
        if sql == self.rollback:
            self.rollback_count += 1
            self.rows.difference_update(self.expected)
            return ""
        raise AssertionError(f"unexpected SQL sent to fake database: {sql[:100]!r}")


class DatabaseQualification(unittest.TestCase):
    def test_apply_idempotence_rollback_and_reapply_produce_bound_attestation(self):
        database = MemoryDatabase()
        attestations = qualify_database(PACKAGE, "arena_foundry_disposable_test", database)
        self.assertEqual(database.migration_count, 3)
        self.assertEqual(database.rollback_count, 2)
        self.assertEqual(database.rows, set())
        proof = attestations["database_qualification"]
        manifest = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(proof["checked_rows"], len(database.expected))
        self.assertEqual(proof["manifest_sha256"], hashlib.sha256(
            (PACKAGE / "manifest.json").read_bytes()).hexdigest())
        self.assertEqual(attestations["subject"]["manifest_sha256"],
                         proof["manifest_sha256"])
        self.assertEqual(attestations["levels"]["ARENA-DB"]["status"], "PASS")
        self.assertEqual(manifest["spec"], "frost-armory")

    def test_db_proof_is_semantically_bound_to_package_and_schema_pin(self):
        # The in-memory runner only tests proof validation structure; it is not DB evidence.
        attestations = qualify_database(
            PACKAGE, "arena_foundry_disposable_test", MemoryDatabase())
        manifest_sha = integrity_check(PACKAGE)["manifest_sha256"]
        pin = json.loads((helpers.REPO / "foundry/core-pins.json").read_text())[
            "azerothcore_wotlk"]
        proof = attestations["database_qualification"]
        proof["schema_source"] = {
            "schema": "arena-db-schema-source/1",
            "repository": pin["repository"],
            "revision": pin["revision"],
            "tables": {
                f"data/sql/base/db_world/{table}.sql": "a" * 64
                for table in ("creature_template", "creature_template_model", "game_event",
                              "item_template", "npc_vendor", "quest_template", "spell_dbc")
            },
        }
        proof_bytes = (json.dumps(proof, sort_keys=True, separators=(",", ":")) + "\n").encode()
        attestations["levels"]["ARENA-DB"]["evidence"] = (
            "sha256:" + hashlib.sha256(proof_bytes).hexdigest())
        self.assertTrue(_valid_database_attestation(attestations, manifest_sha, PACKAGE))

        proof["migration_sha256"] = "0" * 64
        proof_bytes = (json.dumps(proof, sort_keys=True, separators=(",", ":")) + "\n").encode()
        attestations["levels"]["ARENA-DB"]["evidence"] = (
            "sha256:" + hashlib.sha256(proof_bytes).hexdigest())
        self.assertFalse(_valid_database_attestation(attestations, manifest_sha, PACKAGE))

    def test_refuses_collisions_and_unsafe_database_names_before_writes(self):
        database = MemoryDatabase()
        database.rows.update(database.expected)
        with self.assertRaisesRegex(RuntimeError, "expected 0, found 1"):
            qualify_database(PACKAGE, "arena_foundry_disposable_test", database)
        self.assertEqual(database.migration_count, 0)
        self.assertEqual(database.rollback_count, 0)

        calls = []
        with self.assertRaisesRegex(ValueError, "disposable_ prefix"):
            qualify_database(PACKAGE, "production_world", lambda sql: calls.append(sql) or "")
        self.assertEqual(calls, [])

    def test_failed_partial_apply_attempts_cleanup_and_emits_no_attestation(self):
        database = MemoryDatabase(fail_first_migration=True)
        with self.assertRaisesRegex(RuntimeError, "injected migration interruption"):
            qualify_database(PACKAGE, "arena_foundry_disposable_test", database)
        self.assertEqual(database.rollback_count, 1)
        self.assertEqual(database.rows, set())

    def test_attestation_write_is_atomic_and_never_overwrites(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "attestations.json"
            payload = {"schema": "arena-qualification-attestations/1"}
            write_attestations(path, payload)
            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                write_attestations(path, {"schema": "forged"})
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(tmp).glob(".attestations.json.*")), [])

    def test_cleanup_only_creature_model_predicates_are_preflighted_and_rolled_back(self):
        spec = {"creatures": [{"key": "keeper"}]}
        allocations = {"allocations": {"keeper": 901010}}
        expected = expected_world_rows(spec, allocations)
        migration = [
            "DELETE FROM creature_template_model WHERE CreatureID = 901010;",
            "DELETE FROM creature_template WHERE entry = 901010;",
            "INSERT INTO creature_template (entry) VALUES (901010);",
        ]
        rollback = [
            "DELETE FROM creature_template_model WHERE CreatureID = 901010;",
            "DELETE FROM creature_template WHERE entry = 901010;",
        ]
        deleted, inserted = inspect_generated_sql(migration, rollback, expected,
                                                  spec, allocations)
        self.assertEqual(inserted, expected)
        self.assertEqual(len(deleted - inserted), 1)
        self.assertIn(("creature_template_model", (("creatureid", 901010),)),
                      deleted - inserted)

    def test_sql_artifacts_reject_ddl_and_second_statements(self):
        with self.assertRaisesRegex(ValueError, "outside the allowed DML subset"):
            validate_generated_sql("DROP TABLE item_template;", rollback=False)
        with self.assertRaisesRegex(ValueError, "multiple statements"):
            validate_generated_sql("DELETE FROM item_template WHERE entry = 1; DROP TABLE x;",
                                   rollback=True)
        validate_generated_sql("-- generated\nDELETE FROM item_template WHERE entry = 1;\n",
                               rollback=True)
        with self.assertRaisesRegex(ValueError, "unsupported generated SQL predicate"):
            _parse_predicate("item_template", "entry = 1 OR 1=1")
        with self.assertRaisesRegex(ValueError, "unsupported generated SQL predicate"):
            _parse_predicate("item_template", "entry = 1 AND AND")


if __name__ == "__main__":
    unittest.main()
