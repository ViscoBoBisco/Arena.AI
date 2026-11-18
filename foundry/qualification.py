"""Independent package integrity, qualification, and release gates.

Build creates a preview bundle. Verify checks only byte integrity. Qualify
binds a qualification report to one manifest, and release only copies a bundle
whose report passes every policy gate. None of these steps mutates the bundle.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def integrity_check(package: Path) -> Dict[str, Any]:
    """Hash package payload/metadata without implying qualification."""
    manifest_path = package / "manifest.json"
    try:
        manifest = _read_json(manifest_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "problems": [f"cannot read manifest: {exc}"],
                "manifest": None, "manifest_sha256": None}
    manifest_sha256 = _sha256(manifest_path.read_bytes())
    files = manifest.get("files")
    metadata_files = manifest.get("metadata_files", {})
    if not isinstance(files, dict) or not isinstance(metadata_files, dict):
        return {"ok": False, "problems": ["manifest files and metadata_files must be objects"],
                "manifest": manifest, "manifest_sha256": manifest_sha256}

    problems: List[str] = []
    if set(files) & set(metadata_files):
        problems.append("manifest payload and metadata file paths overlap")
    listed = dict(files)
    listed.update(metadata_files)
    for rel, meta in listed.items():
        if not isinstance(rel, str):
            problems.append(f"INVALID {rel!r}: manifest path must be a string")
            continue
        path = PurePosixPath(rel)
        if path.is_absolute() or ".." in path.parts or not path.parts or not isinstance(meta, dict):
            problems.append(f"INVALID {rel!r}: unsafe path or malformed metadata")
            continue
        if (not isinstance(meta.get("sha256"), str)
                or len(meta["sha256"]) != 64
                or not isinstance(meta.get("size"), int)
                or isinstance(meta.get("size"), bool)
                or meta["size"] < 0):
            problems.append(f"INVALID {rel}: malformed hash or size")
            continue
        target = package.joinpath(*path.parts)
        try:
            target.resolve().relative_to(package.resolve())
        except (OSError, ValueError):
            problems.append(f"INVALID {rel}: path escapes package")
            continue
        if target.is_symlink() or not target.is_file():
            problems.append(f"MISSING {rel}")
            continue
        data = target.read_bytes()
        if _sha256(data) != meta.get("sha256") or len(data) != meta.get("size"):
            problems.append(f"CHANGED {rel}")

    actual = {p.relative_to(package).as_posix()
              for p in package.rglob("*") if p.is_file() or p.is_symlink()}
    expected = set(listed) | {"manifest.json"}
    for rel in sorted(actual - expected):
        problems.append(f"UNLISTED {rel}")
    return {"ok": not problems, "problems": problems, "manifest": manifest,
            "manifest_sha256": _sha256(manifest_path.read_bytes())}


def _passed_record(record: Any) -> bool:
    return (isinstance(record, dict) and record.get("status") == "PASS"
            and isinstance(record.get("evidence"), str)
            and bool(record["evidence"].strip()))


def _valid_database_attestation(attestations: Any, manifest_sha256: str,
                                package: Path) -> bool:
    """Semantically bind DB proof, migrations and schema source to this package/pin."""
    if (not isinstance(attestations, dict)
            or attestations.get("schema") != "arena-qualification-attestations/1"
            or attestations.get("subject") != {"manifest_sha256": manifest_sha256}):
        return False
    levels = attestations.get("levels")
    record = levels.get("ARENA-DB") if isinstance(levels, dict) else None
    proof = attestations.get("database_qualification")
    if (not _passed_record(record) or not isinstance(proof, dict)
            or proof.get("schema") != "arena-db-qualification/2"
            or proof.get("manifest_sha256") != manifest_sha256):
        return False
    database = proof.get("database")
    if (not isinstance(database, str) or len(database) > 64
            or not re.fullmatch(r"arena_foundry_disposable_[A-Za-z0-9_]+", database)):
        return False
    if not isinstance(proof.get("server_version"), str) or not proof["server_version"].strip():
        return False
    checked_rows = proof.get("checked_rows")
    checked_values = proof.get("checked_values")
    if (not isinstance(checked_rows, int) or isinstance(checked_rows, bool) or checked_rows < 1
            or not isinstance(checked_values, int) or isinstance(checked_values, bool)
            or checked_values < checked_rows):
        return False
    expected_sequence = [
        "apply", "verify", "apply-idempotently", "verify", "rollback", "verify-empty",
        "reapply-after-rollback", "verify", "rollback", "verify-empty",
    ]
    if proof.get("sequence") != expected_sequence:
        return False
    try:
        migration_path = package / "sql" / "01_world.sql"
        rollback_path = package / "sql" / "99_rollback.sql"
        migration_hash = _sha256(migration_path.read_bytes())
        rollback_hash = _sha256(rollback_path.read_bytes())
        pins = _read_json(Path(__file__).resolve().parent / "core-pins.json")
        from .db_qualification import (expected_world_rows, inspect_generated_sql,
                                       semantic_insert_rows, _semantic_digest,
                                       validate_generated_sql)
        from .generate import world_sql
        spec = _read_json(package / "spec.json")
        allocation = _read_json(package / "id-allocation.json")
        baseline = _read_json(package / "id-baseline.json")
        migration_statements = validate_generated_sql(
            migration_path.read_text(encoding="utf-8"), rollback=False)
        rollback_statements = validate_generated_sql(
            rollback_path.read_text(encoding="utf-8"), rollback=True)
        expected_rows = expected_world_rows(spec, allocation)
        migration_deletes, _ = inspect_generated_sql(
            migration_statements, rollback_statements, expected_rows, spec, allocation)
        semantic_rows = semantic_insert_rows(migration_statements)
        canonical_rows = semantic_insert_rows(validate_generated_sql(
            world_sql(spec, reserved=baseline.get("reserved_ids", {})), rollback=False))
        if semantic_rows != canonical_rows:
            return False
    except (OSError, ValueError, KeyError, TypeError):
        return False
    expected_values = sum(len(values) for values in semantic_rows.values())
    if (proof.get("migration_sha256") != migration_hash
            or proof.get("rollback_sha256") != rollback_hash
            or checked_rows != len(migration_deletes)
            or checked_values != expected_values
            or proof.get("semantic_sha256") != _semantic_digest(semantic_rows)):
        return False
    source = proof.get("schema_source")
    pin = pins.get("azerothcore_wotlk") if isinstance(pins, dict) else None
    if (not isinstance(source, dict)
            or source.get("schema") != "arena-db-schema-source/1"
            or not isinstance(pin, dict)
            or source.get("repository") != pin.get("repository")
            or source.get("revision") != pin.get("revision")):
        return False
    tables = source.get("tables")
    required_tables = {
        f"data/sql/base/db_world/{table}.sql" for table in (
            "creature_template", "creature_template_model", "game_event", "item_template",
            "npc_vendor", "quest_template", "spell_dbc",
        )
    }
    return (isinstance(tables, dict) and set(tables) == required_tables
            and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                    for value in tables.values())
            and record.get("evidence") == "sha256:" + _sha256(
                (json.dumps(proof, sort_keys=True, separators=(",", ":")) + "\n").encode()))


def _valid_characters_database_attestation(proof: Any, manifest_sha256: str,
                                           package: Path, table: str) -> bool:
    """Validate one table-scoped Characters proof against this exact package.

    The result establishes semantic correspondence to disposable-MySQL checks,
    not provenance authenticity. Callers must keep externally supplied records
    labelled EXTERNAL_UNTRUSTED and must never use this result to bypass release
    gates for server/client/runtime evidence.
    """
    expected_keys = {
        "schema", "qualification", "status", "trust", "package", "package_version",
        "manifest_sha256", "table", "database", "server_version", "migration_sha256",
        "rollback_note_sha256", "atomic_claim_upsert_sha256", "module_source_sha256",
        "spec_sha256", "core_source", "table_schema", "checks", "sequence", "limitations",
        "environment",
    }
    if not isinstance(proof, dict) or set(proof) != expected_keys:
        return False
    if (proof.get("schema") != "arena-characters-db-qualification/2"
            or proof.get("qualification") != "ARENA-DB-CHARACTERS"
            or proof.get("status") != "PASS"
            or proof.get("trust") != "SYNTHETIC_DISPOSABLE_MYSQL"
            or proof.get("manifest_sha256") != manifest_sha256
            or proof.get("table") != table):
        return False
    if (proof.get("database") != "arena_foundry_disposable_characters_ci"
            or not isinstance(proof.get("server_version"), str)
            or not proof["server_version"].strip()):
        return False

    environment = proof.get("environment")
    expected_environment_keys = {
        "workflow_run_id", "workflow_job", "workflow_name", "workflow_ref",
        "source_commit", "core_repository", "core_revision",
    }
    if (not isinstance(environment, dict) or set(environment) != expected_environment_keys
            or not isinstance(environment.get("workflow_run_id"), str)
            or not re.fullmatch(r"[0-9]{1,20}", environment["workflow_run_id"])
            or environment.get("workflow_job") != "qualify-generated-world-sql"
            or environment.get("workflow_name") != "pinned AzerothCore disposable DB qualification"
            or not isinstance(environment.get("workflow_ref"), str)
            or ".github/workflows/azerothcore-db-qualification.yml@" not in environment["workflow_ref"]
            or not isinstance(environment.get("source_commit"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", environment["source_commit"])):
        return False

    try:
        from .generate import characters_claim_upsert_sql, characters_sql_files
        spec = _read_json(package / "spec.json")
        protocol = spec.get("addon_protocol")
        if (not isinstance(protocol, dict) or protocol.get("claims_table") != table
                or proof.get("package") != spec.get("name")
                or proof.get("package_version") != spec.get("version")):
            return False
        pins = _read_json(Path(__file__).resolve().parent / "core-pins.json")
        pin = pins.get("azerothcore_wotlk") if isinstance(pins, dict) else None
        if (not isinstance(pin, dict)
                or proof.get("core_source") != {"repository": pin.get("repository"),
                                                "revision": pin.get("revision")}
                or environment.get("core_repository") != pin.get("repository")
                or environment.get("core_revision") != pin.get("revision")):
            return False
        spec_bytes = (package / "spec.json").read_bytes()
        migration_rel = f"sql/characters/{table}.sql"
        rollback_rel = f"sql/characters/99_{spec['name']}_rollback.sql"
        expected_files = characters_sql_files(spec)
        migration_bytes = (package / migration_rel).read_bytes()
        rollback_bytes = (package / rollback_rel).read_bytes()
        if (migration_bytes != expected_files.get(migration_rel, "").encode("utf-8")
                or rollback_bytes != expected_files.get(rollback_rel, "").encode("utf-8")):
            return False
        module_rel = (f"server/mod-{spec['name']}/src/"
                      f"{''.join(part.capitalize() for part in spec['name'].split('-'))}.cpp")
        module_hash = _sha256((package / module_rel).read_bytes())
        test_guid = 4_294_967_000
        claim_sql = characters_claim_upsert_sql({"addon_protocol": protocol}).replace(
            "{}", str(test_guid))
        expected_schema = {
            "engine": "InnoDB",
            "columns": [
                "guid|int unsigned|NO|PRI|1",
                "total_claims|int unsigned|NO||2",
                "last_claim_day|date|YES||3",
            ],
            "primary_key": "guid",
        }
    except (OSError, ValueError, KeyError, TypeError):
        return False

    expected_checks = {
        "migration_applied_twice", "schema_matches_declared_ledger",
        "same_day_duplicate_ignored", "concurrent_daily_increment_serialized",
        "concurrent_target_cap_enforced", "post_target_claim_refused",
        "duplicate_character_row_rejected", "module_state_query", "fixture_rows_removed",
    }
    expected_sequence = [
        "apply", "verify-schema", "apply-idempotently", "verify-schema",
        "claim-same-day-twice", "simulate-day-boundary", "race-two-claim-connections",
        "race-two-claims-at-target-minus-one", "attempt-claim-after-target",
        "reject-second-character-row", "verify-module-state-query", "delete-test-fixture",
    ]
    return (
        proof.get("migration_sha256") == _sha256(migration_bytes)
        and proof.get("rollback_note_sha256") == _sha256(rollback_bytes)
        and proof.get("atomic_claim_upsert_sha256") == _sha256(claim_sql.encode("utf-8"))
        and proof.get("module_source_sha256") == module_hash
        and proof.get("spec_sha256") == _sha256(spec_bytes)
        and proof.get("table_schema") == expected_schema
        and isinstance(proof.get("checks"), dict)
        and set(proof["checks"]) == expected_checks
        and all(value == "PASS" for value in proof["checks"].values())
        and proof.get("sequence") == expected_sequence
        and isinstance(proof.get("limitations"), list)
        and all(isinstance(line, str) and line.strip() for line in proof["limitations"])
    )


_ARTIFACT_KINDS = {
    "world_sql", "data_migration", "characters_sql", "server_module", "addon_files",
    "client_dbc", "server_dbc", "mpq", "config",
}
_TABLE_SCOPED_ARTIFACTS = {"characters_sql", "client_dbc", "server_dbc", "mpq"}


def _valid_delivery_contract(delivery: Any) -> bool:
    if (not isinstance(delivery, dict)
            or delivery.get("schema") != "arena-delivery-requirements/1"
            or not isinstance(delivery.get("artifacts"), list)
            or not isinstance(delivery.get("capabilities"), list)):
        return False
    if not all(isinstance(item, dict)
               and isinstance(item.get("name"), str) and bool(item["name"].strip())
               and isinstance(item.get("required"), bool)
               and isinstance(item.get("version"), str)
               and isinstance(item.get("activation"), str)
               for item in delivery["capabilities"]):
        return False
    artifacts = delivery["artifacts"]
    if not all(isinstance(item, dict)
               and isinstance(item.get("kind"), str)
               and item["kind"] in _ARTIFACT_KINDS
               and isinstance(item.get("version"), str)
               and isinstance(item.get("activation"), str)
               and ("table" not in item
                    or isinstance(item.get("table"), str) and bool(item["table"].strip()))
               and (item["kind"] not in _TABLE_SCOPED_ARTIFACTS
                    or isinstance(item.get("table"), str) and bool(item["table"].strip()))
               for item in artifacts):
        return False
    identities = [(item["kind"], item.get("table")) for item in artifacts]
    if len(identities) != len(set(identities)):
        return False
    client_tables = {item["table"] for item in artifacts if item["kind"] == "client_dbc"}
    server_tables = {item["table"] for item in artifacts if item["kind"] == "server_dbc"}
    if client_tables != server_tables:
        return False
    if any(item["kind"] == "mpq" for item in artifacts) and not client_tables:
        return False
    return True


def _database_scopes(artifacts: list) -> tuple[bool, set[str]]:
    """Derive independent world and Characters DB scopes from delivery artifacts."""
    world_required = any(
        isinstance(item, dict)
        and (item.get("kind") == "world_sql"
             or item.get("kind") == "data_migration" and not item.get("table"))
        for item in artifacts)
    character_tables = {
        item.get("table") for item in artifacts
        if isinstance(item, dict)
        and item.get("kind") in ("characters_sql", "data_migration")
        and isinstance(item.get("table"), str)
    }
    return world_required, character_tables


def _compile_qualification_matches(package: Path, evidence: dict) -> bool:
    """Require an exact checked-in compile record and matching packaged source hashes."""
    record = evidence.get("compile_qualification")
    if not isinstance(record, dict):
        return False
    foundry_root = Path(__file__).resolve().parent
    try:
        pins = _read_json(foundry_root / "core-pins.json")
        core_pin = pins.get("azerothcore_wotlk")
        if not isinstance(core_pin, dict) or not isinstance(evidence.get("spec"), str):
            return False
        pinned_revision = core_pin.get("revision")
        subject = record.get("subject", {})
        spec_name = evidence["spec"]
        module_name = f"mod-{spec_name}"
        source_dir = package / "server" / module_name / "src"
        source_hashes = {
            path.relative_to(package).as_posix(): _sha256(path.read_bytes())
            for path in source_dir.rglob("*")
            if path.is_file() and path.suffix in (".cpp", ".h")
        }
        records_dir = foundry_root / "qualifications"
        canonical_records = [_read_json(path) for path in sorted(records_dir.glob("*.json"))]
    except (OSError, ValueError, AttributeError, TypeError):
        return False
    if not source_hashes:
        return False
    return any(
        record == canonical
        and record.get("schema") == "arena-qualification/1"
        and record.get("qualification") == "ARENA-COMPILE"
        and record.get("status") == "PASS"
        and isinstance(subject, dict)
        and subject.get("spec") == spec_name
        and subject.get("module") == module_name
        and subject.get("source_sha256") == source_hashes
        and isinstance(record.get("environment"), dict)
        and record["environment"].get("core_revision") == pinned_revision
        for canonical in canonical_records
    )


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return _sha256(encoded)


def qualify_package(package: Path, attestations_path: Optional[Path] = None,
                    *, _attestations_override: Optional[dict] = None) -> dict:
    """Produce a reproducible manifest-bound report from raw attestation records."""
    integrity = integrity_check(package)
    report = {
        "schema": "arena-qualification-report/1",
        "package": str(package),
        "manifest_sha256": integrity.get("manifest_sha256"),
        "integrity": "PASS" if integrity["ok"] else "FAIL",
        "status": "BLOCKED",
        "qualified": False,
        "gates": [],
        "blocked_reasons": [],
    }

    def gate(name: str, ok: bool, detail: str) -> None:
        report["gates"].append({"name": name, "status": "PASS" if ok else "BLOCKED",
                                "detail": detail})
        if not ok:
            report["blocked_reasons"].append(f"{name}: {detail}")

    gate("integrity", integrity["ok"],
         "manifest and every listed package byte match" if integrity["ok"]
         else "; ".join(integrity["problems"]))
    if not integrity["ok"]:
        return report

    try:
        evidence = _read_json(package / "evidence.json")
        manifest = integrity["manifest"]
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        gate("evidence", False, f"cannot read package evidence: {exc}")
        return report

    try:
        attestations = (_attestations_override if _attestations_override is not None
                        else _read_json(attestations_path) if attestations_path else {})
        if not isinstance(attestations, dict):
            raise ValueError("attestations must be a JSON object")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        attestations = {}
        report["attestations_error"] = str(exc)
    report["attestations"] = attestations
    subject = attestations.get("subject") if isinstance(attestations, dict) else None
    attested = (isinstance(attestations, dict)
                and attestations.get("schema") == "arena-qualification-attestations/1"
                and isinstance(subject, dict)
                and subject.get("manifest_sha256") == integrity["manifest_sha256"])
    report["attestations_sha256"] = (
        _canonical_json_sha256(attestations) if attested else None)
    gate("attestation_binding", attested,
         "attestations bind to this manifest" if attested
         else "manifest-bound qualification attestations are required")

    validation = evidence.get("validation", {})
    verdicts = validation.get("verdicts", {}) if isinstance(validation, dict) else {}
    gate("spec_validation", isinstance(validation, dict) and validation.get("ok") is True,
         "all essential spec checks passed" if isinstance(validation, dict)
         and validation.get("ok") is True else "spec validation is not fully PASS")
    lifecycle = evidence.get("lifecycle", {})
    lifecycle_ok = (isinstance(lifecycle, dict)
                    and lifecycle.get("installable") is False
                    and lifecycle.get("release") == "BLOCKED")
    gate("build_is_not_release", lifecycle_ok,
         "build output is explicitly marked non-installable pending release" if lifecycle_ok
         else "package lacks fail-closed build lifecycle markers")
    preview_ok = evidence.get("allow_unverified") is False
    gate("preview_allowance", preview_ok,
         "no unverified-preview allowance was used" if preview_ok
         else "--allow-unverified packages cannot be released")
    assets_ok = (verdicts.get("asset-verification") == "PASS"
                 and not evidence.get("unverified_asset_refs"))
    gate("asset_provenance", assets_ok,
         "all referenced assets have matching source evidence" if assets_ok
         else "missing or unverified asset provenance")

    delivery = manifest.get("delivery_requirements", {})
    artifact_list = delivery.get("artifacts", []) if isinstance(delivery, dict) else []
    delivery_ok = _valid_delivery_contract(delivery)
    gate("delivery_contract", delivery_ok,
         "well-formed mandatory artifact requirements are present" if delivery_ok
         else "delivery requirements are missing or malformed")
    try:
        from .spec import delivery_requirements as derive_delivery_requirements
        package_spec = _read_json(package / "spec.json")
        derived_delivery = derive_delivery_requirements(package_spec)
        delivery_binding = (delivery_ok and delivery == derived_delivery
                            and evidence.get("delivery_requirements") == derived_delivery)
    except (OSError, ValueError, KeyError, TypeError):
        delivery_binding = False
    gate("delivery_contract_binding", delivery_binding,
         "manifest and evidence inventories match the package spec and emitter contract"
         if delivery_binding else "delivery inventory does not match package spec/emitter output")
    artifact_kinds = {
        item.get("kind") for item in artifact_list
        if isinstance(item, dict) and isinstance(item.get("kind"), str)
    }
    from .maturity import evaluate as evaluate_maturity
    world_required, character_tables = _database_scopes(artifact_list)
    world_db_pass = (_valid_database_attestation(
        attestations, integrity["manifest_sha256"], package) if attested and world_required else False)
    character_records = (attestations.get("characters_database_qualifications", [])
                         if attested else [])
    if not isinstance(character_records, list):
        character_records = []
    records_by_table: dict[str, list[dict]] = {}
    for record in character_records:
        if isinstance(record, dict) and isinstance(record.get("table"), str):
            records_by_table.setdefault(record["table"], []).append(record)
    characters_db_pass = {
        table: (len(records_by_table.get(table, [])) == 1
                and _valid_characters_database_attestation(
                    records_by_table[table][0], integrity["manifest_sha256"], package, table))
        for table in character_tables
    }
    compile_pass = False
    external_levels = attestations.get("levels", {}) if attested else {}
    if not isinstance(external_levels, dict):
        external_levels = {}
    external_artifacts = attestations.get("artifacts", []) if attested else []
    if not isinstance(external_artifacts, list):
        external_artifacts = []

    dbc_tables = sorted({item["table"] for item in artifact_list
                         if isinstance(item, dict) and item.get("kind") == "client_dbc"
                         and isinstance(item.get("table"), str)})
    for table in dbc_tables:
        parity_records = [record for record in external_artifacts
                          if isinstance(record, dict)
                          and record.get("kind") == "dbc_parity"
                          and record.get("table") == table]
        parity_pass = bool(parity_records) and all(_passed_record(record)
                                                    for record in parity_records)
        gate(f"dbc_parity:{table}", parity_pass,
             f"client/server DBC hash-parity evidence required for {table}" if not parity_pass
             else f"client/server DBC hashes match for {table}")

    database_kinds = {"world_sql", "data_migration", "characters_sql"}
    if artifact_kinds & database_kinds:
        world_scope_pass = not world_required or world_db_pass
        if world_required:
            gate("database_migrations:world", world_db_pass,
                 "pinned world-schema apply/rollback proof required" if not world_db_pass
                 else "world-schema proof matches this package and the pinned source")
        for table in sorted(character_tables):
            table_pass = characters_db_pass.get(table, False)
            gate(f"database_migrations:characters:{table}", table_pass,
                 f"matching table-scoped Characters proof required for {table}" if not table_pass
                 else f"Characters proof semantically matches {table} and this package manifest")
        records_exact = (
            isinstance(attestations.get("characters_database_qualifications", []), list)
            and len(character_records) == len(character_tables)
            and set(records_by_table) == character_tables
            and all(len(records_by_table.get(table, [])) == 1 for table in character_tables)
        )
        gate("characters_database_evidence_inventory", records_exact,
             "one proof per declared Characters table and no undeclared proof records"
             if records_exact else
             "Characters proofs must be unique and exactly match declared table identities")
        database_pass = world_scope_pass and all(characters_db_pass.values())
        gate("database_migrations", database_pass,
             "every declared database scope needs its own matching proof"
             if not database_pass else
             "all declared world and Characters database scopes have matching proofs")
    else:
        world_db_pass = False
        characters_db_pass = {}

    if "server_module" in artifact_kinds:
        compile_pass = _compile_qualification_matches(package, evidence)
        gate("server_module", compile_pass,
             "matching pinned-core record and exact packaged source hashes required"
             if not compile_pass else
             "checked-in pinned-core record matches the packaged source hashes")

    try:
        maturity = evaluate_maturity(
            package, delivery, evidence, attestations,
            world_db_pass=world_db_pass, characters_db_pass=characters_db_pass,
            compile_pass=compile_pass, assets_pass=assets_ok)
    except ValueError as exc:
        maturity = {"schema": "arena-artifact-maturity/1",
                    "trust": "EXTERNAL_UNTRUSTED", "inventory_issues": [], "artifacts": []}
        gate("artifact_maturity_inventory", False, str(exc))
    else:
        inventory_ok = not maturity.get("inventory_issues")
        gate("artifact_maturity_inventory", inventory_ok,
             "artifact behaviors derived from declared emitters" if inventory_ok
             else "undeclared emitted artifacts: " + ", ".join(maturity["inventory_issues"]))
    report["artifact_maturity"] = maturity
    for artifact in maturity["artifacts"]:
        for behavior, status in sorted(artifact["status"].items()):
            passed = status == "PASS"
            gate(f"artifact_maturity:{artifact['identity']}:{behavior}", passed,
                 "observed from package bytes or qualification evidence" if passed
                 else f"required behavior is {status}; independent evidence is absent")
    if "mpq" in artifact_kinds:
        required_mpq = [item for item in artifact_list
                        if isinstance(item, dict) and item.get("kind") == "mpq"]
        mpq_checks = []
        for required in required_mpq:
            matching = [record for record in external_artifacts
                        if isinstance(record, dict)
                        and record.get("kind") == "mpq"
                        and record.get("table") == required.get("table")]
            mpq_checks.append(bool(matching) and all(_passed_record(r) for r in matching))
        mpq_pass = bool(mpq_checks) and all(mpq_checks)
        gate("mpq_delivery", mpq_pass,
             "each required MPQ/table merge needs evidence" if not mpq_pass
             else "MPQ merge records supplied for every required table")

    acceptance = evidence.get("acceptance", [])
    if not isinstance(acceptance, list) or not acceptance:
        acceptance = []
        gate("acceptance_manifest", False, "package acceptance list is missing or malformed")
    acceptance_records = attestations.get("acceptance", []) if attested else []
    if not isinstance(acceptance_records, list):
        acceptance_records = []
    for item in acceptance:
        statement = item.get("statement") if isinstance(item, dict) else str(item)
        record = next((r for r in acceptance_records
                       if isinstance(r, dict) and r.get("statement") == statement), None)
        gate(f"acceptance:{statement}", _passed_record(record),
             "independent acceptance evidence required" if not _passed_record(record)
             else "acceptance evidence supplied")
    live_pass = _passed_record(external_levels.get("CAMPAIGN-LIVE"))
    gate("live_gameplay", live_pass,
         "client/Playerbots runtime qualification required" if not live_pass
         else "live gameplay qualification record supplied")

    report["qualified"] = not report["blocked_reasons"]
    report["status"] = "QUALIFIED" if report["qualified"] else "BLOCKED"
    return report


def _required_gate_names(evidence: dict, manifest: dict) -> set:
    """Derive the complete gate set so a truncated report cannot authorize release."""
    required = {
        "integrity", "attestation_binding", "spec_validation", "build_is_not_release",
        "preview_allowance", "asset_provenance", "delivery_contract",
        "delivery_contract_binding", "artifact_maturity_inventory", "live_gameplay",
    }
    delivery = manifest.get("delivery_requirements", {})
    artifacts = delivery.get("artifacts", []) if isinstance(delivery, dict) else []
    kinds = {item.get("kind") for item in artifacts
             if isinstance(item, dict) and isinstance(item.get("kind"), str)}
    if kinds & {"world_sql", "data_migration", "characters_sql"}:
        required.add("database_migrations")
        world_required, character_tables = _database_scopes(artifacts)
        if world_required:
            required.add("database_migrations:world")
        for table in character_tables:
            required.add(f"database_migrations:characters:{table}")
        required.add("characters_database_evidence_inventory")
    if "server_module" in kinds:
        required.add("server_module")
    if "mpq" in kinds:
        required.add("mpq_delivery")
    for item in artifacts:
        if (isinstance(item, dict) and item.get("kind") == "client_dbc"
                and isinstance(item.get("table"), str)):
            required.add(f"dbc_parity:{item['table']}")
    try:
        from .maturity import gate_names as maturity_gate_names
        required.update(maturity_gate_names(delivery))
    except ValueError:
        # The inventory gate itself blocks malformed or unknown artifact kinds.
        pass
    acceptance = evidence.get("acceptance", [])
    if isinstance(acceptance, list) and acceptance:
        for item in acceptance:
            statement = item.get("statement") if isinstance(item, dict) else str(item)
            required.add(f"acceptance:{statement}")
    else:
        required.add("acceptance_manifest")
    return required


def release_package(package: Path, qualification_path: Path, out_dir: Path) -> dict:
    """Atomically copy only an integrity-checked package with a bound PASS report."""
    report = _read_json(qualification_path)
    integrity = integrity_check(package)
    if not integrity["ok"]:
        raise ValueError("package integrity failed: " + "; ".join(integrity["problems"]))
    gates = report.get("gates", [])
    try:
        evidence = _read_json(package / "evidence.json")
        required_gates = _required_gate_names(evidence, integrity["manifest"])
        raw_attestations = report.get("attestations")
        if not isinstance(raw_attestations, dict):
            raise ValueError("qualification report does not contain raw attestation records")
        recomputed = qualify_package(package, _attestations_override=raw_attestations)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"package evidence is not releasable: {exc}") from exc
    if report != recomputed:
        raise ValueError("qualification report does not match recomputed policy from package and raw attestations")
    gate_names = [gate.get("name") for gate in gates if isinstance(gate, dict)] \
        if isinstance(gates, list) else []
    if (report.get("schema") != "arena-qualification-report/1"
            or report.get("qualified") is not True
            or report.get("status") != "QUALIFIED"
            or report.get("integrity") != "PASS"
            or report.get("blocked_reasons") != []
            or not isinstance(gates, list) or not gates
            or any(not isinstance(gate, dict) or gate.get("status") != "PASS"
                   or not isinstance(gate.get("detail"), str) or not gate["detail"].strip()
                   for gate in gates)
            or len(gate_names) != len(set(gate_names))
            or set(gate_names) != required_gates
            or not _valid_delivery_contract(integrity["manifest"].get("delivery_requirements"))
            or report.get("manifest_sha256") != integrity["manifest_sha256"]):
        raise ValueError("qualification report is blocked, incomplete, or does not bind to this package manifest")

    package = package.resolve()
    if out_dir.exists() or out_dir.is_symlink():
        raise ValueError(f"release output already exists: {out_dir}")
    out_dir = out_dir.resolve()
    if out_dir == package:
        raise ValueError("release output must differ from the preview package")
    try:
        out_dir.relative_to(package)
    except ValueError:
        pass
    else:
        raise ValueError("release output cannot be inside the preview package")
    if out_dir.exists() or out_dir.is_symlink():
        raise ValueError(f"release output already exists: {out_dir}")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.stage-", dir=out_dir.parent))
    try:
        stage.rmdir()
        shutil.copytree(package, stage)
        staged_integrity = integrity_check(stage)
        if (not staged_integrity["ok"]
                or staged_integrity["manifest_sha256"] != integrity["manifest_sha256"]):
            raise ValueError("staged release copy failed integrity recheck")
        os.replace(stage, out_dir)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        raise
    return {
        "schema": "arena-release-receipt/1",
        "status": "RELEASED",
        "package": str(out_dir),
        "manifest_sha256": integrity["manifest_sha256"],
        "qualification_sha256": _sha256(qualification_path.read_bytes()),
    }
