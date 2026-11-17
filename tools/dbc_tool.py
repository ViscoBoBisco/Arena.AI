#!/usr/bin/env python3
"""dbc_tool.py - read-only inspection of WoW 3.3.5a (build 12340) DBC files.

Why this exists
---------------
Almost every "custom content" bug on a 3.3.5a private server is a *data* bug in a
DBC file: a duplicate ID, a reference to a row that does not exist in another
table, a client patch built from a different DBC than the server runs, or a
record count/field count that silently drifted.  These are cheap to detect
offline and expensive to debug in-game, so this tool gives you the checks you
want to run in CI before shipping a patch.

Scope
-----
* Handles the plain WDBC layout used by 1.x - 3.3.5a (magic + 4 uint32 header,
  fixed-size records, string block at the end).
* Read-only. It never writes a DBC; edit with WDBX Editor / Spell Editor and use
  this to verify the result.
* Field indices are 0-based, matching the wiki tables (field 0 == first column).
* No external dependencies (stdlib only), so it runs anywhere Python 3.8+ runs.

Examples
--------
  python3 dbc_tool.py info Item.dbc
  python3 dbc_tool.py ids Item.dbc --min 800000 --max 800999
  python3 dbc_tool.py free Spell.dbc --from 800000 --to 800100
  python3 dbc_tool.py refs Item.dbc --field 5 --must-exist ItemDisplayInfo.dbc
  python3 dbc_tool.py refs Spell.dbc --field 133 --must-exist SpellIcon.dbc
  python3 dbc_tool.py diff old/Item.dbc new/Item.dbc
  python3 dbc_tool.py dump Item.dbc --out Item.csv
  python3 dbc_tool.py verify-pair client/Item.dbc server/Item.dbc

Exit codes: 0 = ok, 1 = a check failed, 2 = usage/IO error.
"""

from __future__ import annotations

import argparse
import csv
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

MAGIC = b"WDBC"
HEADER = struct.Struct("<4sIIII")  # magic, recordCount, fieldCount, recordSize, stringSize
MAX_FIELD_COUNT = 512  # Spell.dbc in 3.3.5a has 234; anything beyond this is corruption


class DbcError(Exception):
    pass


@dataclass
class Dbc:
    path: Path
    field_count: int
    record_size: int
    string_size: int
    records: List[Tuple[int, ...]]
    raw: bytes

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, path: Path) -> "Dbc":
        try:
            raw = path.read_bytes()
        except OSError as exc:  # pragma: no cover - IO error path
            raise DbcError(f"{path}: cannot read ({exc})") from exc

        if len(raw) < HEADER.size:
            raise DbcError(f"{path}: file shorter than a DBC header ({len(raw)} bytes)")

        magic, record_count, field_count, record_size, string_size = HEADER.unpack_from(raw, 0)
        if magic != MAGIC:
            raise DbcError(
                f"{path}: bad magic {magic!r} (expected b'WDBC'). "
                "For 4.x+ DB2 files or an MPQ archive, extract/convert first."
            )
        if field_count == 0 or field_count > MAX_FIELD_COUNT:
            raise DbcError(f"{path}: implausible field count {field_count}")
        if record_size != field_count * 4:
            raise DbcError(
                f"{path}: record size {record_size} != fieldCount*4 ({field_count * 4}); "
                "file is truncated or from another client build"
            )
        expected = HEADER.size + record_count * record_size + string_size
        if expected != len(raw):
            raise DbcError(
                f"{path}: size mismatch: header implies {expected} bytes, file has {len(raw)}. "
                "Truncated download or hand-edited header."
            )

        records: List[Tuple[int, ...]] = []
        fmt = struct.Struct("<" + "i" * field_count)
        off = HEADER.size
        for _ in range(record_count):
            records.append(fmt.unpack_from(raw, off))  # signed: -1 is meaningful in many tables
            off += record_size
        return cls(path=path, field_count=field_count, record_size=record_size,
                   string_size=string_size, records=records, raw=raw)

    # ---------------------------------------------------------------- helpers
    @property
    def record_count(self) -> int:
        return len(self.records)

    @property
    def string_offset(self) -> int:
        return HEADER.size + self.record_count * self.record_size

    def ids(self, field: int = 0) -> List[int]:
        return [r[field] for r in self.records]

    def by_id(self, field: int = 0) -> Dict[int, Tuple[int, ...]]:
        return {r[field]: r for r in self.records}

    def string(self, offset: int) -> str:
        """Read a NUL-terminated string from the string block at *offset*."""
        if offset < 0 or offset >= self.string_size:
            return ""
        start = self.string_offset + offset
        end = self.raw.find(b"\0", start)
        if end == -1:
            end = self.string_offset + self.string_size
        return self.raw[start:end].decode("utf-8", "replace")

    def string_fields(self) -> List[int]:
        """Best-effort: which fields look like string-block offsets."""
        if self.string_size == 0 or not self.records:
            return []
        out = []
        for f in range(self.field_count):
            vals = [r[f] for r in self.records]
            if all(0 <= v < self.string_size for v in vals) and any(v > 0 for v in vals):
                out.append(f)
        return out

    def duplicate_ids(self, field: int = 0) -> List[int]:
        seen: Dict[int, int] = {}
        dupes = []
        for r in self.records:
            v = r[field]
            seen[v] = seen.get(v, 0) + 1
            if seen[v] == 2:
                dupes.append(v)
        return dupes


