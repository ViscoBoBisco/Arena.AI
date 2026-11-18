"""Source-, schema-, row- and receipt-bound disposable World qualification."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from .db_qualification import (
    _DB_SCHEMA_TABLES,
    _INSERT_KEYS,
    _pinned_schema_source,
    expected_world_rows,
    semantic_insert_rows,
    validate_generated_sql,
)
from .generate import rollback_sql, world_sql
from .qualification import integrity_check
from .world_ownership import operate_on_package, _identity_json
from .world_schema import _extract_create_table, verify_pinned_world_schema


def _canonical_hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _runtime_hash_valid(value: dict, hash_field: str) -> bool:
    if not isinstance(value, dict):
        return False
    given = value.get(hash_field)
    body = dict(value)
    body.pop(hash_field, None)
    return isinstance(given, str) and _canonical_hash(body) == given


def _verify_recovery_evidence(evidence: dict, source: dict, schema: dict) -> None:
    schema_fields = ("schema", "database", "trust", "source", "created_tables",
                     "runtime_tables", "base_data_imported")
    schema_body = {key: evidence.get(key) for key in schema_fields}
    evidence_body = dict(evidence)
    evidence_hash = evidence_body.pop("evidence_sha256", None)
    binding_errors = []
    calculated_evidence_hash = _canonical_hash(evidence_body)
    if not isinstance(evidence_hash, str) or calculated_evidence_hash != evidence_hash:
        binding_errors.append(
            f"evidence digest expected={evidence_hash!r} calculated={calculated_evidence_hash}")
    if (not isinstance(evidence.get("runtime_schema_sha256"), str)
            or _canonical_hash(schema_body) != evidence.get("runtime_schema_sha256")):
        binding_errors.append("runtime schema digest")
    if evidence.get("schema") != "arena-world-schema-source/1":
        binding_errors.append("proof schema")
    if evidence.get("trust") != "SYNTHETIC_DISPOSABLE_MYSQL_PINNED_SCHEMA":
        binding_errors.append("trust scope")
    if evidence.get("database") != schema.get("database"):
        binding_errors.append("database name")
    if evidence.get("source") != source:
        binding_errors.append("pinned source hashes")
    if evidence.get("runtime_tables") != schema.get("runtime_tables"):
        binding_errors.append("runtime table metadata")
    if evidence.get("created_tables") != schema.get("created_tables"):
        binding_errors.append("created DDL hashes")
    if evidence.get("base_data_imported") is not False:
        binding_errors.append("base data scope")
    if binding_errors:
        raise ValueError("crash-recovery evidence is not bound to the current pinned schema: "
                         + ", ".join(binding_errors))
    tables = evidence.get("world_row_checks")
    if (not isinstance(tables, dict) or tables.get("status") != "PASS"
            or set(tables.get("tables", {})) != set(_DB_SCHEMA_TABLES)
            or any(item != {"apply": "PASS", "rollback": "PASS"}
                   for item in tables["tables"].values())):
        raise ValueError("recovery evidence does not cover apply and rollback for all seven tables")
    crash = evidence.get("process_recovery")
    if (not isinstance(crash, dict) or crash.get("status") != "PASS"
            or crash.get("child_exit_code") != 73
            or crash.get("crash_after_npc_vendor_insert_before_commit") != "PASS"
            or crash.get("uncommitted_rows_and_journal_state_changes_absent") != "PASS"
            or crash.get("same_request_restart_reapplied_seven_rows") != "PASS"
            or crash.get("same_request_receipt_replayed") != "PASS"
            or crash.get("owned_rollback_removed_all_seven_rows") != "PASS"):
        raise ValueError("recovery evidence does not prove real process death and restart recovery")


def _query_receipt(cursor, request_id: str, package_name: str,
                   package_version: str, manifest_sha256: str,
                   action: str) -> dict:
    cursor.execute(
        "SELECT request_id, fingerprint_sha256, package_name, package_version, "
        "manifest_sha256, action, result_json FROM arena_foundry_world_receipts "
        "WHERE request_id=%s", (request_id,))
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError(f"durable World operation receipt is missing: {request_id}")
    names = ("request_id", "fingerprint_sha256", "package_name", "package_version",
             "manifest_sha256", "action", "result_json")
    values = [row[name] for name in names] if isinstance(row, dict) else list(row)
    record = dict(zip(names, values))
    result = json.loads(record["result_json"])
    if (record["package_name"] != package_name
            or record["package_version"] != package_version
            or record["manifest_sha256"] != manifest_sha256
            or record["action"] != action
            or result.get("request_id") != request_id
            or result.get("manifest_sha256") != manifest_sha256
            or result.get("action") != action):
        raise RuntimeError(f"durable World operation receipt does not match request {request_id}")
    record["result_json_sha256"] = hashlib.sha256(
        record["result_json"].encode("utf-8")).hexdigest()
    return record


def qualify_owned_world_package(connection, package: Path, database: str,
                                core_source: Path, schema_proof: dict,
                                recovery_evidence: dict, *,
                                request_prefix: str | None = None) -> dict:
    """Qualify one exact package using the transactional World ownership service.

    The selected database must already contain the pinned seven-table schema
    created by ``initialize_pinned_empty_world_schema``. Existing unrelated
    rows are not overwritten. This function writes only package rows and Arena
    ownership/receipt records in the explicitly disposable database.
    """
    if (not isinstance(database, str)
            or not database.startswith("arena_foundry_disposable_")
            or len(database) > 64):
        raise ValueError("World qualification requires an explicitly disposable database")
    source = _pinned_schema_source(Path(core_source))
    schema_fields = ("schema", "database", "trust", "source", "created_tables",
                     "runtime_tables", "base_data_imported")
    schema_body = {key: schema_proof.get(key) for key in schema_fields}
    if (not isinstance(schema_proof.get("runtime_schema_sha256"), str)
            or _canonical_hash(schema_body) != schema_proof.get("runtime_schema_sha256")):
        raise ValueError("pinned runtime schema proof digest is invalid")
    if schema_proof.get("database") != database or schema_proof.get("source") != source:
        raise ValueError("pinned schema proof belongs to another database or source revision")
    verify_pinned_world_schema(connection, core_source, database, schema_proof)
    _verify_recovery_evidence(recovery_evidence, source, schema_proof)

    integrity = integrity_check(Path(package))
    if not integrity["ok"]:
        raise ValueError("package integrity failed: " + "; ".join(integrity["problems"]))
    manifest = integrity["manifest"]
    requirements = manifest.get("delivery_requirements", {})
    artifacts = requirements.get("artifacts", []) if isinstance(requirements, dict) else []
    if not any(isinstance(item, dict) and item.get("kind") == "world_sql"
               for item in artifacts):
        raise ValueError("package does not declare World SQL delivery")
    spec = json.loads((Path(package) / "spec.json").read_text(encoding="utf-8"))
    allocation = json.loads((Path(package) / "id-allocation.json").read_text(encoding="utf-8"))
    baseline = json.loads((Path(package) / "id-baseline.json").read_text(encoding="utf-8"))
    migration_path = Path(package) / "sql" / "01_world.sql"
    rollback_path = Path(package) / "sql" / "99_rollback.sql"
    migration = migration_path.read_text(encoding="utf-8")
    rollback = rollback_path.read_text(encoding="utf-8")
    migration_statements = validate_generated_sql(migration, rollback=False)
    if rollback != rollback_sql(spec, reserved=baseline.get("reserved_ids", {})):
        raise ValueError("package rollback file does not match the canonical fail-closed ownership guard")
    canonical = validate_generated_sql(
        world_sql(spec, reserved=baseline.get("reserved_ids", {})), rollback=False)
    semantic = semantic_insert_rows(migration_statements)
    if semantic != semantic_insert_rows(canonical):
        raise ValueError("package World migration differs from spec-derived canonical SQL")
    rows = expected_world_rows(spec, allocation)
    if not rows or len(semantic) != len(rows):
        raise ValueError("package does not have unique, supported generated World row identities")

    cursor = connection.cursor()
    try:
        cursor.execute("SELECT DATABASE()")
        row = cursor.fetchone()
        selected = row[0] if row and not isinstance(row, dict) else (
            next(iter(row.values())) if row else None)
        if selected != database:
            raise ValueError(f"connected database {selected!r} does not match {database!r}")
        cursor.execute("SELECT VERSION()")
        row = cursor.fetchone()
        server_version = row[0] if row and not isinstance(row, dict) else (
            next(iter(row.values())) if row else None)
        if not server_version:
            raise RuntimeError("MySQL server version unavailable")
    finally:
        cursor.close()

    prefix = request_prefix or ("q-" + integrity["manifest_sha256"][:16])
    if (not isinstance(prefix, str) or not prefix or len(prefix) > 52
            or any(not (char.isascii() and (char.isalnum() or char in "._:-"))
                   for char in prefix)):
        raise ValueError("operation request prefix must be 1-52 safe ASCII identifier characters")
    requests = {
        "apply": (prefix + "-apply-1", "apply"),
        "idempotent": (prefix + "-apply-2", "apply"),
        "rollback": (prefix + "-rollback-1", "rollback"),
        "reapply": (prefix + "-apply-3", "apply"),
        "final_rollback": (prefix + "-rollback-2", "rollback"),
    }
    package_name, package_version = spec["name"], spec["version"]
    apply_result = operate_on_package(connection, package, action="apply",
                                      request_id=requests["apply"][0])
    if apply_result["changed_rows"] != len(rows):
        raise RuntimeError("initial transactional apply did not create every generated row")
    replay = operate_on_package(connection, package, action="apply",
                                request_id=requests["apply"][0])
    if not replay.get("replayed"):
        raise RuntimeError("same-request apply retry did not replay its durable receipt")
    no_op = operate_on_package(connection, package, action="apply",
                               request_id=requests["idempotent"][0])
    if no_op["changed_rows"] != 0:
        raise RuntimeError("independent idempotent apply changed owned World rows")

    def assert_row_count(expected_count: int) -> None:
        check = connection.cursor()
        try:
            for identity in rows:
                table, keys = identity
                where = " AND ".join(f"`{column}`=%s" for column, _ in keys)
                check.execute(f"SELECT COUNT(*) FROM `{table}` WHERE {where}",
                              tuple(value for _, value in keys))
                found = check.fetchone()
                count = found[0] if not isinstance(found, dict) else next(iter(found.values()))
                if int(count) != expected_count:
                    raise RuntimeError(f"{table} expected {expected_count} row, found {count}")
        finally:
            check.close()

    assert_row_count(1)
    rolled = operate_on_package(connection, package, action="rollback",
                                request_id=requests["rollback"][0])
    if rolled["changed_rows"] != len(rows):
        raise RuntimeError("owned rollback did not remove every generated row")
    assert_row_count(0)
    reapplied = operate_on_package(connection, package, action="apply",
                                   request_id=requests["reapply"][0])
    if reapplied["changed_rows"] != len(rows):
        raise RuntimeError("reapply after owned rollback did not restore all generated rows")
    final_rollback = operate_on_package(connection, package, action="rollback",
                                        request_id=requests["final_rollback"][0])
    if final_rollback["changed_rows"] != len(rows):
        raise RuntimeError("final owned rollback did not remove every generated row")
    assert_row_count(0)

    receipts = []
    cursor = connection.cursor()
    try:
        for key in ("apply", "idempotent", "rollback", "reapply", "final_rollback"):
            request_id, action = requests[key]
            receipts.append(_query_receipt(cursor, request_id, package_name,
                                           package_version, integrity["manifest_sha256"], action))
        cursor.execute(
            "SELECT COUNT(*) FROM arena_foundry_world_rows "
            "WHERE package_name=%s AND manifest_sha256=%s AND state='ROLLED_BACK'",
            (package_name, integrity["manifest_sha256"]))
        journal_row = cursor.fetchone()
        journal_count = int(journal_row[0] if not isinstance(journal_row, dict)
                            else next(iter(journal_row.values())))
    finally:
        cursor.close()
    if journal_count != len(rows):
        raise RuntimeError("durable ownership journal does not contain a rolled-back row per identity")

    source_files = {}
    for rel in ("foundry/world_ownership.py", "foundry/world_schema.py",
                "foundry/db_qualification.py", "foundry/world_qualification.py",
                "foundry/qualification.py"):
        source_files[rel] = hashlib.sha256((Path(__file__).resolve().parents[1] / rel).read_bytes()).hexdigest()
    commit = os.environ.get("GITHUB_SHA")
    if not commit:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], check=True,
                                capture_output=True, text=True).stdout.strip()
    proof = {
        "schema": "arena-db-qualification/3",
        "trust": "SYNTHETIC_DISPOSABLE_MYSQL_PINNED_SCHEMA",
        "manifest_sha256": integrity["manifest_sha256"],
        "database": database,
        "server_version": str(server_version),
        "source_commit": commit,
        "schema_source": source,
        "pinned_schema": schema_proof,
        "runtime_schema_sha256": schema_proof["runtime_schema_sha256"],
        "recovery_evidence": recovery_evidence,
        "recovery_evidence_sha256": _canonical_hash(recovery_evidence),
        "recovery_checks": {
            "all_seven_supported_tables_apply_and_rollback": "PASS",
            "late_foreign_collision_rolls_back_prior_tables": "PASS",
            "foreign_row_edit_refuses_rollback": "PASS",
            "process_crash_mid_multi_table_transaction_recovers": "PASS",
        },
        "operation_checks": {
            "transactional_apply_all_rows": "PASS",
            "same_request_receipt_replay": "PASS",
            "independent_idempotent_apply": "PASS",
            "owned_rollback_all_rows": "PASS",
            "reapply_after_rollback": "PASS",
            "final_rollback_all_rows": "PASS",
            "durable_receipts_and_rolled_back_journal": "PASS",
        },
        "qualification_sources": source_files,
        "migration_sha256": hashlib.sha256(migration.encode("utf-8")).hexdigest(),
        "rollback_sha256": hashlib.sha256(rollback.encode("utf-8")).hexdigest(),
        "semantic_sha256": _canonical_hash({
            "rows": [[table, [[column, value] for column, value in keys], values]
                     for (table, keys), values in sorted(semantic.items())]}),
        "row_count": len(rows),
        "row_tables": {table: sum(1 for row_table, _ in rows if row_table == table)
                       for table in _DB_SCHEMA_TABLES},
        "journal_rolled_back_rows": journal_count,
        "receipts": receipts,
        "receipt_set_sha256": _canonical_hash(receipts),
        "sequence": ["apply", "same-request-replay", "independent-idempotent-apply",
                     "rollback", "verify-empty", "reapply", "rollback", "verify-empty"],
    }
    proof_hash = hashlib.sha256((json.dumps(
        proof, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")).hexdigest()
    return {
        "schema": "arena-qualification-attestations/1",
        "subject": {"manifest_sha256": integrity["manifest_sha256"]},
        "levels": {"ARENA-DB": {"status": "PASS", "evidence": "sha256:" + proof_hash}},
        "acceptance": [],
        "artifacts": [],
        "database_qualification": proof,
    }


def attestations_sha256(attestations: dict) -> str:
    """Stable digest used to bind an attestation to its proof envelope."""
    return _canonical_hash(attestations)
