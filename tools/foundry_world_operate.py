#!/usr/bin/env python3
"""Apply or rollback one integrity-checked Foundry package on a disposable World DB."""

from __future__ import annotations

import argparse
import configparser
import json
import re
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from foundry.world_ownership import OwnershipError, operate_on_package  # noqa: E402


def _args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("apply", "rollback"))
    parser.add_argument("package", type=Path, help="integrity-checked Foundry package")
    parser.add_argument("--database", required=True,
                        help="disposable schema beginning arena_foundry_disposable_")
    parser.add_argument("--request-id", required=True,
                        help="stable 1-96 character request ID; reuse it when retrying")
    parser.add_argument("--defaults-extra-file", type=Path, required=True,
                        help="MySQL [client] option file; protect it with chmod 600")
    parser.add_argument("--confirm-disposable", action="store_true",
                        help="confirm the target is an isolated disposable database")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _args(argv)
    if (not args.confirm_disposable
            or not re.fullmatch(r"arena_foundry_disposable_[A-Za-z0-9_]{1,39}", args.database)):
        print("error: explicitly confirm a schema named arena_foundry_disposable_*", file=sys.stderr)
        return 2
    if not args.defaults_extra_file.is_file():
        print(f"error: MySQL option file does not exist: {args.defaults_extra_file}", file=sys.stderr)
        return 2
    if stat.S_IMODE(args.defaults_extra_file.stat().st_mode) & 0o077:
        print("error: MySQL option file must not be readable by group or other users (chmod 600)",
              file=sys.stderr)
        return 2
    try:
        import pymysql
    except ImportError:
        print("error: install optional PyMySQL dependency to use this command", file=sys.stderr)
        return 2
    config = configparser.ConfigParser()
    try:
        with args.defaults_extra_file.open(encoding="utf-8") as stream:
            config.read_file(stream)
        client = config["client"]
        connection = pymysql.connect(
            host=client.get("host", "localhost"),
            port=client.getint("port", 3306),
            user=client.get("user", ""),
            password=client.get("password", ""),
            database=args.database,
            charset="utf8mb4",
            autocommit=True,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT DATABASE()")
                selected = cursor.fetchone()[0]
            if selected != args.database:
                raise OwnershipError(f"connected schema {selected!r} does not match requested database")
            result = operate_on_package(connection, args.package,
                                        action=args.action, request_id=args.request_id)
        finally:
            connection.close()
    except Exception as exc:
        print(f"owned World operation failed safely: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    print("Evidence is disposable-DB only; this command does not qualify a live AzerothCore server.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
