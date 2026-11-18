"""Exact pinned AzerothCore DDL and abrupt-process-restart recovery tests.

The integration path is opt-in and must point at a fresh, empty database whose
name is explicitly disposable. It never drops databases or World rows.
"""
from __future__ import annotations

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
from foundry.package import build_package  # noqa: E402
from foundry.spec import validate_spec  # noqa: E402
from foundry.world_ownership import OwnershipError, operate_on_package  # noqa: E402
from foundry.world_schema import (  # noqa: E402
    _extract_create_table, initialize_pinned_empty_world_schema,
)

_PIN = "6ffb62abb867dabb258de6813ff5d06d6b011543"
_SOURCE = os.environ.get("ARENA_PINNED_CORE_SOURCE")
_REQUIRED = ("ARENA_MYSQL_HOST", "ARENA_MYSQL_DATABASE", "ARENA_MYSQL_USER",
             "ARENA_MYSQL_PASSWORD", "ARENA_PINNED_CORE_SOURCE")
_ENABLED = all(os.environ.get(key) is not None for key in _REQUIRED)


def _crash_during_apply(database: str, package: str, env: dict) -> None:
    """Die after MySQL executes the first World INSERT but before commit."""
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
            if sql.lstrip().upper().startswith("INSERT INTO `ITEM_TEMPLATE`") or \
                    sql.lstrip().upper().startswith("INSERT INTO ITEM_TEMPLATE"):
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
        cls.package = Path(cls.temp.name) / "pinned-world-fixture"
        spec = {
            "spec_version": 1, "name": "pinned-world-fixture", "version": "1.0.0",
            "title": "Pinned World schema process recovery fixture",
            "description": "Synthetic disposable DB fixture; no installable gameplay.",
            "id_range": {"start": 902000, "end": 902001},
            "requires": {"core": f"azerothcore-wotlk@{_PIN}", "client_build": "3.3.5a.12340"},
            "items": [{"key": "process-recovery-item", "name": "Process recovery fixture",
                       "description": "Exact pinned DDL test."}],
            "acceptance": [
                {"check": "DDL compatibility", "expected": "pass"},
                {"check": "crash recovery", "expected": "pass"},
                {"check": "rollback ownership", "expected": "pass"},
            ],
        }
        result = validate_spec(spec, world_tables={"item_template"})
        if not result.ok:
            raise AssertionError(f"fixture spec failed validation: {result.essential_blocked}")
        build_package(spec, result, cls.package)
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
        cls.entry = next(iter(json.loads((cls.package / "id-allocation.json").read_text())[
            "allocations"].values()))

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

    def test_real_process_death_mid_transaction_rolls_back_then_restart_recovers(self):
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
        self.assertEqual(proc.exitcode, 73, "child must terminate at the injected World INSERT")

        conn = self.connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM item_template WHERE entry=%s", (self.entry,))
                self.assertEqual(cursor.fetchone()[0], 0, "uncommitted World row survived client death")
                cursor.execute("SELECT COUNT(*) FROM arena_foundry_world_rows WHERE table_name='item_template'")
                self.assertEqual(cursor.fetchone()[0], 0, "ownership journal partially committed")
                cursor.execute("SELECT COUNT(*) FROM arena_foundry_world_receipts WHERE request_id=%s",
                               ("process-crash-restart-apply",))
                self.assertEqual(cursor.fetchone()[0], 0, "receipt partially committed")
            outcome = operate_on_package(conn, self.package, action="apply",
                                         request_id="process-crash-restart-apply")
            self.assertEqual(outcome["changed_rows"], 1)
            repeated = operate_on_package(conn, self.package, action="apply",
                                          request_id="process-crash-restart-apply")
            self.assertTrue(repeated["replayed"])
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM item_template WHERE entry=%s", (self.entry,))
                self.assertEqual(cursor.fetchone()[0], 1)
            rolled = operate_on_package(conn, self.package, action="rollback",
                                        request_id="process-crash-restart-rollback")
            self.assertEqual(rolled["changed_rows"], 1)
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM item_template WHERE entry=%s", (self.entry,))
                self.assertEqual(cursor.fetchone()[0], 0, "owned row did not cleanly roll back")
            proof = dict(self.schema_proof)
            proof["process_recovery"] = {
                "status": "PASS",
                "child_exit_code": proc.exitcode,
                "crash_after_first_insert_before_commit": "PASS",
                "uncommitted_world_row_and_journal_absent_after_restart": "PASS",
                "same_request_retry_committed_once": "PASS",
                "same_request_receipt_replayed": "PASS",
                "owned_rollback_removed_unchanged_row": "PASS",
            }
            proof.pop("runtime_schema_sha256", None)
            proof["runtime_schema_sha256"] = hashlib.sha256(
                json.dumps(proof, sort_keys=True, separators=(",", ":"), default=str)
                .encode("utf-8")).hexdigest()
            proof_path = os.environ.get("ARENA_WORLD_SCHEMA_PROOF")
            if proof_path:
                Path(proof_path).write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n",
                                            encoding="utf-8")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
