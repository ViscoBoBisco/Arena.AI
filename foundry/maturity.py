"""Structured, artifact-derived maturity requirements for Foundry qualification.

Free-form implementation descriptions are deliberately not policy inputs. The
inventory comes from the delivery contract; behavior requirements are fixed by
artifact kind; observations are derived from packaged bytes or explicit
qualification records.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

SCHEMA = "arena-artifact-maturity/1"
_DBC_TABLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}\.dbc$")
_SQL_TABLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,53}$")

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
    "dbc_delta": ("source_present", "generated", "schema_valid"),
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
        if item["kind"] in ("client_dbc", "server_dbc", "dbc_delta") \
                and (not isinstance(item.get("table"), str)
                     or not _DBC_TABLE_RE.fullmatch(item["table"])):
            raise ValueError(f"invalid DBC table identity: {item!r}")
        if item["kind"] == "characters_sql" or \
                item["kind"] == "data_migration" and item.get("table") is not None:
            if (not isinstance(item.get("table"), str)
                    or not _SQL_TABLE_RE.fullmatch(item["table"])):
                raise ValueError(f"invalid Characters table identity: {item!r}")
        if "requires_install" in item and not isinstance(item.get("requires_install"), bool):
            raise ValueError(f"malformed requires_install flag: {item!r}")
        requires_install = item.get("requires_install", True)
        if item["kind"] == "dbc_delta" and requires_install:
            raise ValueError("DBC delta intermediates must not be installable artifacts")
        if item["kind"] in ("client_dbc", "server_dbc") and not requires_install:
            raise ValueError("materialized client/server DBCs must be required install artifacts")
        ident = _identity(item)
        if ident in seen:
            raise ValueError(f"duplicate artifact identity: {ident}")
        seen.add(ident)
        result.append({"identity": ident, "kind": item["kind"],
                       **({"table": item["table"]} if "table" in item else {}),
                       "requires_install": item.get("requires_install", True),
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
        if "characters" in path.parts and not path.stem.startswith("99_"):
            observed.add(f"characters_sql:{path.stem}")
            observed.add(f"data_migration:{path.stem}")
    if any("server" in path.parts and path.suffix in (".cpp", ".h")
           for path in package.rglob("*")):
        observed.add("server_module")
    if any("addon" in path.parts and path.suffix.lower() in (".lua", ".toc")
           for path in package.rglob("*")):
        observed.add("addon_files")
    for path in package.rglob("*.delta.json"):
        if path.parent.name == "dbc":
            table = path.name.removesuffix(".delta.json")
            observed.add(f"dbc_delta:{table}")
    for side in ("client", "server"):
        for path in (package / side / "dbc").glob("*.dbc") if (package / side / "dbc").is_dir() else ():
            observed.add(f"{side}_dbc:{path.name}")
    if any(path.suffix.lower() == ".mpq" for path in package.rglob("*")):
        observed.add("mpq")
    if any(path.suffix.lower() in (".conf", ".dist") for path in package.rglob("*")):
        observed.add("config")
    return observed


def derive_inventory(source, delivery: dict, *, qualified: Optional[dict] = None,
                     installed: Optional[set[str]] = None) -> list[dict]:
    """Recompute artifact lifecycle state from package paths, not spec prose.

    ``source`` is either a package directory or a mapping of package-relative
    paths to bytes. A delta JSON is an available generated intermediate; it is
    never treated as a materialized client/server DBC or installed output.
    """
    inventory = expected_artifacts(delivery)
    qualified = qualified or {}
    installed = installed or set()

    if isinstance(source, dict):
        paths = set(source)
    else:
        root = Path(source)
        paths = {path.relative_to(root).as_posix() for path in root.rglob("*")
                 if path.is_file() and not path.is_symlink()}

    def exists(path: str) -> bool:
        return path in paths

    def has(artifact: dict) -> bool:
        kind, table = artifact["kind"], artifact.get("table")
        if kind == "world_sql" or kind == "data_migration" and not table:
            return exists("sql/01_world.sql")
        if kind in ("data_migration", "characters_sql"):
            return bool(table) and exists(f"sql/characters/{table}.sql")
        if kind == "server_module":
            return any(path.startswith("server/") and path.endswith((".cpp", ".h"))
                       for path in paths)
        if kind == "addon_files":
            return any(path.startswith("addon/") and path.lower().endswith((".lua", ".toc"))
                       for path in paths)
        if kind == "config":
            return any(path.startswith("conf/") and path.lower().endswith((".conf", ".dist"))
                       for path in paths)
        if kind == "client_dbc":
            return bool(table) and exists(f"client/dbc/{table}")
        if kind == "server_dbc":
            return bool(table) and exists(f"server/dbc/{table}")
        if kind == "dbc_delta":
            return bool(table) and exists(f"dbc/{table}.delta.json")
        if kind == "mpq":
            return any(path.lower().endswith(".mpq") for path in paths)
        return False

    records = []
    for artifact in inventory:
        present = has(artifact)
        identity = artifact["identity"]
        records.append({
            "identity": identity,
            "kind": artifact["kind"],
            **({"table": artifact["table"]} if "table" in artifact else {}),
            "generated": present,
            "available": present,
            "qualified": bool(qualified.get(identity, False)),
            "requires_install": artifact["requires_install"],
            "installed": identity in installed,
        })
    return records


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
             *, world_db_pass: bool, characters_db_pass: dict[str, bool],
             compile_pass: bool, assets_pass: bool,
             qualified_artifacts: Optional[dict[str, bool]] = None) -> dict:
    """Evaluate requirements from observed files and qualification evidence.

    User-supplied JSON evidence remains EXTERNAL_UNTRUSTED. This matrix records
    policy outcomes; it is not a cryptographic authorization statement.
    """
    inventory = expected_artifacts(delivery)
    declared = {artifact["identity"] for artifact in inventory}
    observed = _observed_artifacts(package)
    inventory_issues = sorted(observed - declared)
    computed = derive_inventory(
        package, delivery,
        qualified={"server_module": compile_pass, **(qualified_artifacts or {})})
    claimed = evidence.get("artifact_inventory")
    if claimed is not None:
        def byte_claim(record):
            if not isinstance(record, dict):
                return None
            return (record.get("identity"), record.get("kind"), record.get("table"),
                    record.get("generated"), record.get("available"),
                    record.get("requires_install"), record.get("installed"))
        if (not isinstance(claimed, list)
                or sorted(map(byte_claim, claimed), key=str)
                != sorted(map(byte_claim, computed), key=str)):
            inventory_issues.append("artifact_inventory does not match package bytes")
            inventory_issues.sort()
    state_by_identity = {item["identity"]: item for item in computed}
    records = attestations.get("artifacts", [])
    evaluated = []
    for artifact in inventory:
        kind = artifact["kind"]
        state = state_by_identity[artifact["identity"]]
        present = state["generated"]
        statuses = {"source_present": "PASS" if present else "BLOCKED",
                    "generated": "PASS" if present else "BLOCKED"}
        for behavior in artifact["required_behaviors"]:
            if behavior in statuses:
                continue
            if behavior in ("schema_valid", "migration_tested"):
                if kind == "world_sql":
                    passed = world_db_pass
                elif kind == "characters_sql":
                    passed = characters_db_pass.get(artifact.get("table"), False)
                elif kind == "data_migration":
                    table = artifact.get("table")
                    passed = (characters_db_pass.get(table, False)
                              if table else world_db_pass)
                else:
                    passed = _external_behavior_passes(records, artifact, behavior)
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
        "artifact_inventory": computed,
        "artifacts": evaluated,
    }


def gate_names(delivery: dict) -> set[str]:
    return {f"artifact_maturity:{artifact['identity']}:{behavior}"
            for artifact in expected_artifacts(delivery)
            for behavior in artifact["required_behaviors"]}
