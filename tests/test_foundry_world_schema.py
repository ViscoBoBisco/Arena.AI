"""Exact pinned AzerothCore DDL and seven-table ownership recovery tests.

The integration path is opt-in and must target a fresh, empty database whose
name is explicitly disposable. It never drops databases or World rows.
"""
from __future__ import annotations

import copy
import hashlib
import json
import multiprocessing
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402
sys.path.insert(0, str(helpers.REPO))
from foundry.assets import ingest_dbc_dir  # noqa: E402
from foundry.db_qualification import _DB_SCHEMA_TABLES, expected_world_rows  # noqa: E402
from foundry.package import build_package  # noqa: E402
from foundry.spec import validate_spec  # noqa: E402
from foundry.world_ownership import OwnershipError, operate_on_package  # noqa: E402
from foundry.world_qualification import qualify_owned_world_package  # noqa: E402
from foundry.db_qualification import write_attestations  # noqa: E402
from foundry.qualification import _valid_database_attestation, integrity_check  # noqa: E402
from foundry.world_schema import (  # noqa: E402
    _extract_create_table, initialize_pinned_empty_world_schema,
)

_PIN = "6ffb62abb867dabb258de6813ff5d06d6b011543"
_SOURCE = os.environ.get("ARENA_PINNED_CORE_SOURCE")
_REQUIRED = ("ARENA_MYSQL_HOST", "ARENA_MYSQL_DATABASE", "ARENA_MYSQL_USER",
             "ARENA_MYSQL_PASSWORD", "ARENA_PINNED_CORE_SOURCE")
_ENABLED = all(os.environ.get(key) is not None for key in _REQUIRED)


def _crash_during_apply(database: str, package: str, env: dict) -> None:
    """Die after vendor INSERT, with five supported-table writes uncommitted."""
    for key, value in env.items():
        os.environ[key] = value
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import pymysql
    conn = pymysql.connect(
        host=os.environ["ARENA_MYSQL_HOST"],
        port=int(os.environ.get("ARENA_MYSQL_PORT", "3306")),
        user=os.environ["ARENA_MYSQL_USER"], password=os.environ["ARENA_MYSQL_PASSWORD"],
        database=database, charset="utf8mb4", autocommit=True,
    )

    class CrashCursor:
        def __init__(self, inner):
            self.inner = inner

        def execute(self, sql, params=None):
            result = self.inner.execute(sql, params) if params is not None else self.inner.execute(sql)
            if sql.lstrip().upper().startswith("INSERT INTO NPC_VENDOR"):
                os._exit(73)
            return result

        def __getattr__(self, name):
            return getattr(self.inner, name)

    class CrashConnection:
        def cursor(self):
            return CrashCursor(conn.cursor())

        def __getattr__(self, name):
            return getattr(conn, name)

    operate_on_package(CrashConnection(), Path(package), action="apply",
                       request_id="process-crash-restart-apply")
    os._exit(74)  # the expected interruption point must be reached


def _row_count(cursor, identity) -> int:
    table, keys = identity
    where = " AND ".join(f"`{column}`=%s" for column, _ in keys)
    cursor.execute(f"SELECT COUNT(*) FROM `{table}` WHERE {where}",
                   tuple(value for _, value in keys))
    return int(cursor.fetchone()[0])


def _assert_identity_counts(testcase, connection, identities, expected):
    with connection.cursor() as cursor:
        for identity in identities:
            testcase.assertEqual(_row_count(cursor, identity), expected,
                                 f"unexpected count for {identity!r}")


class PinnedDDLUnit(unittest.TestCase):
    def test_create_extractor_returns_one_unmodified_table_ddl(self):
        sql = "-- fixture\nCREATE TABLE `item_template` (\n  `entry` int NOT NULL,\n  PRIMARY KEY (`entry`)\n) ENGINE=InnoDB;\n"
        self.assertEqual(_extract_create_table(sql, "item_template"),
                         "CREATE TABLE `item_template` (\n  `entry` int NOT NULL,\n  PRIMARY KEY (`entry`)\n) ENGINE=InnoDB;")

    def test_create_extractor_fails_closed_for_missing_or_ambiguous_source(self):
        with self.assertRaises(OwnershipError):
            _extract_create_table("-- no DDL\n", "item_template")
        duplicate = ("CREATE TABLE `item_template` (\n`entry` int\n) ENGINE=InnoDB;\n" * 2)
        with self.assertRaises(OwnershipError):
            _extract_create_table(duplicate, "item_template")


