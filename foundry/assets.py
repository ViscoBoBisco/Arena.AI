"""Asset intelligence: index what the client already ships, search it.

Two inputs, neither of which distributes proprietary game files:

* a directory of DBC extracts the operator supplies locally (parsed with
  the same WDBC reader as ``tools/dbc_tool.py``), or
* an inventory JSON (``{"assets": [{kind, id, tags, verified}]}``).

The index answers the foundry's central question for any candidate asset:
*does using this need a client patch?* — ``none`` if the id already exists
in the supplied data, ``dbc-rows`` if it must be added as a DBC row,
``assets`` if it needs new art (out of scope for reuse-first work).
"""

from __future__ import annotations

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
    "FactionTemplate.dbc": "faction",
}


class AssetError(Exception):
    pass


def _dbc_ids(path: Path) -> List[int]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AssetError(f"{path}: cannot read ({exc})") from exc
    if len(raw) < HEADER.size:
        raise AssetError(f"{path}: shorter than a DBC header")
    magic, rc, fc, rs, ss = HEADER.unpack_from(raw, 0)
    if magic != MAGIC or rs != fc * 4:
        raise AssetError(f"{path}: not a plain WDBC file")
    fmt = struct.Struct("<i")
    ids = []
    off = HEADER.size
    for _ in range(rc):
        ids.append(fmt.unpack_from(raw, off)[0])
        off += rs
    return ids


def ingest_dbc_dir(root: Path) -> Dict[str, dict]:
    """Index every known DBC in *root* -> {kind: {id: {verified: True}}}."""
    index: Dict[str, dict] = {}
    for filename, kind in DBC_KINDS.items():
        path = root / filename
        if not path.is_file():
            continue
        index[kind] = {str(i): {"verified": True, "source": filename}
                       for i in _dbc_ids(path)}
    return index


def ingest_inventory(path: Path) -> Dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetError(f"{path}: cannot read inventory ({exc})") from exc
    index: Dict[str, dict] = {}
    for asset in data.get("assets", []):
        kind, aid = asset.get("kind"), asset.get("id")
        if kind is None or aid is None:
            raise AssetError(f"{path}: asset without kind/id: {asset!r}")
        index.setdefault(kind, {})[str(aid)] = {
            "verified": bool(asset.get("verified")),
            "tags": list(asset.get("tags") or []),
            "source": asset.get("source", "inventory"),
        }
    return index


def patch_cost(index: Dict[str, dict], kind: str, asset_id) -> str:
    """none | dbc-rows | assets for one candidate asset."""
    if str(asset_id) in index.get(kind, {}):
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
            hits.append({"kind": k, "id": int(aid),
                         "verified": meta.get("verified", False),
                         "tags": meta.get("tags") or [],
                         "patch_cost": patch_cost(index, k, aid)})
    return hits


def summary(index: Dict[str, dict]) -> dict:
    return {k: {"count": len(v),
                "verified": sum(1 for m in v.values() if m.get("verified"))}
            for k, v in sorted(index.items())}
