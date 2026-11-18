"""Disposable-schema migration qualification for Foundry-generated SQL.

This module executes only the package's generated world migration/rollback in
an explicitly named disposable database. It refuses key collisions before the
first write, checks all generated identities, and binds the resulting evidence
to the package manifest hash.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from decimal import Decimal, InvalidOperation
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .qualification import integrity_check
from .generate import world_sql

RowIdentity = Tuple[str, Tuple[Tuple[str, int], ...]]
QueryRunner = Callable[[str], str]

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _json_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _item_ref(value: Any):
    return value.get("value") if isinstance(value, dict) else value


def _identity(table: str, **where: int) -> RowIdentity:
    if not _IDENT.fullmatch(table) or not where:
        raise ValueError("invalid generated row identity")
    if any(not _IDENT.fullmatch(column) or not isinstance(value, int)
           or isinstance(value, bool) or value < 0 for column, value in where.items()):
        raise ValueError("generated row identity must use non-negative integer keys")
    normalized = {column.lower(): value for column, value in where.items()}
    if len(normalized) != len(where):
        raise ValueError("duplicate row identity key after case normalization")
    return table.lower(), tuple(sorted(normalized.items()))


def expected_world_rows(spec: dict, allocations: dict) -> Set[RowIdentity]:
    """Describe every world row this Foundry generator promises to insert."""
    ids = allocations.get("allocations")
    if not isinstance(ids, dict):
        raise ValueError("ID allocation file has no allocations object")

    def assigned(key: Any) -> int:
        value = ids.get(key) if isinstance(key, str) else None
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"missing or invalid allocated ID for {key!r}")
        return value

    rows: Set[RowIdentity] = set()
    for item in spec.get("items") or []:
        rows.add(_identity("item_template", entry=assigned(item["key"])))
    for spell in spec.get("spells") or []:
        rows.add(_identity("spell_dbc", Id=assigned(spell["key"])))
    for quest in spec.get("quests") or []:
        rows.add(_identity("quest_template", Id=assigned(quest["key"])))
    for creature in spec.get("creatures") or []:
        creature_id = assigned(creature["key"])
        rows.add(_identity("creature_template", entry=creature_id))
        if _item_ref(creature.get("display_id")) is not None:
            rows.add(_identity("creature_template_model", CreatureID=creature_id, Idx=0))
    for vendor in spec.get("vendors") or []:
        creature_id = assigned(vendor["creature_key"])
        for stock in vendor.get("rows") or []:
            rows.add(_identity("npc_vendor", entry=creature_id,
                               item=assigned(stock["item_key"]), ExtendedCost=0))
    for event in spec.get("events") or []:
        rows.add(_identity("game_event", eventEntry=assigned(event["key"])))
    return rows


def _without_inline_comment(line: str) -> str:
    quote = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in ("'", "`"):
            quote = char
        elif (line[index:index + 2] == "--"
              and (index == 0 or line[index - 1].isspace())):
            return line[:index].rstrip()
    return line


def validate_generated_sql(text: str, *, rollback: bool) -> List[str]:
    """Accept only the generator's one-statement-per-line DML subset."""
    allowed = ("DELETE FROM",) if rollback else ("DELETE FROM", "INSERT INTO")
    statements = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = _without_inline_comment(raw).strip()
        if not line:
            continue
        if not line.startswith(allowed) or not line.endswith(";"):
            raise ValueError(f"generated SQL line {number} is outside the allowed DML subset")
        quote = None
        escaped = False
        for index, char in enumerate(line):
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if quote:
                if char == quote:
                    quote = None
                continue
            if char in ("'", "`"):
                quote = char
            elif char == ";" and index != len(line) - 1:
                raise ValueError(f"generated SQL line {number} contains multiple statements")
        if quote:
            raise ValueError(f"generated SQL line {number} has an unterminated quote")
        statements.append(line)
    if not statements:
        raise ValueError("generated SQL contains no executable statements")
    return statements


