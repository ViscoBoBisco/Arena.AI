"""Transactional, manifest-bound ownership for generated World rows.

This v1 deliberately supports only the pinned schema's known generated tables
and InnoDB targets.  It does not emit executable raw rollback SQL: callers use
this service with one persistent DB connection so a MySQL advisory lock and a
single transaction cover the row writes and the durable ownership journal.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Iterable

from .db_qualification import (
    _INSERT_KEYS,
    _parse_insert,
    _split_values,
    semantic_insert_rows,
    validate_generated_sql,
)

_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_REQUEST = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")
_DATABASE = re.compile(r"^arena_foundry_disposable_[A-Za-z0-9_]{1,39}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LEDGER = "arena_foundry_world_rows"
_RECEIPTS = "arena_foundry_world_receipts"


class OwnershipError(RuntimeError):
    """A safe world-row operation could not prove exclusive ownership."""


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"$bytes_b64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (datetime, date, time)):
        return {"$iso8601": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return {"$type": type(value).__name__, "$repr": repr(value)}


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return _json_value(value)


def _canonical(value: Any) -> str:
    return json.dumps(_normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _expected_rows(statements: Iterable[str]) -> dict:
    statements = list(statements)
    if any(not line.upper().startswith("INSERT INTO") for line in statements):
        raise OwnershipError("owned World apply accepts generated INSERT statements only")
    rows = semantic_insert_rows(statements)
    if len(rows) != len(statements):
        raise OwnershipError("generated migration contains duplicate or unsupported row identities")
    return rows


def _statement_by_identity(statements: Iterable[str]) -> dict:
    result = {}
    for line in statements:
        identity = _parse_insert(line)
        if identity in result:
            raise OwnershipError(f"duplicate INSERT identity {identity!r}")
        result[identity] = line
    return result


def _identity_json(identity) -> str:
    table, keys = identity
    return _canonical([table, [[column, value] for column, value in keys]])


def _quote_identity(identity) -> tuple[str, str]:
    table, keys = identity
    if table not in _INSERT_KEYS or not keys:
        raise OwnershipError("row identity is not in the pinned supported-table allowlist")
    if any(column not in _INSERT_KEYS[table] for column, _ in keys):
        raise OwnershipError("row identity does not match the pinned primary key")
    where = " AND ".join(f"`{column}` = %s" for column, _ in keys)
    params = tuple(value for _, value in keys)
    return where, params


def _row_snapshot(cursor, identity) -> dict | None:
    table, _keys = identity
    where, params = _quote_identity(identity)
    cursor.execute(f"SELECT * FROM `{table}` WHERE {where} FOR UPDATE", params)
    row = cursor.fetchone()
    if row is None:
        return None
    columns = [entry[0].lower() for entry in cursor.description]
    if isinstance(row, dict):
        values = {str(key).lower(): value for key, value in row.items()}
    else:
        values = dict(zip(columns, row))
    return {key: _json_value(value) for key, value in sorted(values.items())}


def _assert_engine(cursor, database: str, tables: set[str]) -> None:
    for table in sorted(tables | {_LEDGER, _RECEIPTS}):
        cursor.execute(
            "SELECT ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s", (database, table))
        row = cursor.fetchone()
        engine = row[0] if row and not isinstance(row, dict) else (
            next(iter(row.values())) if row else None)
        if not row or str(engine).lower() != "innodb":
            raise OwnershipError(f"table {table} must exist and use InnoDB; found {engine!r}")
        cursor.execute(
            "SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND CONSTRAINT_NAME='PRIMARY' "
            "ORDER BY ORDINAL_POSITION", (database, table))
        key_rows = cursor.fetchall()
        actual_key = tuple(str(item[0] if not isinstance(item, dict)
                               else next(iter(item.values()))).lower() for item in key_rows)
        expected_key = _INSERT_KEYS.get(table)
        if expected_key is not None and actual_key != expected_key:
            raise OwnershipError(
                f"table {table} primary key {actual_key!r} does not match pinned key {expected_key!r}")


def _bootstrap(cursor) -> None:
    # DDL is intentionally outside the data transaction: MySQL implicitly
    # commits DDL.  These two CREATE IF NOT EXISTS statements are the only DDL
    # this subsystem performs; they never alter AzerothCore tables.
    cursor.execute(f"""CREATE TABLE IF NOT EXISTS `{_LEDGER}` (
        `table_name` VARCHAR(64) NOT NULL,
        `row_key` VARCHAR(512) NOT NULL,
        `package_name` VARCHAR(63) NOT NULL,
        `package_version` VARCHAR(64) NOT NULL,
        `manifest_sha256` CHAR(64) NOT NULL,
        `expected_json` LONGTEXT NOT NULL,
        `expected_sha256` CHAR(64) NOT NULL,
        `snapshot_json` LONGTEXT NOT NULL,
        `snapshot_sha256` CHAR(64) NOT NULL,
        `state` VARCHAR(16) NOT NULL,
        `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (`table_name`, `row_key`),
        KEY `idx_package_manifest` (`package_name`, `manifest_sha256`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    cursor.execute(f"""CREATE TABLE IF NOT EXISTS `{_RECEIPTS}` (
        `request_id` VARCHAR(96) NOT NULL,
        `fingerprint_sha256` CHAR(64) NOT NULL,
        `package_name` VARCHAR(63) NOT NULL,
        `package_version` VARCHAR(64) NOT NULL,
        `manifest_sha256` CHAR(64) NOT NULL,
        `action` VARCHAR(16) NOT NULL,
        `result_json` LONGTEXT NOT NULL,
        `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (`request_id`),
        KEY `idx_package_manifest` (`package_name`, `manifest_sha256`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")


def _acquire_lock(cursor, database: str) -> str:
    lock_name = "arena-world:" + hashlib.sha256(database.encode()).hexdigest()[:48]
    cursor.execute("SELECT GET_LOCK(%s, 15)", (lock_name,))
    row = cursor.fetchone()
    value = row[0] if row and not isinstance(row, dict) else (
        next(iter(row.values())) if row else None)
    if value != 1:
        raise OwnershipError("could not acquire the per-database Arena World migration lock")
    return lock_name


def _release_lock(cursor, lock_name: str) -> None:
    cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
    cursor.fetchone()


def _receipt_replay(cursor, request_id: str, fingerprint: str) -> dict | None:
    cursor.execute(
        f"SELECT fingerprint_sha256, result_json FROM `{_RECEIPTS}` WHERE request_id = %s",
        (request_id,))
    row = cursor.fetchone()
    if not row:
        return None
    if isinstance(row, dict):
        prior_fingerprint = row["fingerprint_sha256"]
        result = row["result_json"]
    else:
        prior_fingerprint, result = row
    if prior_fingerprint != fingerprint:
        raise OwnershipError("request_id was already used for a different operation")
    decoded = json.loads(result)
    decoded["replayed"] = True
    return decoded


def _record_receipt(cursor, *, request_id: str, fingerprint: str,
                    package_name: str, package_version: str,
                    manifest_sha256: str, action: str, result: dict) -> None:
    cursor.execute(
        f"INSERT INTO `{_RECEIPTS}` "
        "(request_id, fingerprint_sha256, package_name, package_version, "
        "manifest_sha256, action, result_json) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (request_id, fingerprint, package_name, package_version, manifest_sha256,
         action, _canonical(result)))


def _journal_row(cursor, identity):
    table, _ = identity
    cursor.execute(
        f"SELECT package_name, package_version, manifest_sha256, expected_json, "
        f"expected_sha256, snapshot_json, snapshot_sha256, state "
        f"FROM `{_LEDGER}` WHERE table_name = %s AND row_key = %s FOR UPDATE",
        (table, _identity_json(identity)))
    row = cursor.fetchone()
    if not row:
        return None
    if isinstance(row, dict):
        return row
    return dict(zip(("package_name", "package_version", "manifest_sha256",
                     "expected_json", "expected_sha256", "snapshot_json",
                     "snapshot_sha256", "state"), row))


def _assert_row_snapshot(cursor, identity, expected_snapshot, expected_digest) -> bool:
    current = _row_snapshot(cursor, identity)
    if current is None:
        return False
    if _digest(current) != expected_digest or _canonical(current) != expected_snapshot:
        raise OwnershipError(
            f"owned row {identity[0]} {_identity_json(identity)} changed since apply; refusing deletion")
    return True


def _insert_identity_values(cursor, statement: str, identity, expected: dict) -> None:
    cursor.execute(statement)
    actual = _row_snapshot(cursor, identity)
    if actual is None:
        raise OwnershipError(f"insert did not create expected row in {identity[0]}")
    for column, value in expected.items():
        if column not in actual:
            raise OwnershipError(f"inserted row is missing expected column {column}")
        actual_value = actual[column]
        if isinstance(actual_value, dict) and "$decimal" in actual_value:
            actual_value = actual_value["$decimal"]
        if value is None:
            match = actual_value is None
        elif isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            try:
                match = Decimal(str(actual_value)) == Decimal(str(value))
            except Exception:
                match = False
        else:
            match = actual_value == _json_value(value)
        if not match:
            raise OwnershipError(
                f"database row {identity[0]}.{column} differs from generated migration value")


def _check_inputs(package_name: str, package_version: str,
                  manifest_sha256: str, request_id: str) -> None:
    if not isinstance(package_name, str) or not _NAME.fullmatch(package_name):
        raise OwnershipError("package name must be a lower-case namespaced slug")
    if not isinstance(package_version, str) or not package_version or len(package_version) > 64:
        raise OwnershipError("package version must be a non-empty string of at most 64 characters")
    if not isinstance(manifest_sha256, str) or not _SHA256.fullmatch(manifest_sha256):
        raise OwnershipError("manifest SHA-256 must be 64 lowercase hexadecimal characters")
    if not isinstance(request_id, str) or not _REQUEST.fullmatch(request_id):
        raise OwnershipError("request_id must be 1–96 safe ASCII characters")


def _owned_world_operation(connection, *, migration: str, package_name: str,
                           package_version: str, manifest_sha256: str,
                           request_id: str, action: str) -> dict:
    _check_inputs(package_name, package_version, manifest_sha256, request_id)
    if action not in ("apply", "rollback"):
        raise OwnershipError("unknown owned World operation")
    statements = validate_generated_sql(migration, rollback=False)
    expected_rows = _expected_rows(statements)
    statement_by_identity = _statement_by_identity(statements)
    if set(expected_rows) != set(statement_by_identity):
        raise OwnershipError("migration row identities are inconsistent")
    fingerprint_payload = {
        "action": action, "manifest_sha256": manifest_sha256,
        "package_name": package_name, "package_version": package_version,
        "request_id": request_id,
    }
    fingerprint = _digest(fingerprint_payload)
    database = None
    lock_name = None
    get_autocommit = getattr(connection, "get_autocommit", None)
    if callable(get_autocommit) and not get_autocommit():
        raise OwnershipError("connection autocommit must be enabled; the service starts its own transaction")
    server_status = getattr(connection, "server_status", None)
    if not isinstance(server_status, int):
        raise OwnershipError("MySQL connector must expose protocol server_status for transaction safety")
    # MySQL protocol SERVER_STATUS_IN_TRANS is bit 0 (value 1). Checking the
    # connection state avoids a probe query that itself could begin a transaction.
    if server_status & 1:
        raise OwnershipError("connection must be idle; refusing to commit caller-owned transaction state")
    cursor = connection.cursor()
    transaction_started = False
    try:
        cursor.execute("SELECT DATABASE()")
        selected = cursor.fetchone()
        database = selected[0] if selected and not isinstance(selected, dict) else (
            next(iter(selected.values())) if selected else None)
        if not isinstance(database, str) or not _DATABASE.fullmatch(database) or len(database) > 64:
            raise OwnershipError("owned World DB operations require a simple arena_foundry_disposable_* schema")
        _bootstrap(cursor)
        tables = {identity[0] for identity in expected_rows}
        _assert_engine(cursor, database, tables)
        lock_name = _acquire_lock(cursor, database)
        connection.begin()
        transaction_started = True
        replay = _receipt_replay(cursor, request_id, fingerprint)
        if replay is not None:
            connection.rollback()
            transaction_started = False
            return replay
        changed = 0
        # Preserve generated dependency order on apply and reverse it on rollback.
        identities = list(statement_by_identity)
        if action == "rollback":
            identities.reverse()
        for identity in identities:
            table, keys = identity
            expected = expected_rows[identity]
            expected_json = _canonical(expected)
            expected_sha = _digest(expected)
            record = _journal_row(cursor, identity)
            where, params = _quote_identity(identity)
            if action == "apply":
                if record:
                    if (record["package_name"] != package_name
                            or record["package_version"] != package_version
                            or record["manifest_sha256"] != manifest_sha256
                            or record["expected_sha256"] != expected_sha
                            or record["expected_json"] != expected_json):
                        raise OwnershipError(
                            f"row {table} is already owned by another package version/manifest")
                    if record["state"] == "APPLIED":
                        if not _assert_row_snapshot(cursor, identity,
                                                    record["snapshot_json"],
                                                    record["snapshot_sha256"]):
                            raise OwnershipError(f"journaled row {table} is missing")
                        continue
                    if record["state"] != "ROLLED_BACK":
                        raise OwnershipError(f"row {table} has unsupported journal state {record['state']!r}")
                    if _row_snapshot(cursor, identity) is not None:
                        raise OwnershipError(f"rolled-back row {table} was recreated outside Arena")
                    _insert_identity_values(cursor, statement_by_identity[identity], identity, expected)
                    snapshot = _row_snapshot(cursor, identity)
                    snapshot_json = _canonical(snapshot)
                    cursor.execute(
                        f"UPDATE `{_LEDGER}` SET snapshot_json=%s, snapshot_sha256=%s, state='APPLIED' "
                        "WHERE table_name=%s AND row_key=%s",
                        (snapshot_json, _digest(snapshot), table, _identity_json(identity)))
                    changed += 1
                    continue
                if _row_snapshot(cursor, identity) is not None:
                    raise OwnershipError(
                        f"pre-existing unowned row collision in {table}; no row was overwritten")
                _insert_identity_values(cursor, statement_by_identity[identity], identity, expected)
                snapshot = _row_snapshot(cursor, identity)
                snapshot_json = _canonical(snapshot)
                cursor.execute(
                    f"INSERT INTO `{_LEDGER}` "
                    "(table_name,row_key,package_name,package_version,manifest_sha256,"
                    "expected_json,expected_sha256,snapshot_json,snapshot_sha256,state) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'APPLIED')",
                    (table, _identity_json(identity), package_name, package_version,
                     manifest_sha256, expected_json, expected_sha, snapshot_json,
                     _digest(snapshot)))
                changed += 1
            else:
                if not record:
                    raise OwnershipError(f"rollback refused: no ownership journal for {table}")
                if (record["package_name"] != package_name
                        or record["package_version"] != package_version
                        or record["manifest_sha256"] != manifest_sha256
                        or record["expected_sha256"] != expected_sha
                        or record["expected_json"] != expected_json):
                    raise OwnershipError(f"rollback refused: wrong package owner for {table}")
                if record["state"] == "ROLLED_BACK":
                    if _row_snapshot(cursor, identity) is not None:
                        raise OwnershipError(f"rolled-back row {table} was recreated outside Arena")
                    continue
                if record["state"] != "APPLIED":
                    raise OwnershipError(f"rollback refused: invalid journal state {record['state']!r}")
                if not _assert_row_snapshot(cursor, identity,
                                            record["snapshot_json"],
                                            record["snapshot_sha256"]):
                    raise OwnershipError(f"rollback refused: owned row {table} is missing")
                cursor.execute(f"DELETE FROM `{table}` WHERE {where}", params)
                if cursor.rowcount != 1:
                    raise OwnershipError(f"rollback did not delete exactly one owned row from {table}")
                cursor.execute(
                    f"UPDATE `{_LEDGER}` SET state='ROLLED_BACK' "
                    "WHERE table_name=%s AND row_key=%s",
                    (table, _identity_json(identity)))
                changed += 1
        result = {
            "schema": "arena-world-row-operation/1", "action": action,
            "package": package_name, "version": package_version,
            "manifest_sha256": manifest_sha256, "request_id": request_id,
            "rows": len(expected_rows), "changed_rows": changed, "replayed": False,
        }
        _record_receipt(cursor, request_id=request_id, fingerprint=fingerprint,
                        package_name=package_name, package_version=package_version,
                        manifest_sha256=manifest_sha256, action=action, result=result)
        connection.commit()
        transaction_started = False
        return result
    except Exception:
        if transaction_started:
            try:
                connection.rollback()
            except Exception:
                pass
        raise
    finally:
        if lock_name is not None:
            try:
                _release_lock(cursor, lock_name)
            except Exception:
                pass
        cursor.close()



def operate_on_package(connection, package, *, action: str,
                       request_id: str) -> dict:
    """Apply/rollback only an integrity-checked package's canonical World SQL."""
    from pathlib import Path

    from .generate import world_sql
    from .qualification import integrity_check

    package = Path(package)
    integrity = integrity_check(package)
    if not integrity["ok"]:
        raise OwnershipError("package integrity failed: " + "; ".join(integrity["problems"]))
    manifest = integrity["manifest"]
    requirements = manifest.get("delivery_requirements", {})
    artifacts = requirements.get("artifacts", []) if isinstance(requirements, dict) else []
    if not any(isinstance(item, dict) and item.get("kind") == "world_sql"
               for item in artifacts):
        raise OwnershipError("package does not declare World SQL delivery")
    spec = json.loads((package / "spec.json").read_text(encoding="utf-8"))
    baseline = json.loads((package / "id-baseline.json").read_text(encoding="utf-8"))
    reserved = baseline.get("reserved_ids", {})
    if not isinstance(reserved, dict):
        raise OwnershipError("package ID baseline is malformed")
    statements = validate_generated_sql(
        (package / "sql" / "01_world.sql").read_text(encoding="utf-8"), rollback=False)
    canonical_statements = validate_generated_sql(
        world_sql(spec, reserved=reserved), rollback=False)
    if any(not line.upper().startswith("INSERT INTO") for line in statements):
        raise OwnershipError("package World SQL contains unsupported non-INSERT statements")
    if semantic_insert_rows(statements) != semantic_insert_rows(canonical_statements):
        raise OwnershipError("package World SQL differs from current spec-derived canonical output")
    args = {
        "migration": "\n".join(statements) + "\n",
        "package_name": spec.get("name"),
        "package_version": spec.get("version"),
        "manifest_sha256": integrity["manifest_sha256"],
        "request_id": request_id,
    }
    if action not in ("apply", "rollback"):
        raise OwnershipError("action must be apply or rollback")
    return _owned_world_operation(connection, action=action, **args)
