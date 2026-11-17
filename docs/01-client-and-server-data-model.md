# 01 — The client/server data model on 3.3.5a

Everything below assumes **WoW 3.3.5a build 12340** and a WotLK core (AzerothCore or
TrinityCore 3.3.5). CMaNGOS/mangos-wotlk behave similarly for the parts that matter here.

---

## 1. Where the client keeps data

```
World of Warcraft/
  Wow.exe                     <- x86 executable, build 12340
  Data/
    common.MPQ                <- most models, textures, maps, sounds
    common-2.MPQ
    expansion.MPQ             <- TBC assets
    lichking.MPQ              <- WotLK assets
    patch.MPQ
    patch-2.MPQ
    patch-3.MPQ
    <locale>/                 <- "enUS", "enGB", "deDE", "ruRU", "zhCN", ...
      locale-<locale>.MPQ
      expansion-locale-<locale>.MPQ
      lichking-locale-<locale>.MPQ
      speech-<locale>.MPQ ...
      patch-<locale>.MPQ
      patch-<locale>-2.MPQ
      patch-<locale>-3.MPQ    <- in 3.3.5a the DBFilesClient/*.dbc tables live mainly here
  Cache/                      <- WDB files: server data the client remembers
  WTF/                        <- Config.wtf, macros, addon saved variables
  Interface/AddOns/           <- Lua/XML addons (no MPQ needed)
```

Two independent extension points exist and they solve different problems:

| Extension point | Cost | What it can change |
|---|---|---|
| `Interface/AddOns/*` (Lua + XML) | zero distribution risk, hot-swappable | UI, frames, event display, local parsing, tooltip text, custom windows for server-pushed data |
| `Data/**/patch-*.MPQ` | must be shipped to every player, versioned, cache-cleared | DBC rows, models, textures, maps, sounds, FrameXML — anything the client reads |

## 2. MPQ in one page

