# 14 — The frontier: make Azeroth feel unbounded without inventing new art

This chapter answers the bigger design question: **How far can a 3.3.5a world be pushed when we reuse the client's assets and let the server own the rules?** Quite far—but the exact boundary matters. Some ideas are server-only compositions; some need new DBC rows that point at stock assets; some need a compiled module; none are automatically safe for Campaign Party just because public AzerothCore exposes a hook.

The machine-readable design notebook is [`designs/azeroth-unbound.json`](../designs/azeroth-unbound.json). It is deliberately marked `DESIGN_ONLY_NOT_A_FOUNDRY_SPEC`: it is a set of source-backed feasibility assessments and a flagship campaign concept, not a package the current compiler can build. No candidate Frostmourne, armor, spell-visual, map or taxi ID is asserted to exist. Those need locally supplied build-12340 data.

## A surprising but feasible flagship

### The Atlas of Unfinished Wars

Instead of sending the player through a fixed level corridor, give them an atlas with a small set of available regional mysteries. Choose any available lead in Kalimdor, the Eastern Kingdoms, Outland or Northrend; investigate the local conflict; bring a server-validated clue back to the campaign; repeat until the finale opens. Zone choice becomes narrative choice. World scaling keeps ordinary encounters fair, while region caps, boss rules and explicit quest proofs preserve difficulty and story pacing.

The campaign loop is:

```text
choose a region -> investigate stock places/creatures -> earn a server-owned clue
 -> restore a route or alter a local phase -> choose the next branch
 -> form the one-player + four-Altbot party -> enter a repeatable memory encounter
 -> change the next scheduled world front
```

The reveal: the "open world" feature is not just making every mob level 80. It's combining **route freedom + encounter scaling + a world-state atlas + a genuine endgame chase**. The world can remain recognizable while the campaign uses nearly every continent and an existing dungeon map as new playable space.

### Five chapters, one continuous story

| Chapter | Player fantasy | Reused 3.3.5a machinery | Acceptance focus |
|---|---|---|---|
| **The Map That Lies** | Pick an Eastern Kingdoms or Kalimdor lead; uncover a different local story each run. | Quest chains, gossip, page text, stock objects/NPCs, phase masks, addon-only journal. | The player can start in either region; the server records the clue; the addon cannot award it. |
| **Roads Above the Ruins** | Reopen an old travel corridor; earn an air charter as a world-changing reward. | Existing taxi paths, ships/zeppelins, flight mounts, quest progression. | Existing routes work first; bot boarding is proven before routes are sold as party-ready. |
| **The King's Last Shadow** | Hunt an echo of the Lich King's power; choose whether to bind a Frostmourne echo or dismantle it. | Reused verified item display, stock frost spell effects/icons, item upgrades, quest flags, level-scaling values. | Every appearance is found in local build-12340 DBCs; if an NPC model has no wearable equivalent, use a wearable stock set instead. |
| **Five Against the Echo** | Replay a familiar dungeon as a five-character tactical memory with checkpoints. | Existing instance map, server module, creature scripts, loot and encounter state machine. | One controlled character + four real Playerbots can interrupt, reposition, heal, complete objectives and recover from a wipe. |
| **A World That Remembers** | A scheduled frontier reflects the player's choices; revisit earlier regions to see different phases. | `game_event`, conditions, phase masks, spawn groups, repeatable rewards. | Shared/global versus per-character state is explicit; event IDs stay within the target schema's numeric limits. |

This narrative intentionally avoids a forced route. A character can earn story proofs in a level-appropriate place rather than being told to grind the next bracket zone. It does **not** claim that every existing quest automatically scales: quest admission, XP/reward curves and group ownership each need their own implementation and tests. A safe first pass uses fixed campaign proofs and deterministic tokens; only verified stock scaling data is used for item stats.

## The three headline examples, engineered honestly

### 1) Flying across the old world

