#!/usr/bin/env python3
"""Table-scoped qualification for generated Characters DB migrations.

This synthetic disposable-MySQL check qualifies the generated ledger schema,
its idempotent application, primary-key uniqueness and the module's read query.
It does not qualify a running AzerothCore server, client, or gameplay behavior.
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


def qualify(package: Path, database: str, defaults_file: Path) -> dict:
    from foundry.generate import characters_claim_upsert_sql

    if not DB_RE.fullmatch(database):
        raise ValueError("database name must be an arena_foundry_disposable_characters_* name")
    if defaults_file.is_symlink() or not defaults_file.is_file():
        raise ValueError("MySQL defaults file must be an existing non-symlink file")
    spec, protocol, migration, rollback, manifest_sha256 = _check_package(package)
    table = protocol["claims_table"]
    table_sql = f"`{table}`"

    version = _mysql(database, defaults_file, "SELECT VERSION()")
    _mysql(database, defaults_file, input_bytes=migration)
    _mysql(database, defaults_file, input_bytes=migration)  # idempotent application

    columns = _mysql(database, defaults_file,
        "SELECT CONCAT(COLUMN_NAME, '|', COLUMN_TYPE, '|', IS_NULLABLE, '|', COLUMN_KEY, '|', ORDINAL_POSITION) "
        "FROM information_schema.columns WHERE table_schema = DATABASE() "
        f"AND table_name = '{table}' ORDER BY ORDINAL_POSITION").splitlines()
    expected_columns = [
        "guid|int unsigned|NO|PRI|1",
        "total_claims|int unsigned|NO||2",
        "last_claim_day|date|YES||3",
    ]
    if columns != expected_columns:
        raise ValueError(f"Characters ledger columns differ from contract: {columns!r}")
    engine = _mysql(database, defaults_file,
        "SELECT ENGINE FROM information_schema.tables WHERE table_schema = DATABASE() "
        f"AND table_name = '{table}'").strip()
    if engine != "InnoDB":
        raise ValueError(f"Characters ledger must use InnoDB, got {engine!r}")
    primary = _mysql(database, defaults_file,
        "SELECT GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX SEPARATOR ',') "
        "FROM information_schema.statistics WHERE table_schema = DATABASE() "
        f"AND table_name = '{table}' AND index_name = 'PRIMARY'").strip()
    if primary != "guid":
        raise ValueError(f"Characters counter primary key differs from contract: {primary!r}")

    target = protocol["target"]
    claim_sql = characters_claim_upsert_sql({"addon_protocol": protocol}).replace(
        "{}", str(TEST_GUID))
    state_query = (f"SELECT total_claims, (last_claim_day = CURRENT_DATE()) "
                   f"FROM {table_sql} WHERE guid = {TEST_GUID}")
    try:
        _mysql(database, defaults_file, f"DELETE FROM {table_sql} WHERE guid = {TEST_GUID}")
        _mysql(database, defaults_file, claim_sql)
        _mysql(database, defaults_file, claim_sql)  # same-day duplicate is idempotent
        state = _mysql(database, defaults_file, state_query).strip().split("\t")
        if state != ["1", "1"]:
            raise ValueError(f"same-day idempotency failed: expected ['1','1'], got {state!r}")

        # Simulate a day boundary, then race two independent MySQL connections.
        _mysql(database, defaults_file,
            f"UPDATE {table_sql} SET last_claim_day = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY) "
            f"WHERE guid = {TEST_GUID}")
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: _mysql(database, defaults_file, claim_sql), range(2)))
        state = _mysql(database, defaults_file, state_query).strip().split("\t")
        if state != ["2", "1"]:
            raise ValueError(f"concurrent daily increment was not serialized: got {state!r}")

        # The declared target must also hold when competing claims reach it.
        _mysql(database, defaults_file,
            f"UPDATE {table_sql} SET total_claims = {target - 1}, "
            f"last_claim_day = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY) WHERE guid = {TEST_GUID}")
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: _mysql(database, defaults_file, claim_sql), range(2)))
        capped = _mysql(database, defaults_file, state_query).strip().split("\t")
        if capped != [str(target), "1"]:
            raise ValueError(f"concurrent claim crossed target {target}: got {capped!r}")
        _mysql(database, defaults_file,
            f"UPDATE {table_sql} SET last_claim_day = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY) "
            f"WHERE guid = {TEST_GUID}")
        _mysql(database, defaults_file, claim_sql)
        still_capped = _mysql(database, defaults_file, state_query).strip().split("\t")
        if still_capped[0] != str(target):
            raise ValueError(f"claim after target changed total: got {still_capped!r}")

        # A second state row for the same character must hit the unique GUID key.
        _mysql(database, defaults_file,
            f"INSERT INTO {table_sql} (guid, total_claims, last_claim_day) "
            f"VALUES ({TEST_GUID}, 1, CURRENT_DATE())", expect_success=False)
        _mysql(database, defaults_file,
            f"SELECT COALESCE(MAX(total_claims), 0), "
            f"COALESCE(MAX(last_claim_day = CURRENT_DATE()), 0) "
            f"FROM {table_sql} WHERE guid = {TEST_GUID}")
    finally:
        # Only this fixed fixture GUID in the explicitly disposable DB is touched.
        _mysql(database, defaults_file,
               f"DELETE FROM {table_sql} WHERE guid = {TEST_GUID}")
    fixture_rows = _mysql(database, defaults_file,
        f"SELECT COUNT(*) FROM {table_sql} WHERE guid = {TEST_GUID}").strip()
    if fixture_rows != "0":
        raise ValueError(f"test fixture cleanup failed: {fixture_rows} row(s) remain")

    proof_body = {
        "schema": "arena-characters-db-qualification/1",
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
        "atomic_claim_upsert_sha256": _sha256(claim_sql.encode("utf-8")),
        "module_source_sha256": integrity_check(package)["manifest"]["files"][
            f"server/mod-{spec['name']}/src/{''.join(part.capitalize() for part in spec['name'].split('-'))}.cpp"]["sha256"],
        "spec_sha256": _sha256((package.resolve() / "spec.json").read_bytes()),
        "schema": {"engine": engine, "columns": columns, "primary_key": primary},
        "checks": {
            "migration_applied_twice": "PASS",
            "schema_matches_declared_ledger": "PASS",
            "same_day_duplicate_ignored": "PASS",
            "concurrent_daily_increment_serialized": "PASS",
            "concurrent_target_cap_enforced": "PASS",
            "post_target_claim_refused": "PASS",
            "duplicate_character_row_rejected": "PASS",
            "module_state_query": "PASS",
            "fixture_rows_removed": "PASS"
        },
        "sequence": ["apply", "verify-schema", "apply-idempotently", "verify-schema",
                     "claim-same-day-twice", "simulate-day-boundary", "race-two-claim-connections",
                     "race-two-claims-at-target-minus-one", "attempt-claim-after-target",
                     "reject-second-character-row", "verify-module-state-query", "delete-test-fixture"],
        "limitations": [
            "Table-scoped synthetic MySQL schema/data check only; no AzerothCore characters database baseline or full schema was loaded.",
            "Does not run the generated module, worldserver, addon, client, Playerbots, or Campaign Party.",
            "The feature rollback intentionally retains user rows; this proof does not purge persisted state."
        ],
        "environment": {
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
            "workflow_job": os.environ.get("GITHUB_JOB"),
            "workflow_ref": os.environ.get("GITHUB_REF"),
            "source_commit": os.environ.get("GITHUB_SHA"),
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
