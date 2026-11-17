# Two Cities — the same coordinates, twice

Two versions of one quartermaster, standing in exactly the same place, each visible to a different
player. This is phasing: a per-player filter applied before the server sends anything, which means
the client is never told that anything happened.

| | |
|---|---|
| **Patch cost** | none |
| **Reused client assets** | one `CreatureDisplayInfo` for both versions, a stock phase aura |
| **Clock** | 1 — spawn masks and an area aura |

| Element | Where it lives |
|---|---|
| Which players see which spawn | `creature.phaseMask` (bit 1 = phase 1, bit 2 = phase 2) |
| How a player changes phase | an aura with a *phase* effect, applied by `spell_area` in an area |
| The tell | two `creature_text` lines, one per version |
| Everything visual | the same `CreatureDisplayInfo` row for both — it is one man |

## Files

```
sql/01_twins.sql                        two templates, one display, two spawns, identical x/y/z
sql/02_how_a_player_enters_phase_two.sql  a spell_area row that applies the phase aura
sql/03_smart_scripts_hint.sql           the two lines of speech, via smart_scripts
sql/99_rollback.sql
```

## Install

```bash
for f in sql/0*.sql; do mysql -u acore -p acore_world < "$f"; done
```

Restart. Both spawns are at the same spot in Ashenvale (map `1`); your phase decides which one you see.
`.aura <phase spell>` forces the change for testing.

## Acceptance

| Check | How |
|---|---|
| One body, not two | you never see both at once, ever |
| Identical coordinates | `SELECT guid, phaseMask, position_x, position_y FROM creature WHERE guid IN (9007001,9007002)` |
| Phase change works | `.aura <phase spell>` on a character standing there → the NPC's name changes |
| It is not a disguise | the other version's gossip/stock can differ entirely (different template entry) |
| Real phasing is area-based | `spell_area`: enter the area, get the aura, phase changes; leave, it reverts |
| Speech proves it | the greeting differs; nothing else on your screen moved |
| Rollback is clean | `99_rollback.sql`: scripts, text, area row, spawns, templates |

## The rule that makes this trustworthy

**Never let phasing be the only record of what a player did.** A phase mask is presentation; the quest
state behind it is the truth. Two spawns at one spot is the cheapest illusion in the game, and also
the easiest way to lose a player in a version of the world you can no longer see. Keep the flag
(quest, achievement, item) authoritative and the phase derived.

## Where it stops being free

| Change | Cost |
|---|---|
| Two phases of existing NPCs | none |
| A rebuilt district (different buildings) | assets — that is geometry |
| A phase aura that does not exist yet | `Spell.dbc` → **DBC rows** |
| Phase-gated vendors/quests | none, via `conditions` (`CONDITION_QUESTSTATE`, `CONDITION_AURA`) |

## Why this layer

`docs/09` files phasing under `novel.phasing-scenes`: two places at the same coordinates, built from a
column and a spell. `docs/10 §2.6` uses it as the worked example of "the world is data".
