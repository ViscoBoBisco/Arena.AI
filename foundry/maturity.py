"""Structured, artifact-derived maturity requirements for Foundry qualification.

Free-form implementation descriptions are deliberately not policy inputs. The
inventory comes from the delivery contract; behavior requirements are fixed by
artifact kind; observations are derived from packaged bytes or explicit
qualification records.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

SCHEMA = "arena-artifact-maturity/1"

# These are the minimum independently falsifiable behaviors for each emitted
# surface. Adding an artifact kind without defining its requirements is an error.
REQUIRED_BEHAVIORS = {
    "world_sql": ("source_present", "generated", "schema_valid", "migration_tested"),
    "data_migration": ("source_present", "generated", "schema_valid", "migration_tested"),
    "characters_sql": ("source_present", "generated", "schema_valid", "migration_tested"),
    "server_module": ("source_present", "generated", "compiled", "runtime_exercised"),
    "addon_files": ("source_present", "generated", "schema_valid", "runtime_exercised"),
    "client_dbc": ("source_present", "generated", "schema_valid", "asset_verified"),
    "server_dbc": ("source_present", "generated", "schema_valid", "asset_verified"),
    "mpq": ("generated", "schema_valid", "asset_verified"),
    "config": ("source_present", "generated", "schema_valid"),
}


def _identity(artifact: dict[str, Any]) -> str:
    kind = artifact["kind"]
    table = artifact.get("table")
    return f"{kind}:{table}" if table else kind


def expected_artifacts(delivery: Any) -> list[dict[str, Any]]:
    """Validate and normalize the artifact inventory declared by the spec."""
    if (not isinstance(delivery, dict)
            or delivery.get("schema") != "arena-delivery-requirements/1"
            or not isinstance(delivery.get("artifacts"), list)):
        raise ValueError("missing or malformed structured delivery inventory")
    result = []
    seen = set()
    for item in delivery["artifacts"]:
        if not isinstance(item, dict) or item.get("kind") not in REQUIRED_BEHAVIORS:
            raise ValueError(f"unrecognized or malformed artifact kind: {item!r}")
        if item.get("table") is not None and not isinstance(item.get("table"), str):
            raise ValueError(f"malformed artifact table: {item!r}")
        ident = _identity(item)
        if ident in seen:
            raise ValueError(f"duplicate artifact identity: {ident}")
        seen.add(ident)
        result.append({"identity": ident, "kind": item["kind"],
                       **({"table": item["table"]} if "table" in item else {}),
                       "required_behaviors": list(REQUIRED_BEHAVIORS[item["kind"]])})
    return sorted(result, key=lambda item: item["identity"])


def _observed_artifacts(package: Path) -> set[str]:
    """Discover generated surfaces so manifest omission cannot hide an emitter."""
    observed = set()
    world_sql = package / "sql" / "01_world.sql"
    if world_sql.is_file() and any(
            line.lstrip().upper().startswith(("INSERT INTO", "DELETE FROM"))
            for line in world_sql.read_text(encoding="utf-8").splitlines()):
        observed.update(("world_sql", "data_migration"))
    for path in package.rglob("*.sql"):
        if "characters" in path.parts:
            observed.add(f"characters_sql:{path.stem}")
    if any("server" in path.parts and path.suffix in (".cpp", ".h")
           for path in package.rglob("*")):
        observed.add("server_module")
    if any("addon" in path.parts and path.suffix.lower() in (".lua", ".toc")
           for path in package.rglob("*")):
        observed.add("addon_files")
    for path in package.rglob("*.delta.json"):
        if "dbc" in path.parts:
            table = path.name.removesuffix(".delta.json")
            observed.add(f"client_dbc:{table}")
    if any(path.suffix.lower() in (".conf", ".dist") for path in package.rglob("*")):
        observed.add("config")
    if any(path.suffix.lower() == ".mpq" for path in package.rglob("*")):
        observed.add("mpq")
    return observed


def _has_files(package: Path, kind: str, table: Optional[str]) -> bool:
    files = [path for path in package.rglob("*") if path.is_file() and not path.is_symlink()]
    if kind in ("world_sql", "data_migration", "characters_sql"):
        if kind == "characters_sql":
            return any("characters" in path.parts and path.suffix.lower() == ".sql" for path in files)
        return (package / "sql" / "01_world.sql").is_file()
    if kind == "server_module":
        return any("server" in path.parts and path.suffix in (".cpp", ".h") for path in files)
    if kind == "addon_files":
        return any("addon" in path.parts and path.suffix.lower() in (".lua", ".toc") for path in files)
    if kind in ("client_dbc", "server_dbc"):
        return bool(table) and any(
            path.name == f"{table}.delta.json" for path in files)
    if kind == "mpq":
        return any(path.suffix.lower() == ".mpq" for path in files)
    if kind == "config":
        return any(path.suffix.lower() in (".conf", ".dist") for path in files)
    return False


def _external_behavior_passes(records: Any, artifact: dict, behavior: str) -> bool:
    if not isinstance(records, list):
        return False
    for record in records:
        if (isinstance(record, dict)
                and record.get("kind") == "maturity"
                and record.get("artifact_kind") == artifact["kind"]
                and record.get("table") == artifact.get("table")
                and record.get("behavior") == behavior
                and record.get("status") == "PASS"
                and isinstance(record.get("evidence"), str)
                and record["evidence"].strip()):
            return True
    return False


def evaluate(package: Path, delivery: dict, evidence: dict, attestations: dict,
             *, db_pass: bool, compile_pass: bool, assets_pass: bool) -> dict:
    """Evaluate requirements from observed files and qualification evidence.

    User-supplied JSON evidence remains EXTERNAL_UNTRUSTED. This matrix records
    policy outcomes; it is not a cryptographic authorization statement.
    """
    inventory = expected_artifacts(delivery)
    declared = {artifact["identity"] for artifact in inventory}
    observed = _observed_artifacts(package)
    inventory_issues = sorted(observed - declared)
    records = attestations.get("artifacts", [])
    evaluated = []
    for artifact in inventory:
        kind = artifact["kind"]
        present = _has_files(package, kind, artifact.get("table"))
        statuses = {"source_present": "PASS" if present else "BLOCKED",
                    "generated": "PASS" if present else "BLOCKED"}
        for behavior in artifact["required_behaviors"]:
            if behavior in statuses:
                continue
            if behavior in ("schema_valid", "migration_tested"):
                passed = db_pass if kind in ("world_sql", "data_migration", "characters_sql") else \
                    _external_behavior_passes(records, artifact, behavior)
            elif behavior == "compiled":
                passed = compile_pass
            elif behavior == "asset_verified":
                passed = assets_pass
            else:
                passed = _external_behavior_passes(records, artifact, behavior)
            statuses[behavior] = "PASS" if passed else "NOT_ASSESSED"
        evaluated.append({**artifact, "status": statuses})
    return {
        "schema": SCHEMA,
        "trust": "EXTERNAL_UNTRUSTED" if attestations else "LOCAL_REPRODUCED",
        "inventory_issues": inventory_issues,
        "artifacts": evaluated,
    }


def gate_names(delivery: dict) -> set[str]:
    return {f"artifact_maturity:{artifact['identity']}:{behavior}"
            for artifact in expected_artifacts(delivery)
            for behavior in artifact["required_behaviors"]}