# Primary keys verified from the pinned AzerothCore WotLK base world schema.
# Keep these identities aligned with the actual table keys: CreatureID alone
# and (entry, item) alone are not unique for these composite-key tables.
_INSERT_KEYS = {
    "item_template": ("entry",),
    "spell_dbc": ("id",),
    "quest_template": ("id",),
    "creature_template": ("entry",),
    "creature_template_model": ("creatureid", "idx"),
    "npc_vendor": ("entry", "item", "extendedcost"),
    "game_event": ("evententry",),
}
_DELETE_RE = re.compile(r"^DELETE FROM `?(\w+)`? WHERE (.+);$", re.IGNORECASE)
_INSERT_RE = re.compile(
    r"^INSERT INTO `?(\w+)`?\s*\(([^)]*)\)\s*VALUES\s*\((.*)\);$",
    re.IGNORECASE,
)


def _parse_predicate(table: str, text: str) -> RowIdentity:
    clauses = re.split(r"\s+AND\s+", text, flags=re.IGNORECASE)
    pairs = []
    for clause in clauses:
        match = re.fullmatch(r"`?([A-Za-z_]\w*)`?\s*=\s*(\d+)", clause.strip())
        if not match:
            raise ValueError(f"unsupported generated SQL predicate for {table}: {text}")
        pairs.append(match.groups())
    where = {column: int(value) for column, value in pairs}
    if len(where) != len(pairs):
        raise ValueError(f"duplicate key column in generated SQL predicate for {table}")
    return _identity(table, **where)


def _split_values(text: str) -> List[str]:
    values = []
    start = 0
    quote = None
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char == "'":
            quote = char
        elif char == ",":
            values.append(text[start:index].strip())
            start = index + 1
    if quote:
        raise ValueError("unterminated string in generated INSERT")
    values.append(text[start:].strip())
    return values


def _parse_insert(line: str) -> RowIdentity:
    match = _INSERT_RE.fullmatch(line)
    if not match:
        raise ValueError("unsupported generated INSERT statement")
    table, column_text, value_text = match.groups()
    columns = [column.strip().strip("`").lower() for column in column_text.split(",")]
    values = _split_values(value_text)
    if len(columns) != len(values) or len(columns) != len(set(columns)):
        raise ValueError(f"generated INSERT column/value mismatch for {table}")
    key_columns = _INSERT_KEYS.get(table.lower())
    if not key_columns or not set(key_columns) <= set(columns):
        raise ValueError(f"unsupported generated INSERT target: {table}")
    keys = {}
    for column in key_columns:
        value = values[columns.index(column)]
        if not value.isdigit():
            raise ValueError(f"generated INSERT has a non-integer key for {table}.{column}")
        keys[column] = int(value)
    return _identity(table, **keys)


def _parse_sql_value(value: str):
    """Parse the generator's scalar SQL literal subset into a Python value."""
    value = value.strip()
    if value.upper() == "NULL":
        return None
    if value.startswith("'") and value.endswith("'") and len(value) >= 2:
        raw = value[1:-1]
        # MySQL string escapes emitted by generate._sql_str.
        return re.sub(r"\\([\\'0bnrtZ])", lambda match: {
            "\\": "\\", "'": "'", "0": "\0", "b": "\b",
            "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a",
        }[match.group(1)], raw)
    if re.fullmatch(r"[+-]?\d+", value):
        return int(value)
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?", value):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"invalid numeric SQL literal: {value!r}") from exc
    raise ValueError(f"unsupported generated SQL value: {value!r}")


def semantic_insert_rows(statements: List[str]) -> dict[RowIdentity, dict[str, Any]]:
    """Return normalized column values for every generated INSERT row."""
    result = {}
    for line in statements:
        if not line.upper().startswith("INSERT INTO"):
            continue
        match = _INSERT_RE.fullmatch(line)
        if not match:
            raise ValueError("unsupported generated INSERT statement")
        table, column_text, value_text = match.groups()
        columns = [column.strip().strip("`").lower() for column in column_text.split(",")]
        values = [_parse_sql_value(value) for value in _split_values(value_text)]
        if len(columns) != len(values) or len(columns) != len(set(columns)):
            raise ValueError(f"generated INSERT column/value mismatch for {table}")
        identity = _parse_insert(line)
        if identity in result:
            raise ValueError(f"duplicate generated INSERT identity: {identity}")
        result[identity] = dict(zip(columns, values))
    return result


