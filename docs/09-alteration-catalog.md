# 09 — What you can alter, and what changes in play

[01](01-client-and-server-data-model.md)–[08](08-how-integration-actually-works.md) answer *how the machinery
works*. This chapter answers the next question: **what can I actually change, and what will a player notice?**

Every lever this project knows about is in one machine-readable place — `catalog/alterations.json`, 110
entries — and queryable:

```bash
python3 tools/catalog.py stats                          # 110 entries, 6 layers, 4 delivery kinds
python3 tools/catalog.py query --no-mpq                 # 96 levers that need no MPQ change
python3 tools/catalog.py query --clock 0                # 13 that apply to the next event
python3 tools/catalog.py show content.quest-new          # one lever in full
python3 tools/catalog.py surfaces --layer content        # what data you would touch
python3 tools/catalog.py matrix                          # layer x delivery grid
```

Each entry carries: the surfaces you edit, **what the player experiences**, how long until they see it, and
how to prove it in game. This chapter is the model behind that data.

---

## 1. The reflection model: what the client can show

The client renders from exactly three kinds of input. Everything else about custom content follows from
which kind you are working with.

| Kind | Examples | Who supplies it | When the player sees it |
|---|---|---|---|
| **Strings** | NPC names, subnames, gossip text, quest prose, creature say/yell, mail bodies, item names, guild names | the server, in query and chat packets | on the next query — *you own these* |
| **Numbers** | item stats, creature health/damage, aura duration and charges, XP awards, prices, cooldowns | the server, in the same packets | on the next query — *you own these* |
| **IDs into client tables** | models, icons, spell visuals and tooltips, set bonuses, map geometry, sounds, item classes | the client's own DBCs | only after a patch, and only if the row exists — *the client owns these* |

Hence the rule that predicts most surprises:

> **If it is a string or a number, you can change it server-side. If it is an ID, you can only choose among
> IDs the client already has.**

That is why an NPC with a new name, new level, new faction, new dialogue and a reused model takes ten minutes
and no patch, while a "new" item with a never-before-seen icon takes a patch, a launcher and a support burden
— the difference is not effort, it is *which side of the table owns the thing you changed*.

The failure signature tells you which kind you got wrong:

| What players report | Which kind failed |
|---|---|
| Red `?` instead of an item or creature | an ID the client cannot resolve — item, display or creature |
| Icon missing, item looks like a grey box | `ItemDisplayInfo.InventoryIcon` absent client-side |
| Aura does nothing visible but the mechanic works | `Spell.dbc` row absent client-side (server applied it anyway) |
| No set block on the tooltip, but bonuses fire | `ItemSet.dbc` row absent client-side |
| Name and level right, model wrong or a cube | the query path worked, the display ID did not |
| Dialogue fine, no voice, no camera | text is server-sent; anything richer is client content |

---

## 2. Six layers of alteration

| Layer | Surface | A typical change | Clock | Query |
|---|---|---|---|---|
| **Policy** | `worldserver.conf` | XP rate, level cap, drop rates, cross-faction chat | 0 | `query --layer policy` |
| **Content** | world DB rows | a quest, a vendor, a spawn, a loot table, an item's stats | 1 | `query --layer content` |
| **Mechanics** | rule tables, SmartAI, scripts | spell linkage, procs, area auras, conditions, disables | 1 | `query --layer mechanics` |
| **Systems** | module / C++ / Eluna | commands, addon protocol, currencies, death rules | 1 | `query --layer systems` |
| **Presentation** | DBC rows, MPQ, addon Lua | icons, set bonuses, tooltips, UI frames, maps | 2 | `query --layer presentation` |
| **Novel** | existing systems, repurposed | the quest log as a HUD, gossip as an app, books as a CMS, the addon channel as a socket | 1–2 | `query --layer novel` |

The `novel` layer is [10](10-novel-alterations.md): mechanisms used for something they were not built for.
It is the most productive layer per hour, because it reuses both the code and the client's assumptions.

The distribution is the argument of this whole project (`tools/catalog.py matrix` is authoritative
if the numbers drift):

```
layer                   none  client-files      dbc-rows        assets   total
policy                    15             .             .             .      15
content                   27             .             5             .      32
mechanics                 15             .             .             .      15
systems                    8             .             .             .       8
presentation               2             4             5             4      15
novel                     23             2             .             .      25
------------------------------------------------------------------------------
total                     90             6            10             4     110
```

**96 of 110 levers need no MPQ at all** (90 need nothing client-side; 6 are an addon folder). Every policy, mechanics and systems lever is patch-free, and so are 23 of the 25 novel ones. Content is 84% patch-free. Only presentation is inherently
client-side — which is exactly why the design playbook sends you to [06](06-design-playbook-existing-assets.md)
first: most features can be *expressed* in content and mechanics instead of presentation.

