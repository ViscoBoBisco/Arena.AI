# The Ashen Vault — a zero-client-patch custom feature

A worked example of the project's thesis: a complete new piece of content built only from parts
the 3.3.5a client already ships. **No MPQ, no DBC rows, no patch, no launcher change.** Everything
here is server-side data plus one small script hook.

Use it as a template. The shape of the work matters more than the specific numbers: every ID below
is either an entry the server owns (9xxxxx range) or a *display* ID that must already exist in the
client's DBCs.

## What the feature is

A sealed vault in a cave in Ashenvale (map `1`). A Warden NPC outside gates access; inside, two
Sentinel constructs guard a Vault Keeper that enrages, summons adds, and drops loot on death.

| Element | Reused client asset |
|---|---|
| Warden, Sentinels, Vault Keeper | existing `CreatureDisplayInfo` rows (pick any that fit the fiction) |
| Vault door, braziers | existing `GameObjectDisplayInfo` rows |
| Enrage / summon visuals | existing `Spell.dbc` rows cast by SmartAI |
| Loot | existing item entries |
| Access gate | an existing key item + a server-side check |

Nothing in the left column is new to the client, so nothing has to be patched into it.

## Why each layer is where it is

| Concern | Layer | Why not the other |
|---|---|---|
| Creature names, levels, flags, factions | `creature_template` (server SQL) | Names/flags are sent at query time — see [docs/08 §4](../../docs/08-how-integration-actually-works.md) |
| Creature *appearance* | `creature_template_model.CreatureDisplayID` → existing client display | The client draws it; it needs no server row at all if the display exists |
| Behaviour | `smart_scripts` (+ optional module for inventory checks) | Behaviour is server authority |
| Loot | `creature_loot_template` | Delivered in the loot window packet |
| Door/brazier visuals | `gameobject_template.displayId` → existing display | Same argument as creatures |
| Access gate | script hook / conditions | Needs an inventory check SmartAI can't express cleanly |

If the Warden had to sell something for a custom token, the price would live in
`ItemExtendedCost.dbc` — **that** would force a client patch. The example deliberately uses a
scripted hand-in instead, so the patch count stays at zero. See
[docs/02](../../docs/02-feature-integration-matrix.md) for the full decision table.

## Files

```
README.md                 this page
sql/01_creature_templates.sql   creature_template + creature_template_model
sql/02_spawns.sql               creature spawn rows (map 1)
sql/03_gameobjects.sql          gameobject_template + spawns
sql/04_smart_scripts.sql        boss and guard behaviour
sql/05_loot.sql                 boss loot (existing items only)
sql/99_rollback.sql             removes everything above, by entry range
```

Run them in order against `acore_world` (or your core's world DB), then restart the worldserver
and `.reload creature_template` / `.reload smart_scripts` if you are iterating without a restart.

> **These scripts are illustrative.** They use the column set of AzerothCore's WotLK world DB at
> the time of writing (`creature_template`, `creature_template_model`, `creature`, `gameobject`,
> `gameobject_template`, `smart_scripts`, `creature_loot_template`). Check the schema of your own
> core (or use Keira3) before running, and replace every value marked `TODO(verify)` with an ID you
> confirmed in your own client files with `tools/dbc_tool.py ids`.

## Acceptance checks

Before you call it done, all of these should hold:

1. `SELECT COUNT(*) FROM creature_template WHERE entry BETWEEN 900100 AND 900199;` → the expected count.
2. Every `CreatureDisplayID` you used exists in the client's `CreatureDisplayInfo.dbc`
   (`tools/dbc_tool.py ids CreatureDisplayInfo.dbc --from 0 --to 999999 | grep <id>`).
3. Spawning the boss shows a model, a name, a level, and no red `?` or naked cube.
4. The boss's abilities appear in your combat log as the spells you configured (server authority).
5. Loot opens and the items are the ones you configured.
6. `sql/99_rollback.sql` returns the world DB to its pre-install state (run it, then re-run 01–05).
7. No file was added to the client's `Data/` directory. `git status` on your patch repo is empty.

## What would force a client patch

Only these, and each is a deliberate choice rather than an accident:

- a **new item** (needs `Item.dbc` + `ItemDisplayInfo.dbc` rows) — instead, reuse an existing item
  and vary its `item_template` stats;
- a **new spell** with its own tooltip/icon (needs a `Spell.dbc` row) — instead, reuse a stock
  spell or drive the mechanic with an invisible serverside spell;
- a **custom currency price** (`ItemExtendedCost.dbc`) — instead, script the hand-in;
- a **new map** or new terrain — instead, build in a map the client already has.

## Rollback

`sql/99_rollback.sql` deletes by entry range (`900100–900199` creatures, `900200–900299` game
objects). It deliberately does not touch anything outside that range, which is why the ID-range
policy in [docs/04 §4](../../docs/04-patching-toolchain-and-release.md) matters even for content
that never ships a patch.
