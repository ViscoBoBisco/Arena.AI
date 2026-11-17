#!/usr/bin/env python3
"""manifest.py - describe and verify the contents of a client patch staging directory.

Why: on a 3.3.5a server the client patch is a release artifact. The two failure modes that hurt
most are (a) players running a patch that is not the one you shipped, and (b) the patch and the
server's DataDir/dbc silently diverging. A manifest turns both into a hash comparison.

Usage
-----
  # stamp a staging directory (which normally contains DBFilesClient/*.dbc)
  python3 tools/manifest.py make --staging patch/ --version 2026.11.1 --out manifest.json

  # include the packed archive's own hash
  python3 tools/manifest.py make --staging patch/ --version 2026.11.1 \\
      --patch dist/patch-4.MPQ --out manifest.json

  # verify a staging directory (or a freshly extracted patch) against a manifest
  python3 tools/manifest.py verify --manifest manifest.json --staging patch/

  # verify the shipped archive too
  python3 tools/manifest.py verify --manifest manifest.json --staging patch/ \\
      --patch dist/patch-4.MPQ

Output is deterministic (sorted keys, no timestamps unless --stamp), so a manifest diff in code
review shows exactly what changed between releases.

Exit codes: 0 ok, 1 verification mismatch, 2 usage/IO error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

SCHEMA = 1
SKIP_NAMES = {".DS_Store", "Thumbs.db", ".gitkeep"}
CHUNK = 1 << 20


class ManifestError(Exception):
    pass


# ------------------------------------------------------------------ primitives
def sha256_file(path: Path) -> Tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def scan(staging: Path) -> Dict[str, Dict[str, object]]:
    """Return {relative/posix/path: {sha256, size}} for every regular file under *staging*."""
    if not staging.is_dir():
        raise ManifestError(f"{staging}: not a directory")
    out: Dict[str, Dict[str, object]] = {}
    for root, dirs, files in os.walk(staging):
        dirs.sort()
        for name in sorted(files):
            if name in SKIP_NAMES or name.startswith("._"):
                continue
            full = Path(root) / name
            if full.is_symlink() or not full.is_file():
                continue
            rel = full.relative_to(staging).as_posix()
            digest, size = sha256_file(full)
            out[rel] = {"sha256": digest, "size": size}
    return out


# ---------------------------------------------------------------------- make
def cmd_make(args: argparse.Namespace) -> int:
    files = scan(args.staging)
    if not files:
        raise ManifestError(f"{args.staging}: no files found (nothing to stamp)")

    manifest = {
        "schema": SCHEMA,
        "content_version": args.version,
        "client_build": args.client_build,
        "files": files,
        "summary": {
            "files": len(files),
            "bytes": sum(int(meta["size"]) for meta in files.values()),
        },
    }

    if args.patch:
        if not args.patch.is_file():
            raise ManifestError(f"{args.patch}: patch file not found")
        digest, size = sha256_file(args.patch)
        manifest["patch"] = {"file": args.patch.name, "sha256": digest, "size": size}
    else:
        manifest["patch"] = None

    if args.stamp:
        from datetime import datetime, timezone
        manifest["generated_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(files)} files, {manifest['summary']['bytes']} bytes)")
    else:
        sys.stdout.write(text)
    return 0


# -------------------------------------------------------------------- verify
def cmd_verify(args: argparse.Namespace) -> int:
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"{args.manifest}: cannot read manifest ({exc})") from exc

    expected: Dict[str, Dict[str, object]] = manifest.get("files") or {}
    if not expected:
        raise ManifestError(f"{args.manifest}: manifest has no 'files' section")

    actual = scan(args.staging)
    problems = 0

    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(
        rel for rel in set(expected) & set(actual)
        if expected[rel].get("sha256") != actual[rel].get("sha256")
    )

    for rel in missing:
        print(f"MISSING  {rel}")
        problems += 1
    for rel in extra:
        print(f"EXTRA    {rel}")
        problems += 1
    for rel in changed:
        print(f"CHANGED  {rel}")
        problems += 1

    patch_info = manifest.get("patch")
    if args.patch and patch_info:
        if not args.patch.is_file():
            print(f"MISSING  {args.patch} (patch archive)")
            problems += 1
        else:
            digest, _ = sha256_file(args.patch)
            if digest != patch_info.get("sha256"):
                print(f"CHANGED  {args.patch} (patch archive)")
                problems += 1
    elif args.patch and not patch_info:
        print("note: manifest has no patch hash; skipping archive check")

    version = manifest.get("content_version", "?")
    if problems:
        print(f"\nmanifest {version}: FAIL ({problems} problem(s))")
        return 1
    print(f"manifest {version}: OK ({len(actual)} file(s) match)")
    return 0


# ----------------------------------------------------------------------- cli
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Create or verify a manifest for a WoW 3.3.5a client patch staging directory."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("make", help="stamp a staging directory into a manifest")
    m.add_argument("--staging", type=Path, required=True, help="dir to stamp (e.g. patch/)")
    m.add_argument("--version", required=True, help="content version, e.g. 2026.11.1")
    m.add_argument("--out", type=Path, help="write manifest here (default: stdout)")
    m.add_argument("--patch", type=Path, help="optional packed .MPQ to hash as well")
    m.add_argument("--client-build", default="3.3.5a.12340")
    m.add_argument("--stamp", action="store_true", help="include a generated_utc timestamp")
    m.set_defaults(func=cmd_make)

    v = sub.add_parser("verify", help="check a staging directory against a manifest")
    v.add_argument("--manifest", type=Path, required=True)
    v.add_argument("--staging", type=Path, required=True)
    v.add_argument("--patch", type=Path, help="optional packed .MPQ to verify")
    v.set_defaults(func=cmd_verify)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)  # type: ignore[no-any-return]
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
