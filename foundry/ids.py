"""Per-table ID registry: limits, allocation windows, collision detection.

ID policy follows docs/04 section 4 (reserved ranges per feature) with one
hardening: every table has a numeric ceiling from its column type, and the
registry refuses IDs the column cannot hold. The canonical trap is
``game_event.eventEntry`` (tinyint unsigned, 0-255) — a 900xxx ID there is
not "custom", it is a truncated value.
"""

from __future__ import annotations

from typing import Dict, Optional, Set

# table -> (min, max, why). Only tables the generators emit are listed;
# absence means "no known ceiling" and the check says so (SKIPPED upstream).
TABLE_LIMITS: Dict[str, tuple] = {
    "item_template": (0, 2**31 - 1, "int32 entry"),
    "spell_dbc": (0, 2**31 - 1, "int32 id"),
    "quest_template": (0, 2**31 - 1, "int32 entry"),
    "creature_template": (0, 2**31 - 1, "int32 entry"),
    "gameobject_template": (0, 2**31 - 1, "int32 entry"),
    "npc_vendor": (0, 2**31 - 1, "int32 entry"),
    "game_event": (0, 255, "tinyint unsigned eventEntry"),
    "creature": (0, 2**31 - 1, "int32 guid"),
    "gameobject": (0, 2**31 - 1, "int32 guid"),
    "page_text": (0, 2**31 - 1, "int32 ID"),
    "conditions": (0, 2**31 - 1, "int32 SourceEntry"),
}

# Reserved windows used by the worked examples (examples/INDEX.md). New
# specs must not allocate inside another feature's window.
EXAMPLE_WINDOWS = {
    "ashen-vault": (900100, 900299),
    "vault-terminal": (900300, 900300),
    "reliquary": (900400, 900499),
    "ritual-gate": (900500, 900599),
    "fair-weather-market": (900600, 900699),
    "two-cities": (900700, 900799),
}


def check_id(table: str, value: int) -> Optional[str]:
    """Return a problem string, or None if the id fits the table."""
    if table not in TABLE_LIMITS:
        return None
    lo, hi, why = TABLE_LIMITS[table]
    if not (lo <= value <= hi):
        return f"id {value} does not fit {table} ({why}: {lo}..{hi})"
    return None


def collisions(ids: Set[int], baseline: Set[int]) -> Set[int]:
    return set(ids) & set(baseline)


def window_overlaps(start: int, end: int, windows=EXAMPLE_WINDOWS) -> list:
    """Names of example windows overlapping [start, end]."""
    return [name for name, (lo, hi) in windows.items()
            if not (end < lo or start > hi)]


def free_window(used: Set[int], lo: int, hi: int, count: int) -> Optional[int]:
    """First start of a free run of *count* ids in [lo, hi], or None."""
    if count <= 0 or lo > hi:
        return None
    run_start = lo
    run = 0
    for v in range(lo, hi + 1):
        if v in used:
            run = 0
            run_start = v + 1
        else:
            run += 1
            if run >= count:
                return run_start
    return None