MPQ is a hash-indexed archive: files are found by hashing `"DBFilesClient\Spell.dbc"`, not by
storing a name. A `(listfile)` inside the archive maps hashes back to names, which is only
needed by *editors* — the game already knows the names it will ask for. `(attributes)` is
optional; classic guides tell you not to write it for 3.3.5 patches. The format used by the
3.3.5 client is **MPQ v1 (`FormatVersion = 0`)**, 4096-byte sectors
(`SectorSizeShift = 3`), hash table size a power of two ([wowdev: MPQ](https://wowdev.wiki/MPQ),
[getMaNGOS: MPQ file](https://www.getmangos.eu/wiki/referenceinfo/clientfiles/mpq-file-r20035/)).

The client opens **every** MPQ in its load order and, when several archives contain the same
hashed path, uses the highest-priority one. That is the entire mechanism behind custom patches:
*you never modify Blizzard's archives, you add a higher-priority archive that shadows a file.*

### Load order (this is where people get burned)

Higher = later = wins ([WoTLK Modding Wiki: Client](https://wotlkdev.github.io/wiki/theory/client),
corroborated by Noggit Red's reverse-engineered priority list):

**English clients (enUS/enGB):**

1. locale non-patch archives (`locale-enUS.MPQ`, …)
2. locale numeric patches (`patch-enUS-1..9.MPQ`)
3. locale alphabetic patches (`patch-enUS-A..Z.MPQ`)
4. base non-patch archives (`common.MPQ`, `lichking.MPQ`, …)
5. base numeric patches (`patch-1..9.MPQ`)
6. base alphabetic patches (`patch-A..Z.MPQ`)  ← highest

**Russian/Chinese clients (ruRU/zhCN, and possibly others):**

1. base non-patch
2. base numeric patches
3. base alphabetic patches
4. locale non-patch
5. locale numeric patches
6. locale alphabetic patches  ← highest

**Consequence, and it is not obvious:** on an English client, `Data/patch-4.MPQ` beats the DBCs
in `Data/enUS/patch-enUS-3.MPQ`, so a single root-level patch works. On a Russian client the
locale patches win, so the very same `Data/patch-4.MPQ` **loses** and your custom `Item.dbc` is
shadowed by the stock one. If you support non-English clients you must ship
`Data/<locale>/patch-<locale>-4.MPQ` (or ship both copies, identical). Verify the winning file
per locale; do not guess beyond `4..9` / `A..Z` ([paradox of the `patch-enus-z` case](https://github.com/haha2345/awesome-wow-335-dev/blob/main/docs/zh-CN/client-data/hd-enus-zhcn-localization.md)).

Naming: stock 3.3.5a already uses `patch.MPQ`, `patch-2.MPQ`, `patch-3.MPQ`, so custom work
starts at **`patch-4.MPQ`**. Numeric sorts before alphabetic, so letters are the escape hatch
when you need to out-rank another mod. Both TrinityCore's and AzerothCore's extractor tooling
recognise `patch-4.MPQ` and later lettered patches when extracting DBCs/maps
(`CONF_mpq_list`), which is why `patch-4` is the de-facto standard.

### Development convenience: folder-as-MPQ

With a small binary patch to `Wow.exe`, the client will happily treat a *folder* named
`Data/patch-4.MPQ/` as an archive containing loose files. This is how modders iterate without
repacking on every change ([guide](https://wotlkdev.github.io/wiki/your_first_mod/your_first_client_edit)).
Use it locally; **do not ship** a patched exe as your content-distribution mechanism
(antivirus noise, no reproducibility, players already have their own launcher preferences).

## 3. DBC files

A DBC is a flat, fixed-width table:

```
struct header { char magic[4] = "WDBC";
                uint32 recordCount, fieldCount, recordSize /* = fieldCount*4 */, stringSize; }
record[recordCount]   // fieldCount × int32/float32 (same width)
char  stringBlock[stringSize]   // NUL-separated; string fields hold offsets into this
```

Facts that matter for custom content:

- **Records are addressed by their first field (the ID).** Most tables are binary-searched by ID;
  the client and core both assume IDs are unique and, in many tables, *sorted ascending*. WDBX
  Editor and the cores will tolerate unsorted rows in most cases, but appending new rows at the
  end and never re-sorting is the safe habit.
- **Field counts are part of the wire/disk contract.** The core validates them and refuses to
  start on a mismatch:
  `dbc exists, and has 13 field(s) (expected 12). Extracted file might be from wrong client
  version or a database-update has been forgotten.`
  ([AzerothCore: common errors](https://www.azerothcore.org/wiki/common-errors)). A DBC from a
  later build (or a hand-edited CSV with an added column) will produce exactly this.
- **String fields are offsets into the string block.** You cannot "just append a name" in a hex
  editor: the offset has to be valid, or you get garbage/asserts. This is why you edit DBCs with
  WDBX Editor / Spell Editor, which rebuild the string block for you.
- **References are unchecked at load time.** `Item.dbc.DisplayInfoID = 12345` is accepted even if
  `ItemDisplayInfo.dbc` has no row 12345. The failure appears later: invisible model, red `?`,
  or a crash when the client tries to resolve the model path. Hence
  `tools/dbc_tool.py refs`.

### Which DBC the *client* uses

The client reads `DBFilesClient\<Table>.dbc` out of its MPQ chain and keeps it in memory for the
whole session. Anything it renders or self-validates comes from there: item class/subclass and
icon, spell name/description/icon/cast time/range/tooltip, spell visuals, creature and
game-object models, titles, achievements, talents, area names, taxi paths, …

### Which DBC the *server* uses

The core reads its **own copy** of the DBCs from `DataDir/dbc`:

```ini
# worldserver.conf
DataDir = "."     # then: ./dbc, ./maps, ./vmaps, ./mmaps
```

(AzerothCore `worldserver.conf.dist`, lines ~183–186.) That directory is normally produced by
`map_extractor`/`vmap4_extractor` from *a* client. If it was extracted from a different client
build than the one players run, the two sets disagree — and the core mostly does not complain,
it silently drops what it cannot resolve. A well-documented real case
([CoA PR #1484](https://github.com/jealous-sound/azerothcore-wotlk-coa/pull/1484)): a stock
server DBC set lacked 59,106 creature displays, 117,115 game-object displays and 12,805 extended
costs versus the players' client, so the core *silently stripped item limit categories from
60,751 items* and *skipped 1,225 game-object spawns* at startup.

**Operational rule:** extract the server DBC set from the exact client archive set your players
run (including your own patches), and re-extract whenever the patch changes.

### Server-side DBC overlays (`*_dbc` SQL tables)

AzerothCore ships **110** `*_dbc` tables in the world database — `item_dbc`, `spell_dbc`,
`itemdisplayinfo_dbc`, `creaturedisplayinfo_dbc`, `spellvisual_dbc`, `itemset_dbc`,
`gemproperties_dbc`, `itemextendedcost_dbc`, `chartitles_dbc`, `achievement_dbc`, `talent_dbc`,
`skilllineability_dbc`, `areatable_dbc`, … Rows there are loaded **after** the DBC files and
either add a new row or replace the row with the same ID
([AzerothCore wiki: spell_dbc](https://github.com/azerothcore/wiki/blob/master/docs/spell_dbc.md)).

This is genuinely useful and genuinely dangerous:

- ✅ You can add/retune server-side behaviour without touching binary DBC files, and without
  restarting your DBC-extraction pipeline. Great for hotfixes and for "invisible" mechanics.
- ✅ It lets the server accept content whose client row you ship in the patch, without editing
  the server's `dbc/` folder.
- ⚠️ **It never changes the client.** A `spell_dbc` row that makes a spell hit for 10k does not
  change the tooltip, the cast bar, or the cooldown the client shows. Player-facing numbers must
  match the client's `Spell.dbc` or the game will feel broken/buggy.
- ⚠️ Overlay rows that duplicate client rows cause real, subtle bugs. Another well-documented
  case ([CoA PR #1498](https://github.com/jealous-sound/azerothcore-wotlk-coa/pull/1498)):
  3,728 `spell_dbc` rows merely restated client data, 760 *replaced* spells the client defines
  differently, and `spelldifficulty_dbc` rows remapped spells the client already maps. The fix
  was to delete the overlays and move genuinely serverside spells into free ID space.
- ⚠️ TrinityCore has fewer of these tables; check per core before designing around them.

## 4. The cache (`Cache/*.wdb`)

The client caches server-provided records — `itemcache.wdb`, `creaturecache.wdb`,
`questcache.wdb`, and friends — to avoid re-querying every login. During development this
produces "ghost" data: you change `item_template`, the client still shows yesterday's stats or
yesterday's icon.

- Delete `Cache/` after any DB/DBC change, or
- use a client build patched to never load the cache, or
- (production) bump your patch version and have the launcher nuke `Cache/` on update.

Item-related cache files are the usual suspects when a fix "doesn't apply"
([TrinityCore discussion #26489](https://github.com/TrinityCore/TrinityCore/discussions/26489)).

## 5. The authority model (memorise this table)

| Data | Client source of truth | Server source of truth | Practical meaning |
|---|---|---|---|
| Item class / subclass / inventory slot / icon / model | `Item.dbc` + `ItemDisplayInfo.dbc` | `item_template` (+ `item_dbc` overlay) | Unknown ID → red `?`, can't equip/use in action bar, "requires a weapon" spells fail |
| Item stats, damage, armor, sockets, spells, bonding, prices | (displayed only) | `item_template` | Freely editable server-side; client just renders what it is sent |
| Spell name/description/icon/visual/cast time/range/cooldown display | `Spell.dbc` and satellite DBCs | `Spell.dbc` + `spell_dbc` | Divergence = lying tooltips and weird cast-bar behaviour |
| Spell effects, damage, aura mechanics, procs | (local prediction only) | server `Spell.dbc`/`spell_dbc` + C++ scripts | Server decides outcomes |
| Creature/appearance/scale | `CreatureDisplayInfo.dbc`, `CreatureModelData.dbc` | `creature_template.modelid` + overlays | Existing display ID → no patch needed |
| Creature stats/AI/loot/spawns | — | `creature_template`, `smart_scripts`, spawns | Pure server content |
| Game-object model | `GameObjectDisplayInfo.dbc` | `gameobject_template.displayId` | Existing display ID → no patch |
| Quests, gossip, vendors, trainers, conditions | (text arrives in packets) | `quest_template`, `npc_text`, `npc_vendor`, `*_trainer`, `conditions` | Fully server-side, including quest text |
| Titles, achievements, talents, glyphs, item sets, area names | Their DBCs | DB + DBC overlays | Player-visible metadata requires a client row |
| UI | addon Lua/XML, FrameXML | — | Addons are the zero-risk client extension point |

Two corollaries that save entire weeks:

1. **You can change the stats of any existing item without any client change.** You cannot
   change its look (that is `ItemDisplayInfo`, keyed by the `displayid` in `item_template`) or
   its "what kind of thing is this" (that is `Item.dbc`).
2. **You can change the *mechanics* of any existing spell server-side without a patch** (via
   `spell_dbc`), but the client will keep telling the player the old numbers. Do that only for
   internal effects the player does not read off a tooltip.

## 6. Config flags you will touch

| Config | Effect | Notes |
|---|---|---|
| `DataDir` (both cores) | Where `dbc/`, `maps/`, `vmaps/`, `mmaps/` live | Must be the client-matching DBC set |
| `DBC.EnforceItemAttributes` (AzerothCore, default `1`) | `1` = refuse DB values that contradict `Item.dbc` and log errors; `0` = let the DB win | Set `0` only if you *deliberately* want `item_template` to override DBC-derived class/subclass/slot. It does **not** change client behaviour ([conf source](https://github.com/azerothcore/azerothcore-wotlk/blob/master/src/server/apps/worldserver/worldserver.conf.dist)) |
| `Rate.*`, `MaxPlayerLevel`, `Expansion` | Content tuning | Server-only, no patch |
| `Warden.*` (AzerothCore/TC) | Anti-cheat module | Not a content-version check; do not confuse it with patch verification |

AzerothCore's own FAQ is blunt about scope, and it is worth repeating: the project exists for
theory-crafting/learning and does not endorse client modification
([AzerothCore FAQ](https://www.azerothcore.org/wiki/faq)). Everything here is standard
private-server modding practice; whether you distribute Blizzard-derived bytes is a legal
question, not a technical one — see
[04](04-patching-toolchain-and-release.md#10-legal-and-distribution-hygiene).
