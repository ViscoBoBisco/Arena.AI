# 06 — Design playbook: building only from what the client already ships

The constraint "use existing features/models" sounds like a limit on creativity. In practice it
moves the work from *asset production* (slow, specialist, breaks with every patch) to *systems and
data* (fast, iterable, safe). The 3.3.5a client is an enormous parts bin; the job is to be a good
kitbasher and a good systems designer.

---

## 1. The three layers of any custom feature

```
KERNEL   server truth: SQL rows, scripts, module logic, progression, rewards
SKIN     client presentation: display IDs, spell visuals, icons, sounds, models
GLUE     the interface: gossip options, addon panels, tooltips, markers
```

Every feature is a combination. The engineering rule is:

> **Skin and glue never decide anything. The kernel decides and then tells the client what to
> draw.**

Why it matters: the client is editable by any player. If a reward is granted client-side, it is
not granted. If ownership is decided in an addon, it is not owned. Conversely, a kernel decision
that the client cannot render (an ID it has never heard of) looks like a bug.

## 2. Your parts bin (what you can reuse without adding one byte)

| Parts | Approx. count in a stock 3.3.5a set | Where they are addressed from |
|---|---|---|
| Creature displays (models, skins, scale) | ~59,000 | `creature_template.modelid` |
| Game-object displays | ~117,000 | `gameobject_template.displayId` |
| Item display combinations (model + texture + icon + geosets) | ~117,000 | `item_template.displayid` |
| Spells (effects, auras, visuals, sounds, icons) | ~65,000 | `Spell.dbc`, `spell_dbc` |
| Spell visuals / kits / effect models | ~7,000 visuals, thousands of kits and effect models | `SpellVisual*.dbc` |
| Icons | tens of thousands | `Interface/Icons/<name>`, `SpellIcon.dbc`, `ItemDisplayInfo.InventoryIcon` |
| Sounds | thousands | `SoundEntries.dbc`, `SoundKit` fields |
| Emotes / animations | full player & creature animation sets | `Emotes.dbc`, spell visuals, `creature_addon` |
| Areas/subzones/zone names | ~4,000 areas | `AreaTable.dbc` |
| Taxi network | full flight graph | `TaxiPath*.dbc` |
| Vehicles/seats | full 3.3.5 vehicle system | `Vehicle.dbc`, `VehicleSeat.dbc` |
| Transport models/routes | ships, zeppelins, towers | `Transports`, `transport_*` tables |

Anything in that bin can be recombined into "new" content. Anything outside it is an art or
binary-patching project.

## 3. Ten reuse patterns

1. **Reskin** — same stats, different display: pick another `modelid`/`displayid` and give it a
   new name and lore. Cheapest possible "new" content.
2. **Remix** — existing ID, new stats: an existing item/dropped weapon retuned into your
   progression curve. Zero client risk; ideal for balance work.
3. **Recombination** — new `SpellVisual`/`SpellVisualKit`/`SpellVisualEffectName`/`ItemDisplayInfo`
   rows assembled from existing paths. New *looks*, no new art files.
4. **Scale and aura identity** — the same display made distinct by scale, mount, weapons
   (`creature_equip_template`), auras (`creature_addon.auras`), particles, or a name/title.
5. **Repurpose** — use an existing system for a new purpose: reputations as faction standing,
   quests as story chapters, game objects as interactive systems, vehicles as mini-games, taxi
   paths as your world's travel network, emote spells as social features.
6. **Kit-bash a creature** — display + equipment + mount + pet + aura + AI. Two identical models
   become a captain (armoured, mounted, aura), a grunt (bare), and a spirit (translucent aura,
   scale 1.4) in the same afternoon.
7. **Borrowed identity** — refill an existing area's population with your own creatures and
   quests. The terrain, skybox, music and ambience are already there and already good.
8. **Icon as symbol** — a custom currency, "school", or achievement category can be represented
   entirely by reusing existing icon names in your UI/QoS layer.
9. **Addon as the front end** — new systems get a real UI without touching the client patch:
   panels, tabs, counters, search boxes, all in Lua.
10. **Instance in a box** — a raid/dungeon/scenario built inside an existing map's unused space,
    with `instance_template` + spawns + scripts. No terrain work.

## 4. Effort ladder (choose the cheapest rung that delivers the fantasy)

| Rung | Change | Typical effort | Distribution risk |
|---|---|---|---|
| 1 | SQL/scripts only | hours–days | none |
| 2 | + addon UI | +days | none (addon ship) |
| 3 | + DBC rows referencing existing assets | +days–weeks | patch versioning |
| 4 | + new M2/BLP assets | +weeks | patch size, art review |
| 5 | + new maps | +weeks–months | large patches, extraction pipeline |
| 6 | + executable/DLL changes | +months | client fork, AV, support burden |

Design rule: **a feature should live on the lowest rung that still delivers the player's
experience.** "New armour set" is rung 3 (new `ItemDisplayInfo` rows + recolor of existing
textures), not rung 4. "New raid" is rung 1/2, not rung 5.

