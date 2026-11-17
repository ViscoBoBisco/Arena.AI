"""Shared fixtures for the test tiers.

Deliberately tiny: every DBC here is synthesised, so the suite runs with no WoW files and no
third-party packages. Imported by test_tier_*.py.
"""

from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
DBC_TOOL = TOOLS / "dbc_tool.py"
PREFLIGHT = TOOLS / "preflight.sh"
MANIFEST = TOOLS / "manifest.py"
CATALOG_TOOL = TOOLS / "catalog.py"
CATALOG = REPO / "catalog" / "alterations.json"


def dbc_bytes(field_count: int, rows, strings: bytes = b"\0", *,
              record_count: int = None, record_size: int = None,
              string_size: int = None, magic: bytes = b"WDBC") -> bytes:
    """Build a WDBC file. Overrides exist so tests can produce self-consistent-but-wrong files."""
    rows = list(rows)
    rc = len(rows) if record_count is None else record_count
    rs = field_count * 4 if record_size is None else record_size
    ss = len(strings) if string_size is None else string_size
    header = struct.pack("<4sIIII", magic, rc, field_count, rs, ss)
    body = b"".join(struct.pack("<" + "i" * field_count, *row) for row in rows)
    return header + body + strings


def write_dbc(path: Path, field_count: int, rows, strings: bytes = b"\0", **kwargs) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dbc_bytes(field_count, rows, strings, **kwargs))
    return path


def item_pair(root: Path, *, item_display: int = 41179, dangling: bool = False,
              drift: bool = False) -> tuple:
    """A minimal but internally consistent client/server DBC pair for preflight."""
    client, server = root / "client", root / "server"
    write_dbc(client / "ItemDisplayInfo.dbc", 25, [(41179, *([0] * 24))])
    write_dbc(client / "Item.dbc", 8,
              [(800000, 2, 7, -1, 1, 999999 if dangling else item_display, 17, 3)])
    write_dbc(client / "SpellIcon.dbc", 2, [(1, 0)])
    write_dbc(client / "SpellVisual.dbc", 2, [(1, 0)])
    write_dbc(client / "Spell.dbc", 234, [(800000, *([0] * 130), 1, 0, 1, *([0] * 100))])
    server.mkdir(parents=True, exist_ok=True)
    for name in ("Item.dbc", "ItemDisplayInfo.dbc", "SpellIcon.dbc", "SpellVisual.dbc", "Spell.dbc"):
        (server / name).write_bytes((client / name).read_bytes())
    if drift:
        write_dbc(server / "Item.dbc", 8, [(800000, 2, 7, -1, 1, item_display, 13, 3)])
    return client, server


def run_tool(tool: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(tool), *map(str, args)],
                          capture_output=True, text=True)


def run_bash(script: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run([str(script), *map(str, args)], capture_output=True, text=True)


def load_catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8-sig"))


def import_tool(name: str):
    """Import a tools/*.py module in-process (fast, for fuzzing and mutation tests)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    # dataclasses look their module up in sys.modules while decorating; register before exec
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module
