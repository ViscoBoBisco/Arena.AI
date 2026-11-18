#!/usr/bin/env python3
"""Run a source-, schema-, receipt-bound disposable World DB qualification."""
from __future__ import annotations

import argparse
import configparser
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from foundry.db_qualification import write_attestations  # noqa: E402
from foundry.world_qualification import qualify_owned_world_package  # noqa: E402


def _read_mysql_options(path: Path | None) -> dict:
    values = {}
    if path is not None:
        if not path.is_file():
            raise ValueError(f"MySQL option file does not exist: {path}")
        parser = configparser.ConfigParser()
        parser.read(path)
        if parser.has_section("client"):
            values.update(dict(parser.items("client")))
    return {
        "host": os.environ.get("ARENA_MYSQL_HOST", values.get("host", "127.0.0.1")),
        "port": int(os.environ.get("ARENA_MYSQL_PORT", values.get("port", "3306"))),
        "user": os.environ.get("ARENA_MYSQL_USER", values.get("user", "root")),
        "password": os.environ.get("ARENA_MYSQL_PASSWORD", values.get("password", "")),
        "charset": values.get("default-character-set", "utf8mb4"),
        "autocommit": True,
    }


def _args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--database", required=True)
    parser.add_argument("--core-source", required=True, type=Path)
    parser.add_argument("--schema-proof", required=True, type=Path)
    parser.add_argument("--recovery-proof", required=True, type=Path)
    parser.add_argument("--defaults-extra-file", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--confirm-disposable", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _args(argv)
    if not args.confirm_disposable:
        print("error: use only on a throwaway arena_foundry_disposable_* schema and pass --confirm-disposable",
              file=sys.stderr)
        return 2
    if not args.database.startswith("arena_foundry_disposable_") or len(args.database) > 64:
        print("error: database must use the arena_foundry_disposable_ prefix", file=sys.stderr)
        return 2
    if args.out.exists() or args.out.is_symlink():
        print(f"error: refusing to overwrite attestation output: {args.out}", file=sys.stderr)
        return 2
    for evidence in (args.schema_proof, args.recovery_proof):
        if not evidence.is_file():
            print(f"error: required evidence file does not exist: {evidence}", file=sys.stderr)
            return 2
    try:
        args.out.resolve().relative_to(args.package.resolve())
    except ValueError:
        pass
    else:
        print("error: attestation output must be outside the package", file=sys.stderr)
        return 2
    try:
        import pymysql
        schema_proof = json.loads(args.schema_proof.read_text(encoding="utf-8"))
        recovery_proof = json.loads(args.recovery_proof.read_text(encoding="utf-8"))
        connection = pymysql.connect(database=args.database, **_read_mysql_options(args.defaults_extra_file))
        try:
            attestations = qualify_owned_world_package(
                connection, args.package, args.database, args.core_source,
                schema_proof, recovery_proof)
        finally:
            connection.close()
        write_attestations(args.out, attestations)
    except Exception as exc:
        print(f"World database qualification failed closed: {exc}", file=sys.stderr)
        return 1
    proof = attestations["database_qualification"]
    print("ARENA-DB PASS: transactional World operation attested against pinned disposable schema")
    print(f"manifest_sha256: {attestations['subject']['manifest_sha256']}")
    print(f"schema_source_revision: {proof['schema_source']['revision']}")
    print(f"row_count: {proof['row_count']}; receipts: {len(proof['receipts'])}")
    print(f"attestations: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