## 5. A content pipeline that scales

```
1. SPEC        one page: fantasy, player loop, rewards, which layer owns each part
2. KERNEL      SQL + scripts; testable with GM commands, no client work
3. SKIN        pick existing IDs; only if impossible, add DBC rows
4. GLUE        gossip/addon/markers
5. GATES       dbc_tool preflight, clean-client test, old-patch regression test
6. RELEASE     patch version + manifest + changelog + rollback artifact
```

### Spec sheet template (copy per feature)

```yaml
feature: "The Ashen Vault"          # name
fantasy: "A timed vault run for 5 players, rewarding tokens"
owner_layers:
  kernel: "instance_template, spawns, SmartAI + module for the timer"
  skin: "reused creature displays 24512/24519/31233; existing door GO 2010"
  glue: "addon timer bar + gossip entry NPC"
client_patch: false                 # verdict, with justification
new_ids: { creature: [900100-900140], gameobject: [900200-900210] }
rewards: "token item 800010 (existing item 40752 reskinned -> NEW ID? no: reuse 40752)"
acceptance:
  - "5 players can enter, timer starts on first boss pull"
  - "clean client (cache purged) shows correct models and loot"
  - "no red ? in rewards; no missing tooltips"
rollback: "disable access_requirement row; content stays inert"
```

## 6. Naming and identity conventions

Content almost never breaks technically; it breaks *narratively* — the custom boss that is
obviously a reskinned stock mob, the item named "Sword of Testing". Cheap fixes:

- **Name everything as if it belongs.** A reskinned wolf is not a wolf if it is called and
  dressed as something else. Names, titles, and dialogue carry more identity than models.
- **Prefix custom content internally** (`AV_` script names, `ashen_vault_` tables/comments) and
  keep the *player-facing* names in-world.
- **One visual language per faction/zone**: pick a palette of existing displays/icons per
  region so new content reads as a set rather than a collage.
- **Custom ID bands, documented forever** (see [04 §9](04-patching-toolchain-and-release.md#9-content-migration-discipline)).

## 7. What the ecosystem actually does (proof the playbook works)

- **Project Ascension** — a classless server built almost entirely out of recombined stock spells
  and systems, shipped with a patched launcher + a client patch (`Patch-A`) that carries the
  custom interface. Shows how far "recombine existing spells" scales
  ([launch thread](https://www.reddit.com/r/wowservers/comments/6nadrf/project_ascension_is_now_live_your_connection/)).
- **Duskhaven Reforged** — 3.3.5-based with custom classes/races and systemic rewrites (combo
  points, focus). Client patch + deep core work; the expensive rung done deliberately
  ([project thread](https://www.reddit.com/r/WoWPrivateServers/comments/166t4ne/duskhaven_reforged_information_about_project/)).
- **mod-mount-autoselect** — a handful of `Spells.csv` rows in `Spell.dbc` plus a module gives
  zone/skill-aware mount speed. Small patch, big feature
  ([repo](https://github.com/cthu1hoo/mod-mount-autoselect)).
- **mod-visible-bags** — a *generated* client patch (model conversion + `Spell.dbc`/`Item.dbc`
  rows + matching `spell_dbc` SQL) for retail backpack cloaks. Excellent example of treating the
  patch as a build artifact with a scripted toolchain
  ([repo](https://github.com/buildthehomelab/wow-mod-visible-bags)).
- **EvolvedWOTLK** — ships a `Patch-Z.MPQ` plus an "AIO" addon framework that custom client-side
  systems depend on: patch for data, addon for UI, launcher for both
  ([downloads](https://www.evolvedwotlk.com/downloads.php)).
- **mod-individual-progression** — ships *optional* `.mpq`/`.dbc` files for the few things that
  genuinely need client data (Vanilla reagents) and works without them otherwise. The right
  default: **patch is optional unless the client must see it**
  ([repo](https://github.com/ZhengPeiRu21/mod-individual-progression)).

## 8. Anti-scope-creep list

Do not start here, even if the feature "needs" it:

- new maps/continents (use existing interiors first);
- new item classes or professions (use existing classes as containers);
- new client opcodes (use addon messages);
- custom executable patches (use DBC rows);
- new M2 models (use recolors/variants from existing textures);
- custom classes/races (use specs/talents/trainer gates first).

Every one of these is a legitimate rung-4/5/6 choice *later*, once the kernel is proven and the
player loop is fun.

## 9. Acceptance criteria that catch real bugs

Before a feature ships, on a **clean client** (cache deleted, only your addons):

- [ ] every model resolves (no `?`, no invisible NPC/GO);
- [ ] every tooltip is truthful (numbers, costs, set bonuses, "Use:" lines);
- [ ] every reward is obtainable and visible in the reward pane;
- [ ] the feature works for a *new* character, not just your GM;
- [ ] the feature degrades sanely if the client is old (no crash, a warning);
- [ ] the server log is clean for the touched IDs;
- [ ] the previous patch still works if rolled back (content is inert, not corrupting).