def _semantic_digest(rows: dict[RowIdentity, dict[str, Any]]) -> str:
    normalized = {
        f"{table}|" + ",".join(f"{column}={value}" for column, value in where):
            values
        for (table, where), values in sorted(rows.items())
    }
    data = json.dumps(normalized, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _value_query(identity: RowIdentity, columns: List[str]) -> str:
    table, where = identity
    if not columns or any(not _IDENT.fullmatch(column) for column in columns):
        raise ValueError("invalid semantic query columns")
    projection = ", ".join(f"`{column}`" for column in columns)
    predicate = " AND ".join(f"`{column}` = {value}" for column, value in where)
    return f"SELECT {projection} FROM `{table}` WHERE {predicate};"


def _decode_mysql_batch_value(value: str, expected: Any):
    if expected is None:
        return None if value in (r"\N", "NULL") else value
    if isinstance(expected, (int, Decimal)) and not isinstance(expected, bool):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise RuntimeError(f"database returned non-numeric semantic value {value!r}") from exc
    # mysql --batch escapes control characters unless --raw is enabled.
    return re.sub(r"\\([0bnrtZ])", lambda match: {
        "0": "\0", "b": "\b", "n": "\n", "r": "\r",
        "t": "\t", "Z": "\x1a",
    }[match.group(1)], value).replace(r"\\", "\\")


def _assert_semantic_rows(runner: QueryRunner,
                          expected_rows: dict[RowIdentity, dict[str, Any]]) -> None:
    problems = []
    for identity, expected in sorted(expected_rows.items()):
        columns = list(expected)
        raw = runner(_value_query(identity, columns)).rstrip("\r\n")
        values = raw.split("\t") if raw else []
        if len(values) != len(columns):
            problems.append(f"{identity[0]} returned {len(values)} values, expected {len(columns)}")
            continue
        for column, actual_raw in zip(columns, values):
            actual = _decode_mysql_batch_value(actual_raw, expected[column])
            want = expected[column]
            if isinstance(want, (int, Decimal)) and not isinstance(want, bool):
                want = Decimal(want)
            if actual != want:
                problems.append(f"{identity[0]}.{column} expected {want!r}, found {actual!r}")
    if problems:
        raise RuntimeError("database semantic row mismatch: " + "; ".join(problems))


def inspect_generated_sql(migration: List[str], rollback: List[str],
                          expected: Set[RowIdentity], spec: dict,
                          allocations: dict) -> Tuple[Set[RowIdentity], Set[RowIdentity]]:
    """Check SQL touches only expected package rows and rollback mirrors its deletes."""
    migration_deletes = set()
    rollback_deletes = set()
    inserts = set()
    for statements, output in ((migration, migration_deletes), (rollback, rollback_deletes)):
        for line in statements:
            match = _DELETE_RE.fullmatch(line)
            if not match:
                if output is rollback_deletes:
                    raise ValueError("rollback contains a non-DELETE statement")
                inserts.add(_parse_insert(line))
                continue
            table, predicate = match.groups()
            output.add(_parse_predicate(table, predicate))
    known_creature_models = {
        _identity("creature_template_model", CreatureID=allocations["allocations"][creature["key"]], Idx=0)
        for creature in spec.get("creatures") or []
        if isinstance(allocations.get("allocations", {}).get(creature.get("key")), int)
        and not isinstance(allocations["allocations"][creature["key"]], bool)
    }
    if inserts != expected:
        raise ValueError("migration INSERT identities do not exactly match spec/ID allocation")
    if not inserts <= migration_deletes:
        raise ValueError("migration INSERT is missing a preceding matching cleanup predicate")
    extra_deletes = migration_deletes - inserts
    if not extra_deletes <= known_creature_models:
        raise ValueError("migration deletes rows outside its declared generated identities")
    if migration_deletes != rollback_deletes:
        raise ValueError("rollback DELETE identities do not mirror migration cleanup identities")
    return migration_deletes, inserts


def _count_query(identity: RowIdentity) -> str:
    table, where = identity
    clauses = " AND ".join(f"`{column}` = {value}" for column, value in where)
    return f"SELECT COUNT(*) FROM `{table}` WHERE {clauses};"


def _count(runner: QueryRunner, identity: RowIdentity) -> int:
    output = runner(_count_query(identity)).strip()
    try:
        value = int(output)
    except ValueError as exc:
        raise RuntimeError(f"database returned a non-count for {identity[0]}: {output!r}") from exc
    if value < 0:
        raise RuntimeError("database returned a negative row count")
    return value


def _assert_rows(runner: QueryRunner, rows: Set[RowIdentity], expected: int) -> None:
    mismatches = []
    for identity in sorted(rows):
        actual = _count(runner, identity)
        if actual != expected:
            table, where = identity
            label = ", ".join(f"{column}={value}" for column, value in where)
            mismatches.append(f"{table}({label}) expected {expected}, found {actual}")
    if mismatches:
        raise RuntimeError("; ".join(mismatches))


_DB_SCHEMA_TABLES = (
    "creature_template", "creature_template_model", "game_event", "item_template",
    "npc_vendor", "quest_template", "spell_dbc",
)


def _pinned_schema_source(core_source: Path) -> dict:
    """Record hashes of the exact pinned AzerothCore base SQL used to initialize tables."""
    pins_path = Path(__file__).resolve().parent / "core-pins.json"
    pins = _json_object(pins_path)
    pin = pins.get("azerothcore_wotlk")
    if not isinstance(pin, dict):
        raise ValueError("AzerothCore source pin is missing")
    repo = core_source.resolve()
    try:
        revision = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
            capture_output=True, text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"cannot identify pinned AzerothCore checkout: {exc}") from exc
    if revision != pin.get("revision"):
        raise ValueError(f"AzerothCore checkout {revision!r} does not match pinned revision")
    table_hashes = {}
    source_paths = [f"data/sql/base/db_world/{table}.sql" for table in _DB_SCHEMA_TABLES]
    try:
        dirty = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "--", *source_paths],
            check=True, capture_output=True, text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"cannot verify pinned AzerothCore schema sources: {exc}") from exc
    if dirty:
        raise ValueError("AzerothCore schema source files have local modifications")
    for rel in source_paths:
        path = repo / rel
        try:
            path.resolve().relative_to(repo)
            if path.is_symlink() or not path.is_file():
                raise OSError("not a regular file")
            content = path.read_bytes()
        except (OSError, ValueError) as exc:
            raise ValueError(f"pinned world-schema source is unavailable: {rel}: {exc}") from exc
        if not content:
            raise ValueError(f"pinned world-schema source is empty: {rel}")
        table_hashes[rel] = hashlib.sha256(content).hexdigest()
    expected_hashes = pin.get("world_schema_sha256")
    if not isinstance(expected_hashes, dict) or table_hashes != expected_hashes:
        raise ValueError("pinned World schema source hashes differ from the checked-in revision contract")
    return {
        "schema": "arena-db-schema-source/1",
        "repository": pin.get("repository"),
        "revision": revision,
        "tables": table_hashes,
    }


