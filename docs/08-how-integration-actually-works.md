# 08 — How custom content actually works, mechanically

The other documents tell you *what* to do. This one explains *why it works*, traced through the
code paths on both sides, so you can reason about cases nobody wrote a guide for.

Everything marked **[core]** below was read from AzerothCore `master` source (file and symbol
named), so it is verifiable. Everything marked **[client]** is documented/observed client
behaviour: the client is closed-source, so those are behavioural claims, not code citations.

---

## 0. The whole system in four steps

| Step | Where it happens | What exists afterwards |
|---|---|---|
| **1. Author** | your repo | `sql/*.sql`, DBC rows (edited in WDBX), module code, addon Lua |
| **2. Build** | your repo / CI | one validated client patch (`patch-N.MPQ` holding `DBFilesClient\*.dbc`), a matching server `dbc/` set, a manifest with the hash |
| **3. Boot** | client and server, separately | client: DBC tables parsed from the MPQ chain. Server: its own DBC files, then `*_dbc` SQL overlays applied on top, then world DB templates |
| **4. Play** | the connection | packets carry *state* (queries, casts, auras, object updates); each side resolves **IDs** against its own tables and draws its own conclusions |

The load-bearing sentence: **the protocol carries state, never definitions.** Definitions are
duplicated by hand on both sides, and nothing checks that the two copies agree except you.

Every bug in this domain is a violation of one of those three boxes: the definitions disagree,
the patch did not reach the client, or the server's runtime state references an ID one side
cannot resolve.

---

## 1. Two worlds, one ID space

| | Client | Server |
|---|---|---|
| Static definitions | DBCs inside the MPQ chain, loaded once at boot | its own `DataDir/dbc/*.dbc` + `*_dbc` SQL overlays + world DB tables |
| Mutable state | none (except `Cache/*.wdb`, a *copy* of what the server told it) | world DB, character DB |
| Authority | rendering, local validation, prediction | all outcomes, ownership, persistence |
| Update mechanism | ship a patch and restart | SQL + restart |

The only thing the two sides share is **integers**: item entries, spell IDs, display IDs,
creature entries, quest IDs, area IDs. There is no negotiation, no schema handshake, and no
version check. A shared integer means the same thing on both sides *only because you made it so*.

That is why "custom content" is really "extend a shared namespace in two places at once".

## 2. Boot mechanics

### 2.1 Client boot **[client]**

