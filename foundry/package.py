"""Package assembly: validated spec -> reproducible release bundle.

Bundle layout (all paths relative to the bundle root):
  spec.json            the exact spec bytes the package was built from
  sql/01_world.sql     collision-failing world migration
  sql/99_rollback.sql  guarded refusal until row ownership is tracked
  dbc/<table>.delta.json  DBC delta (only when the spec needs dbc rows)
  server/...           generated C++ module (when the spec declares one)
  addon/...            generated addon (when the spec declares one)
  conf/...             generated config (when the spec declares keys)
  README.md            generated human summary + acceptance table
  manifest.json        sha256 per file + patch cost + versions
  evidence.json        per-check verdicts + evidence levels earned
  compatibility.json   adapter-contract dry run against the fake host

Reproducibility: building the same spec twice yields identical bytes for
every file except manifest.json's optional ``generated_utc`` stamp.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Dict, Optional

from . import ADAPTER_VERSION, SPEC_VERSION
from . import generate
from .adapter import FakeHost
from .spec import (ValidationResult, allocate_ids, asset_evidence_matches,
                   delivery_requirements, patch_cost)

EVIDENCE_LEVELS = (
    "ARENA-STATIC", "ARENA-SYNTHETIC", "ARENA-DB", "ARENA-COMPILE",
    "ARENA-PACK", "CAMPAIGN-ADMISSION", "CAMPAIGN-LIVE",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rename_directory_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish a directory without replacing a concurrently-created path."""
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except AttributeError as exc:
        raise OSError(errno.ENOSYS, "atomic no-replace directory publication is unavailable") from exc
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p,
                          ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    at_fdcwd, rename_noreplace = -100, 1
    result = renameat2(at_fdcwd, os.fsencode(source), at_fdcwd,
                       os.fsencode(destination), rename_noreplace)
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def _matching_compile_qualification(spec: dict, files: dict) -> Optional[dict]:
    """Return a pinned-core record only when it qualifies these exact sources."""
    if not spec.get("module"):
        return None
    repo = Path(__file__).resolve().parents[1]
    qualifications = repo / "foundry" / "qualifications"
    record_path = qualifications / f"{spec['name']}-azerothcore-module.json"
    if not record_path.is_file():
        record_path = qualifications / "azerothcore-module.json"
    pin_path = repo / "foundry" / "core-pins.json"
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        pin = json.loads(pin_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    subject = record.get("subject", {})
    module_name = f"mod-{spec['name']}"
    source_prefix = f"server/{module_name}/src/"
    actual_sources = {
        rel: _sha256(data)
        for rel, data in files.items()
        if rel.startswith(source_prefix) and Path(rel).suffix in (".cpp", ".h")
    }
    expected_revision = pin.get("azerothcore_wotlk", {}).get("revision")
    if (record.get("schema") != "arena-qualification/1"
            or record.get("qualification") != "ARENA-COMPILE"
            or record.get("status") != "PASS"
            or subject.get("spec") != spec.get("name")
            or subject.get("module") != module_name
            or record.get("environment", {}).get("core_revision") != expected_revision
            or not actual_sources
            or subject.get("source_sha256") != actual_sources):
        return None
    return record


def build_package(spec: dict, result: ValidationResult, out_dir: Path, *,
                  allow_unverified: bool = False, stamp: bool = False,
                  reserved=None, asset_index=None,
                  host: Optional[FakeHost] = None) -> dict:
    """Assemble the bundle. Raises ValueError if validation failed closed."""
    if not result.ok and not (
            allow_unverified
            and result.essential_blocked == ["asset-verification"]
            and result.verdicts.get("asset-verification") == "SKIPPED"):
        raise ValueError(
            "validation failed closed: " + "; ".join(result.essential_blocked))
    if out_dir.is_symlink():
        raise ValueError(f"package output cannot be a symlink: {out_dir}")
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ValueError(f"package output already exists: {out_dir}")
    try:
        out_dir.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"cannot create package output parent: {exc}") from exc
    files: Dict[str, bytes] = {}

    def put(rel: str, content) -> None:
        if not isinstance(rel, str):
            raise ValueError("generated package path must be a string")
        path = PurePosixPath(rel)
        if (path.is_absolute() or not path.parts or ".." in path.parts
                or path.as_posix() != rel or "\\" in rel):
            raise ValueError(f"unsafe generated package path: {rel!r}")
        if rel in files or any(existing.startswith(rel + "/") or rel.startswith(existing + "/")
                               for existing in files):
            raise ValueError(f"generated package path collision: {rel}")
        files[rel] = content.encode() if isinstance(content, str) else content

    put("spec.json", generate.spec_json(spec))
    put("sql/01_world.sql", generate.world_sql(spec, reserved=reserved))
    put("sql/99_rollback.sql", generate.rollback_sql(spec, reserved=reserved))
    delta = generate.dbc_delta(spec, reserved=reserved)
    baseline = {
        "schema": "arena-id-baseline/1",
        "reserved_ids": {table: sorted(values)
                         for table, values in sorted((reserved or {}).items())},
    }
    baseline_bytes = (json.dumps(baseline, indent=2, sort_keys=True) + "\n").encode()
    baseline_sha256 = _sha256(baseline_bytes)
    put("id-baseline.json", baseline_bytes)
    put("id-allocation.json", json.dumps({
        "schema": "arena-id-allocation/1",
        "spec": spec["name"],
        "package_version": spec.get("version"),
        "baseline_sha256": baseline_sha256,
        "allocations": allocate_ids(spec, reserved=reserved),
    }, indent=2, sort_keys=True) + "\n")
    if delta:
        put(f"dbc/{delta['table']}.delta.json",
            json.dumps(delta, indent=2, sort_keys=True) + "\n")
    for rel, content in (generate.module_cpp(spec) or {}).items():
        put(rel, content)
    for rel, content in (generate.addon_files(spec) or {}).items():
        put(rel, content)
    for rel, content in generate.characters_sql_files(spec).items():
        put(rel, content)
    for rel, content in generate.protocol_files(spec).items():
        put(rel, content)
    for rel, content in generate.config_files(spec).items():
        put(rel, content)
    compile_qualification = _matching_compile_qualification(spec, files)
    put("README.md", _readme(spec, result, compile_qualification))

    # compatibility: dry-run the adapter contract against a host
    ids = allocate_ids(spec, reserved=reserved)
    by_table: Dict[str, list] = {}
    for kind, (spec_key, table, _req) in _kind_tables().items():
        for obj in spec.get(spec_key) or []:
            if isinstance(obj, dict) and obj.get("key") in ids:
                by_table.setdefault(table, []).append(ids[obj["key"]])
    unverified = _unverified_refs(spec, asset_index=asset_index)
    requirements = delivery_requirements(spec)
    available_artifacts = []
    if "sql/01_world.sql" in files and any(
            spec.get(spec_key) for spec_key, _table, _req in _kind_tables().values()):
        available_artifacts.extend(({"kind": "world_sql"}, {"kind": "data_migration"}))
    if any(path.startswith("server/") for path in files):
        available_artifacts.append({"kind": "server_module"})
    if any(path.startswith("addon/") for path in files):
        available_artifacts.append({"kind": "addon_files"})
    for rel in files:
        if rel.startswith("sql/characters/") and rel.endswith(".sql") and "/99_" not in rel:
            available_artifacts.append({"kind": "characters_sql", "table": Path(rel).stem})
    if any(path.startswith("conf/") or path.endswith(".conf.dist") for path in files):
        available_artifacts.append({"kind": "config"})
    for path, content in files.items():
        if path.startswith("dbc/") and path.endswith(".delta.json"):
            table = json.loads(content.decode("utf-8")).get("table")
            available_artifacts.append({"kind": "client_dbc", "table": table})
    host = host or FakeHost(reserved_ids=reserved)
    intent = {
        "idempotency_key": f"{spec['name']}-{spec['version']}",
        "kind": "dry_run",
        "allow_unverified": allow_unverified,
        "payload": {
            "patch_cost": patch_cost(spec),
            "feature_spec": spec,
            "delivery_requirements": requirements,
            "available_artifacts": available_artifacts,
            "ids": by_table,
            "schema_version": "1.0.0",
            "unverified": unverified,
        },
    }
    receipt = host.admit(intent)
    compat = {"adapter_version": ADAPTER_VERSION, "receipt": receipt,
              "host_snapshot_hash": host.snapshot_hash()}

    evidence = {
        "spec": spec["name"],
        "spec_version": spec.get("spec_version"),
        "package_version": spec.get("version"),
        "validation": result.to_dict(),
        "patch_cost": patch_cost(spec),
        "delivery_requirements": requirements,
        "allow_unverified": allow_unverified,
        "unverified_asset_refs": unverified,
        "lifecycle": {
            "phase": "BUILD",
            "status": "PREVIEW" if (not result.ok or allow_unverified) else "BUILT_UNQUALIFIED",
            "integrity": "NOT_RUN",
            "qualification": "NOT_RUN",
            "release": "BLOCKED",
            "installable": False,
        },
        "acceptance": [
            {"statement": item.get("check", str(item)) if isinstance(item, dict) else str(item),
             "expected": item.get("expected") if isinstance(item, dict) else None,
             "status": "NOT_ASSESSED",
             "evidence": None}
            for item in (spec.get("acceptance") or [])
        ],
        "compile_qualification": compile_qualification,
        "generated_scope": {
            "sql_migrations": "generated; schema/application not DB-qualified",
            "rollback": "generated; rollback not DB-qualified",
            "cpp_module": (
                "server-authoritative daily-counter protocol and persistent ledger logic; "
                "compiled against the pinned core but not runtime-qualified"
                if compile_qualification and spec.get("addon_protocol") else
                "server-authoritative daily-counter protocol and persistent ledger logic; "
                "not compiled or runtime-qualified"
                if spec.get("addon_protocol") else
                "registration skeleton; compiled against the pinned core, not feature-complete"
                if compile_qualification else
                "registration skeleton only; not compiled or feature-complete"),
            "addon": (
                "functional protocol UI and request client; not tested in the 3.3.5a game client"
                if spec.get("addon_protocol") else
                "presentation placeholder only; not client-runtime tested"),
            "recipes": "descriptive dependency comments; no executable profession recipe",
            "dbc_delta": "narrow Item.dbc delta only when explicitly requested; no MPQ assembly",
            "asset_ids": "unverified unless supported by local source evidence",
        },
        "evidence_levels": {
            "ARENA-STATIC": "spec validation executed; see per-check verdicts above",
            "ARENA-SYNTHETIC": "NOT_ASSESSED by package build; run repository tests separately",
            "ARENA-DB": "pending (requires disposable compatible database)",
            "ARENA-COMPILE": (
                f"PASS; pinned-core qualification record: {compile_qualification['environment']['run_url']}"
                if compile_qualification else
                "pending (module not compiled against a pinned core)"),
            "ARENA-PACK": "bundle assembled; run foundry verify before claiming integrity qualification",
            "CAMPAIGN-ADMISSION": "pending (needs the real Campaign Party baseline)",
            "CAMPAIGN-LIVE": "pending (needs client + Playerbots)",
        },
    }

    manifest = {
        "schema": "arena-foundry-manifest/1",
        "spec": spec["name"],
        "package_version": spec.get("version"),
        "spec_version": spec.get("spec_version"),
        "client_build": "3.3.5a.12340",
        "patch_cost": patch_cost(spec),
        "delivery_requirements": requirements,
        "id_baseline_sha256": baseline_sha256,
        "files": {rel: {"sha256": _sha256(data), "size": len(data)}
                  for rel, data in sorted(files.items())},
        "summary": {"files": len(files),
                    "bytes": sum(len(d) for d in files.values())},
    }
    if stamp:
        from datetime import datetime, timezone
        manifest["generated_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    put("evidence.json", json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    put("compatibility.json", json.dumps(compat, indent=2, sort_keys=True) + "\n")
    manifest["metadata_files"] = {
        rel: {"sha256": _sha256(files[rel]), "size": len(files[rel])}
        for rel in ("evidence.json", "compatibility.json")
    }
    put("manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    stage = None
    try:
        stage = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.stage-",
                                      dir=out_dir.parent))
        for rel, data in sorted(files.items()):
            target = stage.joinpath(*PurePosixPath(rel).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        _rename_directory_noreplace(stage, out_dir)
    except OSError as exc:
        raise ValueError(f"atomic package publication failed: {exc}") from exc
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
    return manifest


def _kind_tables():
    from .spec import KINDS
    return KINDS


def _unverified_refs(spec: dict, *, asset_index=None) -> list:
    out = []
    for kind, (spec_key, _table, _req) in _kind_tables().items():
        for obj in spec.get(spec_key) or []:
            if not isinstance(obj, dict):
                continue
            for field_name in ("display_id", "model_id", "spell_id", "item_id",
                               "visual_id", "icon_id"):
                ref = obj.get(field_name)
                if ref is None:
                    continue
                value = ref.get("value") if isinstance(ref, dict) else ref
                evidence = ref.get("evidence") if isinstance(ref, dict) else None
                if (not isinstance(value, int) or isinstance(value, bool)
                        or not asset_evidence_matches(kind, field_name, value,
                                                      evidence, asset_index)):
                    out.append(f"{obj.get('key')}.{field_name}={value}")
    return out


def _readme(spec: dict, result: ValidationResult,
            compile_qualification: Optional[dict] = None) -> str:
    compile_status = (
        f"ARENA-COMPILE qualified for the exact generated module sources against "
        f"the pinned core ({compile_qualification['environment']['run_url']})"
        if compile_qualification else "ARENA-COMPILE pending")
    has_world_content = any(spec.get(key) for key in
                            ("items", "spells", "quests", "creatures", "gameobjects",
                             "vendors", "recipes", "events"))
    module_dir = f"mod-{spec['name']}"
    install_commands = []
    if has_world_content:
        install_commands.append("mysql -u acore -p acore_world < sql/01_world.sql")
    protocol = spec.get("addon_protocol")
    if isinstance(protocol, dict):
        table = protocol["claims_table"]
        install_commands.append(
            f"mysql -u acore -p acore_characters < sql/characters/{table}.sql")
        install_commands.extend((
            f"cp -a server/{module_dir} /path/to/azerothcore/modules/",
            f"cp -a addon/{''.join(p.capitalize() for p in spec['name'].split('-'))} "
            "/path/to/WotLK/Interface/AddOns/",
            "# Merge the generated module config and AddonChannel = 1 into worldserver.conf.",
            "# Reconfigure/rebuild AzerothCore with the module enabled, then restart worldserver.",
        ))
    elif spec.get("module"):
        install_commands.append(f"cp -a server/{module_dir} /path/to/azerothcore/modules/")
    if not install_commands:
        install_commands.append("# No install-time database or runtime artifacts are declared.")
    rollback_commands = []
    if has_world_content:
        rollback_commands.append(
            "# World SQL rollback is intentionally refused: generated rows have no durable ownership ledger.")
        rollback_commands.append(
            "# Preserve the world rows; manual cleanup requires independently proven ownership and review.")
    if isinstance(protocol, dict):
        rollback_commands.extend((
            "# Disable the module, stop worldserver, then remove its module and addon folders.",
            f"# Preserve acore_characters.{protocol['claims_table']} to retain user progress.",
            "# Purge persistent state only as a separate reviewed action after backup.",
        ))
    if not rollback_commands:
        rollback_commands.append("# No persistent/runtime artifacts are declared.")
    lines = [
        f"# {spec.get('title', spec['name'])}",
        "",
        f"Generated by Arena Foundry from spec `{spec['name']}` v{spec.get('version')} "
        f"(spec_version {spec.get('spec_version')}).",
        "",
        f"| | |",
        f"|---|---|",
        f"| **Patch cost** | {patch_cost(spec)} |",
        f"| **Validation** | {'PASS' if result.ok else 'see evidence.json'} |",
        f"| **Evidence** | Per-check validation is in evidence.json; run repository tests and `foundry verify` separately. "
        f"ARENA-DB pending; {compile_status}; CAMPAIGN-* pending |",
        "",
        "## Files",
        "",
        "```",
        "spec.json            the spec this package was built from",
        "id-baseline.json     reserved IDs used for conflict-aware allocation",
        "id-allocation.json   deterministic table/key -> ID assignment",
        "sql/01_world.sql     generated world migration (when world objects are declared)",
        "sql/99_rollback.sql  guarded refusal; no automatic world rollback without ownership proof",
        "sql/characters/     characters-DB migrations; retained state is not dropped on rollback",
        "server/             generated AzerothCore module sources, loader, and config (when declared)",
        "addon/              generated Interface 30300 addon (when declared)",
        "protocol/           machine-readable protocol contract (when declared)",
        "manifest.json        sha256 per file",
        "evidence.json        verdicts + evidence levels",
        "compatibility.json   adapter dry run",
        "```",
        "",
        generate.acceptance_md(spec).rstrip(),
        "",
        "## Qualification warning",
        "",
        "This bundle is an engineering output, not evidence that gameplay acceptance passed. "
        "Check `evidence.json`: generated acceptance items start as NOT_ASSESSED. Do not install "
        "until the applicable database, compile, asset and downstream checks are independently "
        "qualified and recorded.",
        "",
        "## Integration candidate export (non-installing, non-authorizing)",
        "",
        "For review or integration testing, export the integrity-checked package without changing "
        "its qualification or release status:",
        "",
        "```bash",
        "python3 tools/foundry.py export-candidate /path/to/package --out /tmp/feature-candidate.zip",
        "```",
        "",
        "The ZIP contains `CANDIDATE.json` marking `installable: false` and `release_authorized: false`. "
        "This path does not admit, install, qualify, or release the package and does not bypass the "
        "production release gate.",
        "",
        "## Install (only after qualification)",
        "",
        "Run the following in order after inspecting the ID baseline and qualification evidence:",
        "",
        "```bash",
        *install_commands,
        "```",
        "",
        "## Rollback",
        "",
        "Disable behavior before removing runtime files. Persistent character state is retained by default.",
        "",
        "```bash",
        *rollback_commands,
        "```",
        "",
    ]
    return "\n".join(lines)