def qualify_database(package: Path, database: str, runner: QueryRunner,
                     *, core_source: Optional[Path] = None) -> dict:
    """Run apply/idempotence/rollback/reapply and return manifest-bound attestations."""
    schema_source = _pinned_schema_source(core_source) if core_source is not None else None
    if not isinstance(database, str) or not database.startswith("arena_foundry_disposable_"):
        raise ValueError("database must use the arena_foundry_disposable_ prefix")
    if not _IDENT.fullmatch(database) or len(database) > 64:
        raise ValueError("database name must be a simple MySQL SQL identifier (max 64 characters)")

    integrity = integrity_check(package)
    if not integrity["ok"]:
        raise ValueError("package integrity failed: " + "; ".join(integrity["problems"]))
    manifest = integrity["manifest"]
    requirements = manifest.get("delivery_requirements", {})
    artifacts = requirements.get("artifacts", []) if isinstance(requirements, dict) else []
    if not any(isinstance(item, dict) and item.get("kind") == "world_sql"
               for item in artifacts):
        raise ValueError("package does not declare world SQL delivery")

    spec = _json_object(package / "spec.json")
    allocation = _json_object(package / "id-allocation.json")
    migration_path = package / "sql" / "01_world.sql"
    rollback_path = package / "sql" / "99_rollback.sql"
    migration = migration_path.read_text(encoding="utf-8")
    rollback = rollback_path.read_text(encoding="utf-8")
    if "SIGNAL SQLSTATE '45000'" in rollback and "REFUSED:" in rollback:
        raise ValueError(
            "ARENA-DB apply/rollback cycle refused: package has no durable world-row ownership ledger")
    migration_statements = validate_generated_sql(migration, rollback=False)
    rollback_statements = validate_generated_sql(rollback, rollback=True)
    baseline = _json_object(package / "id-baseline.json")
    reserved = baseline.get("reserved_ids", {})
    if not isinstance(reserved, dict):
        raise ValueError("ID baseline has no reserved_ids object")
    canonical_statements = validate_generated_sql(
        world_sql(spec, reserved=reserved), rollback=False)
    semantic_rows = semantic_insert_rows(migration_statements)
    canonical_semantic_rows = semantic_insert_rows(canonical_statements)
    if semantic_rows != canonical_semantic_rows:
        raise ValueError("generated migration values do not match the spec-derived SQL")
    rows = expected_world_rows(spec, allocation)
    if not rows:
        raise ValueError("package declares SQL delivery but has no supported generated world rows")
    migration_deletes, inserted_rows = inspect_generated_sql(
        migration_statements, rollback_statements, rows, spec, allocation)
    cleanup_only = migration_deletes - inserted_rows

    selected_database = runner("SELECT DATABASE();").strip()
    if selected_database != database:
        raise RuntimeError(f"connected database {selected_database!r} does not match {database!r}")
    server_version = runner("SELECT VERSION();").strip()
    if not server_version:
        raise RuntimeError("database did not report a server version")

    # Refuse to overwrite even one pre-existing row in any generated DELETE predicate.
    _assert_rows(runner, migration_deletes, 0)
    mutated = False
    try:
        mutated = True
        runner(migration)
        _assert_rows(runner, inserted_rows, 1)
        _assert_rows(runner, cleanup_only, 0)
        _assert_semantic_rows(runner, semantic_rows)
        runner(migration)
        _assert_rows(runner, inserted_rows, 1)
        _assert_rows(runner, cleanup_only, 0)
        _assert_semantic_rows(runner, semantic_rows)
        runner(rollback)
        _assert_rows(runner, migration_deletes, 0)
        mutated = True
        runner(migration)
        _assert_rows(runner, inserted_rows, 1)
        _assert_rows(runner, cleanup_only, 0)
        _assert_semantic_rows(runner, semantic_rows)
        runner(rollback)
        _assert_rows(runner, migration_deletes, 0)
        mutated = False
    except Exception as exc:
        cleanup_note = ""
        if mutated:
            try:
                runner(rollback)
                _assert_rows(runner, rows, 0)
            except Exception as cleanup_exc:  # keep original failure plus cleanup status
                cleanup_note = f"; cleanup rollback also failed: {cleanup_exc}"
        raise RuntimeError(f"database qualification failed: {exc}{cleanup_note}") from exc

    proof = {
        "schema": "arena-db-qualification/2",
        "manifest_sha256": integrity["manifest_sha256"],
        "database": database,
        "server_version": server_version,
        "migration_sha256": hashlib.sha256(migration.encode("utf-8")).hexdigest(),
        "rollback_sha256": hashlib.sha256(rollback.encode("utf-8")).hexdigest(),
        "checked_rows": len(migration_deletes),
        "checked_values": sum(len(values) for values in semantic_rows.values()),
        "semantic_sha256": _semantic_digest(semantic_rows),
        "sequence": ["apply", "verify", "apply-idempotently", "verify", "rollback",
                     "verify-empty", "reapply-after-rollback", "verify", "rollback",
                     "verify-empty"],
    }
    if schema_source is not None:
        proof["schema_source"] = schema_source
    proof_bytes = (json.dumps(proof, sort_keys=True, separators=(",", ":")) + "\n").encode()
    proof_sha256 = hashlib.sha256(proof_bytes).hexdigest()
    return {
        "schema": "arena-qualification-attestations/1",
        "subject": {"manifest_sha256": integrity["manifest_sha256"]},
        "levels": {
            "ARENA-DB": {
                "status": "PASS",
                "evidence": f"sha256:{proof_sha256}",
            }
        },
        "acceptance": [],
        "artifacts": [],
        "database_qualification": proof,
    }


def write_attestations(path: Path, attestations: dict) -> None:
    """Atomically write evidence without overwriting existing attestation files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(attestations, indent=2, sort_keys=True) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
