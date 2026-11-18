#!/usr/bin/env python3
"""Table-scoped qualification for generated Characters DB migrations.

This disposable-MySQL check qualifies the request-receipt schema and its atomic
transaction semantics. It does not qualify a running AzerothCore server, client,
or gameplay behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
DB_RE = re.compile(r"arena_foundry_disposable_characters_[A-Za-z0-9_]{1,32}\Z")
TEST_GUID = 4_294_967_000
LEGACY_GUID = 4_294_966_999


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _mysql(database: str, defaults_file: Path, sql: Optional[str] = None,
           *, input_bytes: Optional[bytes] = None, expect_success: bool = True) -> str:
    command = ["mysql", f"--defaults-extra-file={defaults_file}", "--batch", "--raw",
               "--skip-column-names", f"--database={database}"]
    result = subprocess.run(command, input=input_bytes if input_bytes is not None else sql,
                            text=input_bytes is None, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if expect_success and result.returncode:
        raise ValueError(f"mysql command failed: {result.stderr.strip()}")
    if not expect_success and result.returncode == 0:
        raise ValueError("negative SQL control unexpectedly succeeded")
    return result.stdout


def _check_package(package: Path) -> tuple[dict, dict, bytes, bytes, str]:
    if package.is_symlink() or not package.is_dir():
        raise ValueError("package must be an existing non-symlink directory")
    from foundry.generate import characters_sql_files
    from foundry.qualification import integrity_check
    from foundry.spec import validate_spec

    package = package.resolve()
    integrity = integrity_check(package)
    if not integrity["ok"]:
        raise ValueError("package integrity check failed: " + "; ".join(integrity["problems"]))
    spec = _read_json(package / "spec.json")
    tables_path = REPO / "catalog" / "world_tables.txt"
    world_tables = {line.strip() for line in tables_path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.startswith("#")}
    validation = validate_spec(spec, world_tables=world_tables)
    if not validation.ok:
        raise ValueError("package spec did not validate: " + "; ".join(validation.essential_blocked))
    protocol = spec.get("addon_protocol")
    if not isinstance(protocol, dict):
        raise ValueError("package has no addon_protocol Characters ledger declaration")
    table = protocol.get("claims_table")
    if not isinstance(table, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", table):
        raise ValueError("unsafe or missing Characters table name in spec")
    if (spec.get("delivery", {}).get("characters_sql_tables") != [table]):
        raise ValueError("delivery contract does not identify the exact Characters table")
    generated = characters_sql_files(spec)
    migration_rel = f"sql/characters/{table}.sql"
    rollback_rel = f"sql/characters/99_{spec['name']}_rollback.sql"
    migration = generated.get(migration_rel)
    rollback = generated.get(rollback_rel)
    if migration is None or rollback is None:
        raise ValueError("generator did not emit the declared Characters migration and rollback note")
    migration_bytes = migration.encode("utf-8")
    rollback_bytes = rollback.encode("utf-8")
    if (package / migration_rel).read_bytes() != migration_bytes:
        raise ValueError("packaged Characters migration differs from current spec generator")
    if (package / rollback_rel).read_bytes() != rollback_bytes:
        raise ValueError("packaged Characters rollback note differs from current spec generator")
    if re.search(rb"(?im)^\s*(DROP|ALTER|DELETE|TRUNCATE)\b", migration_bytes):
        raise ValueError("Characters migration contains a destructive/unreviewed statement")
    if re.search(rb"(?im)^\s*(DROP|DELETE|TRUNCATE|UPDATE|INSERT)\b", rollback_bytes):
        raise ValueError("Characters rollback note contains a data-changing statement")
    return spec, protocol, migration_bytes, rollback_bytes, integrity["manifest_sha256"]


def _format_claim_statements(templates: list, guid: int, request_id: str) -> list[str]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id):
        raise ValueError("unsafe claim request id")
    values = {"guid": str(guid), "request_id": request_id}
    return [sql.format(*(values[arg] for arg in args)) for sql, args in templates]


def _run_claim(database: str, defaults_file: Path, templates: list,
               receipt_table: str, guid: int, request_id: str) -> list[str]:
    statements = _format_claim_statements(templates, guid, request_id)
    transaction = "START TRANSACTION;\n" + ";\n".join(statements) + ";\nCOMMIT;\n"
    _mysql(database, defaults_file, transaction)
    result = _mysql(database, defaults_file,
        f"SELECT request_id, status, total_claims FROM `{receipt_table}` "
        f"WHERE guid = {guid} AND request_id = '{request_id}'").strip().split("\t")
    if len(result) != 3 or result[0] != request_id:
        raise ValueError(f"request-scoped receipt missing or malformed for {request_id!r}: {result!r}")
    return result


def qualify(package: Path, database: str, defaults_file: Path) -> dict:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from foundry.generate import characters_claim_transaction_sql
    from foundry.qualification import integrity_check

    if not DB_RE.fullmatch(database):
        raise ValueError("database name must be an arena_foundry_disposable_characters_* name")
    if defaults_file.is_symlink() or not defaults_file.is_file():
        raise ValueError("MySQL defaults file must be an existing non-symlink file")
    spec, protocol, migration, rollback, manifest_sha256 = _check_package(package)
    table = protocol["claims_table"]
    receipt_table = f"{table}_requests"
    table_sql = f"`{table}`"
    receipt_sql = f"`{receipt_table}`"
    target = protocol["target"]
    templates = characters_claim_transaction_sql(spec)
    transaction_sha256 = _sha256("\n".join(sql for sql, _args in templates).encode("utf-8"))

    version = _mysql(database, defaults_file, "SELECT VERSION()")
    existing_tables = _mysql(database, defaults_file,
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() "
        f"AND table_name IN ('{table}', '{receipt_table}', '{table}_legacy_v1', '{table}_stage_v2')").strip()
    if existing_tables != "0":
        raise ValueError("disposable Characters database already has Vault Terminal tables; refusing to modify it")

    def clean_reserved_fixtures():
        for fixture_table in (table, receipt_table, f"{table}_legacy_v1", f"{table}_stage_v2"):
            exists = _mysql(database, defaults_file,
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() "
                f"AND table_name = '{fixture_table}'").strip()
            if exists == "1":
                _mysql(database, defaults_file,
                    f"DELETE FROM `{fixture_table}` WHERE guid IN ({TEST_GUID}, {LEGACY_GUID})")

    # Seed the previous one-row-per-day schema, then prove that the current migration aggregates
    # its full history and keeps the source rows in the documented legacy backup table.
    try:
        _mysql(database, defaults_file,
            f"CREATE TABLE {table_sql} (guid int unsigned NOT NULL, claim_day date NOT NULL, "
            "claim_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (guid, claim_day)) "
            "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        _mysql(database, defaults_file,
            f"INSERT INTO {table_sql} (guid, claim_day) VALUES ({LEGACY_GUID}, "
            "DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)), "
            f"({LEGACY_GUID}, CURRENT_DATE())")
        _mysql(database, defaults_file, input_bytes=migration)
        _mysql(database, defaults_file, input_bytes=migration)  # idempotent after legacy conversion
        migrated_legacy = _mysql(database, defaults_file,
            f"SELECT total_claims, (last_claim_day = CURRENT_DATE()) FROM {table_sql} "
            f"WHERE guid = {LEGACY_GUID}").strip()
        legacy_rows = _mysql(database, defaults_file,
            f"SELECT COUNT(*) FROM `{table}_legacy_v1` WHERE guid = {LEGACY_GUID}").strip()
        if migrated_legacy != "2\t1" or legacy_rows != "2":
            raise ValueError("legacy per-day claims were not aggregated and retained by the migration")
    except Exception:
        clean_reserved_fixtures()
        raise
    # Refuse to remove or overwrite any prior fixture; only these reserved GUIDs are used.
    existing = _mysql(database, defaults_file,
        f"SELECT (SELECT COUNT(*) FROM {table_sql} WHERE guid = {TEST_GUID}), "
        f"(SELECT COUNT(*) FROM {receipt_sql} WHERE guid = {TEST_GUID})").strip().split("\t")
    if existing != ["0", "0"]:
        raise ValueError("disposable test GUID already has rows; refusing to overwrite existing data")

    try:
        columns = _mysql(database, defaults_file,
            "SELECT CONCAT(COLUMN_NAME, '|', COLUMN_TYPE, '|', IS_NULLABLE, '|', COLUMN_KEY, '|', ORDINAL_POSITION) "
            "FROM information_schema.columns WHERE table_schema = DATABASE() "
            f"AND table_name = '{table}' ORDER BY ORDINAL_POSITION").splitlines()
        expected_columns = [
            "guid|int unsigned|NO|PRI|1",
            "total_claims|int unsigned|NO||2",
            "last_claim_day|date|YES||3",
            "last_request_id|varchar(64)|YES||4",
        ]
        if columns != expected_columns:
            raise ValueError(f"Characters state columns differ from contract: {columns!r}")
        engine = _mysql(database, defaults_file,
            "SELECT ENGINE FROM information_schema.tables WHERE table_schema = DATABASE() "
            f"AND table_name = '{table}'").strip()
        if engine != "InnoDB":
            raise ValueError(f"Characters state table must use InnoDB, got {engine!r}")
        primary = _mysql(database, defaults_file,
            "SELECT GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX SEPARATOR ',') "
            "FROM information_schema.statistics WHERE table_schema = DATABASE() "
            f"AND table_name = '{table}' AND index_name = 'PRIMARY'").strip()
        if primary != "guid":
            raise ValueError(f"Characters state primary key differs from contract: {primary!r}")

        receipt_columns = _mysql(database, defaults_file,
            "SELECT CONCAT(COLUMN_NAME, '|', COLUMN_TYPE, '|', IS_NULLABLE, '|', COLUMN_KEY, '|', ORDINAL_POSITION) "
            "FROM information_schema.columns WHERE table_schema = DATABASE() "
            f"AND table_name = '{receipt_table}' ORDER BY ORDINAL_POSITION").splitlines()
        expected_receipt_columns = [
            "guid|int unsigned|NO|PRI|1",
            "request_id|varchar(64)|NO|PRI|2",
            "status|varchar(24)|NO||3",
            "total_claims|int unsigned|NO||4",
            "claim_day|date|NO||5",
            "created_at|timestamp|NO||6",
        ]
        if receipt_columns != expected_receipt_columns:
            raise ValueError(f"request receipt columns differ from contract: {receipt_columns!r}")
        receipt_engine = _mysql(database, defaults_file,
            "SELECT ENGINE FROM information_schema.tables WHERE table_schema = DATABASE() "
            f"AND table_name = '{receipt_table}'").strip()
        receipt_primary = _mysql(database, defaults_file,
            "SELECT GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX SEPARATOR ',') "
            "FROM information_schema.statistics WHERE table_schema = DATABASE() "
            f"AND table_name = '{receipt_table}' AND index_name = 'PRIMARY'").strip()
        if receipt_engine != "InnoDB" or receipt_primary != "guid,request_id":
            raise ValueError("request receipt table must be InnoDB with primary key (guid, request_id)")

        # Same key, independent MySQL processes/connections: both callers must replay one receipt.
        same_id = "qualify-same-0001"
        barrier = Barrier(2)
        def same_request(_index):
            barrier.wait(timeout=10)
            return _run_claim(database, defaults_file, templates, receipt_table, TEST_GUID, same_id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            same_results = list(pool.map(same_request, range(2)))
        if same_results != [[same_id, "GRANTED", "1"]] * 2:
            raise ValueError(f"same-request retry did not replay one grant: {same_results!r}")

        # The successful command's reply is intentionally discarded. A fresh client connection
        # retries the same request key and must get the persisted receipt without another increment.
        lost_response = _run_claim(database, defaults_file, templates, receipt_table, TEST_GUID,
                                   "qualify-same-0001")
        if lost_response != [same_id, "GRANTED", "1"]:
            raise ValueError(f"lost-response replay differs from original receipt: {lost_response!r}")
        state_query = (f"SELECT total_claims, (last_claim_day = CURRENT_DATE()), last_request_id "
                       f"FROM {table_sql} WHERE guid = {TEST_GUID}")
        state = _mysql(database, defaults_file, state_query).strip().split("\t")
        if state != ["1", "1", same_id]:
            raise ValueError(f"same request mutated state more than once: {state!r}")

        # Distinct concurrent request IDs serialize on the state row and each receives its own result.
        _mysql(database, defaults_file,
            f"UPDATE {table_sql} SET last_claim_day = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY) "
            f"WHERE guid = {TEST_GUID}")
        distinct_ids = ("qualify-race-a", "qualify-race-b")
        barrier = Barrier(2)
        def distinct_request(request_id):
            barrier.wait(timeout=10)
            return _run_claim(database, defaults_file, templates, receipt_table, TEST_GUID, request_id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            distinct_results = list(pool.map(distinct_request, distinct_ids))
        statuses = sorted(result[1] for result in distinct_results)
        if statuses != ["DENIED_COOLDOWN", "GRANTED"]:
            raise ValueError(f"distinct racing requests were misattributed: {distinct_results!r}")
        if {result[0] for result in distinct_results} != set(distinct_ids):
            raise ValueError(f"race response request IDs were substituted: {distinct_results!r}")
        state = _mysql(database, defaults_file, state_query).strip().split("\t")
        if state[0:2] != ["2", "1"]:
            raise ValueError(f"different request race must increment exactly once: {state!r}")

        # New ID after the DB date changes is allowed; retrying yesterday's ID remains its old receipt.
        rollover_id = "qualify-rollover-1"
        _mysql(database, defaults_file,
            f"UPDATE {table_sql} SET last_claim_day = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY) "
            f"WHERE guid = {TEST_GUID}")
        rollover = _run_claim(database, defaults_file, templates, receipt_table, TEST_GUID, rollover_id)
        if rollover[1] != "GRANTED":
            raise ValueError(f"claim after date rollover was refused: {rollover!r}")
        if _run_claim(database, defaults_file, templates, receipt_table, TEST_GUID, rollover_id) != rollover:
            raise ValueError("date-rollover request retry did not replay its original receipt")

        # Cap race: reset fixture state below target, then force the DB day boundary.
        _mysql(database, defaults_file,
            f"UPDATE {table_sql} SET total_claims = {target - 1}, "
            f"last_claim_day = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY), last_request_id = NULL "
            f"WHERE guid = {TEST_GUID}")
        cap_ids = ("qualify-cap-a", "qualify-cap-b")
        barrier = Barrier(2)
        def cap_request(request_id):
            barrier.wait(timeout=10)
            return _run_claim(database, defaults_file, templates, receipt_table, TEST_GUID, request_id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            cap_results = list(pool.map(cap_request, cap_ids))
        cap_statuses = sorted(result[1] for result in cap_results)
        if cap_statuses != ["DENIED_TARGET", "GRANTED"]:
            raise ValueError(f"concurrent cap requests crossed or lost the target: {cap_results!r}")
        capped = _mysql(database, defaults_file, state_query).strip().split("\t")
        if capped[0] != str(target):
            raise ValueError(f"counter exceeded declared target {target}: {capped!r}")
        after_target = _run_claim(database, defaults_file, templates, receipt_table, TEST_GUID, "qualify-cap-c")
        if after_target[1] != "DENIED_TARGET" or after_target[2] != str(target):
            raise ValueError(f"claim after target was not refused: {after_target!r}")

        # A DB failure after state update but before receipt finalization must roll the whole
        # transaction back; the same id is then retried successfully.
        _mysql(database, defaults_file,
            f"UPDATE {table_sql} SET total_claims = 0, last_claim_day = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY), "
            f"last_request_id = NULL WHERE guid = {TEST_GUID}")
        failure_id = "qualify-db-failure"
        failure_statements = _format_claim_statements(templates, TEST_GUID, failure_id)
        failed_transaction = ("START TRANSACTION;\n" + ";\n".join(failure_statements[:3])
                             + ";\nUPDATE `arena_intentionally_missing_table` SET x = 1;\nCOMMIT;\n")
        _mysql(database, defaults_file, failed_transaction, expect_success=False)
        rolled_back = _mysql(database, defaults_file, state_query).strip().split("\t")
        pending_count = _mysql(database, defaults_file,
            f"SELECT COUNT(*) FROM {receipt_sql} WHERE guid = {TEST_GUID} "
            f"AND request_id = '{failure_id}'").strip()
        if rolled_back[0] != "0" or pending_count != "0":
            raise ValueError("failed request transaction left partial state or a pending receipt")
        retried = _run_claim(database, defaults_file, templates, receipt_table, TEST_GUID, failure_id)
        if retried[1] != "GRANTED":
            raise ValueError(f"same request could not retry after transaction rollback: {retried!r}")

        # Normal state query and unique character row checks remain independent of receipts.
        duplicate = _mysql(database, defaults_file,
            f"INSERT INTO {table_sql} (guid, total_claims, last_claim_day, last_request_id) "
            f"VALUES ({TEST_GUID}, 1, CURRENT_DATE(), NULL)", expect_success=False)
        _mysql(database, defaults_file,
            f"SELECT COALESCE(MAX(total_claims), 0), "
            f"COALESCE(MAX(last_claim_day = CURRENT_DATE()), 0) "
            f"FROM {table_sql} WHERE guid = {TEST_GUID}")
    finally:
        # Only the two fixed out-of-range test GUIDs in the explicitly disposable DB are removed.
        clean_reserved_fixtures()
    fixture_rows = _mysql(database, defaults_file,
        f"SELECT (SELECT COUNT(*) FROM {table_sql} WHERE guid IN ({TEST_GUID}, {LEGACY_GUID})), "
        f"(SELECT COUNT(*) FROM {receipt_sql} WHERE guid IN ({TEST_GUID}, {LEGACY_GUID})), "
        f"(SELECT COUNT(*) FROM `{table}_legacy_v1` WHERE guid IN ({TEST_GUID}, {LEGACY_GUID}))").strip()
    if fixture_rows != "0\t0\t0":
        raise ValueError(f"test fixtures were not removed: {fixture_rows}")

    from foundry.generate import characters_claim_transaction_sql
    pins = _read_json(REPO / "foundry" / "core-pins.json")["azerothcore_wotlk"]
    proof_body = {
        "schema": "arena-characters-db-qualification/3",
        "qualification": "ARENA-DB-CHARACTERS",
        "status": "PASS",
        "trust": "SYNTHETIC_DISPOSABLE_MYSQL",
        "package": spec["name"],
        "package_version": spec["version"],
        "manifest_sha256": manifest_sha256,
        "table": table,
        "database": database,
        "server_version": version.strip(),
        "migration_sha256": _sha256(migration),
        "rollback_note_sha256": _sha256(rollback),
        "request_transaction_sha256": transaction_sha256,
        "module_source_sha256": integrity_check(package)["manifest"]["files"][
            f"server/mod-{spec['name']}/src/{''.join(part.capitalize() for part in spec['name'].split('-'))}.cpp"]["sha256"],
        "spec_sha256": _sha256((package.resolve() / "spec.json").read_bytes()),
        "core_source": {"repository": pins["repository"], "revision": pins["revision"]},
        "table_schema": {"engine": engine, "columns": columns, "primary_key": primary},
        "request_table_schema": {"engine": receipt_engine, "columns": receipt_columns,
                                  "primary_key": receipt_primary},
        "checks": {
            "migration_applied_twice": "PASS",
            "legacy_daily_claims_migrated_and_retained": "PASS",
            "schema_matches_declared_ledger": "PASS",
            "same_request_concurrent_retry_replayed": "PASS",
            "lost_response_retry_replayed": "PASS",
            "concurrent_distinct_requests_attributed": "PASS",
            "claim_day_rollover": "PASS",
            "concurrent_target_cap_enforced": "PASS",
            "transaction_failure_rolled_back": "PASS",
            "retry_after_transaction_failure": "PASS",
            "receipt_survives_new_connection": "PASS",
            "module_state_query": "PASS",
            "duplicate_character_row_rejected": "PASS",
            "fixture_rows_removed": "PASS",
        },
        "sequence": [
            "seed-legacy-per-day-schema", "migrate-and-verify-history-preservation",
            "verify-state-and-receipt-schema", "apply-migration-idempotently",
            "race-same-request-id", "drop-success-response-and-retry-on-new-connection",
            "race-distinct-request-ids", "advance-database-day-and-retry-same-id",
            "race-at-target-minus-one", "attempt-after-target", "inject-mid-transaction-failure",
            "retry-failed-id", "verify-primary-key", "remove-only-test-guid-rows",
        ],
        "limitations": [
            "Disposable MySQL transaction/receipt checks only; this does not exercise the AzerothCore chat handler.",
            "Does not load a complete AzerothCore Characters database or run worldserver, addon, client, Playerbots, or Campaign Party.",
            "Request receipts are retained with character state; this check removes only its fixed disposable GUID rows.",
        ],
        "environment": {
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
            "workflow_job": os.environ.get("GITHUB_JOB"),
            "workflow_name": os.environ.get("GITHUB_WORKFLOW"),
            "workflow_ref": os.environ.get("GITHUB_WORKFLOW_REF"),
            "source_commit": os.environ.get("GITHUB_SHA"),
            "core_repository": pins["repository"],
            "core_revision": pins["revision"],
        },
    }
    return proof_body


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--database", required=True)
    parser.add_argument("--defaults-extra-file", type=Path, required=True)
    parser.add_argument("--confirm-disposable", action="store_true",
                        help="required acknowledgement; database name must use the protected disposable prefix")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.confirm_disposable:
        print("qualification refused: pass --confirm-disposable", file=sys.stderr)
        return 2
    if args.out.exists() or args.out.is_symlink():
        print(f"qualification refused: output already exists: {args.out}", file=sys.stderr)
        return 2
    try:
        args.out.resolve().relative_to(args.package.resolve())
    except ValueError:
        pass
    else:
        print("qualification refused: proof output must be outside the source package", file=sys.stderr)
        return 2
    try:
        proof = qualify(args.package, args.database, args.defaults_extra_file)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{args.out.name}.", dir=args.out.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(proof, stream, indent=2, sort_keys=True)
                stream.write("\n")
            try:
                os.link(temp_name, args.out)
            except FileExistsError as exc:
                raise ValueError(f"qualification output appeared during write: {args.out}") from exc
            os.unlink(temp_name)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"Characters DB qualification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