# --------------------------------------------------------------------- checks
def check_header(path: Path, verbose: bool = True) -> int:
    if not path.exists():
        print(f"ERROR {path}: not found", file=sys.stderr)
        return 2
    try:
        dbc = Dbc.load(path)
    except DbcError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1

    size_mb = path.stat().st_size / (1024 * 1024)
    if verbose:
        print(f"file           {path}")
        print(f"magic          {MAGIC.decode()} (ok)")
        print(f"records        {dbc.record_count}")
        print(f"fields         {dbc.field_count}")
        print(f"record size    {dbc.record_size} bytes")
        print(f"string block   {dbc.string_size} bytes")
        print(f"file size      {size_mb:.2f} MB")
        strings = dbc.string_fields()
        if strings:
            shown = ", ".join(str(f) for f in strings[:12])
            more = "" if len(strings) <= 12 else f", ... ({len(strings)} total)"
            print(f"string fields  {shown}{more}")

    dupes = dbc.duplicate_ids()
    if dupes:
        preview = ", ".join(str(d) for d in dupes[:20])
        print(f"WARN duplicate IDs in field 0: {len(dupes)} ({preview}{'...' if len(dupes) > 20 else ''})",
              file=sys.stderr)
        return 1
    return 0


def check_free_ids(path: Path, lo: int, hi: int) -> int:
    dbc = Dbc.load(path)
    used = {v for v in dbc.ids() if lo <= v <= hi}
    if not used:
        print(f"{path}: {hi - lo + 1} IDs free in [{lo}, {hi}]")
        return 0
    gaps = []
    cursor = lo
    for v in sorted(used):
        if v > cursor:
            gaps.append((cursor, v - 1))
        cursor = v + 1
    if cursor <= hi:
        gaps.append((cursor, hi))
    print(f"{path}: {len(used)} IDs used in [{lo}, {hi}]")
    if gaps:
        print("free ranges:")
        for a, b in gaps:
            print(f"  {a}-{b}  ({b - a + 1})")
    else:
        print("free ranges: none")
    return 0 if gaps else 1


def check_refs(path: Path, field: int, must_exist: Path, allow_zero: bool = True) -> int:
    src = Dbc.load(path)
    ref = Dbc.load(must_exist)
    if field >= src.field_count:
        print(f"ERROR {path}: field {field} out of range (0..{src.field_count - 1})", file=sys.stderr)
        return 2
    target = set(ref.ids())
    missing: Dict[int, int] = {}
    for r in src.records:
        value = r[field]
        if value == 0 and allow_zero:
            continue
        if value not in target:
            missing[value] = missing.get(value, 0) + 1
    if not missing:
        print(f"OK {path} field {field}: all {src.record_count} values exist in {must_exist.name}")
        return 0
    print(f"FAIL {path} field {field}: {len(missing)} distinct values missing from {must_exist.name}",
          file=sys.stderr)
    for value in sorted(missing)[:40]:
        print(f"  {value} (referenced by {missing[value]} record(s))", file=sys.stderr)
    if len(missing) > 40:
        print(f"  ... and {len(missing) - 40} more", file=sys.stderr)
    return 1


def check_pair(a_path: Path, b_path: Path, ids_field: int = 0) -> int:
    """Compare two copies of the same table (e.g. client patch vs server DataDir)."""
    a = Dbc.load(a_path)
    b = Dbc.load(b_path)
    problems = 0
    if a.field_count != b.field_count:
        print(f"FAIL field count differs: {a_path.name}={a.field_count}, {b_path.name}={b.field_count}",
              file=sys.stderr)
        problems += 1
    a_ids, b_ids = set(a.ids(ids_field)), set(b.ids(ids_field))
    only_a, only_b = a_ids - b_ids, b_ids - a_ids
    if only_a:
        print(f"WARN {len(only_a)} IDs only in {a_path.name}: {sorted(only_a)[:10]}")
        problems += 1
    if only_b:
        print(f"WARN {len(only_b)} IDs only in {b_path.name}: {sorted(only_b)[:10]}")
        problems += 1
    shared = a_ids & b_ids
    a_map, b_map = a.by_id(ids_field), b.by_id(ids_field)
    differing = [i for i in shared if a_map[i] != b_map[i]]
    if differing:
        print(f"WARN {len(differing)} shared IDs differ in values: {sorted(differing)[:10]}")
        problems += 1
    if not problems:
        print(f"OK {a_path.name} and {b_path.name} are identical ({len(shared)} records)")
        return 0
    print("(a mismatch between the client patch and the server's DBC set is the most common "
          "cause of 'works on my machine' custom content)")
    return 1