---

## 3. Three clocks: how long until it shows

### Clock 0 — applies to the next event

Config dials. No reload, no restart, nothing to distribute. Raise `Rate.XP.Kill` and the next kill pays more;
change `Rate.MoveSpeed.Player` and the next movement update is faster. The player experience changes at the
granularity of the next tick — which makes policy the cheapest experimentation surface on the server
(`policy.xp-rate`, `policy.money-drops`, `policy.cross-faction`).

### Clock 1 — the server must re-read it

Most content lives in tables loaded at boot or on reload. Three things can make a clock-1 change look broken:

1. **Nothing reloaded.** Some tables have `.reload <table>`; many do not, and only a restart picks them up.
   `.help reload` lists what your fork supports; a restart is the universal fallback and the only honest
   answer when a change "did nothing".
2. **The player's client cached the old answer.** Item info, creature names and quest text are cached in the
   client's `Cache/` WDB files. Change `item_template` and a player who has seen that item can keep reading
   the old tooltip until the cache is purged or the ID changes. This is why `requires_cache_purge` exists in
   the release manifest ([04](04-patching-toolchain-and-release.md)) and why "bump to a fresh ID" is a real
   remedy rather than superstition.
3. **The spawn is already in memory.** Template edits apply to newly spawned creatures; the one standing in
   front of you may still be the old copy until it respawns or the grid reloads.

### Clock 2 — the client must be restarted with new files

DBC rows, MPQ contents and addon folders. Here the interesting part is not "restart the client" but *which
players did*: a patch is a distribution event, so the version becomes a compatibility fact — players without
it see red `?` where everyone else sees a sword. That asymmetry is why `policy.item-attribute-enforcement`
matters so much: with `DBC.EnforceItemAttributes = 1` the client's table overwrites your server row, so a
half-distributed patch changes the item's *appearance* while leaving its stats correct — the most confusing
failure mode in the whole catalog.

---

## 4. Policy: the levers nobody can see you pull

| Lever | What changes in play |
|---|---|
| `policy.xp-rate` — `Rate.XP.Kill/Quest/Explore/Pet` | the pace of the bar; alts, not power |
| `policy.level-cap` — `MaxPlayerLevel` + `player_xp_for_level` | how long the game is; needs client stat curves to stay honest |
| `policy.money-drops` — `Rate.Drop.Money`, vendor prices | how tight the economy feels, without touching an item |
| `policy.item-drop-rates` — `Rate.Drop.Item.*` | how often the "ooh" moment happens |
| `policy.reputation-rate` — `Rate.Reputation.*`, spillover | how many factions one grind unlocks |
| `policy.start-character` — `StartPlayerLevel/Money`, `playercreateinfo_*` | what a new player's first minute feels like |
| `policy.move-speed` | the world's apparent size |
| `policy.death-and-resurrection` — corpse delays, decay | how expensive a mistake is |
| `policy.honor-arena` — rates, caps, `PvPToken.*` | how fast PvP gear arrives |
| `policy.cross-faction` — `AllowTwoSide.*` | whether the server feels like one population |
| `policy.addon-channel` | whether a custom UI can exist at all |

These are the alterations with the highest ratio of player-visible change to work performed, and the lowest
risk of breaking the client — because nothing client-side is involved. They are also the ones that make a
server *feel* different rather than *contain more*.

## 5. Content: the body of the game

Representative levers; the full set is `query --layer content`.

| Lever | What changes in play |
|---|---|
| `content.creature-new` + `content.creature-appearance` | a named, levelled, factioned creature wearing any existing model |
| `content.creature-dialogue`, `content.gossip-menu` | the world talks: say/yell lines, branching menus |
| `content.quest-new`, `content.quest-gating` | the spine of progression — the highest-value patch-free content there is |
| `content.vendor-offer`, `content.vendor-token` | what money and tokens buy, and who may buy |
| `content.loot-table`, `content.loot-reference-table` | the reward loop |
| `content.item-stats-existing` | a familiar item with a completely different purpose |
| `content.spawns-density`, `content.world-event` | whether the world feels alive *today* |
| `content.gameobjects-doors` | interaction: doors, chests, braziers, levers |
| `content.boss-encounter` + `content.phasing` | set pieces and state-change presentation |

Three content levers deserve special attention because they are underused:

- **`content.world-event`** — a schedule is a reason to log in. Events can add creatures, gameobjects, vendors
  and quests without editing any of them.
- **`content.item-stats-existing`** — the cheapest "new item" in the game; reuse an existing ID's icon and
  name, rewrite stats, spells and requirements server-side.