This is possible as a **controlled feature**, not as a one-line server setting. In the inspected AzerothCore source revision, `AreaTableEntry::IsFlyable()` checks `AREA_FLAG_OUTLAND`; `SpellInfo::CheckLocation()` rejects flying-area spells if the relevant area row isn't flyable, and also rejects strict no-fly areas. `Player::canFlyInZone()` calls a PlayerScript policy hook, but that hook runs after the flyable-area test. Therefore:

1. Keep Outland/Northrend's current behavior intact.
2. Build a reviewed allowlist covering the flyable outdoor zones of Kalimdor/Eastern Kingdoms, while deliberately excluding interiors, dungeons and story no-fly spaces; don't switch every AreaTable row blindly.
3. For selected zones, add the flyable bit in a client **and** server `AreaTable.dbc` delta, preserving unrelated flags and `AREA_FLAG_NO_FLY_ZONE` restrictions.
4. Use `OnPlayerCanFlyInZone` to enforce charter progression, map/zone restrictions, instance policy and other server-owned rules.
5. Test mount casts, zone boundaries, roofs/terrain, transports, anticheat, relog, death, flight-path transitions and Playerbot follow.

**Patch cost:** a DBC delta and client/server parity, but no art. **Important:** the existing DBC writer currently only supports a narrow Item.dbc delta; it cannot yet generate this AreaTable change. Do not edit a target MPQ directly or assume the current custom patch can be replaced. This work needs a new lossless, merge-aware AreaTable path and package admission against the actual patch inventory.

### 2) Frostmourne and the Lich King's armor

A server can define new item entries, names, restrictions, stats, quest gates, proc chains and upgrade choices. The client still needs a valid item identity and appearance. The low-risk order is:

1. Search the operator's local 3.3.5a DBC inventory for the exact Frostmourne / plate appearances, including `Item.dbc` and `ItemDisplayInfo.dbc` relationships.
2. If a stock item row already has the desired display, verify class, subclass, inventory slot, sheath and server-enforced attributes before reusing its client row.
3. If the display exists but no suitable item identity does, add a matching custom `Item.dbc` row referencing that **verified existing** display and ship matching client/server DBC sets.
4. If the client only has an NPC/creature appearance, don't pretend it's wearable. Compose the fantasy from existing wearable armor displays, weapons, auras and particle effects instead.
5. Let AzerothCore award an explicit item or upgrade exchange; don't let addon UI decide ownership. Favor stock level-scaling stat distributions where their ranges and behavior have been checked.

**No new model or texture is required.** Exact appearance IDs are not verified in this repository because no client files are present. WotLK also has no stock transmog system: an item must be a real equipable item, or its cosmetic imitation needs a separate, tested existing mechanic.

### 3) New professions—or professions without a new profession slot

There are two different features hidden in the phrase "new profession":

- A **true client-visible profession** can use the stock skill UI and profession category, but it is a DBC+server project. The inspected source classifies primary professions by the `SkillLine` category, identifies first-rank profession spells by their skill effect, and uses `MaxPrimaryTradeSkill` to gate slots (config documents a range up to 11, default 2). That config does not create a skill line. A real new line needs verified `SkillLine`, `SkillRaceClassInfo`, `SkillLineAbility`, learn-spell/recipe data, trainer or quest acquisition, skill persistence, icons and a clean-client UI test. Existing icons and art can be reused; a new line still means client DBC rows and parity.
- A **sidecar vocation**—for example Cartographer, Relicwright or Skyway Engineer—can be presented in a 3.3.5a addon and processed by an Arena module without consuming a primary-profession slot. The module stores authoritative progression on the server and adapts to Campaign Party's existing Material Exchange through deterministic identities. It must not replace existing profession learning, crafting, inventory, bag or exchange systems.

A vocation is the cheaper experiment; a genuine profession is the richer but more invasive client experience. Either can be fun. Neither should be called complete until recipe learning, persistence, rollback, party permissions and actual UI behavior are tested.