@unittest.skipUnless(_ENABLED, "requires a fresh disposable MySQL DB and exact pinned core checkout")
class PinnedWorldSchemaCrashRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import pymysql
        except ImportError as exc:
            raise unittest.SkipTest("install PyMySQL for disposable MySQL integration") from exc
        cls.pymysql = pymysql
        cls.database = os.environ["ARENA_MYSQL_DATABASE"]
        if not cls.database.startswith("arena_foundry_disposable_"):
            raise AssertionError("integration target must use disposable database prefix")
        cls.temp = tempfile.TemporaryDirectory(prefix="arena-pinned-world-")
        cls.package = Path(os.environ.get("ARENA_WORLD_FIXTURE_PACKAGE",
                                         str(Path(cls.temp.name) / "pinned-world-fixture")))
        if cls.package.exists():
            raise AssertionError(f"refusing to overwrite package fixture: {cls.package}")
        cls.package.parent.mkdir(parents=True, exist_ok=True)
        dbc_dir = Path(cls.temp.name) / "dbc"
        helpers.write_dbc(dbc_dir / "CreatureDisplayInfo.dbc", 2, [(1, 0)])
        cls.asset_index = ingest_dbc_dir(dbc_dir)
        spec = {
            "spec_version": 1, "name": "world-seven-table-fixture", "version": "1.0.0",
            "title": "World seven-table ownership fixture",
            "description": "Synthetic disposable DB fixture; no installable gameplay.",
            "id_range": {"start": 903000, "end": 903100},
            "requires": {"core": f"azerothcore-wotlk@{_PIN}", "client_build": "3.3.5a.12340"},
            "items": [{"key": "fixture-item", "name": "Fixture item",
                       "description": "Synthetic exact-schema integration."}],
            "spells": [{"key": "fixture-spell", "name": "Fixture spell"}],
            "quests": [{"key": "fixture-quest", "title": "Fixture quest",
                         "objectives": "Synthetic integration only."}],
            "creatures": [{"key": "fixture-creature", "name": "Fixture creature",
                            "display_id": {"value": 1,
                                           "evidence": cls.asset_index["creature_display"]["1"]["evidence"]}}],
            "vendors": [{"key": "fixture-vendor", "creature_key": "fixture-creature",
                         "rows": [{"item_key": "fixture-item", "slot": 1}]}],
            "events": [{"key": "fixture-event", "name": "Fixture event", "id": 241,
                        "start_time": "2026-01-01 00:00:00",
                        "end_time": "2026-12-31 00:00:00"}],
            "acceptance": [
                {"check": "all seven generated table writes", "expected": "pass"},
                {"check": "process crash and restart recovery", "expected": "pass"},
                {"check": "foreign collision and owned rollback", "expected": "pass"},
            ],
        }
        cls.spec = spec
        world_tables = set(_DB_SCHEMA_TABLES)
        result = validate_spec(spec, world_tables=world_tables,
                               asset_index=cls.asset_index)
        if not result.ok:
            raise AssertionError(f"fixture spec must validate: {result.essential_blocked}")
        build_package(spec, result, cls.package, asset_index=cls.asset_index)
        allocation = json.loads((cls.package / "id-allocation.json").read_text(encoding="utf-8"))
        cls.identities = expected_world_rows(spec, allocation)
        cls.identities_by_table = {table: [row for row in cls.identities if row[0] == table]
                                   for table in _DB_SCHEMA_TABLES}
        if set(table for table, _ in cls.identities) != set(_DB_SCHEMA_TABLES):
            raise AssertionError("fixture must emit at least one row for every supported World table")
        cls.event_id = allocation["allocations"]["fixture-event"]
        cls.item_id = allocation["allocations"]["fixture-item"]
        conn = cls.connect()
        try:
            cls.schema_proof = initialize_pinned_empty_world_schema(
                conn, Path(_SOURCE), cls.database)
        finally:
            conn.close()
        if cls.schema_proof["source"]["revision"] != _PIN:
            raise AssertionError("schema proof does not bind to required upstream commit")
        proof_path = os.environ.get("ARENA_WORLD_SCHEMA_PROOF")
        if proof_path:
            Path(proof_path).write_text(json.dumps(cls.schema_proof, indent=2, sort_keys=True) + "\n",
                                        encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temp"):
            cls.temp.cleanup()

    @classmethod
    def connect(cls):
        return cls.pymysql.connect(
            host=os.environ["ARENA_MYSQL_HOST"],
            port=int(os.environ.get("ARENA_MYSQL_PORT", "3306")),
            user=os.environ["ARENA_MYSQL_USER"], password=os.environ["ARENA_MYSQL_PASSWORD"],
            database=cls.database, charset="utf8mb4", autocommit=True,
        )

    def test_schema_proof_binds_all_seven_exact_sources_and_runtime_keys(self):
        proof = self.schema_proof
        self.assertEqual(proof["trust"], "SYNTHETIC_DISPOSABLE_MYSQL_PINNED_SCHEMA")
        self.assertFalse(proof["base_data_imported"])
        self.assertEqual(proof["source"]["revision"], _PIN)
        self.assertEqual(len(proof["created_tables"]), 7)
        self.assertEqual(set(proof["created_tables"]), set(proof["runtime_tables"]))
        expected_keys = {
            "creature_template": ["entry"],
            "creature_template_model": ["creatureid", "idx"],
            "game_event": ["evententry"],
            "item_template": ["entry"],
            "npc_vendor": ["entry", "item", "extendedcost"],
            "quest_template": ["id"],
            "spell_dbc": ["id"],
        }
        for table, evidence in proof["created_tables"].items():
            source_path = Path(_SOURCE) / evidence["source_path"]
            source_bytes = source_path.read_bytes()
            self.assertEqual(hashlib.sha256(source_bytes).hexdigest(), evidence["source_sha256"])
            ddl = _extract_create_table(source_bytes.decode("utf-8"), table)
            self.assertEqual(hashlib.sha256(ddl.encode("utf-8")).hexdigest(),
                             evidence["ddl_sha256"])
            self.assertEqual(proof["runtime_tables"][table]["engine"], "innodb")
            self.assertEqual(proof["runtime_tables"][table]["primary_key"], expected_keys[table])

    def test_all_seven_tables_apply_retry_collision_and_owned_rollback(self):
        conn = self.connect()
        try:
            # The event row is emitted last; a foreign key collision there must
            # roll back inserts/journal entries already attempted for six other tables.
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO game_event(eventEntry) VALUES(%s)", (self.event_id,))
            with self.assertRaisesRegex(OwnershipError, "pre-existing unowned row collision"):
                operate_on_package(conn, self.package, action="apply",
                                   request_id="seven-table-foreign-collision")
            with conn.cursor() as cursor:
                for identity in self.identities:
                    self.assertEqual(_row_count(cursor, identity),
                                     1 if identity[0] == "game_event" else 0)
                cursor.execute("SELECT COUNT(*) FROM arena_foundry_world_rows")
                self.assertEqual(cursor.fetchone()[0], 0, "collision left partial ownership rows")
                cursor.execute("SELECT COUNT(*) FROM arena_foundry_world_receipts "
                               "WHERE request_id='seven-table-foreign-collision'")
                self.assertEqual(cursor.fetchone()[0], 0, "failed transaction left a receipt")
                cursor.execute("DELETE FROM game_event WHERE eventEntry=%s", (self.event_id,))

            first = operate_on_package(conn, self.package, action="apply",
                                       request_id="seven-table-apply-1")
            self.assertEqual((first["rows"], first["changed_rows"]), (7, 7))
            replay = operate_on_package(conn, self.package, action="apply",
                                        request_id="seven-table-apply-1")
            self.assertTrue(replay["replayed"])
            idempotent = operate_on_package(conn, self.package, action="apply",
                                           request_id="seven-table-apply-2")
            self.assertEqual(idempotent["changed_rows"], 0)
            _assert_identity_counts(self, conn, self.identities, 1)

            with conn.cursor() as cursor:
                cursor.execute("SELECT name FROM item_template WHERE entry=%s", (self.item_id,))
                original_name = cursor.fetchone()[0]
                cursor.execute("UPDATE item_template SET name='foreign edit' WHERE entry=%s",
                               (self.item_id,))
            with self.assertRaisesRegex(OwnershipError, "changed since apply"):
                operate_on_package(conn, self.package, action="rollback",
                                   request_id="seven-table-edited-rollback")
            with conn.cursor() as cursor:
                cursor.execute("SELECT name FROM item_template WHERE entry=%s", (self.item_id,))
                self.assertEqual(cursor.fetchone()[0], "foreign edit")
                cursor.execute("UPDATE item_template SET name=%s WHERE entry=%s",
                               (original_name, self.item_id))
            rolled = operate_on_package(conn, self.package, action="rollback",
                                        request_id="seven-table-rollback-1")
            self.assertEqual(rolled["changed_rows"], 7)
            _assert_identity_counts(self, conn, self.identities, 0)
            reapplied = operate_on_package(conn, self.package, action="apply",
                                           request_id="seven-table-reapply")
            self.assertEqual(reapplied["changed_rows"], 7)
            rolled_again = operate_on_package(conn, self.package, action="rollback",
                                              request_id="seven-table-rollback-2")
            self.assertEqual(rolled_again["changed_rows"], 7)
            _assert_identity_counts(self, conn, self.identities, 0)
        finally:
            conn.close()

    def test_process_death_mid_multi_table_transaction_recovers_all_rows(self):
        env = {key: os.environ[key] for key in _REQUIRED}
        env["ARENA_MYSQL_PORT"] = os.environ.get("ARENA_MYSQL_PORT", "3306")
        proc = multiprocessing.get_context("fork").Process(
            target=_crash_during_apply, args=(self.database, str(self.package), env))
        proc.start()
        proc.join(timeout=30)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=5)
            self.fail("process-crash fixture hung instead of terminating")
        self.assertEqual(proc.exitcode, 73, "worker must die immediately after npc_vendor insert")

        conn = self.connect()
        try:
            _assert_identity_counts(self, conn, self.identities, 0)
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM arena_foundry_world_rows WHERE state='APPLIED'")
                self.assertEqual(cursor.fetchone()[0], 0,
                                 "crashed operation left any row marked applied")
                cursor.execute("SELECT COUNT(*) FROM arena_foundry_world_receipts "
                               "WHERE request_id='process-crash-restart-apply'")
                self.assertEqual(cursor.fetchone()[0], 0, "uncommitted receipt survived process death")
            outcome = operate_on_package(conn, self.package, action="apply",
                                         request_id="process-crash-restart-apply")
            self.assertEqual(outcome["changed_rows"], 7)
            repeated = operate_on_package(conn, self.package, action="apply",
                                          request_id="process-crash-restart-apply")
            self.assertTrue(repeated["replayed"])
            _assert_identity_counts(self, conn, self.identities, 1)
            rolled = operate_on_package(conn, self.package, action="rollback",
                                        request_id="process-crash-restart-rollback")
            self.assertEqual(rolled["changed_rows"], 7)
            _assert_identity_counts(self, conn, self.identities, 0)
            proof = dict(self.schema_proof)
            proof["world_row_checks"] = {
                "status": "PASS",
                "tables": {table: {"apply": "PASS", "rollback": "PASS"}
                           for table in sorted(self.identities_by_table)},
                "foreign_collision_at_last_table_rolled_back_prior_writes": "PASS",
                "changed_row_refused_rollback_without_deleting_operator_edit": "PASS",
            }
            proof["process_recovery"] = {
                "status": "PASS",
                "child_exit_code": proc.exitcode,
                "crash_after_npc_vendor_insert_before_commit": "PASS",
                "uncommitted_rows_and_journal_state_changes_absent": "PASS",
                "same_request_restart_reapplied_seven_rows": "PASS",
                "same_request_receipt_replayed": "PASS",
                "owned_rollback_removed_all_seven_rows": "PASS",
            }
            proof["evidence_sha256"] = hashlib.sha256(
                json.dumps(proof, sort_keys=True, separators=(",", ":"), default=str)
                .encode("utf-8")).hexdigest()
            type(self).schema_proof = proof
            proof_path = os.environ.get("ARENA_WORLD_SCHEMA_PROOF")
            if proof_path:
                Path(proof_path).write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n",
                                            encoding="utf-8")
        finally:
            conn.close()

    def test_source_schema_receipt_attestation_passes_world_gate_and_rejects_forgery(self):
        conn = self.connect()
        try:
            attestations = qualify_owned_world_package(
                conn, self.package, self.database, Path(_SOURCE), self.schema_proof,
                self.schema_proof, request_prefix="integration-test-q")
        finally:
            conn.close()
        manifest_sha = integrity_check(self.package)["manifest_sha256"]
        self.assertTrue(_valid_database_attestation(attestations, manifest_sha, self.package))
        output = os.environ.get("ARENA_WORLD_QUALIFICATION_ATTESTATIONS")
        if output:
            write_attestations(Path(output), attestations)

        forged = copy.deepcopy(attestations)
        proof = forged["database_qualification"]
        proof["schema_source"]["tables"]["data/sql/base/db_world/item_template.sql"] = "0" * 64
        forged["levels"]["ARENA-DB"]["evidence"] = "sha256:" + hashlib.sha256(
            (json.dumps(proof, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()
        self.assertFalse(_valid_database_attestation(forged, manifest_sha, self.package),
                         "rehashed proof with drifted pinned source hash must fail closed")

        forged = copy.deepcopy(attestations)
        proof = forged["database_qualification"]
        receipt = proof["receipts"][0]
        receipt["result_json"] = receipt["result_json"].replace('"changed_rows":7', '"changed_rows":6')
        receipt["result_json_sha256"] = hashlib.sha256(receipt["result_json"].encode()).hexdigest()
        proof["receipt_set_sha256"] = hashlib.sha256(json.dumps(
            proof["receipts"], sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
        forged["levels"]["ARENA-DB"]["evidence"] = "sha256:" + hashlib.sha256(
            (json.dumps(proof, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()
        self.assertFalse(_valid_database_attestation(forged, manifest_sha, self.package),
                         "receipt row-count drift must fail closed after attacker rehashes proof")


if __name__ == "__main__":
    unittest.main()
