#!/usr/bin/env python3
"""selftest.py - build synthetic DBC files and exercise dbc_tool.py against them.

Run:  python3 selftest.py

No WoW client files needed: the fixtures are generated here, so this also documents
the on-disk layout the tool expects (WDBC header, int32 records, string block).
"""

from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE / "dbc_tool.py"


def build_dbc(path: Path, field_count: int, records, strings: bytes = b"\0") -> Path:
    record_size = field_count * 4
    header = struct.pack("<4sIIII", b"WDBC", len(records), field_count, record_size, len(strings))
    body = b"".join(struct.pack("<" + "i" * field_count, *row) for row in records)
    path.write_bytes(header + body + strings)
    return path


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True)


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal failures
        status = "ok  " if cond else "FAIL"
        print(f"[{status}] {name}{(' - ' + detail) if (detail and not cond) else ''}")
        if not cond:
            failures += 1

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # ItemDisplayInfo-like table: id + icon string offset + model string offset
        strings = b"\0INV_Misc_QuestionMark\0Sword_2H_Claymore_A_01.mdx\0"
        icon_off = 1
        model_off = 1 + len("INV_Misc_QuestionMark") + 1
        displays = build_dbc(
            tmp / "ItemDisplayInfo.dbc", 3,
            [(100, icon_off, model_off), (101, icon_off, model_off)],
            strings,
        )

        # Item-like table: the real 3.3.5a layout is 8 int fields
        items = build_dbc(
            tmp / "Item.dbc", 8,
            [
                (800000, 2, 7, -1, 1, 100, 17, 3),   # exists
                (800001, 4, 0, -1, 7, 101, 5, 0),    # exists
                (800002, 4, 0, -1, 7, 999, 5, 0),    # dangling DisplayInfoID
            ],
        )

        r = run("info", str(items))
        check("info accepts a valid file", r.returncode == 0, r.stderr.strip())
        check("info reports the 8-field layout", "fields         8" in r.stdout, r.stdout)

        r = run("refs", str(items), "--field", "5", "--must-exist", str(displays))
        check("refs catches a dangling display id", r.returncode == 1 and "999" in r.stderr, r.stderr)

        r = run("free", str(items), "--from", "800000", "--to", "800010")
        check("free reports the used window", r.returncode == 0 and "800003-800010" in r.stdout, r.stdout)

        r = run("verify-pair", str(items), str(items))
        check("verify-pair passes for identical files", r.returncode == 0, r.stderr)

        # same table, one extra record -> must warn
        items2 = build_dbc(
            tmp / "Item.new.dbc", 8,
            [(800000, 2, 7, -1, 1, 100, 17, 3),
             (800001, 4, 0, -1, 7, 101, 5, 0),
             (800002, 4, 0, -1, 7, 999, 5, 0),
             (800003, 4, 0, -1, 7, 101, 5, 0)],
        )
        r = run("verify-pair", str(items), str(items2))
        check("verify-pair catches a client/server drift", r.returncode == 1 and "800003" in r.stdout,
              r.stdout + r.stderr)

        # wrong field count (the classic "extracted file from the wrong patch" error)
        bad = build_dbc(tmp / "Bad.dbc", 12, [(1,) * 12])
        r = run("info", str(bad))
        check("info accepts any self-consistent field count", r.returncode == 0, r.stderr)

        # truncated file -> must fail loudly
        trunc = tmp / "Trunc.dbc"
        trunc.write_bytes(items.read_bytes()[:-5])
        r = run("info", str(trunc))
        check("info rejects a truncated file", r.returncode == 1 and "size mismatch" in r.stderr, r.stderr)

        # duplicate IDs -> warning + exit 1
        dupes = build_dbc(tmp / "Dupes.dbc", 2, [(5, 0), (5, 0), (6, 0)])
        r = run("info", str(dupes))
        check("info reports duplicate IDs", r.returncode == 1 and "duplicate" in r.stderr.lower(), r.stderr)

        r = run("dump", str(items), "--out", str(tmp / "Item.csv"))
        csv_text = (tmp / "Item.csv").read_text()
        check("dump writes one row per record", r.returncode == 0 and
              len([x for x in csv_text.splitlines() if x.strip()]) == 3, csv_text)

    print()
    print("selftest:", "PASS" if failures == 0 else f"{failures} FAILURE(S)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