- **`content.vendor-token`** — reuse an existing `ItemExtendedCost` id and the vendor shows a token price
  with no patch; invent a new cost row and you have bought yourself a release process.

## 6. Mechanics: changing what existing pieces do

| Lever | What changes in play |
|---|---|
| `mech.spell-tuning`, `mech.spell-cooldown` | the numbers on a familiar ability, tooltip included |
| `mech.spell-linked` | "new" abilities composed from two existing spells |
| `mech.spell-proc` | a class that reacts differently to the same rotation |
| `mech.spell-area` | zone rules: cold, corruption, sanctuary |
| `mech.spell-scripts` | effects the DBC cannot express |
| `mech.smartai`, `mech.waypoints-formations` | behaviour: patrols, packs, phases, summons |
| `mech.conditions-engine` | *any* row gated on quest/item/aura/reputation/level |
| `mech.disables` | an expansion-shaped hole in the game |
| `mech.spell-group-stacking` | which buffs coexist |

`mech.conditions-engine` is the most reusable mechanic in the schema: it is how you gate vendors, loot, gossip,
spawns and quests without writing code. If you learn one table from this catalog beyond `creature_template`,
learn that one.

## 7. Systems: behaviour that spans content

| Lever | What changes in play |
|---|---|
| `system.chat-command` | a new verb players can type |
| `system.addon-protocol` | **a custom UI fed by live server state — the only patch-free path to new interface** |
| `system.currency-sink` | a token economy with a visible sink |
| `system.progression-unlock` | account-wide unlocks and attunements |
| `system.death-rules` | hardcore/broadcast/no-release rules |
| `system.auto-scaling` | instance difficulty matched to the group |
| `system.custom-loot` | pity timers, guaranteed drops, per-player rolls |
| `system.anticheat` | integrity, not content |

`system.addon-protocol` is the strategic one. Because the client can run Lua shipped as a plain addon folder,
and because the addon channel (`policy.addon-channel`) lets the server push strings at it, you can build a
custom interface — scoreboard, tracker, progression pane, custom bank — with **no MPQ, no DBC row, and no
launcher change**. The split that makes it work: the addon owns pixels, the server owns truth.

## 8. Presentation: the layer that costs a release

| Lever | Delivery | What changes in play |
|---|---|---|
| `client.model-reuse`, `client.sound-reuse` | none | new content that looks and sounds authored |
| `client.addon-frame`, `client.addon-data`, `client.tooltip-hook` | addon folder | a custom interface with live data |
| `client.dbc-row` | `dbc-rows` | icons, tooltips, set blocks, spell visuals |
| `content.item-new-id`, `client.achievements`, `client.titles`, `client.talents` | `dbc-rows` | genuinely new client content |
| `client.mpq-patch` | `assets` | simultaneous delivery to everyone who updates |
| `client.new-model`, `client.new-map`, `client.interface-skin` | `assets` | art and geography that never shipped |

Read this table as a budget: each row is a step up in *distribution* cost, not just effort. The first row is
free, the second row is a folder you hand out, the third row is a versioned release with a checksum, and the
last row is an art pipeline.

---

## 9. Compositions: levers that add up to a "mode"

No single lever makes a server feel like a different game; five or six do. These are patch-free compositions —
the levers are named so you can look each one up in the catalog.

**1. Hardcore season.** Goal: death is the content. Levers: `policy.death-and-resurrection` (long reclaim,
slow decay), `system.death-rules` (broadcast + permanent flag), `policy.xp-rate` (slower), `policy.item-drop-rates`
(lower), `content.world-event` (season window), `system.addon-protocol` (a death counter frame). Players see a
world where every pull matters and the leaderboard lives in a UI the patch never shipped.

**2. Solo/duo dungeon realm.** Goal: group content at any size. Levers: `system.auto-scaling`, `content.boss-encounter`
with `mech.spell-difficulty`, `policy.instance-reset`, `content.loot-table` (rewards scaled to group size),
`policy.reputation-rate` (so solo players still progress). Players see the same instances tuned for who showed up.

**3. Story realm.** Goal: the world is worth reading. Levers: `content.quest-new`, `content.quest-gating`,
`content.creature-dialogue`, `content.gossip-menu`, `content.phasing`, `content.world-event` for chapters.
Players see prose, gating and a world that changes as they finish things — with zero client files.

**4. PvP brawl realm.** Goal: instant, loud, short. Levers: `policy.honor-arena` (rates, caps, tokens),
`content.battleground-arena`, `mech.spell-tuning` + `mech.spell-linked` (brawl-flavoured abilities),
`policy.cross-faction` (one queue), `system.addon-protocol` (scoreboard). Players see fast gear, chaotic
fights and a scoreboard that exists only as an addon.

