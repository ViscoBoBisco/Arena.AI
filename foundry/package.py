"""Package assembly: validated spec -> reproducible release bundle.

Bundle layout (all paths relative to the bundle root):
  spec.json            the exact spec bytes the package was built from
  sql/01_world.sql     idempotent world migration
  sql/99_rollback.sql  exact rollback
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

import hashlib
import json
from pathlib import Path
from typing import Dict, Optional

from . import ADAPTER_VERSION, SPEC_VERSION
from . import generate
from .adapter import FakeHost
from .spec import ValidationResult, allocate_ids, patch_cost

EVIDENCE_LEVELS = (
    "ARENA-STATIC", "ARENA-SYNTHETIC", "ARENA-DB", "ARENA-COMPILE",
    "ARENA-PACK", "CAMPAIGN-ADMISSION", "CAMPAIGN-LIVE",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_package(spec: dict, result: ValidationResult, out_dir: Path, *,
                  allow_unverified: bool = False, stamp: bool = False,
                  host: Optional[FakeHost] = None) -> dict:
    """Assemble the bundle. Raises ValueError if validation failed closed."""
    if not result.ok and not (allow_unverified and result.essential_blocked == ["asset-verification"]):
        raise ValueError(
            "validation failed closed: " + "; ".join(result.essential_blocked))
    out_dir.mkdir(parents=True, exist_ok=True)
    files: Dict[str, bytes] = {}

    def put(rel: str, content) -> None:
        files[rel] = content.encode() if isinstance(content, str) else content

    put("spec.json", generate.spec_json(spec))
    put("sql/01_world.sql", generate.world_sql(spec))
    put("sql/99_rollback.sql", generate.rollback_sql(spec))
    delta = generate.dbc_delta(spec)
    if delta:
        put(f"dbc/{delta['table']}.delta.json",
            json.dumps(delta, indent=2, sort_keys=True) + "\n")
    for rel, content in (generate.module_cpp(spec) or {}).items():
        put(rel, content)
    for rel, content in (generate.addon_files(spec) or {}).items():
        put(rel, content)
    for rel, content in generate.config_files(spec).items():
        put(rel, content)
    put("README.md", _readme(spec, result))

    # compatibility: dry-run the adapter contract against a host
    ids = allocate_ids(spec)
    by_table: Dict[str, list] = {}
    for kind, (spec_key, table, _req) in _kind_tables().items():
        for obj in spec.get(spec_key) or []:
            if isinstance(obj, dict) and obj.get("key") in ids:
                by_table.setdefault(table, []).append(ids[obj["key"]])
    unverified = _unverified_refs(spec)
    host = host or FakeHost()
    intent = {
        "idempotency_key": f"{spec['name']}-{spec['version']}",
        "kind": "dry_run",
        "allow_unverified": allow_unverified,
        "payload": {
            "patch_cost": patch_cost(spec),
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
        "allow_unverified": allow_unverified,
        "acceptance": [
            {"statement": item.get("check", str(item)) if isinstance(item, dict) else str(item),
             "expected": item.get("expected") if isinstance(item, dict) else None,
             "status": "NOT_ASSESSED",
             "evidence": None}
            for item in (spec.get("acceptance") or [])
        ],
        "generated_scope": {
            "sql_migrations": "generated; schema/application not DB-qualified",
            "rollback": "generated; rollback not DB-qualified",
            "cpp_module": "registration skeleton only; not compiled or feature-complete",
            "addon": "presentation placeholder only; not client-runtime tested",
            "recipes": "descriptive dependency comments; no executable profession recipe",
            "dbc_delta": "narrow Item.dbc delta only when explicitly requested; no MPQ assembly",
            "asset_ids": "unverified unless supported by local source evidence",
        },
        "evidence_levels": {
            "ARENA-STATIC": "spec validation executed; see per-check verdicts above",
            "ARENA-SYNTHETIC": "NOT_ASSESSED by package build; run repository tests separately",
            "ARENA-DB": "pending (requires disposable compatible database)",
            "ARENA-COMPILE": "pending (module not compiled against a pinned core)",
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

    for rel, data in sorted(files.items()):
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return manifest


def _kind_tables():
    from .spec import KINDS
    return KINDS


def _unverified_refs(spec: dict) -> list:
    out = []
    for spec_key, _table, _req in _kind_tables().values():
        for obj in spec.get(spec_key) or []:
            if not isinstance(obj, dict):
                continue
            for field_name in ("display_id", "model_id", "spell_id", "item_id",
                               "visual_id", "icon_id"):
                ref = obj.get(field_name)
                if isinstance(ref, dict) and not ref.get("verified"):
                    out.append(f"{obj.get('key')}.{field_name}={ref.get('value')}")
    return out


def _readme(spec: dict, result: ValidationResult) -> str:
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
        f"ARENA-DB / ARENA-COMPILE / CAMPAIGN-* pending |",
        "",
        "## Files",
        "",
        "```",
        "spec.json            the spec this package was built from",
        "sql/01_world.sql     idempotent world migration",
        "sql/99_rollback.sql  exact rollback",
        "manifest.json        sha256 per file",
        "evidence.json        verdicts + evidence levels",
        "compatibility.json   adapter dry run",
        "```",
        "",
        generate.acceptance_md(spec).rstrip(),
        "",
        "## Install",
        "",
        "```bash",
        "mysql -u acore -p acore_world < sql/01_world.sql",
        "```",
        "",
        "## Rollback",
        "",
        "```bash",
        "mysql -u acore -p acore_world < sql/99_rollback.sql",
        "```",
        "",
    ]
    return "\n".join(lines)
