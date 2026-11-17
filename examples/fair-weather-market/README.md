# The Fair-Weather Market — a shop that exists twelve hours a week

A vendor whom the world spawns and hides on a schedule, with stock gated per player by conditions.
Three systems, no code: `game_event`, `npc_vendor`, `conditions`.

| | |
|---|---|
| **Patch cost** | none |
| **Reused client assets** | `CreatureDisplayInfo`, stock consumable item entries, the merchant window |
| **Clock** | 1 — event, vendor and condition rows |

## The one number that will bite you

`game_event.eventEntry` is **`tinyint unsigned` — 0 to 255**. Every other entry in this project lives
in a 900xxx range; this table cannot. Reserve a high value (240 here) and record it, or you will
collide with Blizzard's own events, which occupy the low numbers.

| Column | Unit | Here |
|---|---|---|
| `occurence` | **minutes** between starts | `10080` (weekly) |
| `length` | **minutes** it lasts | `720` (12 hours) |
| `holiday` | client `HolidayId` | `0` — a holiday ID would make the client draw holiday dressing |
| `start_time` / `end_time` | absolute window | 2026 → 2030 |

## Files

```
sql/01_event.sql   the event, the vendor, the spawn, and the event→spawn link
sql/02_stock.sql   four stock rows and three conditions that gate one of them
sql/99_rollback.sql
```

## Install

```bash
mysql -u acore -p acore_world < sql/01_event.sql
mysql -u acore -p acore_world < sql/02_stock.sql
```

Restart, then `.event start 240`. She spawns in Elwynn (map `0`) at the coordinates in `01_event.sql`.

## The interesting part: four rows of predicate

The veteran trinket is stocked only if **(attuned OR Revered) AND the market is open**:

| `ElseGroup` | `ConditionTypeOrReference` | Meaning |
|---|---|---|
| 1 | `8` `CONDITION_QUESTREWARDED` | quest rewarded |
| 1 | `5` `CONDITION_REPUTATION_RANK` | or Revered with the faction |
| 2 | `12` `CONDITION_ACTIVE_EVENT` | and the event is running |

Same `ElseGroup` = OR; different groups = AND. `SourceGroup` is the vendor's entry, `SourceEntry` the
item's — verified in `ConditionMgr.cpp` (`NpcVendorConditionContainerStore[SourceGroup][SourceEntry]`),
because guessing that wrong yields a vendor that silently sells everything to everyone.

## Acceptance

| Check | How |
|---|---|
| The vendor is absent | log in outside the window — no NPC at the spot |
| The event opens it | `.event start 240` → she appears with three cheap items |
| Stock is per player | a fresh character sees three items; your attuned character sees four |
| The rotation works | `maxcount 1, incrtime 3600` → one trinket per hour, then "out of stock" |
| The schedule is real | `.event list`, then compare with `occurence`/`length` |
| Closing works | `.event stop 240` → NPC gone, vendor window closed |
| Rollback is clean | `99_rollback.sql`, including the conditions rows |

## Where it stops being free

| Change | Cost |
|---|---|
| Different schedule, different stock | none |
| Prices in a custom currency | `ItemExtendedCost.dbc` → **DBC rows** |
| A holiday-themed stall | `holiday` non-zero reuses stock client dressing — still none |
| New items on the stall | `Item.dbc` → **DBC rows** |

## Why this layer

Three tables that were built for other purposes — scheduling, vendoring, predicates — composed into
"a shop that opens on Tuesdays and recognises veterans". `docs/10 §2.5` calls this feature flags
without code; `docs/09` files it as `novel.dynamic-vendor` + `novel.feature-flags`.
