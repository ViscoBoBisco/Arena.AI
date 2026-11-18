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
    _value_query,
    semantic_insert_rows,
    validate_generated_sql,
    expected_world_rows,
    inspect_generated_sql,
    qualify_database,
    validate_generated_sql,
    write_attestations,
)
from foundry.qualification import _valid_database_attestation, integrity_check  # noqa: E402
from foundry.generate import world_sql  # noqa: E402

PACKAGE = helpers.REPO / "packages" / "frost-armory"


class MemoryDatabase:
    def __init__(self, *, initial_rows=None, fail_first_migration=False,
                 wrong_semantic_column=None):
        spec = json.loads((PACKAGE / "spec.json").read_text(encoding="utf-8"))
        allocation = json.loads((PACKAGE / "id-allocation.json").read_text(encoding="utf-8"))
        self.expected = expected_world_rows(spec, allocation)
        self.migration = (PACKAGE / "sql/01_world.sql").read_text(encoding="utf-8")
        self.rollback = (PACKAGE / "sql/99_rollback.sql").read_text(encoding="utf-8")
        self.rows = set(initial_rows or ())
        self.count_queries = {_count_query(row): row for row in self.expected}
        statements = validate_generated_sql(self.migration, rollback=False)
        self.semantic_rows = semantic_insert_rows(statements)
        self.semantic_queries = {
            _value_query(identity, list(values)): (identity, values)
            for identity, values in self.semantic_rows.items()
        }
        self.wrong_semantic_column = wrong_semantic_column
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
        if sql in self.semantic_queries:
            _identity, values = self.semantic_queries[sql]
            output = []
            for column, value in values.items():
                if column == self.wrong_semantic_column:
                    value = "corrupted-value"
                output.append(r"\N" if value is None else str(value))
            return "\t".join(output) + "\n"
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
    def test_spell_overlay_uses_the_pinned_schema_localized_name_column(self):
        spec = json.loads((PACKAGE / "spec.json").read_text(encoding="utf-8"))
        sql = world_sql(spec)
        self.assertIn("spell_dbc (Id, Name_Lang_enUS)", sql)
        self.assertNotIn("spell_dbc (Id, Name)", sql)
        self.assertIn("quest_template (Id, LogTitle, ObjectiveText1, VerifiedBuild)", sql)
        self.assertNotIn("quest_template (Id, Title, Objectives", sql)

    def test_composite_row_identities_match_pinned_world_table_primary_keys(self):
        spec = {
            "creatures": [{"key": "creature", "display_id": {"value": 77}}],
            "vendors": [{"creature_key": "creature", "rows": [
                {"item_key": "item"}]}],
        }
        allocation = {"allocations": {"creature": 901010, "item": 901011}}
        rows = expected_world_rows(spec, allocation)
        self.assertIn(("creature_template_model", (("creatureid", 901010), ("idx", 0))), rows)
        self.assertIn(("npc_vendor", (("entry", 901010), ("extendedcost", 0),
                                       ("item", 901011))), rows)

    def test_world_apply_qualification_refuses_without_ownership_ledger(self):
        calls = []
        with self.assertRaisesRegex(ValueError, "no durable world-row ownership ledger"):
            qualify_database(PACKAGE, "arena_foundry_disposable_test",
                             lambda sql: calls.append(sql) or "")
        self.assertEqual(calls, [], "refusal must occur before any DB query or write")

    def test_collision_and_partial_rollback_paths_are_not_attempted_without_ownership(self):
        database = MemoryDatabase()
        database.rows.update(database.expected)
        with self.assertRaisesRegex(ValueError, "no durable world-row ownership ledger"):
            qualify_database(PACKAGE, "arena_foundry_disposable_test", database)
        self.assertEqual(database.migration_count, 0)
        self.assertEqual(database.rollback_count, 0)

    def test_non_disposable_database_name_is_rejected_before_runner(self):
        calls = []
        with self.assertRaisesRegex(ValueError, "disposable_ prefix"):
            qualify_database(PACKAGE, "production_world", lambda sql: calls.append(sql) or "")
        self.assertEqual(calls, [])

    def test_rehashed_sql_value_drift_does_not_bypass_rollback_refusal(self):
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "modified"
            shutil.copytree(PACKAGE, package)
            sql_path = package / "sql/01_world.sql"
            changed = sql_path.read_text(encoding="utf-8").replace(
                "Frostbrand Blade", "Forged item name")
            self.assertNotEqual(changed, sql_path.read_text(encoding="utf-8"))
            sql_path.write_text(changed, encoding="utf-8")
            manifest_path = package / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            data = sql_path.read_bytes()
            manifest["files"]["sql/01_world.sql"] = {
                "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n",
                                     encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no durable world-row ownership ledger"):
                qualify_database(package, "arena_foundry_disposable_test",
                                 lambda sql: self.fail("guard must refuse before DB access"))

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
            "DELETE FROM creature_template_model WHERE CreatureID = 901010 AND Idx = 0;",
            "DELETE FROM creature_template WHERE entry = 901010;",
            "INSERT INTO creature_template (entry) VALUES (901010);",
        ]
        rollback = [
            "DELETE FROM creature_template_model WHERE CreatureID = 901010 AND Idx = 0;",
            "DELETE FROM creature_template WHERE entry = 901010;",
        ]
        deleted, inserted = inspect_generated_sql(migration, rollback, expected,
                                                  spec, allocations)
        self.assertEqual(inserted, expected)
        self.assertEqual(len(deleted - inserted), 1)
        self.assertIn(("creature_template_model", (("creatureid", 901010), ("idx", 0))),
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
