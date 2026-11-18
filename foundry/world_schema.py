"""Initialize and verify selected World tables from exact pinned AzerothCore DDL."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .db_qualification import (
    _DB_SCHEMA_TABLES,
    _INSERT_KEYS,
    _pinned_schema_source,
)
from .world_ownership import OwnershipError

_DATABASE = re.compile(r"^arena_foundry_disposable_[A-Za-z0-9_]{1,39}$")
_COLUMN_FIELDS = ("COLUMN_NAME", "COLUMN_TYPE", "IS_NULLABLE", "COLUMN_DEFAULT", "EXTRA")


def _extract_create_table(sql: str, table: str) -> str:
    """Extract one CREATE TABLE body from an unmodified pinned mysqldump file."""
    pattern = re.compile(
        rf"(?ms)^CREATE TABLE `{re.escape(table)}` \(\n.*?^\) ENGINE=[^;]+;"
    )
    matches = pattern.findall(sql)
    if len(matches) != 1:
        raise OwnershipError(
            f"pinned source must contain exactly one CREATE TABLE for {table}; found {len(matches)}")
    statement = matches[0].strip()
    if not statement.startswith(f"CREATE TABLE `{table}` (") or "DROP TABLE" in statement.upper():
        raise OwnershipError(f"unsafe schema statement extracted for {table}")
    return statement


def _single_value(cursor, sql: str, params: tuple = ()) -> Any:
    cursor.execute(sql, params)
    row = cursor.fetchone()
    if not row:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def _proof_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def initialize_pinned_empty_world_schema(connection, core_source: Path,
                                         database: str) -> dict:
    """Create only the pinned table DDL in an empty, explicitly disposable schema.

    AzerothCore base rows are intentionally not imported. On failure this does
    not drop partial DDL; discard the named disposable schema explicitly.
    """
    if not isinstance(database, str) or not _DATABASE.fullmatch(database) or len(database) > 64:
        raise OwnershipError("World schema setup requires a simple disposable database name")
    source = _pinned_schema_source(Path(core_source))
    cursor = connection.cursor()
    try:
        selected_database = _single_value(cursor, "SELECT DATABASE()")
        if selected_database != database:
            raise OwnershipError(
                f"connected schema {selected_database!r} does not match requested {database!r}")
        existing = _single_value(
            cursor,
            "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s",
            (database,))
        if existing != 0:
            raise OwnershipError(
                f"refusing to initialize non-empty disposable schema {database!r} ({existing} table(s))")

        created = {}
        for table in _DB_SCHEMA_TABLES:
            rel = f"data/sql/base/db_world/{table}.sql"
            source_path = Path(core_source) / rel
            sql = source_path.read_text(encoding="utf-8")
            ddl = _extract_create_table(sql, table)
            cursor.execute(ddl)
            created[table] = {
                "source_path": rel,
                "source_sha256": source["tables"][rel],
                "ddl_sha256": hashlib.sha256(ddl.encode("utf-8")).hexdigest(),
            }
        connection.commit()
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass
        raise
    finally:
        cursor.close()
    return observe_pinned_world_schema(connection, core_source, database,
                                       created_tables=created, source=source)


def observe_pinned_world_schema(connection, core_source: Path, database: str,
                                *, created_tables: dict | None = None,
                                source: dict | None = None) -> dict:
    """Record live engines, keys and columns for every supported source table."""
    if not isinstance(database, str) or not _DATABASE.fullmatch(database) or len(database) > 64:
        raise OwnershipError("World schema inspection requires a disposable database name")
    source = source or _pinned_schema_source(Path(core_source))
    created_tables = created_tables or {}
    cursor = connection.cursor()
    try:
        selected_database = _single_value(cursor, "SELECT DATABASE()")
        if selected_database != database:
            raise OwnershipError(
                f"connected schema {selected_database!r} does not match requested {database!r}")
        observed = {}
        expected_created = {}
        for table in _DB_SCHEMA_TABLES:
            rel = f"data/sql/base/db_world/{table}.sql"
            source_path = Path(core_source) / rel
            ddl = _extract_create_table(source_path.read_text(encoding="utf-8"), table)
            expected_created[table] = {
                "source_path": rel,
                "source_sha256": source["tables"][rel],
                "ddl_sha256": hashlib.sha256(ddl.encode("utf-8")).hexdigest(),
            }
            engine = _single_value(
                cursor,
                "SELECT ENGINE FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (database, table))
            cursor.execute(
                "SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND CONSTRAINT_NAME='PRIMARY' "
                "ORDER BY ORDINAL_POSITION", (database, table))
            key_rows = cursor.fetchall()
            primary_key = tuple(
                str(row[0] if not isinstance(row, dict) else next(iter(row.values()))).lower()
                for row in key_rows)
            expected_key = _INSERT_KEYS[table]
            if str(engine).lower() != "innodb":
                raise OwnershipError(f"pinned table {table} must be InnoDB; found {engine!r}")
            if primary_key != expected_key:
                raise OwnershipError(
                    f"table {table} primary key {primary_key!r} differs from pinned key {expected_key!r}")
            cursor.execute(
                "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA "
                "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s "
                "ORDER BY ORDINAL_POSITION", (database, table))
            columns = cursor.fetchall()
            observed[table] = {
                "engine": str(engine).lower(),
                "primary_key": list(primary_key),
                "columns": [
                    [row[name] for name in _COLUMN_FIELDS] if isinstance(row, dict) else list(row)
                    for row in columns
                ],
            }
        if created_tables and created_tables != expected_created:
            raise OwnershipError("created DDL hashes do not match the pinned schema source")
    finally:
        cursor.close()

    result = {
        "schema": "arena-world-schema-source/1",
        "database": database,
        "trust": "SYNTHETIC_DISPOSABLE_MYSQL_PINNED_SCHEMA",
        "source": source,
        "created_tables": expected_created,
        "runtime_tables": observed,
        "base_data_imported": False,
    }
    result["runtime_schema_sha256"] = _proof_hash(result)
    return result


def verify_pinned_world_schema(connection, core_source: Path, database: str,
                               proof: dict) -> None:
    """Fail closed unless live runtime metadata exactly matches a prior proof."""
    if (not isinstance(proof, dict) or proof.get("schema") != "arena-world-schema-source/1"
            or proof.get("database") != database
            or proof.get("trust") != "SYNTHETIC_DISPOSABLE_MYSQL_PINNED_SCHEMA"
            or proof.get("base_data_imported") is not False):
        raise OwnershipError("pinned World schema proof has the wrong scope")
    schema_fields = ("schema", "database", "trust", "source", "created_tables",
                     "runtime_tables", "base_data_imported")
    body = {key: proof.get(key) for key in schema_fields}
    given_hash = proof.get("runtime_schema_sha256")
    if not isinstance(given_hash, str) or _proof_hash(body) != given_hash:
        raise OwnershipError("pinned World runtime schema proof digest is invalid")
    observed = observe_pinned_world_schema(connection, core_source, database)
    if any(observed.get(key) != proof.get(key) for key in (*schema_fields, "runtime_schema_sha256")):
        raise OwnershipError("live World table engines, keys or columns differ from pinned schema proof")
