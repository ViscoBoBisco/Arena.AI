# Season Dials — a whole balance pass with no data at all

Two presets, ten config keys each, opposite intents. Copy one into `worldserver.conf`, `.reload config`,
and the realm's feel changes on the next tick.

| | |
|---|---|
| **Patch cost** | none |
| **Reused client assets** | none needed — the client is never sent these values |
| **Clock** | 0 — applies to the next event that touches it |

## Files

```
presets/harvest.conf   faster, richer, forgiving
presets/siege.conf     slower, poorer, expensive deaths
```

## Install

```bash
# keep a copy of the original section
cp worldserver.conf worldserver.conf.bak
cat presets/harvest.conf >> worldserver.conf      # or paste into the matching lines
# in game, as GM:
.reload config
```

## Acceptance

| Check | How |
|---|---|
| XP changed | kill one mob before and after; compare the bar |
| Drops changed | `Rate.Drop.Money` is visible in the loot window immediately |
| Movement changed | `Rate.MoveSpeed.Player` at 1.15 is noticeable without desync (stay well under 3×) |
| Death cost changed | die once: the corpse-reclaim timer in the tooltip |
| Reputation changed | one quest turn-in moves the bar twice as far |
| Reverting is instant | restore the two lines, `.reload config`, no restart, no relog |
| Nothing was cached | no client file, no `Cache/` purge, no DBC — the values are never sent to the client |

## Where this stops being free

| Want | Layer instead |
|---|---|
| A new item, spell, spawn | content / mechanics — clock 1 |
| A price in a custom currency | `ItemExtendedCost.dbc` — clock 2 |
| Different rules per player | `conditions` on the row, not a global rate |
| A rate that changes by level | not a config key; that is a module or a `Rate.*` per-level table in the core |

## Rollback

There is nothing to unload, which is the whole argument of this layer:

```bash
cp worldserver.conf.bak worldserver.conf
# in game:
.reload config
```

No rows to delete, no objects to despawn, no client cache to purge, no patch to redistribute. If you
kept the backup, you were never more than one command away from the previous realm.

## Why this layer

Every one of these keys is in `catalog/config_keys.txt`, and the test suite refuses a preset that
names a key which does not exist — the cheapest possible guard against the recurring failure in this
project: **a claim nobody checked**. `docs/09 §4` introduces the layer; `docs/08 §2` explains why
these values never reach the client.
