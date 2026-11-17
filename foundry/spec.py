"""Versioned feature-spec schema and fail-closed validation.

A spec is a JSON document (``spec_version: 1``) describing one feature as
composable objects. The validator never guesses: unknown keys, dangling
references, out-of-range IDs and unverified asset IDs are reported as
structured problems with a verdict per check. A package may only be built
when every *essential* check passes; ``NOT_ASSESSED`` (e.g. an asset ID
nobody has confirmed against real client data) blocks the build unless the
caller explicitly passes ``allow_unverified=True`` — and that choice is
recorded in the package evidence.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import ADAPTER_VERSION, SPEC_VERSION
from .ids import TABLE_LIMITS, check_id

# Verdicts. Fail-closed: only PASS counts as good.
PASS = "PASS"
FAIL = "FAIL"
SKIPPED = "SKIPPED"
NOT_ASSESSED = "NOT_ASSESSED"

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

TOP_LEVEL_KEYS = {
    "spec_version", "name", "version", "title", "description",
    "id_range", "requires", "items", "spells", "quests", "creatures",
    "gameobjects", "vendors", "recipes", "events", "config",
    "addon", "module", "dependencies", "acceptance", "notes", "delivery",
}
# object kind -> (spec key, id table it allocates from, required fields)
KINDS = {
    "item": ("items", "item_template", ("key", "name")),
    "spell": ("spells", "spell_dbc", ("key", "name")),
    "quest": ("quests", "quest_template", ("key", "title")),
    "creature": ("creatures", "creature_template", ("key", "name")),
    "gameobject": ("gameobjects", "gameobject_template", ("key", "name")),
    "vendor": ("vendors", "npc_vendor", ("key", "creature_key")),
    "recipe": ("recipes", "spell_dbc", ("key", "result_key")),
    "event": ("events", "game_event", ("key", "name")),
}
# These are relationships/compositions, not rows with their own primary ID.
# Vendor stock is keyed by creature+item; recipes are represented by their
# result/spell relationship. Their stable `key` is for references only.
NON_ID_KINDS = {"vendor", "recipe"}
# tables the generators may emit SQL for (subset of catalog/world_tables.txt;
# the full allowlist is enforced against that file at validation time).
SQL_TABLES = {
    "item_template", "spell_dbc", "quest_template", "creature_template",
    "creature_template_model", "creature", "gameobject_template",
    "gameobject", "npc_vendor", "game_event", "conditions",
    "creature_loot_template", "gameobject_loot_template", "page_text",
    "smart_scripts", "creature_text",
}


@dataclass
class Problem:
    check: str
    verdict: str
    message: str

    def to_dict(self) -> dict:
        return {"check": self.check, "verdict": self.verdict, "message": self.message}


@dataclass
class ValidationResult:
    spec_name: str
    problems: List[Problem] = field(default_factory=list)

    @property
    def verdicts(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for p in self.problems:
            # worst verdict wins per check
            rank = {PASS: 0, SKIPPED: 1, NOT_ASSESSED: 2, FAIL: 3}
            if p.check not in out or rank[p.verdict] > rank[out[p.check]]:
                out[p.check] = p.verdict
        return out

    @property
    def ok(self) -> bool:
        return all(v == PASS for v in self.verdicts.values()) and bool(self.verdicts)

    @property
    def essential_blocked(self) -> List[str]:
        return [c for c, v in self.verdicts.items() if v != PASS]

    def to_dict(self) -> dict:
        return {
            "spec": self.spec_name,
            "ok": self.ok,
            "verdicts": self.verdicts,
            "problems": [p.to_dict() for p in self.problems],
        }


class SpecError(Exception):
    """The spec file could not be read or is not JSON at all (exit 2)."""


def load_spec(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise SpecError(f"{path}: cannot read ({exc})") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SpecError(f"{path}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise SpecError(f"{path}: spec must be a JSON object")
    return data


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_spec(spec: dict, *, world_tables=None, reserved=None,
                  allow_unverified: bool = False) -> ValidationResult:
    """Validate *spec*. ``world_tables``/``reserved`` are optional allowlists;
    when omitted, the repo catalog files are used if present."""
    invalid_root = not isinstance(spec, dict)
    if invalid_root:
        spec = {}
        spec_name = "<invalid>"
    else:
        spec_name = str(spec.get("name", "<unnamed>"))
    result = ValidationResult(spec_name=spec_name)
    add = result.problems.append
    if invalid_root:
        add(Problem("schema", FAIL, "spec must be a JSON object"))

    # --- schema -----------------------------------------------------------
    if spec.get("spec_version") != SPEC_VERSION:
        add(Problem("schema", FAIL,
                    f"spec_version must be {SPEC_VERSION}, got {spec.get('spec_version')!r}"))
    unknown = sorted(set(spec) - TOP_LEVEL_KEYS)
    if unknown:
        add(Problem("schema", FAIL, f"unknown top-level keys: {', '.join(unknown)}"))
    name = spec.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        add(Problem("schema", FAIL, f"name must match {NAME_RE.pattern}, got {name!r}"))
    version = spec.get("version")
    if not isinstance(version, str) or not VERSION_RE.match(version):
        add(Problem("schema", FAIL, f"version must be semver X.Y.Z, got {version!r}"))
    title = spec.get("title")
    if not isinstance(title, str) or not title.strip():
        add(Problem("schema", FAIL, "title must be a non-empty string"))
    for field_name in ("requires", "addon", "module"):
        value = spec.get(field_name)
        if value is not None and not isinstance(value, dict):
            add(Problem("schema", FAIL, f"{field_name} must be an object"))
    if spec.get("config") is not None and not isinstance(spec.get("config"), dict):
        add(Problem("schema", FAIL, "config must be an object"))
    delivery = spec.get("delivery")
    if delivery is not None:
        allowed_delivery_keys = {"client_dbc_tables", "server_dbc_tables", "characters_sql_tables"}
        if not isinstance(delivery, dict):
            add(Problem("schema", FAIL, "delivery must be an object"))
        else:
            unknown_delivery = sorted(set(delivery) - allowed_delivery_keys)
            if unknown_delivery:
                add(Problem("schema", FAIL,
                            f"unknown delivery keys: {', '.join(unknown_delivery)}"))
            for table_key in allowed_delivery_keys:
                tables = delivery.get(table_key, [])
                if (not isinstance(tables, list)
                        or any(not isinstance(table, str) or not table.strip() for table in tables)
                        or len(set(tables)) != len(tables)):
                    add(Problem("schema", FAIL,
                                f"delivery.{table_key} must be a unique list of non-empty strings"))
            client_tables = set(delivery.get("client_dbc_tables", [])) \
                if isinstance(delivery.get("client_dbc_tables", []), list) else set()
            server_tables = set(delivery.get("server_dbc_tables", [])) \
                if isinstance(delivery.get("server_dbc_tables", []), list) else set()
            if client_tables != server_tables:
                add(Problem(
                    "delivery", FAIL,
                    "client_dbc_tables and server_dbc_tables must declare "
                    "the same tables for parity"))
    id_range = spec.get("id_range") or {}
    if not isinstance(id_range, dict):
        id_range = {}
    lo, hi = id_range.get("start"), id_range.get("end")
    if not (_is_int(lo) and _is_int(hi) and 0 <= lo <= hi):
        add(Problem("schema", FAIL, f"id_range must be {{start,end}} with start<=end, got {id_range!r}"))
        lo = hi = None

    # --- objects, references, IDs ----------------------------------------
    keys: Dict[str, str] = {}          # key -> kind
    allocated: Dict[str, int] = {}     # key -> numeric id
    unverified: List[str] = []
    try:
        allocated = allocate_ids(spec, reserved=reserved)
    except ValueError as exc:
        add(Problem("ids", FAIL, str(exc)))

    ids_by_table: Dict[str, Dict[int, str]] = {}
    for kind, (spec_key, table, required) in KINDS.items():
        objs = spec.get(spec_key) or []
        if not isinstance(objs, list):
            add(Problem("schema", FAIL, f"{spec_key} must be a list"))
            continue
        for obj in objs:
            if not isinstance(obj, dict):
                add(Problem("schema", FAIL, f"{spec_key} entry must be an object: {obj!r}"))
                continue
            key = obj.get("key")
            where = f"{kind}:{key or obj!r}"
            for req in required:
                if req not in obj:
                    add(Problem("references", FAIL, f"{where}: missing required field '{req}'"))
            if "dbc_row" in obj and not isinstance(obj["dbc_row"], bool):
                add(Problem("schema", FAIL, f"{where}: dbc_row must be boolean"))
            if not isinstance(key, str) or not key:
                continue
            if key in keys:
                add(Problem("references", FAIL, f"duplicate key '{key}'"))
                continue
            keys[key] = kind
            explicit = obj.get("id")
            if explicit is not None and not _is_int(explicit):
                add(Problem("ids", FAIL, f"{where}: id must be an integer"))
                continue
            nid = allocated.get(key)
            if nid is None:
                continue
            previous_key = ids_by_table.setdefault(table, {}).get(nid)
            if previous_key is not None:
                add(Problem("ids", FAIL,
                            f"{where}: id {nid} duplicates {table} id assigned to '{previous_key}'"))
            else:
                ids_by_table[table][nid] = key
            # Per-table range policy: a table whose ceiling is below the
            # spec range (game_event: tinyint 0-255) uses its own window.
            from .ids import TABLE_LIMITS as _TL
            ceiling = _TL.get(table, (0, 2**31 - 1, ""))[1]
            if lo is not None and ceiling >= lo and not (lo <= nid <= hi):
                add(Problem("ids", FAIL,
                            f"{where}: id {nid} outside spec range [{lo}, {hi}]"))
            problem = check_id(table, nid)
            if problem:
                add(Problem("ids", FAIL, f"{where}: {problem}"))
            if reserved and nid in reserved.get(table, set()):
                add(Problem("ids", FAIL, f"{where}: id {nid} collides with baseline {table}"))
            # asset references must be verified or flagged
            for ref_field in ("display_id", "model_id", "spell_id", "item_id",
                              "visual_id", "icon_id"):
                ref = obj.get(ref_field)
                if isinstance(ref, dict):
                    if not _is_int(ref.get("value")):
                        add(Problem("references", FAIL,
                                    f"{where}: {ref_field}.value must be an integer"))
                    if not isinstance(ref.get("verified"), bool):
                        add(Problem("references", FAIL,
                                    f"{where}: {ref_field}.verified must be boolean"))
                    elif not ref["verified"]:
                        unverified.append(f"{where}.{ref_field}={ref.get('value')}")
                elif ref is not None and not _is_int(ref):
                    add(Problem("references", FAIL,
                                f"{where}: {ref_field} must be an int or {{value, verified}}"))

    # cross-references between objects; defensively skip malformed entries
    # after recording their schema errors above (never leak AttributeError).
    def dict_objects(field_name):
        value = spec.get(field_name)
        return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []

    for obj in dict_objects("vendors"):
        if obj.get("creature_key") not in keys:
            add(Problem("references", FAIL,
                        f"vendor:{obj.get('key')}: creature_key {obj.get('creature_key')!r} not in spec"))
        rows = obj.get("rows") or []
        if not isinstance(rows, list):
            add(Problem("schema", FAIL, f"vendor:{obj.get('key')}: rows must be a list"))
            continue
        for row in rows:
            if not isinstance(row, dict):
                add(Problem("schema", FAIL, f"vendor:{obj.get('key')}: row must be an object"))
                continue
            if row.get("item_key") not in keys:
                add(Problem("references", FAIL,
                            f"vendor:{obj.get('key')}: item_key {row.get('item_key')!r} not in spec"))
    for obj in dict_objects("recipes"):
        if obj.get("result_key") not in keys:
            add(Problem("references", FAIL,
                        f"recipe:{obj.get('key')}: result_key {obj.get('result_key')!r} not in spec"))
        ingredients = obj.get("ingredients") or []
        if not isinstance(ingredients, list):
            add(Problem("schema", FAIL, f"recipe:{obj.get('key')}: ingredients must be a list"))
            continue
        for ing in ingredients:
            if not isinstance(ing, dict):
                add(Problem("schema", FAIL, f"recipe:{obj.get('key')}: ingredient must be an object"))
                continue
            if ing.get("item_key") not in keys:
                add(Problem("references", FAIL,
                            f"recipe:{obj.get('key')}: ingredient {ing.get('item_key')!r} not in spec"))
    for obj in dict_objects("quests"):
        required_items = obj.get("requires_items") or []
        if not isinstance(required_items, list):
            add(Problem("schema", FAIL, f"quest:{obj.get('key')}: requires_items must be a list"))
            continue
        for req in required_items:
            if req not in keys:
                add(Problem("references", FAIL,
                            f"quest:{obj.get('key')}: requires_items {req!r} not in spec"))

    # --- SQL tables --------------------------------------------------------
    if world_tables is not None:
        for kind, (spec_key, table, _req) in KINDS.items():
            if spec.get(spec_key) and table not in world_tables:
                add(Problem("tables", FAIL, f"{table} (for {kind}) not in world_tables.txt"))
        if not any(p.check == "tables" and p.verdict == FAIL for p in result.problems):
            add(Problem("tables", PASS, "all emitted tables exist in world_tables.txt"))
    else:
        add(Problem("tables", SKIPPED, "no world_tables allowlist supplied"))

    # --- structured delivery contract + display-only summary --------------
    requirements = delivery_requirements(spec)
    if not any(p.check == "delivery" and p.verdict == FAIL for p in result.problems):
        add(Problem("delivery", PASS,
                    f"{len(requirements['artifacts'])} required artifact(s), "
                    f"{len(requirements['capabilities'])} host capability gate(s)"))
    patch = patch_cost(spec)
    add(Problem("patch-cost", PASS, f"display summary only: {patch}"))

    # --- unverified assets (fail-closed) ----------------------------------
    if unverified:
        verdict = NOT_ASSESSED if not allow_unverified else SKIPPED
        note = "allowed by caller" if allow_unverified else "blocks build"
        add(Problem("asset-verification", verdict,
                    f"{len(unverified)} unverified asset reference(s) ({note}): "
                    + ", ".join(unverified[:8]) + ("..." if len(unverified) > 8 else "")))
    else:
        add(Problem("asset-verification", PASS, "all asset references verified"))

    # --- acceptance --------------------------------------------------------
    acceptance = spec.get("acceptance") or []
    if not isinstance(acceptance, list) or len(acceptance) < 3:
        add(Problem("acceptance", FAIL,
                    "spec needs an acceptance list with at least 3 checks"))
    else:
        malformed_acceptance = False
        for index, check in enumerate(acceptance):
            if isinstance(check, str) and check.strip():
                continue
            if isinstance(check, dict) and isinstance(check.get("check"), str) \
                    and check["check"].strip() and isinstance(check.get("expected"), str):
                continue
            add(Problem("acceptance", FAIL,
                        f"acceptance[{index}] must be a non-empty string or check/expected object"))
            malformed_acceptance = True
        if not malformed_acceptance:
            add(Problem("acceptance", PASS, f"{len(acceptance)} acceptance checks"))

    # --- dependencies ------------------------------------------------------
    deps = spec.get("dependencies") or []
    if not isinstance(deps, list) or not all(isinstance(dep, str) and dep for dep in deps):
        add(Problem("dependencies", FAIL, "dependencies must be a list of non-empty strings"))
    else:
        add(Problem("dependencies", PASS, f"{len(deps)} declared dependencies"))

    if not any(p.check == "schema" and p.verdict == FAIL for p in result.problems):
        add(Problem("schema", PASS, "spec_version, name, version, title, id_range ok"))
    if not any(p.check == "references" and p.verdict == FAIL for p in result.problems):
        add(Problem("references", PASS, "all object references resolve within the spec"))
    if not any(p.check == "ids" and p.verdict == FAIL for p in result.problems):
        add(Problem("ids", PASS, "all ids in range and within per-table limits"))
    return result


def delivery_requirements(spec: dict) -> dict:
    """Return independent artifact and host-capability requirements.

    The summary string returned by :func:`patch_cost` is for display only;
    admission must consume this structured contract instead.
    """
    declared = spec.get("delivery") if isinstance(spec.get("delivery"), dict) else {}

    def string_set(key: str) -> set:
        values = declared.get(key, [])
        if not isinstance(values, list):
            return set()
        return {value for value in values if isinstance(value, str)}

    client_tables = string_set("client_dbc_tables")
    server_tables = string_set("server_dbc_tables")
    items = spec.get("items")
    for obj in items if isinstance(items, list) else []:
        if isinstance(obj, dict) and obj.get("dbc_row"):
            client_tables.add("Item.dbc")
            server_tables.add("Item.dbc")
    spells = spec.get("spells")
    for obj in spells if isinstance(spells, list) else []:
        if isinstance(obj, dict) and obj.get("dbc_row"):
            client_tables.add("Spell.dbc")
            server_tables.add("Spell.dbc")

    artifacts = []

    def artifact(kind: str, *, table: Optional[str] = None, activation: str) -> None:
        entry = {
            "kind": kind,
            "version": str(spec.get("version", "0.0.0")),
            "trust": "generated_unqualified",
            "activation": activation,
        }
        if table is not None:
            entry["table"] = table
        if entry not in artifacts:
            artifacts.append(entry)

    has_world_content = any(spec.get(spec_key) for spec_key, _table, _required in KINDS.values())
    if has_world_content:
        artifact("world_sql", activation="apply_after_schema_qualification")
        artifact("data_migration", activation="apply_after_backup_and_review")
    for table in sorted(string_set("characters_sql_tables")):
        artifact("characters_sql", table=table, activation="apply_after_schema_qualification")
        artifact("data_migration", table=table, activation="apply_after_backup_and_review")
    if spec.get("module"):
        artifact("server_module", activation="compile_before_install")
    if spec.get("addon"):
        artifact("addon_files", activation="copy_after_protocol_review")
    for table in sorted(client_tables):
        artifact("client_dbc", table=table, activation="merge_into_new_patch_after_parity")
    for table in sorted(server_tables):
        artifact("server_dbc", table=table, activation="install_with_matching_client_dbc")
    if client_tables:
        artifact("mpq", activation="build_separate_archive_after_collision_review")
    if spec.get("config") or spec.get("module"):
        artifact("config", activation="install_after_module_review")

    capability_names = set()
    artifact_kinds = {entry["kind"] for entry in artifacts}
    if "world_sql" in artifact_kinds:
        capability_names.add("world_db")
    if "characters_sql" in artifact_kinds:
        capability_names.add("characters_db")
    if "server_module" in artifact_kinds:
        capability_names.add("server_module")
    if "addon_files" in artifact_kinds:
        capability_names.add("addon_channel")
    if "mpq" in artifact_kinds:
        capability_names.update(("mpq_patch", "client_server_dbc_parity"))
    if "config" in artifact_kinds:
        capability_names.add("config_activation")
    capabilities = [{
        "name": name,
        "version": "adapter:" + ADAPTER_VERSION,
        "required": True,
        "trust": "host_claim_and_snapshot_required",
        "activation": "admission_gate",
    } for name in sorted(capability_names)]
    return {"schema": "arena-delivery-requirements/1",
            "artifacts": artifacts, "capabilities": capabilities}


def patch_cost(spec: dict) -> str:
    """Human-readable delivery summary only; never use this for admission."""
    artifacts = {entry["kind"] for entry in delivery_requirements(spec)["artifacts"]}
    needs_dbc = "client_dbc" in artifacts or "mpq" in artifacts
    has_addon = "addon_files" in artifacts
    if needs_dbc and has_addon:
        return "mixed delivery (addon-files + dbc-rows; MPQ patch required)"
    if needs_dbc:
        return "dbc-rows (MPQ patch required)"
    if has_addon:
        return "client-files (addon folder; no MPQ)"
    return "none (server-side only)"


def allocate_ids(spec: dict, *, reserved=None) -> Dict[str, int]:
    """Allocate deterministic, table-aware IDs without internal collisions.

    Explicit IDs are reserved before automatic allocation, so an early
    implicit object cannot steal a later explicit ID. Most objects allocate
    inside the spec's inclusive custom range. Tables whose entire numeric
    domain is smaller (notably ``game_event``) allocate within their own
    table bounds. ``reserved`` maps table names to IDs from an external
    baseline and is honored for automatic allocations; explicit collisions
    are retained for the validator to report as a baseline conflict.
    """
    if not isinstance(spec, dict):
        raise ValueError("spec must be an object")
    id_range = spec.get("id_range") or {}
    if not isinstance(id_range, dict):
        raise ValueError("id_range must be an object")
    range_lo, range_hi = id_range.get("start"), id_range.get("end")
    if not (_is_int(range_lo) and _is_int(range_hi) and 0 <= range_lo <= range_hi):
        raise ValueError("id_range must contain integer start/end with start<=end")
    reserved = reserved or {}
    explicit_by_table: Dict[str, set] = {}
    entries = []
    seen_keys = set()
    for _kind, (spec_key, table, _required) in KINDS.items():
        objects = spec.get(spec_key)
        if objects is None:
            continue
        if not isinstance(objects, list):
            continue  # schema validator emits the structural error
        for obj in objects:
            if not isinstance(obj, dict) or not isinstance(obj.get("key"), str):
                continue
            key = obj["key"]
            if key in seen_keys:
                continue
            seen_keys.add(key)
            if _kind in NON_ID_KINDS:
                if "id" in obj:
                    raise ValueError(f"{_kind}:{key} is a relationship and does not allocate an ID")
                continue
            explicit = obj.get("id")
            if explicit is not None and not _is_int(explicit):
                continue  # schema validator reports malformed ID
            entries.append((key, table, explicit))
            if explicit is not None:
                explicit_by_table.setdefault(table, set()).add(explicit)

    for table, values in explicit_by_table.items():
        if len(values) != sum(1 for _key, t, value in entries if t == table and value is not None):
            raise ValueError(f"duplicate explicit IDs in table {table}")

    out: Dict[str, int] = {}
    used_by_table: Dict[str, set] = {table: set(values)
                                     for table, values in explicit_by_table.items()}
    for key, table, explicit in entries:
        if explicit is not None:
            out[key] = explicit
            continue
        minimum, maximum, _why = TABLE_LIMITS.get(table, (range_lo, range_hi, "spec range"))
        if table not in TABLE_LIMITS or maximum >= range_lo:
            low, high = range_lo, min(range_hi, maximum)
        else:
            low, high = max(minimum, 1), maximum
        occupied = used_by_table.setdefault(table, set()) | set(reserved.get(table, set()))
        candidate = low
        while candidate <= high and candidate in occupied:
            candidate += 1
        if candidate > high:
            raise ValueError(f"no free IDs remain for {table} in [{low}, {high}]")
        out[key] = candidate
        used_by_table[table].add(candidate)
    return out


def table_limits() -> Dict[str, tuple]:
    return dict(TABLE_LIMITS)