## More than three reskins: 10 reusable world systems

These are directions for shared factories, not 10 one-off generators:

1. **A living atlas:** a rotating campaign board selects one of several existing regions, with server-owned objectives and map pins that are merely presentation.
2. **Relics that remember:** campaign choices unlock explicit item upgrade stages; existing level-scaling rows keep the weapon relevant without silently rewriting owned items.
3. **Skyway restoration:** repaired taxi/transport links become unlockable fast travel before unrestricted flight; every route is checked against actual transport and bot behavior.
4. **Five-person hunts:** reusable encounter templates define interrupt windows, tank swaps, healer thresholds, dispels, hazard avoidance and bot-operable fallback telegraphs.
5. **Echo instances:** familiar dungeon spaces host repeatable scenarios, rotating objectives, challenge bands and recoverable checkpoints without new maps.
6. **World fronts:** event schedules rotate rare leaders, patrols, vendor stock and town phases; a campaign decision can influence the next schedule.
7. **A field-notes detective game:** books, gossip, local NPCs and old gameobjects form an evidence chain that can be solved in any region.
8. **Vocations as a second progression layer:** surveying, relic restoration or route engineering rewards exploration without displacing the downstream's profession authority.
9. **Legendary choice, not just legendary loot:** bind, purify or dismantle an artifact; each path unlocks a distinct server-validated spell/upgrade branch using stock visuals.
10. **A world that changes at the edges:** phase in restored camps, neutral markets, evacuation routes or hostile takeovers, while leaving the baseline world intact for rollback.

The common design pattern: **new loop, old presentation**. Reuse existing creatures, item displays, spell visuals, maps, taxi routes, books, gameobjects and sounds; only add DBC rows where the client needs a new identity or a changed capability.

## Feasibility and patch-cost map

| Idea | Cheapest plausible delivery | Client files? | Main bite / qualification gate |
|---|---|---:|---|
| More quests across existing zones | World SQL + server scripts + existing assets | Usually no | Static quest rewards and level requirements do not become dynamic by themselves. |
| Evergreen ordinary creatures | Compiled server scaling policy on spawn | No, if using existing creatures | Spawn-time scale policy, level bounds, nearby-party selection, shared spawns and combat stability. |
| Level-scaling equipment | Stock scaling tables + item rows | Maybe Item.dbc for a new identity | Verify every scaling row/curve and the exact core's item behavior. |
| Old-world mount flight | Allowlisted AreaTable changes + server policy | Yes: AreaTable DBC parity | Current Foundry writer cannot yet generate AreaTable; no-fly and client behavior must be tested. |
| Frostmourne / wearable regalia | Item template + verified stock display; optional Item.dbc identity | Conditional DBC | NPC display is not automatically a wearable equipment display. No IDs are verified here. |
| Genuine profession | Skill-line/ability DBC + spells, SQL and training loop | Yes | 11-slot config is a cap, not a profession generator; client UI must be tested. |
| Sidecar vocation | Server module + character persistence + addon | No required MPQ | Must integrate with existing inventory/economy authorities rather than fork them. |
| Multi-continent campaign journal | Quest SQL/module + optional addon | No required MPQ | Server-owned choices, quest log capacity and addon API 30300 acceptance. |
| Rotating world front | Event/phase/SmartAI SQL + scripts | No for reused assets | Tinyint event IDs, state ownership, reset/rollback, global-versus-personal visibility. |
| Bot-operated memory dungeon | Existing instance + compiled encounter scripts | Usually no | Only actual Playerbots acceptance can qualify movement, target, role and wipe recovery. |

## Evidence trail and verified boundaries

The capability assessment was checked against public AzerothCore WotLK source at commit `6ffb62abb867dabb258de6813ff5d06d6b011543` (2026-11-17). This is a source snapshot, **not** a pinned Campaign Party dependency. Key paths:

- [`AreaTableEntry::IsFlyable`](https://github.com/azerothcore/azerothcore-wotlk/blob/6ffb62abb867dabb258de6813ff5d06d6b011543/src/server/shared/DataStores/DBCStructure.h#L540) tests the `AREA_FLAG_OUTLAND` bit.
- [`SpellInfo::CheckLocation`](https://github.com/azerothcore/azerothcore-wotlk/blob/6ffb62abb867dabb258de6813ff5d06d6b011543/src/server/game/Spells/SpellInfo.cpp#L1545) checks `IsFlyable`, strict no-fly flags and `Player::canFlyInZone`.
- [`Player::canFlyInZone`](https://github.com/azerothcore/azerothcore-wotlk/blob/6ffb62abb867dabb258de6813ff5d06d6b011543/src/server/game/Entities/Player/Player.cpp#L14041) exposes the policy call and keeps the Northrend Cold Weather Flying requirement unless the spell explicitly ignores it.
- [`OnPlayerCanFlyInZone`](https://github.com/azerothcore/azerothcore-wotlk/blob/6ffb62abb867dabb258de6813ff5d06d6b011543/src/server/game/Scripting/ScriptDefines/PlayerScript.h#L796) is a deny-capable hook, not a replacement for the AreaTable flyable bit.
- [`Creature::SelectLevel`](https://github.com/azerothcore/azerothcore-wotlk/blob/6ffb62abb867dabb258de6813ff5d06d6b011543/src/server/game/Entities/Creature/Creature.cpp#L1511) calls [`OnBeforeCreatureSelectLevel`](https://github.com/azerothcore/azerothcore-wotlk/blob/6ffb62abb867dabb258de6813ff5d06d6b011543/src/server/game/Scripting/ScriptDefines/AllCreatureScript.h#L33) before creature base health/mana are generated.
- [`Player::_ApplyItemBonuses`](https://github.com/azerothcore/azerothcore-wotlk/blob/6ffb62abb867dabb258de6813ff5d06d6b011543/src/server/game/Entities/Player/Player.cpp#L6806) reads stock scaling distributions/values and the current player level.
- [`MaxPrimaryTradeSkill`](https://github.com/azerothcore/azerothcore-wotlk/blob/6ffb62abb867dabb258de6813ff5d06d6b011543/src/server/apps/worldserver/worldserver.conf.dist#L2325) documents 0–11 slots; [`SpellInfo::IsPrimaryProfession`](https://github.com/azerothcore/azerothcore-wotlk/blob/6ffb62abb867dabb258de6813ff5d06d6b011543/src/server/game/Spells/SpellInfo.cpp#L1000) recognizes the profession skill effect and category.

A path being present does not mean the current Arena compiler emits it. This branch does not yet write `AreaTable.dbc`, generate genuine skill-line DBC rows, compile the dynamic level module, check stock asset IDs, or run the Playerbot encounters. `ARENA-DB`, `ARENA-COMPILE` for these designs, `CAMPAIGN-ADMISSION` and `CAMPAIGN-LIVE` remain pending.

## Best next implementation slice

1. Add a DBC-schema registry for AreaTable/SkillLine/SkillLineAbility, sourced from 3.3.5a field definitions; reject unknown schemas and preserve untouched records byte-for-byte.
2. Add a flight-policy dry-run: inventory candidate zones locally, show before/after flags, preserve all unrelated bits and reject any attempt to erase a no-fly flag without an explicit reviewed exception.
3. Add a creature scaling module prototype against this exact public core revision, with deterministic policy tests for solo, party, boss caps, level bounds and spawn timing; then pin/build it in CI before calling it `ARENA-COMPILE`.
4. Use verified local asset inventory to select one existing Frostmourne/plate presentation; if no exact wearable row exists, deliberately choose a stock kitbash.
5. Build the first real world chapter from the shared Foundry grammar only after generated quest/item SQL is schema-qualified and rollback-tested.

That sequence explores the big fantasy without pretending that an unverified ID, a source hook or a generated bundle is already a playable feature.
