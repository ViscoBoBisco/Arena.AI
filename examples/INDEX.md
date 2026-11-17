# Worked examples

Seven examples, each a complete piece of content or behaviour you can load into a 3.3.5a server,
verify in game, and remove again. None of them needs a client patch.

They exist because the knowledge base argues, and an argument you can run is worth more than one you
can only read.

## What "full" means here

Every example directory satisfies the same seven requirements, and `tests/test_tier_engineer_examples.py`
enforces them mechanically:

| # | Requirement | Why it is not optional |
|---|---|---|
| 1 | `README.md` with **what it proves** and a **patch-cost table** | an example that does not say what it costs teaches the wrong lesson |
| 2 | the **reused client assets** named explicitly | reuse is the project's constraint, not a detail |
| 3 | **files** — SQL, presets or protocol, depending on the layer | the artifact is the content |
| 4 | **install** steps that work on a stock AzerothCore world DB | "figure it out" is where readers stop |
| 5 | an **acceptance table**: one row per check a human can perform | untestable instructions are folklore |
| 6 | **where it stops being free** — the nearest variant that needs a patch | this is the boundary that matters |
| 7 | a **rollback** that removes everything the example created | content you cannot un-ship is a liability |

Two more rules hold across the set: every numeric ID lives in a declared reserved range (below), and
every value that could not be verified against client data is marked `TODO(verify)` rather than
presented as fact.

## The examples

| Example | Layer · clock · patch | What it demonstrates | Files |
|---|---|---|---|
| [`ashen-vault`](ashen-vault/README.md) | content + mechanics · 1 · none | a custom dungeon: creatures, spawns, doors, SmartAI boss, loot | 5 SQL + spec |
| [`vault-terminal`](vault-terminal/README.md) | systems · 1–2 · addon folder | a server-fed UI on the addon channel: protocol, model, module, SQL | protocol + Lua + C++ + SQL |
| [`reliquary`](reliquary/README.md) | content · 1 · none | books and plaques as a content system: `page_text` chains on type-9 objects | 3 SQL |
| [`ritual-gate`](ritual-gate/README.md) | novel · 1 · none | a door that needs three people: GO type 18, `reqParticipants` | 3 SQL |
| [`fair-weather-market`](fair-weather-market/README.md) | novel · 1 · none | a scheduled shop with per-player stock: `game_event` + `npc_vendor` + `conditions` | 3 SQL |
| [`two-cities`](two-cities/README.md) | novel · 1 · none | phasing: two NPCs, one spot, `phaseMask` + `spell_area` | 4 SQL |
| [`season-dials`](season-dials/README.md) | policy · 0 · none | a balance pass with no data: two config presets | 2 conf |

Read them in that order if you want the arc the project is arguing: content → interface → text →
composition → scheduling → space → the dials underneath everything.

## Reserved ID ranges

Each example owns a block, so they can be loaded side by side without colliding — the practice
[`docs/04 §4`](../docs/04-patching-toolchain-and-release.md) recommends for a real realm.

| Range | Owner | Notes |
|---|---|---|
| `900100–900299` | ashen-vault | creature entries and object entries, spawn guids `9001xxx`/`9002xxx` |
| `900300` | vault-terminal | NPC entry + spawn `9003001` |
| `900400–900499` | reliquary | `page_text` 900400–900429, objects 900401–900403, guids `9004xxx` |
| `900500–900599` | ritual-gate | objects 900500–900510, guids `9005xxx` |
| `900600–900699` | fair-weather-market | vendor 900600; **the `game_event` entry is 240** — that table is `tinyint` |
| `900700–900799` | two-cities | creatures 900700/900701, guids `9007xxx` |

The exception is deliberate and worth internalising: `game_event.eventEntry` cannot hold a 900xxx
value, because the column is `tinyint unsigned` (0–255). A range policy has to be per table.

## How these were verified

| Claim | Source |
|---|---|
| `Data0…Data23` meanings per GO type | `GameObjectData.h` (the union, `GOData.summoningRitual`, `.chest`, `.spellcaster`) |
| approach/`page_text` shape | `data/sql/base/db_world/page_text.sql` |
| `game_event` units and the `tinyint` ceiling | `data/sql/base/db_world/game_event.sql` (column comments: *minutes*) |
| condition source types, e.g. `NPC_VENDOR = 23` | `Conditions/ConditionMgr.h`, `ConditionMgr.cpp` (`[SourceGroup][SourceEntry]`) |
| `spell_area` columns and status masks | `data/sql/base/db_world/spell_area.sql` |
| `creature_text` uses `CreatureID`/`GroupID`/`Text` | `data/sql/base/db_world/creature_text.sql` |
| a module's loader symbol | `modules/CMakeLists.txt` (`Add${dir with - as _}Scripts()`) |

Everything that could not be read from source or a schema dump — display IDs, item entries, quest and
faction IDs, phase spell IDs — is `TODO(verify)` in the SQL, because those live in client files this
repository does not have.
