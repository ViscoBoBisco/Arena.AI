#!/usr/bin/env python3
"""Build a reproducible, non-installing local-agent handoff bundle.

The bundle contains repository provenance and test output only. It never copies
client/DBC/MPQ data, credentials, databases, server builds, or runtime state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_DOCS = (
    ("docs/17-vault-terminal-client-acceptance.md", "CHECKLIST-client.md"),
    ("docs/18-local-runtime-handoff.md", "CHECKLIST-local-runtime.md"),
    ("docs/22-expanded-gameplay-program.md", "GAMEPLAY-PROGRAM.md"),
    ("designs/argent-faction-operations-foundation.json", "argent-foundation.json"),
    ("designs/gameplay-systems-register.json", "gameplay-systems-register.json"),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()


def run_check(name: str, argv: list[str], expected: int, log_dir: Path) -> dict:
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    payload = ("COMMAND: " + " ".join(argv) + "\nEXIT: " + str(result.returncode)
               + "\n--- stdout ---\n" + result.stdout
               + "\n--- stderr ---\n" + result.stderr).encode("utf-8")
    rel = f"logs/{name}.txt"
    (log_dir / "logs" / f"{name}.txt").write_bytes(payload)
    return {"name": name, "command": argv, "expected_exit": expected,
            "exit": result.returncode, "status": "PASS" if result.returncode == expected else "FAIL",
            "log": rel, "log_sha256": sha256(payload)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path,
                        help="new output directory outside this repository")
    parser.add_argument("--skip-tests", action="store_true",
                        help="do not run make check or package/fail-closed smoke checks")
    args = parser.parse_args()
    out = args.out.expanduser().absolute()
    if out == ROOT or ROOT in out.parents:
        parser.error("output must be outside the repository")
    if out.exists() or out.is_symlink():
        parser.error(f"output already exists; refusing to overwrite: {out}")
    out.mkdir(parents=True)
    (out / "logs").mkdir()

    try:
        head = git("rev-parse", "HEAD")
        branch = git("branch", "--show-current")
        status = git("status", "--porcelain=v1")
        diff_sha = sha256(subprocess.run(
            ["git", "diff", "--binary", "HEAD"], cwd=ROOT, check=True,
            capture_output=True).stdout)
    except (OSError, subprocess.CalledProcessError) as exc:
        shutil.rmtree(out, ignore_errors=True)
        parser.error(f"cannot capture repository provenance: {exc}")

    changed_paths = sorted(set(
        line[3:] for line in status.splitlines() if len(line) >= 4
    ))
    source_files = {}
    for rel in changed_paths:
        path = ROOT / rel
        if path.is_file() and not path.is_symlink():
            source_files[rel] = sha256(path.read_bytes())

    copied = {}
    for source, target in BUNDLE_DOCS:
        data = (ROOT / source).read_bytes()
        (out / target).write_bytes(data)
        copied[target] = {"source": source, "sha256": sha256(data), "size": len(data)}

    checks = []
    if not args.skip_tests:
        checks = [
            run_check("make-check", ["make", "check"], 0, out),
            run_check("foundry-verify", [sys.executable, "tools/foundry.py", "verify",
                                          "packages/frost-armory"], 0, out),
            run_check("frost-fail-closed", [sys.executable, "tools/foundry.py", "validate",
                                             "specs/frost-armory.json", "--allow-unverified"],
                      1, out),
        ]

    report = {
        "schema": "arena-local-handoff/1",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {"repository": "ViscoBoBisco/Arena.AI", "branch": branch,
                   "head": head, "worktree_status": "DIRTY" if status else "CLEAN",
                   "status_porcelain": status.splitlines(), "git_diff_head_sha256": diff_sha,
                   "changed_source_sha256": source_files},
        "checks": checks if checks else [{"status": "NOT_ASSESSED", "reason": "--skip-tests"}],
        "evidence_matrix": {
            "ARENA-STATIC": "PASS" if checks and checks[0]["status"] == "PASS" else "NOT_ASSESSED",
            "ARENA-PACK": "PASS" if len(checks) > 1 and checks[1]["status"] == "PASS" else "NOT_ASSESSED",
            "ARENA-DB": "NOT_ASSESSED; ownership service is limited to disposable InnoDB fixtures; exact pinned AzerothCore World schema and recovery proof pending",
            "ARENA-COMPILE": "pinned targeted compile record applies only to exact hashed module sources; full build NOT_ASSESSED",
            "SERVER-BOOT": "NOT_ASSESSED",
            "CLIENT-12340": "NOT_ASSESSED",
            "PLAYERBOT": "NOT_ASSESSED",
            "CAMPAIGN-PARTY": "NOT_ASSESSED",
            "FACTION-OPERATIONS": "DESIGN_ONLY_NOT_PLAYABLE; stock IDs unresolved",
        },
        "ownership_ledger": {
            "schema": "arena-world-row-ownership/1",
            "status": "IMPLEMENTED_FOR_DISPOSABLE_INNODB_FIXTURES; PINNED_WORLD_SCHEMA_PROOF_PENDING",
            "tracked_world_rows": "per-row identity, package/version/manifest, expected values, full-row snapshot and state",
            "automatic_rollback": "AVAILABLE_ONLY_THROUGH_EXPLICIT_TOOL_ON_CONFIRMED_DISPOSABLE_INNODB_SCHEMA",
            "production_writes": "NONE",
        },
        "asset_provenance": {
            "local_dbc_supplied": False,
            "stock_faction_reputation": {"table": "Faction.dbc", "value": None,
                                         "status": "UNRESOLVED"},
            "stock_hostility_template": {"table": "FactionTemplate.dbc", "value": None,
                                         "status": "UNRESOLVED"},
            "proprietary_client_data_copied": False,
        },
        "bundle_files": copied,
        "safety": {"installable": False, "release_authorized": False,
                   "database_modified": False, "client_data_included": False,
                   "credentials_included": False},
    }
    encoded = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    (out / "handoff.json").write_bytes(encoded)

    manifest = {}
    for path in sorted(item for item in out.rglob("*") if item.is_file()
                       and item.name != "manifest.json"):
        rel = path.relative_to(out).as_posix()
        data = path.read_bytes()
        manifest[rel] = {"sha256": sha256(data), "size": len(data)}
    (out / "manifest.json").write_text(
        json.dumps({"schema": "arena-local-handoff-manifest/1", "files": manifest},
                   indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(out), "source_head": head,
                      "checks": checks, "installable": False,
                      "release_authorized": False}, indent=2, sort_keys=True))
    return 0 if all(item["status"] == "PASS" for item in checks) else (1 if checks else 0)


if __name__ == "__main__":
    raise SystemExit(main())
