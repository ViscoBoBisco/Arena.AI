#!/usr/bin/env python3
"""Qualify generated Foundry world SQL against an isolated disposable schema."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from foundry.db_qualification import qualify_database, write_attestations  # noqa: E402


def _args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path, help="integrity-checked Foundry package directory")
    parser.add_argument("--database", required=True,
                        help="dedicated disposable schema (must start arena_foundry_disposable_)")
    parser.add_argument("--core-source", type=Path, required=True,
                        help="clean checkout of the exact revision in foundry/core-pins.json")
    parser.add_argument("--mysql", default="mysql", help="MySQL/MariaDB CLI executable")
    parser.add_argument("--defaults-extra-file", type=Path,
                        help="optional MySQL option file; keep credentials out of shell arguments")
    parser.add_argument("--out", type=Path, required=True,
                        help="new attestations JSON path outside the package")
    parser.add_argument("--confirm-disposable", action="store_true",
                        help="confirm this schema is disposable and contains no unrelated data")
    parser.add_argument("--timeout-sec", type=int, default=120,
                        help="timeout per MySQL invocation (default: 120)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _args(argv)
    if not args.confirm_disposable:
        print("error: pass --confirm-disposable only for an isolated throwaway schema",
              file=sys.stderr)
        return 2
    if args.timeout_sec < 1:
        print("error: --timeout-sec must be positive", file=sys.stderr)
        return 2
    if args.defaults_extra_file and not args.defaults_extra_file.is_file():
        print(f"error: MySQL option file does not exist: {args.defaults_extra_file}",
              file=sys.stderr)
        return 2
    try:
        args.out.resolve().relative_to(args.package.resolve())
    except ValueError:
        pass
    else:
        print("error: attestations output must be outside the package", file=sys.stderr)
        return 2
    if args.out.exists() or args.out.is_symlink():
        print(f"error: output already exists: {args.out}", file=sys.stderr)
        return 2

    def run_mysql(sql: str) -> str:
        command = [args.mysql]
        if args.defaults_extra_file:
            command.append(f"--defaults-extra-file={args.defaults_extra_file}")
        command.extend(("--batch", "--skip-column-names", f"--database={args.database}"))
        is_query = sql.lstrip().upper().startswith("SELECT ")
        if is_query:
            command.extend(("--execute", sql))
            input_sql = None
        else:
            input_sql = sql
        result = subprocess.run(command, input=input_sql, text=True,
                                capture_output=True, timeout=args.timeout_sec, check=False)
        if result.returncode:
            detail = result.stderr.strip() or f"mysql exited {result.returncode}"
            raise RuntimeError(detail)
        return result.stdout

    try:
        attestations = qualify_database(args.package, args.database, run_mysql,
                                        core_source=args.core_source)
        write_attestations(args.out, attestations)
    except Exception as exc:
        print(f"database qualification failed: {exc}", file=sys.stderr)
        return 1
    print(f"ARENA-DB PASS: {attestations['database_qualification']['checked_rows']} generated row identities "
          f"qualified against {args.database}")
    print(f"manifest_sha256: {attestations['subject']['manifest_sha256']}")
    print(f"attestations: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
