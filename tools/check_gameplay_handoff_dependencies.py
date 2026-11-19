#!/usr/bin/env python3
"""Report local integration prerequisites without touching databases or game files."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def git_head(path: Path) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path,
                        help="handoff directory containing bundle.json (defaults beside this script)")
    parser.add_argument("--core-source", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--client-dir", type=Path)
    args = parser.parse_args()
    checks: dict[str, dict] = {
        "python": {"status": "PASS" if sys.version_info >= (3, 9) else "FAIL",
                   "version": sys.version.split()[0]},
        "cmake": {"status": "PASS" if shutil.which("cmake") else "MISSING",
                  "path": shutil.which("cmake")},
        "cxx": {"status": "PASS" if any(shutil.which(x) for x in ("c++", "g++", "clang++")) else "MISSING",
                "path": next((shutil.which(x) for x in ("c++", "g++", "clang++") if shutil.which(x)), None)},
        "sql_client": {"status": "PASS" if any(shutil.which(x) for x in ("mysql", "mariadb")) else "MISSING",
                       "path": next((shutil.which(x) for x in ("mysql", "mariadb") if shutil.which(x)), None)},
    }
    script_dir = Path(__file__).resolve().parent
    bundle_path = args.bundle.expanduser().resolve() if args.bundle else script_dir / "bundle.json"
    if bundle_path.is_file():
        expected_pin = json.loads(bundle_path.read_text(encoding="utf-8"))["core"]["revision"]
    else:
        repo_pin_path = script_dir / "foundry/core-pins.json"
        if not repo_pin_path.is_file():
            repo_pin_path = script_dir.parent / "foundry/core-pins.json"
        if not repo_pin_path.is_file():
            parser.error("cannot find bundle.json or foundry/core-pins.json; pass --bundle")
        expected_pin = json.loads(repo_pin_path.read_text(encoding="utf-8"))["azerothcore_wotlk"]["revision"]
    if args.core_source:
        expected = expected_pin
        actual = git_head(args.core_source.resolve())
        checks["pinned_core"] = {"status": "PASS" if actual == expected else "FAIL",
                                 "expected": expected, "actual": actual}
    else:
        checks["pinned_core"] = {"status": "NOT_ASSESSED", "reason": "pass --core-source"}
    if args.data_dir:
        data = args.data_dir.resolve()
        data_dirs = {name: (data / name).is_dir()
                     for name in ("dbc", "maps", "vmaps", "mmaps")}
        checks["server_data"] = {"status": "PASS" if all(data_dirs.values()) else "FAIL",
                                 **data_dirs, "path": str(data)}
    else:
        checks["server_data"] = {"status": "NOT_ASSESSED", "reason": "pass --data-dir"}
    if args.client_dir:
        client = args.client_dir.resolve()
        executable = next((client / name for name in ("WoW.exe", "Wow.exe", "wow.exe")
                           if (client / name).is_file()), None)
        checks["client"] = {"status": "PRESENT" if executable else "FAIL",
                            "executable": str(executable) if executable else None,
                            "build_12340": "MUST_BE_VERIFIED_IN_CLIENT"}
    else:
        checks["client"] = {"status": "NOT_ASSESSED", "reason": "pass --client-dir"}
    print(json.dumps(checks, indent=2, sort_keys=True))
    required = (checks["python"]["status"], checks["cmake"]["status"],
                checks["cxx"]["status"], checks["sql_client"]["status"])
    return 0 if all(status == "PASS" for status in required) else 1


if __name__ == "__main__":
    raise SystemExit(main())
