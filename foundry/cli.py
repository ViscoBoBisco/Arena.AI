"""Foundry CLI: create/inspect/plan/build/verify/compare/package/explain.

Exit codes follow the repo taxonomy: 0 ok, 1 check failed, 2 usage/IO.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import __version__
from .assets import AssetError, ingest_dbc_dir, ingest_inventory, search, summary
from .package import build_package
from .qualification import integrity_check, qualify_package, release_package
from .spec import SpecError, load_spec, validate_spec

REPO = Path(__file__).resolve().parent.parent


def _world_tables():
    path = REPO / "catalog" / "world_tables.txt"
    if not path.is_file():
        return None
    return {ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")}


def _load(path: Path) -> Optional[dict]:
    try:
        return load_spec(path)
    except SpecError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None


def _load_reserved(path: Optional[Path]):
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpecError(f"{path}: cannot read ID baseline ({exc})") from exc
    if not isinstance(data, dict) or data.get("schema") != "arena-id-baseline/1":
        raise SpecError(f"{path}: expected schema 'arena-id-baseline/1'")
    values = data.get("reserved_ids")
    if not isinstance(values, dict):
        raise SpecError(f"{path}: reserved_ids must be an object mapping table names to ID arrays")
    reserved = {}
    for table, ids in values.items():
        if not isinstance(table, str) or not isinstance(ids, list) or any(
                not isinstance(value, int) or isinstance(value, bool) for value in ids):
            raise SpecError(f"{path}: reserved_ids entries must be table -> integer array")
        if len(set(ids)) != len(ids):
            raise SpecError(f"{path}: duplicate reserved ID in table {table}")
        reserved[table] = set(ids)
    return reserved


def _reserved_arg(args):
    try:
        return _load_reserved(getattr(args, "baseline", None))
    except SpecError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None


def _asset_index_arg(args):
    dbc_dir = getattr(args, "dbc_dir", None)
    if dbc_dir is None:
        return None
    try:
        return ingest_dbc_dir(dbc_dir)
    except AssetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return False


def cmd_validate(args) -> int:
    spec = _load(args.spec)
    if spec is None:
        return 2
    reserved = _reserved_arg(args)
    if reserved is None:
        return 2
    asset_index = _asset_index_arg(args)
    if asset_index is False:
        return 2
    result = validate_spec(spec, world_tables=_world_tables(), reserved=reserved,
                           asset_index=asset_index,
                           allow_unverified=args.allow_unverified)
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0 if result.ok else 1


def cmd_plan(args) -> int:
    spec = _load(args.spec)
    if spec is None:
        return 2
    from .spec import allocate_ids, delivery_requirements, patch_cost
    reserved = _reserved_arg(args)
    if reserved is None:
        return 2
    asset_index = _asset_index_arg(args)
    if asset_index is False:
        return 2
    try:
        ids = allocate_ids(spec, reserved=reserved)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    result = validate_spec(spec, world_tables=_world_tables(), reserved=reserved,
                           asset_index=asset_index, allow_unverified=True)
    print(json.dumps({
        "spec": spec.get("name"),
        "patch_cost": patch_cost(spec),
        "delivery_requirements": delivery_requirements(spec),
        "ids": ids,
        "validation": result.to_dict(),
        "objects": {k: len(spec.get(k) or []) for k in
                    ("items", "spells", "quests", "creatures", "gameobjects",
                     "vendors", "recipes", "events")},
    }, indent=2, sort_keys=True))
    return 0 if not result.essential_blocked or result.essential_blocked == ["asset-verification"] else 1


def cmd_build(args) -> int:
    spec = _load(args.spec)
    if spec is None:
        return 2
    reserved = _reserved_arg(args)
    if reserved is None:
        return 2
    asset_index = _asset_index_arg(args)
    if asset_index is False:
        return 2
    result = validate_spec(spec, world_tables=_world_tables(), reserved=reserved,
                           asset_index=asset_index,
                           allow_unverified=args.allow_unverified)
    try:
        manifest = build_package(spec, result, args.out,
                                 allow_unverified=args.allow_unverified,
                                 reserved=reserved,
                                 asset_index=asset_index,
                                 stamp=args.stamp)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"built {args.out} ({manifest['summary']['files']} files, "
          f"{manifest['summary']['bytes']} bytes, patch cost: {manifest['patch_cost']})")
    return 0


def cmd_verify(args) -> int:
    result = integrity_check(args.package)
    if not result["ok"]:
        for problem in result["problems"]:
            label = next((tag for tag in ("INVALID", "MISSING", "CHANGED", "UNLISTED")
                          if problem.startswith(tag + " ")), None)
            if label:
                print(f"{label}  {problem[len(label) + 1:]}")
            else:
                print(f"INVALID  {problem}")
        print(f"package {args.package}: FAIL ({len(result['problems'])} problem(s))")
        return 1
    manifest = result["manifest"]
    count = len(manifest["files"]) + len(manifest.get("metadata_files", {}))
    print(f"package {manifest.get('spec')}: OK ({count} payload/metadata file(s) match)")
    print("integrity only: qualification and release status are unchanged")
    return 0


def cmd_qualify(args) -> int:
    import os
    import tempfile

    try:
        args.out.resolve().relative_to(args.package.resolve())
    except ValueError:
        pass
    else:
        print("error: qualification report must be outside the package", file=sys.stderr)
        return 2
    try:
        report = qualify_package(args.package, args.attestations)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{args.out.name}.", dir=args.out.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(report, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(temp_name, args.out)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
    except (OSError, ValueError) as exc:
        print(f"qualification failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("qualified") else 1


def cmd_release(args) -> int:
    try:
        receipt = release_package(args.package, args.qualification, args.out)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"release blocked: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


def cmd_assets(args) -> int:
    try:
        if args.dbc_dir:
            index = ingest_dbc_dir(args.dbc_dir)
        elif args.inventory:
            index = ingest_inventory(args.inventory)
        else:
            print("error: pass --dbc-dir or --inventory", file=sys.stderr)
            return 2
    except AssetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.query:
        hits = search(index, args.query, kind=args.kind)
        print(json.dumps(hits, indent=2, sort_keys=True))
    else:
        print(json.dumps(summary(index), indent=2, sort_keys=True))
    return 0


def cmd_explain(args) -> int:
    spec = _load(args.spec)
    if spec is None:
        return 2
    from .spec import patch_cost
    print(f"{spec.get('title', spec['name'])}")
    print(f"patch cost: {patch_cost(spec)}")
    print("Evidence: see docs/12-content-foundry.md for the level ladder.")
    print("Adapter: see docs/13-campaign-party-adapter.md for the contract.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="foundry",
        description=f"Arena Content Foundry v{__version__}: spec -> reproducible handoff bundle")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="validate a spec (fail-closed)")
    v.add_argument("spec", type=Path)
    v.add_argument("--allow-unverified", action="store_true")
    v.add_argument("--baseline", type=Path, help="reserved-ID snapshot (arena-id-baseline/1 JSON)")
    v.add_argument("--dbc-dir", type=Path, help="local client DBC extracts for provenance verification")
    v.set_defaults(func=cmd_validate)

    pl = sub.add_parser("plan", help="preview ids + patch cost without building")
    pl.add_argument("spec", type=Path)
    pl.add_argument("--baseline", type=Path, help="reserved-ID snapshot (arena-id-baseline/1 JSON)")
    pl.add_argument("--dbc-dir", type=Path, help="local client DBC extracts for provenance verification")
    pl.set_defaults(func=cmd_plan)

    b = sub.add_parser("build", help="build a package bundle from a spec")
    b.add_argument("spec", type=Path)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--allow-unverified", action="store_true")
    b.add_argument("--baseline", type=Path, help="reserved-ID snapshot (arena-id-baseline/1 JSON)")
    b.add_argument("--dbc-dir", type=Path, help="local client DBC extracts for provenance verification")
    b.add_argument("--stamp", action="store_true")
    b.set_defaults(func=cmd_build)

    vf = sub.add_parser("verify", help="verify package integrity only")
    vf.add_argument("package", type=Path)
    vf.set_defaults(func=cmd_verify)

    q = sub.add_parser("qualify", help="evaluate qualification gates without modifying the package")
    q.add_argument("package", type=Path)
    q.add_argument("--attestations", type=Path,
                   help="external qualification records bound to this manifest")
    q.add_argument("--out", type=Path, required=True,
                   help="write the manifest-bound qualification report here")
    q.set_defaults(func=cmd_qualify)

    rel = sub.add_parser("release", help="copy only a qualified, integrity-checked package")
    rel.add_argument("package", type=Path)
    rel.add_argument("--qualification", type=Path, required=True)
    rel.add_argument("--out", type=Path, required=True)
    rel.set_defaults(func=cmd_release)

    a = sub.add_parser("assets", help="index + search client assets")
    a.add_argument("--dbc-dir", type=Path)
    a.add_argument("--inventory", type=Path)
    a.add_argument("--query")
    a.add_argument("--kind")
    a.set_defaults(func=cmd_assets)

    e = sub.add_parser("explain", help="explain a spec's cost and evidence")
    e.add_argument("spec", type=Path)
    e.set_defaults(func=cmd_explain)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
