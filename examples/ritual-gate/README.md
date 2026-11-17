# The Vigil Gate — a doorway that needs three people

A game object type the core already implements: `18 GAMEOBJECT_TYPE_SUMMONING_RITUAL`. It opens a
ritual circle, counts the players inside it, and casts a spell when enough of them have joined. No
script, no module, no patch — eight `Data` fields and one existing spell.

| | |
|---|---|
| **Patch cost** | none |
| **Reused client assets** | `GameObjectDisplayInfo`, one stock `Spell.dbc` row, stock items |
| **Clock** | 1 — object template + spell reference |

| Element | Reused client asset |
|---|---|
| Gate visual | an existing `GameObjectDisplayInfo` row |
| The ritual animation | an existing spell ID in `Data2` (or `0` for none) |
| What happens at completion | an existing `Spell.dbc` row in `Data1` |
| The reward | a stock chest display + stock items |

## Files

```
sql/01_gate.sql       the ritual object + a door in front of it
sql/02_aftermath.sql  a chest and its loot table
sql/99_rollback.sql   removes all three
```

## The eight fields, honestly

Read from `GameObjectData.h` (`GOData.summoningRitual`), not from a wiki:

| Field | Meaning | Here |
|---|---|---|
| `Data0` | `reqParticipants` | `3` |
| `Data1` | `spellId` cast at completion | a stock spell (§ below) |
| `Data2` | `animSpell` — looping visual | `0` |
| `Data3` | `ritualPersistent` | `0` (object vanishes/returns to idle) |
| `Data4` | `casterTargetSpell` | `0` |
| `Data5` | `casterTargetSpellTargets` | `0` (caster only) |
| `Data6` | `castersGrouped` — require one group | `1` |
| `Data7` | `ritualNoTargetCheck` | `0` |

`Data6 = 1` is the difference between a mechanic and a griefing tool: without it, three strangers
standing nearby complete your ritual.

## Install

```bash
mysql -u acore -p acore_world < sql/01_gate.sql
mysql -u acore -p acore_world < sql/02_aftermath.sql
```

Restart, then `.gobject add 900500` at your feet.

## Acceptance

| Check | How |
|---|---|
| One player is refused | click the gate alone — the core says the ritual needs more participants |
| Three players work | standing in the circle together completes it and the spell fires |
| Grouping is enforced | three ungrouped players near it do **not** complete it (`Data6 = 1`) |
| The door is real | the door in front of the gate is a separate object; open it and walk through |
| The chest pays out | after the ritual, open the cache — loot comes from `gameobject_loot_template` |
| Rollback is clean | `99_rollback.sql`; no ritual, no door, no chest, no loot rows |

## Where this stops being free

| Change | Cost |
|---|---|
| More participants, different spell | none — two numbers and an ID |
| A *new* summon visual | assets — that is art |
| The ritual granting a custom item | `Item.dbc` row → **DBC rows** patch |
| The ritual granting custom currency | `ItemExtendedCost.dbc` → **DBC rows** patch |

## Why this layer

It is a *composition*: no new table, no new code, one object type used for its intended purpose with
the social constraint dialled up. `docs/10 §3.4` treats exactly this as the pattern for "mechanics
that are about people", and `docs/09` files it under `novel.ritual-gate`.