**5. Economy and professions realm.** Goal: crafting is the game. Levers: `content.loot-table` +
`content.loot-reference-table` (material supply), `policy.money-drops`, `content.vendor-token`,
`mech.spell-tuning` (profession outputs), `content.item-stats-existing` (crafted gear that matters).
Players see prices, supply and demand instead of a gear treadmill.

**6. Staging realm.** Goal: prove changes before production. Levers: `policy.*` mirrored, content tables
restored from production, `tools/preflight.sh` + `tools/manifest.py` as the gate, and a second client install
with the candidate patch. Players (your testers) see exactly what production will, one release ahead.

Each of these is 0 MPQ changes, 0 DBC rows, and 0 art. They differ in *arrangement*, which is the point: the
client is a fixed vocabulary, and custom content is what you say with it.

---

## 10. What will not reflect in play

| Want | Why it fails | Patch-free alternative |
|---|---|---|
| A new item icon or model | the client resolves `ItemDisplayInfo` by ID | reuse an existing display, change stats |
| A new spell visual | visuals are `SpellVisual`/`SpellVisualKit` IDs | compose existing kits behind an existing visual |
| New art, new models | assets do not exist client-side | reuse/recolour by combination |
| A new map or zone | geometry, minimap and area names are client files | build inside an existing map; consider an unused instanced map |
| A new talent node or tree | the talent pane is client DBC driven | raise `Rate.Talent` for more points in existing trees |
| A new achievement or category | the achievements pane is client DBC driven | track it in an addon fed by `system.addon-protocol` |
| A new currency icon in the currency pane | `CurrencyTypes.dbc` is client-side | use an existing item as the token, show it in a gossip/addon UI |
| A new sound file | audio is a client asset | reuse an existing `SoundEntries` id |
| A custom loading screen or login | interface files are client assets | cosmetic patch, high risk, low gameplay value |
| A tooltip line the client does not compute | tooltips are built from DBC data | `client.tooltip-hook` in an addon adds any line you want |

Note the pattern in the right-hand column: when the client will not show it, **either reuse an ID it already
has or move the display into an addon**. Both keep the release count at zero.

---

## 11. The establish → reflect → verify loop

A repeatable way to find out what a change actually does, in this order:

1. **Pick the lever** by intent, not by table: `python3 tools/catalog.py query --search "<what you want>"`.
   Read the entry's `effect` line — if that is not the experience you want, you picked the wrong lever.
2. **Write the smallest change.** One row, one config value. Ambiguous diffs hide the cause of ambiguous results.
3. **Apply it** — reload if your fork supports it, otherwise restart. Restart is not a failure; it is the
   honest baseline.
4. **Prove it in play** with the entry's `verify` steps: `.lookup`/`.npc add`/`.additem`/`.quest add`/`.event start`
   get you to the object; the tooltip, the combat log and the chat frame are the evidence. (Command names are
   AzerothCore-flavoured; `.help <command>` is authoritative for your fork.)
5. **Check the other side.** Asked for a new item appearance? Compare `Item.dbc` with `item_template`. Asked
   for a spell? Compare `Spell.dbc` with `spell_dbc`. Drift is invisible in game and obvious in
   `tools/preflight.sh`.
6. **Write down which clock it was** — 0, 1 or 2. Every future "it didn't work" conversation is shorter once
   you know whether the change needed a reload or a release.

Two habits make this loop honest:

- **Test on a character that did not exist before the change.** Cache and spawn state are the two biggest
  sources of false positives.
- **Test with a player account, not a GM.** Some UI differences (quest markers, gossip availability, vendor
  gating) only appear for accounts without GM flags.

---

## 12. Where to go next

| You want to… | Read |
|---|---|
| know whether a feature needs a patch at all | [02](02-feature-integration-matrix.md) |
| build a specific thing today | [03](03-recipes.md) |
| design something from existing assets | [06](06-design-playbook-existing-assets.md) |
| ship a change to players safely | [04](04-patching-toolchain-and-release.md) |
| debug "it worked yesterday" | [05](05-failure-modes.md) |
| understand why a lever behaves this way | [08](08-how-integration-actually-works.md) |
| browse every lever as data | `catalog/alterations.json`, `tools/catalog.py` |
| repurpose a system for something it was not built for | [10](10-novel-alterations.md) |
| know how deeply a claim has been tested | [11](11-testing-ladder.md) |

The catalog is meant to grow. When you find a lever this project does not list, add an entry — `python3
tools/catalog.py validate` and the test suite will tell you if the shape is right, and the `effect` field is
where you write down what a player will actually notice.
