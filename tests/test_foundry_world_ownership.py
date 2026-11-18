"""World ownership ledger integration against an isolated disposable MySQL schema.

The pure suite deliberately needs no credentials. Set ARENA_MYSQL_* only in the
CI disposable service to exercise real InnoDB transactions and advisory locks.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

sys.path.insert(0, str(helpers.REPO))
from foundry.package import build_package  # noqa: E402
from foundry.spec import validate_spec  # noqa: E402
from foundry.world_ownership import OwnershipError, operate_on_package  # noqa: E402


_REQUIRED_ENV = ("ARENA_MYSQL_HOST", "ARENA_MYSQL_DATABASE", "ARENA_MYSQL_USER",
                 "ARENA_MYSQL_PASSWORD")
_ENABLED = all(os.environ.get(key) is not None for key in _REQUIRED_ENV)


@unittest.skipUnless(_ENABLED, "requires explicitly configured disposable MySQL")
class WorldOwnershipMySQL(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import pymysql
        except ImportError as exc:
            raise unittest.SkipTest("install PyMySQL for disposable MySQL integration") from exc
        cls.pymysql = pymysql
        cls.database = os.environ["ARENA_MYSQL_DATABASE"]
        if not cls.database.startswith("arena_foundry_disposable_"):
            raise AssertionError("integration target must use arena_foundry_disposable_ prefix")
        cls._temp = tempfile.TemporaryDirectory(prefix="arena-world-owned-")
        cls.package_dir = Path(cls._temp.name) / "owned-fixture"
        spec = {
            "spec_version": 1, "name": "owned-fixture", "version": "1.0.0",
            "title": "Synthetic World ownership fixture",
            "description": "Integration fixture only; not installable gameplay.",
            "id_range": {"start": 901000, "end": 901010},
            "requires": {"core": "azerothcore-wotlk@6ffb62abb867dabb258de6813ff5d06d6b011543",
                         "client_build": "3.3.5a.12340"},
            "items": [
                {"key": "ownership-fixture-item", "name": "Owned fixture item",
                 "description": "Disposable MySQL ownership test."},
                {"key": "ownership-fixture-second", "name": "Owned fixture second item",
                 "description": "Used to test transaction rollback after a later collision."},
            ],
            "acceptance": [
                {"check": "fixture 1", "expected": "pass"},
                {"check": "fixture 2", "expected": "pass"},
                {"check": "fixture 3", "expected": "pass"},
            ],
        }
        result = validate_spec(spec, world_tables={"item_template"})
        if not result.ok:
            raise AssertionError(f"fixture spec must validate: {result.essential_blocked}")
        build_package(spec, result, cls.package_dir)
        from foundry.qualification import integrity_check
        cls.manifest_sha = integrity_check(cls.package_dir)["manifest_sha256"]
        cls.row_ids = sorted(json.loads((cls.package_dir / "id-allocation.json").read_text())[
            "allocations"].values())
        conn = cls.connect()
        with conn.cursor() as cursor:
            cursor.execute("CREATE TABLE IF NOT EXISTS item_template ("
                           "entry INT NOT NULL PRIMARY KEY, name VARCHAR(100) NOT NULL, "
                           "description TEXT NOT NULL, VerifiedBuild INT NOT NULL, "
                           "user_note VARCHAR(100) NOT NULL DEFAULT 'unchanged') ENGINE=InnoDB")
        conn.commit()
        conn.close()
        # Bootstrap only Arena-owned tables by running a complete fixture cycle.
        bootstrap_conn = cls.connect()
        try:
            operate_on_package(bootstrap_conn, cls.package_dir,
                              action="apply", request_id="fixture-bootstrap-apply")
            operate_on_package(bootstrap_conn, cls.package_dir,
                              action="rollback", request_id="fixture-bootstrap-rollback")
        finally:
            bootstrap_conn.close()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "_temp"):
            cls._temp.cleanup()

    @classmethod
    def connect(cls):
        return cls.pymysql.connect(
            host=os.environ["ARENA_MYSQL_HOST"],
            port=int(os.environ.get("ARENA_MYSQL_PORT", "3306")),
            user=os.environ["ARENA_MYSQL_USER"],
            password=os.environ["ARENA_MYSQL_PASSWORD"],
            database=cls.database, charset="utf8mb4", autocommit=True,
        )

    def setUp(self):
        conn = self.connect()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM item_template WHERE entry IN (%s,%s)", tuple(self.row_ids))
            cursor.execute("DELETE FROM arena_foundry_world_rows WHERE table_name='item_template'")
            cursor.execute("DELETE FROM arena_foundry_world_receipts")
        conn.commit()
        conn.close()

    def operate(self, action, request_id, *, package=None):
        conn = self.connect()
        try:
            return operate_on_package(conn, package or self.package_dir,
                                      action=action, request_id=request_id)
        finally:
            conn.close()

    def item(self, entry=None):
        conn = self.connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT entry,name,description,VerifiedBuild,user_note "
                               "FROM item_template WHERE entry=%s", (entry or self.row_ids[0],))
                return cursor.fetchone()
        finally:
            conn.close()

    def test_apply_retry_rollback_reapply_and_exact_receipts(self):
        first = self.operate("apply", "apply-fixture-1")
        self.assertEqual((first["rows"], first["changed_rows"]), (2, 2))
        replay = self.operate("apply", "apply-fixture-1")
        self.assertTrue(replay["replayed"])
        again = self.operate("apply", "apply-fixture-2")
        self.assertEqual(again["changed_rows"], 0)
        rolled = self.operate("rollback", "rollback-fixture-1")
        self.assertEqual(rolled["changed_rows"], 2)
        self.assertIsNone(self.item())
        self.assertEqual(self.operate("rollback", "rollback-fixture-2")["changed_rows"], 0)
        self.assertEqual(self.operate("apply", "apply-fixture-3")["changed_rows"], 2)

    def test_preexisting_collision_is_not_claimed_or_modified(self):
        conn = self.connect()
        collision_id = self.row_ids[1]
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO item_template(entry,name,description,VerifiedBuild) "
                           "VALUES(%s,'foreign','foreign',12340)", (collision_id,))
        conn.commit()
        conn.close()
        with self.assertRaisesRegex(OwnershipError, "pre-existing unowned row collision"):
            self.operate("apply", "collision")
        self.assertIsNone(self.item(self.row_ids[0]), "earlier row insert must roll back atomically")
        self.assertEqual(self.item(collision_id)[1], "foreign")
        conn = self.connect()
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM arena_foundry_world_rows "
                           "WHERE table_name='item_template'")
            self.assertEqual(cursor.fetchone()[0], 0, "no partial ownership journal may persist")
        conn.close()

    def test_lost_connection_after_first_row_recovers_by_same_request(self):
        class InterruptingCursor:
            def __init__(self, cursor, connection):
                self.inner = cursor
                self.connection = connection
                self.interrupted = False

            def execute(self, sql, params=None):
                if params is None:
                    result = self.inner.execute(sql)
                else:
                    result = self.inner.execute(sql, params)
                if (not self.interrupted and sql.upper().startswith("INSERT INTO ITEM_TEMPLATE")):
                    self.interrupted = True
                    self.connection.close()
                    raise RuntimeError("simulated lost DB connection after first World row")
                return result

            def __getattr__(self, name):
                return getattr(self.inner, name)

        class InterruptingConnection:
            def __init__(self, inner):
                self.inner = inner
                self.proxy_cursor = None

            def cursor(self):
                self.proxy_cursor = InterruptingCursor(self.inner.cursor(), self.inner)
                return self.proxy_cursor

            def __getattr__(self, name):
                return getattr(self.inner, name)

        interrupted = InterruptingConnection(self.connect())
        with self.assertRaisesRegex(RuntimeError, "simulated lost DB connection"):
            operate_on_package(interrupted, self.package_dir,
                               action="apply", request_id="lost-connection-retry")
        self.assertIsNone(self.item(), "uncommitted earlier row must be rolled back by MySQL")
        conn = self.connect()
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM arena_foundry_world_rows "
                           "WHERE table_name='item_template'")
            self.assertEqual(cursor.fetchone()[0], 0)
        conn.close()
        retried = self.operate("apply", "lost-connection-retry")
        self.assertEqual(retried["changed_rows"], 2)

    def test_full_row_user_change_blocks_rollback(self):
        self.operate("apply", "apply-change")
        conn = self.connect()
        with conn.cursor() as cursor:
            cursor.execute("UPDATE item_template SET user_note='changed by operator' WHERE entry=%s",
                           (self.row_ids[0],))
        conn.commit()
        conn.close()
        with self.assertRaisesRegex(OwnershipError, "changed since apply"):
            self.operate("rollback", "rollback-changed")
        self.assertEqual(self.item()[-1], "changed by operator")

    def test_changed_manifest_wrong_owner_and_reused_request_are_refused(self):
        self.operate("apply", "request-one")
        with self.assertRaisesRegex(OwnershipError, "different operation"):
            self.operate("rollback", "request-one")
        other_spec = json.loads((self.package_dir / "spec.json").read_text())
        other_spec["name"] = "other-fixture"
        other_result = validate_spec(other_spec, world_tables={"item_template"})
        other_package = Path(self._temp.name) / "other-fixture"
        build_package(other_spec, other_result, other_package)
        conn = self.connect()
        try:
            with self.assertRaisesRegex(OwnershipError, "already owned"):
                operate_on_package(conn, other_package, action="apply", request_id="wrong-owner")
        finally:
            conn.close()
        changed_spec = json.loads((self.package_dir / "spec.json").read_text())
        changed_spec["items"][0]["description"] += " updated after build"
        changed_result = validate_spec(changed_spec, world_tables={"item_template"})
        changed_package = Path(self._temp.name) / "changed-fixture"
        build_package(changed_spec, changed_result, changed_package)
        conn = self.connect()
        try:
            with self.assertRaisesRegex(OwnershipError, "already owned"):
                operate_on_package(conn, changed_package, action="apply", request_id="changed-build")
        finally:
            conn.close()

    def test_two_concurrent_same_package_requests_serialize(self):
        barrier = threading.Barrier(2)
        results, errors = [], []

        def worker(index):
            conn = self.connect()
            try:
                barrier.wait(timeout=5)
                results.append(operate_on_package(conn, self.package_dir,
                                                  action="apply", request_id=f"race-{index}"))
            except Exception as exc:  # propagate thread failure to the test thread
                errors.append(exc)
            finally:
                conn.close()

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads), "migration lock deadlocked")
        self.assertEqual(errors, [])
        self.assertEqual(sorted(item["changed_rows"] for item in results), [0, 2])

    def test_engine_requirement_refuses_non_innodb_targets(self):
        conn = self.connect()
        with conn.cursor() as cursor:
            cursor.execute("ALTER TABLE item_template ENGINE=MyISAM")
        conn.commit()
        conn.close()
        try:
            with self.assertRaisesRegex(OwnershipError, "must exist and use InnoDB"):
                self.operate("apply", "myisam-refusal")
        finally:
            conn = self.connect()
            with conn.cursor() as cursor:
                cursor.execute("ALTER TABLE item_template ENGINE=InnoDB")
            conn.commit()
            conn.close()

    def test_refuses_existing_caller_transaction_without_committing_or_rolling_it_back(self):
        conn = self.connect()
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO item_template(entry,name,description,VerifiedBuild) "
                           "VALUES(%s,'caller-owned','pending',12340)", (self.row_ids[1],))
        try:
            with self.assertRaisesRegex(OwnershipError, "connection must be idle"):
                operate_on_package(conn, self.package_dir,
                                   action="apply", request_id="caller-transaction")
            with conn.cursor() as cursor:
                cursor.execute("SELECT name FROM item_template WHERE entry=%s", (self.row_ids[1],))
                self.assertEqual(cursor.fetchone()[0], "caller-owned",
                                 "refusal must not roll back caller-owned work")
        finally:
            conn.rollback()
            conn.close()

    def test_schema_primary_key_must_match_the_pinned_table_identity(self):
        conn = self.connect()
        with conn.cursor() as cursor:
            cursor.execute("ALTER TABLE item_template DROP PRIMARY KEY, "
                           "ADD PRIMARY KEY(entry,name)")
        conn.commit()
        conn.close()
        try:
            with self.assertRaisesRegex(OwnershipError, "primary key .* does not match pinned key"):
                self.operate("apply", "wrong-primary-key")
        finally:
            conn = self.connect()
            with conn.cursor() as cursor:
                cursor.execute("ALTER TABLE item_template DROP PRIMARY KEY, ADD PRIMARY KEY(entry)")
            conn.commit()
            conn.close()


if __name__ == "__main__":
    unittest.main()
