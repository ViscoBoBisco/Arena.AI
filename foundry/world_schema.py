"""Initialize empty disposable schemas from exact pinned AzerothCore World DDL."""

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


def initialize_pinned_empty_world_schema(connection, core_source: Path,
                                         database: str) -> dict:
    """Create only the pinned table DDL in an empty, explicitly disposable schema.

    The source files are fully hashed and revision-checked by
    ``_pinned_schema_source``. Data rows from AzerothCore's large base dumps are
    intentionally not imported: this proves exact schema compatibility and
    generated write/rollback behavior against an empty disposable database,
    not migration behavior against a populated World DB.
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

        observed = {}
        for table in _DB_SCHEMA_TABLES:
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
                    f"created table {table} primary key {primary_key!r} "
                    f"differs from generator identity {expected_key!r}")
            cursor.execute(
                "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA "
                "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s "
                "ORDER BY ORDINAL_POSITION", (database, table))
            columns = cursor.fetchall()
            observed[table] = {
                "engine": str(engine).lower(),
                "primary_key": list(primary_key),
                "columns": [
                    [row[name] for name in ("COLUMN_NAME", "COLUMN_TYPE", "IS_NULLABLE",
                                            "COLUMN_DEFAULT", "EXTRA")]
                    if isinstance(row, dict) else list(row)
                    for row in columns
                ],
            }
        connection.commit()
    except Exception:
        # CREATE TABLE is DDL and MySQL commits it implicitly. Never DROP or
        # overwrite partial results; the caller must discard this disposable DB.
        try:
            connection.rollback()
        except Exception:
            pass
        raise
    finally:
        cursor.close()

    result = {
        "schema": "arena-world-schema-source/1",
        "database": database,
        "trust": "SYNTHETIC_DISPOSABLE_MYSQL_PINNED_SCHEMA",
        "source": source,
        "created_tables": created,
        "runtime_tables": observed,
        "base_data_imported": False,
    }
    payload = json.dumps(result, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    result["runtime_schema_sha256"] = hashlib.sha256(payload).hexdigest()
    return result
