#!/usr/bin/env python3
"""catalog.py - query the alteration catalog (catalog/alterations.json).

The catalog answers one question: *what can I change, what will the player actually experience,
and does it need a client patch?* This tool makes it queryable instead of a wall of prose.

Commands
--------
  python3 tools/catalog.py validate                 # schema + integrity, exit 1 on problems
  python3 tools/catalog.py list                     # id / patch / clock / layer / title
  python3 tools/catalog.py query --no-mpq           # everything that needs no MPQ change
  python3 tools/catalog.py query --layer content --search vendor
  python3 tools/catalog.py query --clock 0          # changes that take effect immediately
  python3 tools/catalog.py show content.quest-new   # full record
  python3 tools/catalog.py surfaces                 # what data you would be touching
  python3 tools/catalog.py matrix                   # layer x patch grid
  python3 tools/catalog.py stats                    # counts

Patch kinds
-----------
  none          server-side only; the client already resolves everything involved
  client-files  loose client files (addon folder); no MPQ rebuild
  dbc-rows      rows added/overridden in an existing DBC, shipped in an MPQ
  assets        new models/textures/maps/interface; out of scope for reuse-first work

Clocks
------
  0  applies to the next relevant event (config dial)
  1  server re-reads it (reload/restart); client caches may still mask it
  2  the client must be restarted with new files before anything appears

Exit codes: 0 ok, 1 validation failure, 2 usage/IO error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

REPO = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = REPO / "catalog" / "alterations.json"
LAYER_PREFIX = {
    "policy": "policy.",
    "content": "content.",
    "mechanics": "mech.",
    "systems": "system.",
    "presentation": "client.",
    "novel": "novel.",
}
CLOCKS = (0, 1, 2)


class CatalogError(Exception):
    pass


# ------------------------------------------------------------------ loading
def load(path: Path) -> Dict:
    if not path.is_file():
        raise CatalogError(f"{path}: catalog not found")
    try:
        # utf-8-sig: editors on Windows routinely add a BOM, which plain json.loads rejects
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise CatalogError(f"{path}: invalid JSON ({exc})") from exc
    if "entries" not in data:
        raise CatalogError(f"{path}: no 'entries' key")
    return data


# --------------------------------------------------------------- validation
def _allowlist(path: Path) -> Optional[set]:
    """Load a one-name-per-line allowlist, ignoring comments. Missing file -> None (skip check)."""
    if not path.is_file():
        return None
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.add(line)
    return names


def validate(data: Dict, base: Path = REPO) -> List[str]:
    problems: List[str] = []
    layers = {layer["id"] for layer in data.get("layers", [])}
    kinds = {kind["id"] for kind in data.get("patch_kinds", [])}
    tables = _allowlist(base / "catalog" / "world_tables.txt")
    conf_keys = _allowlist(base / "catalog" / "config_keys.txt")
    unknown_surfaces: List[str] = []
    if not layers:
        problems.append("no layers defined")
    if not kinds:
        problems.append("no patch_kinds defined")

    seen: Dict[str, int] = {}
    doc_dirs = {p.stem.split("-")[0]: p for p in (base / "docs").glob("[0-9][0-9]-*.md")}

    for index, entry in enumerate(data.get("entries", [])):
        where = entry.get("id") or f"entries[{index}]"
        for field in ("id", "title", "layer", "patch", "clock", "surfaces", "effect", "verify", "docs"):
            if field not in entry:
                problems.append(f"{where}: missing field '{field}'")
        if "id" not in entry:
            continue
        entry_id = entry["id"]
        seen[entry_id] = seen.get(entry_id, 0) + 1

        layer = entry.get("layer")
        if layer not in layers:
            problems.append(f"{entry_id}: unknown layer '{layer}'")
        elif not entry_id.startswith(LAYER_PREFIX[layer]):
            problems.append(
                f"{entry_id}: id should start with '{LAYER_PREFIX[layer]}' for layer '{layer}'"
            )

        if entry.get("patch") not in kinds:
            problems.append(f"{entry_id}: unknown patch kind '{entry.get('patch')}'")
        if entry.get("clock") not in CLOCKS:
            problems.append(f"{entry_id}: clock must be one of {CLOCKS}")

        for field in ("surfaces", "verify", "docs"):
            value = entry.get(field)
            if not isinstance(value, list) or not value:
                problems.append(f"{entry_id}: '{field}' must be a non-empty list")

        if not str(entry.get("effect", "")).strip():
            problems.append(f"{entry_id}: empty effect")

        for surface in entry.get("surfaces", []):
            if ":" not in surface:
                problems.append(f"{entry_id}: surface '{surface}' should be 'kind:target'")
                continue
            kind, rest = surface.split(":", 1)
            target = rest.split(":", 1)[0]          # table:creature_template:HealthModifier -> creature_template
            if kind not in {"conf", "table", "dbc", "file", "addon", "code"}:
                problems.append(f"{entry_id}: unknown surface kind '{kind}'")
            elif kind == "table" and tables is not None and target not in tables:
                unknown_surfaces.append(f"{entry_id}: table '{target}' is not in catalog/world_tables.txt")
            elif kind == "conf" and conf_keys is not None and target not in conf_keys:
                unknown_surfaces.append(f"{entry_id}: conf key '{target}' is not in catalog/config_keys.txt")

        for ref in entry.get("docs", []):
            if ref not in doc_dirs:
                problems.append(f"{entry_id}: doc reference '{ref}' has no docs/{ref}-*.md")

    for entry_id, count in seen.items():
        if count > 1:
            problems.append(f"duplicate id '{entry_id}' ({count} entries)")

    for line in sorted(set(unknown_surfaces)):
        problems.append(line)

    return problems


# ----------------------------------------------------------------- filtering
def select(data: Dict, args: argparse.Namespace) -> List[Dict]:
    entries = data["entries"]
    if getattr(args, "layer", None):
        entries = [e for e in entries if e["layer"] == args.layer]
    if getattr(args, "patch", None):
        entries = [e for e in entries if e["patch"] == args.patch]
    if getattr(args, "clock", None) is not None:
        entries = [e for e in entries if e["clock"] == args.clock]
    if getattr(args, "no_mpq", False):
        entries = [e for e in entries if e["patch"] in ("none", "client-files")]
    if getattr(args, "only_patch", False):
        entries = [e for e in entries if e["patch"] in ("dbc-rows", "assets")]
    search = getattr(args, "search", None)
    if search:
        needle = search.lower()
        entries = [
            e for e in entries
            if needle in json.dumps(e).lower()
        ]
    return entries


# ------------------------------------------------------------------ output
def print_table(entries: Sequence[Dict]) -> None:
    if not entries:
        print("no matching entries")
        return
    widths = {
        "id": max(len("id"), max(len(e["id"]) for e in entries)),
        "patch": max(len("patch"), max(len(e["patch"]) for e in entries)),
    }
    header = f"{'id':<{widths['id']}}  {'patch':<{widths['patch']}}  clock  title"
    print(header)
    print("-" * len(header))
    for entry in entries:
        print(f"{entry['id']:<{widths['id']}}  {entry['patch']:<{widths['patch']}}  "
              f"{entry['clock']:^5}  {entry['title']}")
    print(f"\n{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}")


def print_entry(entry: Dict, kinds: Dict[str, Dict]) -> None:
    kind = kinds.get(entry["patch"], {})
    print(f"{entry['title']}  [{entry['id']}]")
    print("=" * (len(entry["title"]) + len(entry["id"]) + 4))
    print(f"layer   : {entry['layer']}")
    print(f"client  : {entry['patch']} - {kind.get('title', '?')} ({kind.get('detail', '')})")
    print(f"clock   : {entry['clock']} "
          f"({'next event' if entry['clock'] == 0 else 'after reload/restart' if entry['clock'] == 1 else 'after client patch + restart'})")
    print("\nsurfaces:")
    for surface in entry["surfaces"]:
        print(f"  - {surface}")
    print(f"\nin play : {entry['effect']}")
    print("\nverify:")
    for step in entry["verify"]:
        print(f"  - {step}")
    if entry.get("notes"):
        print(f"\nnotes  : {entry['notes']}")
    print(f"\nreading: {', '.join('docs/' + d for d in entry['docs'])}")


# ----------------------------------------------------------------- commands
def cmd_validate(data: Dict, args: argparse.Namespace) -> int:
    problems = validate(data)
    if problems:
        for problem in problems:
            print(f"FAIL  {problem}")
        print(f"\ncatalog: {len(problems)} problem(s)")
        return 1
    print(f"catalog: OK ({len(data['entries'])} entries, "
          f"{len(data['layers'])} layers, {len(data['patch_kinds'])} patch kinds)")
    return 0


def cmd_list(data: Dict, args: argparse.Namespace) -> int:
    entries = select(data, args)
    if args.json:
        print(json.dumps(entries, indent=2, ensure_ascii=False))
    else:
        print_table(entries)
    return 0


def cmd_show(data: Dict, args: argparse.Namespace) -> int:
    kinds = {kind["id"]: kind for kind in data["patch_kinds"]}
    for entry in data["entries"]:
        if entry["id"] == args.id:
            if args.json:
                print(json.dumps(entry, indent=2, ensure_ascii=False))
            else:
                print_entry(entry, kinds)
            return 0
    matches = [e["id"] for e in data["entries"] if args.id.lower() in e["id"].lower()]
    if matches:
        print(f"error: no entry '{args.id}'. Did you mean: {', '.join(matches[:5])}", file=sys.stderr)
    else:
        print(f"error: no entry '{args.id}'", file=sys.stderr)
    return 2


def cmd_surfaces(data: Dict, args: argparse.Namespace) -> int:
    groups: Dict[str, Dict[str, List[str]]] = {}
    for entry in select(data, args):
        for surface in entry["surfaces"]:
            kind, target = surface.split(":", 1)
            groups.setdefault(kind, {}).setdefault(target, []).append(entry["id"])
    for kind in sorted(groups):
        print(f"== {kind} ==")
        for target in sorted(groups[kind], key=lambda t: (-len(groups[kind][t]), t)):
            users = groups[kind][target]
            print(f"  {target:<40} {len(users):>2} entr{'y' if len(users) == 1 else 'ies'}")
        print()
    print(f"{sum(len(v) for v in groups.values())} distinct surfaces")
    return 0


def cmd_matrix(data: Dict, args: argparse.Namespace) -> int:
    layers = [layer["id"] for layer in data["layers"]]
    kinds = [kind["id"] for kind in data["patch_kinds"]]
    counts = {(layer, kind): 0 for layer in layers for kind in kinds}
    for entry in data["entries"]:
        counts[(entry["layer"], entry["patch"])] += 1

    width = max(len(k) for k in kinds) + 2
    print(f"{'layer':<14}" + "".join(f"{k:>{width}}" for k in kinds) + f"{'total':>8}")
    for layer in layers:
        row = f"{layer:<14}" + "".join(f"{counts[(layer, k)] or '.':>{width}}" for k in kinds)
        print(row + f"{sum(counts[(layer, k)] for k in kinds):>8}")
    totals = f"{'total':<14}" + "".join(
        f"{sum(counts[(layer, k)] for layer in layers):>{width}}" for k in kinds)
    print("-" * (14 + width * len(kinds) + 8))
    print(totals + f"{len(data['entries']):>8}")
    return 0


def cmd_stats(data: Dict, args: argparse.Namespace) -> int:
    entries = data["entries"]
    print(f"catalog: {data.get('client', '?')}  ({len(entries)} entries)")
    for field, values in (("layer", [l["id"] for l in data["layers"]]),
                          ("patch", [k["id"] for k in data["patch_kinds"]]),
                          ("clock", [0, 1, 2])):
        print(f"\nby {field}:")
        for value in values:
            count = sum(1 for e in entries if e[field] == value)
            print(f"  {str(value):<14} {count:>3}")
    no_mpq = sum(1 for e in entries if e["patch"] in ("none", "client-files"))
    print(f"\nno MPQ change required: {no_mpq}/{len(entries)} "
          f"({no_mpq * 100 // len(entries)}%)")
    return 0


# --------------------------------------------------------------------- cli
def add_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--layer", help="policy | content | mechanics | systems | presentation")
    parser.add_argument("--patch", help="none | client-files | dbc-rows | assets")
    parser.add_argument("--clock", type=int, choices=CLOCKS)
    parser.add_argument("--no-mpq", action="store_true", help="only none + client-files")
    parser.add_argument("--only-patch", action="store_true", help="only dbc-rows + assets")
    parser.add_argument("--search", help="substring match over the whole record")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query what can be altered on a 3.3.5a server and how it shows up in play."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="check the catalog's schema and integrity")
    p.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)

    for name, help_text in (("list", "list entries as a table"), ("query", "list entries (filterable)")):
        p = sub.add_parser(name, help=help_text)
        add_filters(p)

    p = sub.add_parser("show", help="full record for one entry")
    p.add_argument("id")
    p.add_argument("--json", action="store_true")
    p.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)

    for name, help_text in (("surfaces", "group entries by the data they touch"),
                            ("matrix", "layer x patch grid"),
                            ("stats", "counts by layer, patch kind and clock")):
        p = sub.add_parser(name, help=help_text)
        add_filters(p)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        data = load(getattr(args, "catalog", DEFAULT_CATALOG))
        handlers = {
            "validate": cmd_validate,
            "list": cmd_list,
            "query": cmd_list,
            "show": cmd_show,
            "surfaces": cmd_surfaces,
            "matrix": cmd_matrix,
            "stats": cmd_stats,
        }
        return handlers[args.command](data, args)  # type: ignore[no-any-return]
    except CatalogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