1. `Wow.exe` enumerates `Data/*.MPQ` and `Data/<locale>/*.MPQ`, builds a priority-ordered
   archive list (see [01](01-client-and-server-data-model.md#load-order-this-is-where-people-get-burned)),
   and answers every file open by hashing the path and taking the highest-priority hit.
2. It opens `DBFilesClient\*.dbc` for the tables it needs and parses them into fixed-layout,
   index-by-first-field arrays. From that point on, those arrays *are* the client's world model.
3. It loads `Interface/FrameXML` + `Interface/AddOns`, fires `ADDON_LOADED`/`VARIABLES_LOADED`,
   then connects to the realm.

Consequences:

- A DBC row the client cannot parse is a **boot-time** failure (crash/hang), not a per-feature
  bug. Structure is validated by the binary, semantics are not.
- Nothing revalidates a DBC row later. If `Item.dbc` says entry 800000 is a shield, the client
  will believe that forever, even when the server's item query says it is a two-hander.
- A patch swap requires a client restart; there is no reload path.

### 2.2 Server boot **[core]** — `src/server/game/DataStores/DBCStores.cpp`, `DBCStore.cpp`,
`DBCDatabaseLoader.cpp`

Per table, `LoadDBC()` does three things in order:

```cpp
if (storage.Load(dbcFilename.c_str()))          // 1. read DataDir/dbc/<Table>.dbc
    for (uint8 i = 0; i < TOTAL_LOCALES; ++i)   //    + optional localized string blocks
        storage.LoadStringsFrom(localizedName.c_str());

if (dbTable)                                    // 2. apply the SQL overlay, if the table has one
    storage.LoadFromDB(dbTable, dbFormat ? dbFormat : storage.GetFormat());

if (storage.GetNumRows()) existDBData = true;   // 3. else: log "exists, and has N field(s)..."
```

The overlay loader is the single most important piece of machinery in custom 3.3.5a work:

```cpp
// DBCDatabaseLoader::Load  (abridged)
std::string query = Acore::StringFormat("SELECT * FROM `{}` ORDER BY `ID` DESC", _sqlTableName);
uint32 indexTableSize = std::max(records, (*result)[_sqlIndexPos].Get<uint32>() + 1);  // grow index
// ... for every row:
uint32 indexValue = fields[_sqlIndexPos].Get<uint32>();
char* oldDataValue = indexTable[indexValue];       // the DBC row, possibly null
// numeric columns: taken literally, ALWAYS
// string columns:
if (fields[sqlColumnNumber].Get<std::string>().empty() && oldDataValue)
    *reinterpret_cast<char**>(...) = *reinterpret_cast<char**>(&oldDataValue[dataOffset]); // inherit
else
    *reinterpret_cast<char**>(...) = CloneStringToPool(...);                               // override
```

Read that carefully, because it defines the semantics you get for free:

- **A row in `*_dbc` replaces the DBC row with the same ID** (allocating space if the ID is
  beyond the current table end). Add or replace, nothing else.
- **Numbers are literal.** A column you leave out of your `INSERT` becomes the table default
  (`0`), and `0` *is* the override. Partial inserts do not "inherit" numbers — see
  [03 §6](03-recipes.md#6-an-invisible-serverside-spell-no-client-patch) for the consequence.
- **Empty strings inherit.** This is why you can add one spell row and leave all sixteen locale
  name columns blank: the existing names survive. It is also why you cannot blank a name this way.
- **`ORDER BY ID DESC` is load-bearing**: the first row in the result set is the highest ID, and
  that is what sizes the index array. Never change the ordering.
- Overlays only exist where the core defines a `dbTable` for that store — 110 of them in
  AzerothCore (see [01 §3](01-client-and-server-data-model.md#server-side-dbc-overlays-_dbc-sql-tables)).

### 2.3 Server boot — item templates **[core]** — `ObjectMgr::LoadItemTemplates`

Every `item_template` row is validated against the item store (`Item.dbc` + `item_dbc`):

```cpp
ItemEntry const* dbcitem = sItemStore.LookupEntry(entry);
if (!dbcitem)
{
    LOG_DEBUG("sql.sql", "Item (Entry: {}) does not exist in item.dbc! (not correct id?).", entry);
    continue;                                   // skip the validation/normalisation tail
}

if (enforceDBCAttributes)                       // DBC.EnforceItemAttributes, default 1
{
    if (itemTemplate.Class != dbcitem->ClassID)                    itemTemplate.Class = dbcitem->ClassID;
    if (itemTemplate.SubClass != dbcitem->SubclassID)              itemTemplate.SubClass = dbcitem->SubclassID;
    if (itemTemplate.SoundOverrideSubclass != dbcitem->SoundOverrideSubclassID) ...
    if (itemTemplate.Material != dbcitem->Material)                itemTemplate.Material = dbcitem->Material;
    if (itemTemplate.InventoryType != dbcitem->InventoryType)      itemTemplate.InventoryType = dbcitem->InventoryType;
    if (itemTemplate.DisplayInfoID != dbcitem->DisplayInfoID)      itemTemplate.DisplayInfoID = dbcitem->DisplayInfoID;
    if (itemTemplate.Sheath != dbcitem->SheatheType)               itemTemplate.Sheath = dbcitem->SheatheType;
}
```

Three mechanical facts fall out:

1. **With the default config, the DBC silently wins 7 of the 8 Item.dbc fields.** If
   `item_template.displayid` disagrees with `Item.dbc`, you do not get your model — you get the
   DBC's, with an `ERROR` in the log. "The DBC and the DB must agree" is not style advice; it is
   the literal control flow.
2. Turn enforcement off (`= 0`) and the DB wins *on the server*, but the client's own `Item.dbc`
   still governs the client. You have now deliberately created a disagreement.
3. A missing `Item.dbc` row is not a hard failure. The row stays in the store with whatever the
   DB said, skips the validation/normalisation tail of the loader, and the only trace is a
   **debug-level** line. Default log levels hide it, which is why the failure surfaces as a broken
   item in game rather than a failed boot.

Later in the same function, item→spell links are validated too:

```cpp
SpellInfo const* spellInfo = sSpellMgr->GetSpellInfo(itemTemplate.Spells[j].SpellId);
if (!spellInfo && !sDisableMgr->IsDisabledFor(DISABLE_TYPE_SPELL, ...))
{
    LOG_ERROR("sql.sql", "Item (Entry: {}) has wrong (not existing) spell in spellid_{} ({})", ...);
    itemTemplate.Spells[j].SpellId = 0;         // link is destroyed, item keeps working without it
}
```

So "the item's Use: effect does nothing" often means: *the server* did not know that spell and
silently unhooked it at load. Not the client, not the DBC.

## 3. The four runtime protocols

Content reaches a player through exactly four mechanisms. Knowing which one your feature uses
tells you which side must own the data.

| # | Mechanism | Direction | Carries | Examples |
|---|---|---|---|---|
| 1 | **Query** (pull) | client → server → client | definitions the client cannot know | item templates, creature templates, game-object templates, quest text, NPC text, page text |
| 2 | **Broadcast** (push) | server → client | state and events | object create/update (incl. display ID), auras, cast results, loot, quest events |
| 3 | **Local-only** | client internal | anything the client resolves itself | DBC lookups for icons, models, spell names, tooltips, talent panes |
| 4 | **Overlay** | server internal | rules the client never sees | `spell_dbc`, custom effects, script logic |

The useful mental test for any feature: **does the client need to draw it before the server has
spoken?** If yes, it is mechanism 3 and needs a patch. If the server speaks first, mechanism 1 or
2 can carry it.

### 3.1 Query mechanics, verified

Item **[core]** — `ItemHandler.cpp::HandleItemQuerySingleOpcode`:

```cpp
uint32 item; recvData >> item;
ItemTemplate const* pProto = sObjectMgr->GetItemTemplate(item);
if (pProto) {
    WorldPacket queryData(SMSG_ITEM_QUERY_SINGLE_RESPONSE, 600);
    queryData << pProto->ItemId << pProto->Class << pProto->SubClass
              << pProto->SoundOverrideSubclass << Name /* + ~120 more fields */;
    SendPacket(&queryData);
} else {
    WorldPacket queryData(SMSG_ITEM_QUERY_SINGLE_RESPONSE, 4);
    queryData << uint32(item | 0x80000000);      // "unknown item" sentinel
    SendPacket(&queryData);
}
```

Note what the client *asks for*: a bare entry ID. The server answers with a full description —
including class, subclass and display — from `item_template`, i.e. from the **SQL** side. So why
does `Item.dbc` matter at all?

Because the client **[client]** resolves many things **before** (and independently of) that
query: bag/bank rendering, action-bar placement, equip-slot validation, "is this a weapon" checks
for ability requirements, enchant/gem legality, and the fallback icon. Those paths consult the client's own
`Item.dbc` record. The query enriches the tooltip; the DBC decides whether the client considers
the object a real, usable item. When they disagree, you get items that "exist but can't be used".

Creature **[core]** — `QueryHandler.cpp::HandleCreatureQueryOpcode` sends name, subname, icon,
type flags, `CreatureType.dbc` id, `CreatureFamily.dbc` id, rank, kill credit and **model IDs**
(`ci->GetModelByIdx(0)->CreatureDisplayID`). The client then resolves display ID → model locally.
That is the mechanical reason a brand-new creature needs no patch as long as it reuses an existing
display ID, and why an unknown display ID renders as nothing.

Quest **[core]** — `QuestHandler.cpp::HandleQuestQueryOpcode` calls
`_player->PlayerTalkClass->SendQuestQueryResponse(quest)`, which builds the whole quest body
(title, objectives, details, rewards) from `quest_template`. The client never had this text.

### 3.2 Broadcast mechanics, verified

Spell cast **[core]** — `SpellHandler.cpp::HandleCastSpellOpcode`:

```cpp
uint8 castCount, castFlags; uint32 spellId;
recvPacket >> castCount >> spellId >> castFlags;         // that is ALL the client sends
SpellInfo const* spellInfo = sSpellMgr->GetSpellInfo(spellId);
if (!spellInfo) { LOG_ERROR("network.opcode", "WORLD: unknown spell id {}", spellId); return; }
```

The client sends an **ID**, not a spell definition. Everything about the spell — cost, range,
targets, effects — lives on the server, and the client only ran *its own* copy to decide it was
allowed to send the request at all. Hence the two classic symptoms:

- server doesn't know the spell → log line, silence, client shows the cast start then nothing;
- client doesn't know the spell → no icon, no way to click it, but scripts can still cast it.

Aura application **[core]** — `SpellAuras.cpp::AuraApplication::BuildUpdatePacket` writes
`slot, spellId, flags, casterLevel, stackOrCharges, ...` into `SMSG_AURA_UPDATE`. The client
receives a **spell ID** and looks it up in its own `Spell.dbc` for icon, name, tooltip and
duration display. Unknown ID → the aura exists mechanically but is invisible in the buff bar.
This is exactly why serverside-only spells are acceptable for hidden mechanics and unacceptable
for anything a player must see.

Object creation **[core]** follows the same shape: `SMSG_UPDATE_OBJECT` carries display IDs,
which the client resolves through `CreatureDisplayInfo.dbc` / `GameObjectDisplayInfo.dbc`.

### 3.3 Local-only mechanics

Anything the client decides without asking: icon → texture path resolution, item model →
`ItemDisplayInfo` → `ModelName`/`ModelTexture` → M2/BLP in the MPQs, spell name/description
tokens, talent trees, achievement display, title list, area names, taxi map, and the tooltip's
structure. All of it is DBC or client code. None of it can be authored server-side. This is the
whole reason a patch exists.

### 3.4 Overlay mechanics

`spell_dbc` (and the other 109 `*_dbc` tables) change what the **server** computes, never what
the client draws. The mechanical consequence is permanent: every player-visible number must be
authored twice, or the tooltip lies.

## 4. End-to-end traces

### 4.1 Adding an item

```
SQL insert item_template ──► server boot: LoadItemTemplates
                              └─ sItemStore.LookupEntry(entry)
                                 ├─ missing → debug log, validation skipped
                                 └─ present + EnforceItemAttributes
                                       → class/subclass/material/inventory/display/sheath
                                         OVERWRITTEN from the DBC
client patch Item.dbc row ──► client boot: parsed into the item table
in game: .additem ──► SMSG_ITEM_QUERY_SINGLE_RESPONSE fills the tooltip
                       client also consults its Item.dbc row for usability
                       result cached in Cache/ItemCache.wdb
```

Failure surfaces, in order of how often they bite: client row missing (red `?`), DB/DBC mismatch
(model or slot silently replaced), stale cache, patch shadowed by a higher-priority archive.

### 4.2 Adding a spell

```
Spell.dbc row (both sides) ──► server: sSpellMgr builds SpellInfo from DBC + spell_dbc overlay
                                client: parsed into its spell table at boot
learn (trainer/item/quest/script) ──► client builds the spellbook entry LOCALLY from its DBC
cast ──► client pre-validates with its DBC (cooldown, cost, range, target)
      ──► CMSG_CAST_SPELL{castCount, spellId, castFlags}
      ──► server resolves ITS SpellInfo, validates, executes
      ──► SMSG_SPELL_START/GO + SMSG_AURA_UPDATE{spellId,...}
      ──► client renders visual/aura from ITS DBC + SpellVisual chain
```

Four independent lookups (client book, client cast check, server cast, client render). Any one of
them can fail separately; that is why "the spell does nothing" has six different causes.

### 4.3 Adding a creature

```
creature_template + creature spawn ──► server sends create object (display ID)
                                        client: CreatureDisplayInfo → CreatureModelData → M2
creature query ──► name/subname/type/family/model ids (server-authored)
AI/loot/stats ──► entirely server-side
```

No patch, because the only client-side input is an ID that already exists.

### 4.4 Adding a quest

```
quest_template (+ quest_objectives/poi/reward tables, page_text, conditions)
gossip ──► SMSG_GOSSIP_MESSAGE (server-authored options)
accept ──► SMSG_QUESTGIVER_OFFER_REWARD / quest update events
log/panel text ──► SendQuestQueryResponse (server-authored)
```

No patch, because the client's quest UI is a renderer for server data. The exceptions are the
*rewards*: an item or spell that the client cannot resolve reintroduces the patch requirement.

### 4.5 A custom system (module + addon)

```
module (C++/Lua) ⇄ world DB        ← authority: progression, costs, ownership
module ⇄ addon messages            ← LANG_ADDON chat packets, ≤255 bytes each
addon (Lua)                        ← presentation only
```

**[core]** `ChatHandler.cpp` accepts addon traffic only when `lang == LANG_ADDON` and the
`CONFIG_ADDON_CHANNEL` config allows it, and rejects any message longer than 255 bytes
(`if (msg.length() > 255 ...) return;`). It also parses `.`-commands sent through the addon
channel with `AddonChannelCommandHandler`, and Warden itself rides this channel for its Lua check
responses — which is a good reminder that the addon channel is a *shared, unauthenticated* pipe:
never trust it for authority, only for intent.

## 5. Where the pipeline loses data (the lossy joints)

Number these in your head when debugging; every custom-content bug is one of them.

| # | Joint | What happens | Detection |
|---|---|---|---|
| J1 | DBC structure | client: crash; server: `has N field(s) (expected M)` | `dbc_tool.py info` |
| J2 | Client MPQ priority | your row never loads; another archive's file wins | extract in merged mode, diff |
| J3 | Client lookup | ID has no row → red `?` / invisible / no icon | `dbc_tool.py refs` + in-game |
| J4 | Server DBC presence | row missing → debug log, validation skipped | grep log at debug |
| J5 | Enforcement | DBC overwrites DB fields (7 of 8 item fields) | read the `ERROR` lines at boot |
| J6 | Spell-link validation | item/quest points at spell the server lacks → link zeroed | `has wrong (not existing) spell` |
| J7 | Overlay semantics | partial row zeroes numbers; empty strings inherit | compare overlay row vs client row |
| J8 | Cache | stale `*.wdb` masks everything above | delete `Cache/` |
| J9 | Unknown ID at runtime | server: `unknown spell id`; client: silence | core debug logs |
| J10 | Locale routing | patch loses to locale archive on ruRU/zhCN | check the winning file per locale |

Note how many of these are **silent**. That is the defining property of this domain: the happy
path is loud (things appear), the failure path is quiet (things don't). Build your tooling around
making the quiet failures loud.

## 6. The consistency contract

Seven invariants. If you keep them, custom content behaves; if you break one, you get the
symptoms in [05](05-failure-modes.md).

- **I1 — Structure**: every patched table has the client build's field count and a valid string
  block. Broken → crash, so this is the one you cannot ship by accident.
- **I2 — Namespace**: an ID means exactly one thing, forever, on both sides.
- **I3 — Parity**: for each table both sides read, client bytes == server bytes.
- **I4 — Closure**: every ID a row references exists in the referenced table (icons, displays,
  visuals, durations, ranges, enchantments…).
- **I5 — Agreement**: server DB values that the DBC also defines (item class/subclass/slot/
  display/sheath) equal the DBC's values — or you accept the overwrite explicitly.
- **I6 — Visibility split**: anything the player must see is authored client-side too; anything
  the player must not see is server-only, deliberately.
- **I7 — Delivery**: players run exactly one patch version, the launcher proved it, the cache was
  purged.

Testable form of I1/I3/I4 is `tools/preflight.sh` (structure, parity, references). I2/I7 need
process (manifest + changelog + launcher). I5 is a boot-log assertion. I6 is a design review.

## 7. What the mechanics imply for design

1. **Patch surface = player-visible surface.** Every feature you can express with mechanisms 1, 2
   and 4 avoids the entire patch problem. Push features toward those.
2. **The DB is where iteration happens; the DBC is where the release happens.** Author stats in
   SQL freely (restart-only), touch DBC rows once per release (repack + redistribute).
3. **Server-side overrides are a hotfix tool, not a design tool.** They cannot fix a lie the
   client is telling the player.
4. **ID allocation is the real schema.** In a world with no foreign keys, your namespace plan *is*
   your data model; the manifest is its documentation.
5. **Client and server are deployed at different cadences.** Anything requiring them to change
   together will eventually drift; design so a stale client degrades (missing icon) rather than
   corrupts (crash / wrong item).
6. **Every silent joint is a monitoring opportunity.** Turn the debug logs on in staging and treat
   `does not exist in item.dbc`, `has wrong (not existing) spell`, `unknown spell id` as build
   failures, not warnings.

## 8. The integration engine (turning all this into a machine)

The mechanics reduce to a five-stage state machine. Implement it once, and every feature is data.

```
   [1] SPEC → [2] KERNEL → [3] SKIN → [4] GATE → [5] SHIP
```

- **[3] SKIN** can send you back to **[1] SPEC** when the fantasy turns out not to be expressible
  without new art.
- **[4] GATE** can send you back to **[1]** (design wrong) or **[3]** (data wrong).

- **[1] SPEC** — decide the owning mechanism for each part of the feature
  (query / broadcast / local / overlay). If a part is local, it is patch work; budget for it.
- **[2] KERNEL** — SQL + scripts/module. Verifiable with GM commands alone, no client work.
  Everything that can be tested without a patch should be finished and frozen here.
- **[3] SKIN** — IDs: reuse first (`displayid`, `modelid`, `SpellVisual`, icons), author rows only
  when reuse cannot express the fantasy. Every new ID comes from the manifest's free range.
- **[4] GATE** — `preflight.sh`, boot-log assertions (I5/I6/J4/J6), clean-client playtest,
  rollback test (previous patch + previous SQL still consistent).
- **[5] SHIP** — manifest + sha256 + addon versions + cache purge + forward-only changelog.

The state machine matters because it puts the *irreversible* work (client rows, IDs, patch
versions) last, after the reversible work (SQL, scripts, tuning) is proven. In practice this is
the difference between shipping weekly and shipping quarterly.