def dump_csv(path: Path, out: Path) -> int:
    dbc = Dbc.load(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        for row in dbc.records:
            writer.writerow(row)
    print(f"wrote {dbc.record_count} rows x {dbc.field_count} cols to {out}")
    return 0


def list_ids(path: Path, lo: Optional[int], hi: Optional[int], limit: int) -> int:
    dbc = Dbc.load(path)
    values = [v for v in dbc.ids() if (lo is None or v >= lo) and (hi is None or v <= hi)]
    for v in values[:limit]:
        print(v)
    if len(values) > limit:
        print(f"... {len(values) - limit} more")
    print(f"# {len(values)} IDs", file=sys.stderr)
    return 0


def diff(a_path: Path, b_path: Path, ids_field: int = 0, limit: int = 25) -> int:
    a, b = Dbc.load(a_path), Dbc.load(b_path)
    a_map, b_map = a.by_id(ids_field), b.by_id(ids_field)
    added = sorted(set(b_map) - set(a_map))
    removed = sorted(set(a_map) - set(b_map))
    changed = sorted(i for i in set(a_map) & set(b_map) if a_map[i] != b_map[i])
    print(f"{a_path.name} -> {b_path.name}")
    print(f"  added   {len(added)}: {added[:limit]}{'...' if len(added) > limit else ''}")
    print(f"  removed {len(removed)}: {removed[:limit]}{'...' if len(removed) > limit else ''}")
    print(f"  changed {len(changed)}: {changed[:limit]}{'...' if len(changed) > limit else ''}")
    for rid in changed[:min(limit, 5)]:
        for f, (old, new) in enumerate(zip(a_map[rid], b_map[rid])):
            if old != new:
                print(f"    id {rid} field {f}: {old} -> {new}")
    return 0


# ------------------------------------------------------------------------ cli
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Inspect/validate WoW 3.3.5a DBC files.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("info", help="print header and sanity-check the file")
    s.add_argument("dbc", type=Path)

    s = sub.add_parser("ids", help="list IDs (field 0 by default)")
    s.add_argument("dbc", type=Path)
    s.add_argument("--min", type=int)
    s.add_argument("--max", type=int)
    s.add_argument("--limit", type=int, default=50)

    s = sub.add_parser("free", help="find free ID ranges inside a window")
    s.add_argument("dbc", type=Path)
    s.add_argument("--from", dest="lo", type=int, required=True)
    s.add_argument("--to", dest="hi", type=int, required=True)

    s = sub.add_parser("refs", help="check that a field's values exist in another DBC")
    s.add_argument("dbc", type=Path)
    s.add_argument("--field", type=int, required=True)
    s.add_argument("--must-exist", type=Path, required=True,
                   help="other DBC whose field 0 is the ID namespace being referenced")
    s.add_argument("--fail-on-zero", action="store_true",
                   help="treat 0 as a missing reference instead of 'none'")

    s = sub.add_parser("diff", help="compare two copies of the same table")
    s.add_argument("a", type=Path)
    s.add_argument("b", type=Path)
    s.add_argument("--id-field", type=int, default=0)

    s = sub.add_parser("verify-pair", help="client patch vs server DBC equality check")
    s.add_argument("a", type=Path)
    s.add_argument("b", type=Path)
    s.add_argument("--id-field", type=int, default=0)

    s = sub.add_parser("dump", help="dump numeric records to CSV")
    s.add_argument("dbc", type=Path)
    s.add_argument("--out", type=Path, required=True)

    s = sub.add_parser("strings", help="print the string block entries reachable from string-ish fields")
    s.add_argument("dbc", type=Path)
    s.add_argument("--limit", type=int, default=20)

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "info":
            return check_header(args.dbc)
        if args.cmd == "ids":
            return list_ids(args.dbc, args.min, args.max, args.limit)
        if args.cmd == "free":
            return check_free_ids(args.dbc, args.lo, args.hi)
        if args.cmd == "refs":
            return check_refs(args.dbc, args.field, args.must_exist,
                              allow_zero=not args.fail_on_zero)
        if args.cmd == "diff":
            return diff(args.a, args.b, args.id_field)
        if args.cmd == "verify-pair":
            return check_pair(args.a, args.b, args.id_field)
        if args.cmd == "dump":
            return dump_csv(args.dbc, args.out)
        if args.cmd == "strings":
            dbc = Dbc.load(args.dbc)
            fields = dbc.string_fields()
            if not fields:
                print("no string-like fields detected", file=sys.stderr)
                return 1
            for idx, row in enumerate(dbc.records[:args.limit]):
                values = [dbc.string(row[f]) for f in fields[:3]]
                print(f"{idx} id={row[0]}: " + " | ".join(v for v in values if v))
            return 0
    except DbcError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
