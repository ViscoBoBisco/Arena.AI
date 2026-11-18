"""Asset intelligence: index what the client already ships, search it.

Two inputs, neither of which distributes proprietary game files:

* a directory of DBC extracts the operator supplies locally (parsed with
  the same WDBC reader as ``tools/dbc_tool.py``), or
* an inventory JSON for discovery only. Inventory ``verified`` fields are
  descriptive and are never accepted as provenance.

The index answers the foundry's central question for any candidate asset:
*does using this need a client patch?* — ``none`` if the id already exists
in a parsed local DBC, ``dbc-rows`` if local provenance does not prove the row,
``assets`` if it needs new art (out of scope for reuse-first work).
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Dict, List, Optional

HEADER = struct.Struct("<4sIIII")
MAGIC = b"WDBC"

# DBC filename -> asset kind used by the foundry's search vocabulary.
DBC_KINDS = {
    "ItemDisplayInfo.dbc": "item_display",
    "CreatureDisplayInfo.dbc": "creature_display",
    "GameObjectDisplayInfo.dbc": "gameobject_display",
    "SpellVisual.dbc": "spell_visual",
    "SpellIcon.dbc": "spell_icon",
    "Spell.dbc": "spell",
    "Item.dbc": "item",
    "CreatureModelData.dbc": "creature_model",
    "GameObjectModelData.dbc": "gameobject_model",
    "SoundEntries.dbc": "sound",
    "Emotes.dbc": "emote",
    "AnimationData.dbc": "animation",
    "SkillLine.dbc": "skill",
    "Faction.dbc": "reputation_faction",
    "FactionTemplate.dbc": "faction_template",
}


class AssetError(Exception):
    pass


class AssetIndex(dict):
    """Parsed local-DBC index carrying the source root for freshness checks."""

    def __init__(self, *args, source_root: Path, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_root = source_root.resolve()


def _dbc_ids(path: Path) -> List[int]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AssetError(f"{path}: cannot read ({exc})") from exc
    if len(raw) < HEADER.size:
        raise AssetError(f"{path}: shorter than a DBC header")
    magic, record_count, field_count, record_size, string_size = HEADER.unpack_from(raw, 0)
    if (magic != MAGIC or field_count < 1 or record_size != field_count * 4
            or HEADER.size + record_count * record_size + string_size != len(raw)):
        raise AssetError(f"{path}: invalid WDBC header or truncated/extra data")
    fmt = struct.Struct("<i")
    ids = []
    off = HEADER.size
    for _ in range(record_count):
        ids.append(fmt.unpack_from(raw, off)[0])
        off += record_size
    if len(ids) != len(set(ids)):
        raise AssetError(f"{path}: duplicate first-column IDs")
    return ids


def ingest_dbc_dir(root: Path) -> Dict[str, dict]:
    """Parse supplied DBC extracts and attach content-addressed provenance."""
    if not root.is_dir():
        raise AssetError(f"{root}: DBC source directory does not exist")
    index: Dict[str, dict] = AssetIndex(source_root=root)
    for filename, kind in DBC_KINDS.items():
        path = root / filename
        if not path.exists():
            continue
        if not path.is_file():
            raise AssetError(f"{path}: expected a regular DBC file")
        ids = _dbc_ids(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        index[kind] = {
            str(asset_id): {
                "verified": True,
                "source": filename,
                "evidence": {
                    "schema": "arena-asset-evidence/1",
                    "source": "local_dbc",
                    "table": filename,
                    "sha256": digest,
                    "row_id": asset_id,
                },
            }
            for asset_id in ids
        }
    return index


def ingest_inventory(path: Path) -> Dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetError(f"{path}: cannot read inventory ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("assets", []), list):
        raise AssetError(f"{path}: expected an object containing an assets array")
    index: Dict[str, dict] = {}
    for asset in data.get("assets", []):
        if not isinstance(asset, dict):
            raise AssetError(f"{path}: asset entry must be an object: {asset!r}")
        kind, aid = asset.get("kind"), asset.get("id")
        if not isinstance(kind, str) or not kind or not isinstance(aid, int) or isinstance(aid, bool):
            raise AssetError(f"{path}: asset requires string kind and integer id: {asset!r}")
        index.setdefault(kind, {})[str(aid)] = {
            # A JSON boolean is an author claim, never source verification.
            "verified": False,
            "claimed_verified": asset.get("verified") is True,
            "tags": list(asset.get("tags") or []),
            "source": asset.get("source", "inventory"),
        }
    return index


def patch_cost(index: Dict[str, dict], kind: str, asset_id) -> str:
    """Conservative display estimate; only parsed local DBCs prove reuse."""
    meta = index.get(kind, {}).get(str(asset_id))
    if meta and meta.get("verified"):
        return "none"
    if kind in DBC_KINDS.values():
        return "dbc-rows"
    return "assets"


def search(index: Dict[str, dict], query: str, *, kind: Optional[str] = None) -> List[dict]:
    """Keyword search over kinds, ids and tags. Deterministic order."""
    q = query.strip().lower()
    hits = []
    for k, entries in sorted(index.items()):
        if kind and k != kind:
            continue
        for aid, meta in sorted(entries.items(), key=lambda kv: int(kv[0])):
            hay = " ".join([k, aid] + list(meta.get("tags") or [])).lower()
            if q and q not in hay:
                continue
            hit = {"kind": k, "id": int(aid),
                   "verified": meta.get("verified", False),
                   "tags": meta.get("tags") or [],
                   "patch_cost": patch_cost(index, k, aid)}
            if meta.get("claimed_verified"):
                hit["claimed_verified"] = True
            if meta.get("evidence") is not None:
                hit["evidence"] = meta["evidence"]
            hits.append(hit)
    return hits


def summary(index: Dict[str, dict]) -> dict:
    return {k: {"count": len(v),
                "verified": sum(1 for m in v.values() if m.get("verified"))}
            for k, v in sorted(index.items())}
