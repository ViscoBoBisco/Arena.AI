# 07 — Sources and further reading

Annotated. "Primary" = format documentation or core source. "Field" = practitioner reports
(guides/forums); useful, but check the client build the author was using.

---

## Format & client internals (primary)

- **wowdev.wiki — MPQ** — https://wowdev.wiki/MPQ
  Archive format: header, hash/block tables, `FormatVersion` (0 = v1 used by 3.3.5), sector size
  shift, encryption, patch files. The reference for anything archive-level.
- **getMaNGOS — MPQ file format** — https://www.getmangos.eu/wiki/referenceinfo/clientfiles/mpq-file-r20035/
  Header table and flags in plain language; source of the "sector size shift must be 3" note.
- **wowdev.wiki — DB/Item** — https://wowdev.wiki/DB/Item
  `Item.dbc` structure per build; documents that 3.3.5 has `ID, Class, SubClass,
  sound_override_subclassid, Material, ItemDisplayInfo, InventorySlotID, SheathID`.
- **wowdev.wiki — DB/ItemDisplayInfo** — https://wowdev.wiki/DB/ItemDisplayInfo
  Geoset semantics and the m_texture indices; the geoset-group tables explain why copying a
  "similar item" row is the correct way to make new looks.
- **WoWDBDefs** — https://github.com/wowdev/WoWDBDefs
  Machine-readable field definitions per build (`ItemDisplayInfo.dbd`, etc.). The authoritative
  answer for "which build has which field".
- **WoTLK Modding Wiki — Client** — https://wotlkdev.github.io/wiki/theory/client
  Folder layout, listfiles, **per-locale MPQ load order**, folder-as-MPQ, cache.
- **WoTLK Modding Wiki — Tools** — https://wotlkdev.github.io/wiki/tools/
  Current tool recommendations (WDBX over MyDBCEditor, etc.).
- **WoTLK Modding Wiki — Your First Client Edit** — https://wotlkdev.github.io/wiki/your_first_mod/your_first_client_edit
  Step-by-step MPQ extraction/override, merged mode, folder-as-MPQ.
- **WoTLK Modding Wiki — Your First Data Edits** — https://wotlkdev.github.io/wiki/your_first_mod/your_first_data_edits
  DBC vs SQL table split, editor categories, TSWoW's different paradigm.

## Core source (primary, AzerothCore `master`)

- `src/server/shared/DataStores/DBCStructure.h` — `ItemEntry` (8 fields), `SpellEntry`
  (field indices: Effect 71–73, EffectBasePoints 80–82, EffectApplyAuraName 95–97,
  EffectMiscValue 110–112, EffectTriggerSpell 116–118, SpellVisual 131–132, SpellIconID 133,
  SpellName 136, Rank 153), `ItemSetEntry`, `ItemExtendedCostEntry`.
- `src/server/shared/DataStores/DBCfmt.h` — `Itemfmt[] = "niiiiiii"`;
  `SpellEntryfmt[]` (the 234-field 3.3.5 layout).
- `src/server/apps/worldserver/worldserver.conf.dist` — `DataDir`, `DBC.EnforceItemAttributes`
  (default `1`, with the exact semantics quoted in [01](01-client-and-server-data-model.md)).
- `data/sql/base/db_world/*_dbc.sql` — the **110** `*_dbc` overlay tables (verified by listing the
  tree): `item_dbc`, `spell_dbc`, `itemdisplayinfo_dbc`, `creaturedisplayinfo_dbc`,
  `spellvisual_dbc`, `itemset_dbc`, `gemproperties_dbc`, `itemextendedcost_dbc`,
  `chartitles_dbc`, `achievement_dbc`, `talent_dbc`, …
- **AzerothCore wiki — `spell_dbc`** — https://github.com/azerothcore/wiki/blob/master/docs/spell_dbc.md
  Overlay semantics: rows add or replace spells server-side; every column must be present.
- **AzerothCore wiki — spell aura reference** — https://www.azerothcore.org/wiki/spell-aura-reference
  `SPELL_AURA_MOUNTED (78)` → `EffectMiscValueA = Creature ID` (the mount recipe's key fact).
- **AzerothCore wiki — spell effects reference** — https://www.azerothcore.org/wiki/spell-effects-reference
  Effect IDs and their misc-value meanings.
- **AzerothCore wiki — common errors** — https://www.azerothcore.org/wiki/common-errors
  The `has 13 field(s) (expected 12)` diagnostic and "extract DBCs from the matching client".
- **AzerothCore wiki — item_template** — https://www.azerothcore.org/wiki/item_template
  Field-by-field, `InventoryType` enum, `Material`, `itemset`, `socketBonus`.
- **TrinityCore — item_template / Item.dbc** — https://trinitycore.info/database/335/world/item_template
  and https://trinitycore.atlassian.net/wiki/spaces/tc/pages/2130234/Item
  The `ItemEntry` struct the cores use; "you can not make up new item sets" note.

## Core code paths cited in [08](08-how-integration-actually-works.md) (primary)

All paths are AzerothCore `master`; TrinityCore 3.3.5 is structurally similar but names differ.

- `src/server/game/DataStores/DBCStores.cpp` — `LoadDBC()`: file load → localized strings →
  `LoadFromDB(dbTable)` → field-count error message.
- `src/server/shared/DataStores/DBCStore.cpp` / `DBCDatabaseLoader.cpp` — overlay mechanics:
  `SELECT * FROM <table> ORDER BY ID DESC`, index array growth, **numeric columns override
  literally, empty strings inherit**, rows replace by ID.
- `src/server/game/Globals/ObjectMgr.cpp` — `LoadItemTemplates`: `sItemStore.LookupEntry(entry)`,
  the `DBC.EnforceItemAttributes` block that *overwrites* class/subclass/material/inventory/
  display/sheath from the DBC, and the item→spell link validation that zeroes unknown spells.
- `src/server/game/Handlers/ItemHandler.cpp` — `HandleItemQuerySingleOpcode`:
  `SMSG_ITEM_QUERY_SINGLE_RESPONSE`, and the `entry | 0x80000000` unknown-item sentinel.
- `src/server/game/Handlers/QueryHandler.cpp` — `HandleCreatureQueryOpcode`: model IDs are sent
  from the server, so display reuse needs no patch.
- `src/server/game/Handlers/SpellHandler.cpp` — `HandleCastSpellOpcode`: the client sends only
  `castCount, spellId, castFlags`; the server resolves its own `SpellInfo`.
- `src/server/game/Spells/Auras/SpellAuras.cpp` — `AuraApplication::BuildUpdatePacket`:
  `SMSG_AURA_UPDATE` carries the **spell ID**; the client renders from its own `Spell.dbc`.
- `src/server/game/Handlers/ChatHandler.cpp` — addon traffic: `LANG_ADDON`, `CONFIG_ADDON_CHANNEL`,
  the `msg.length() > 255` rejection, `AddonChannelCommandHandler`, and Warden's use of the same
  channel.
- `src/server/game/Handlers/QuestHandler.cpp` — `HandleQuestQueryOpcode` →
  `SendQuestQueryResponse`: quest text is server-authored.

## Field reports that shaped the "drift is silent" warning

- **CoA PR #1484 — build the server DBC set from the client players run**
  https://github.com/jealous-sound/azerothcore-wotlk-coa/pull/1484
  A stock server DBC set versus the players' client: 59,106 creature displays and 12,805 extended
  costs missing; 60,751 items silently lost their limit category; 1,225 game-object spawns
  skipped. Also: a core assert on non-enUS spell name offsets.
- **CoA PR #1498 — load client DBC data instead of SQL overlays**
  https://github.com/jealous-sound/azerothcore-wotlk-coa/pull/1498
  Quantifies overlay collateral damage: 3,728 `spell_dbc` rows merely restating client data, 760
  rows replacing client spells, difficulty rows remapping spells the client already maps.
- **AzerothCore PR #14675 — item.dbc, item_dbc, enforcement**
  https://github.com/azerothcore/azerothcore-wotlk/pull/14675
  Adds `item_dbc` and `DBC.EnforceItemAttributes`; shows the exact `item_dbc` row shape.
- **TrinityCore discussion #26489 — red mark on custom items**
  https://github.com/TrinityCore/TrinityCore/discussions/26489
  Why "I did everything right" still shows `?` (client limitation / environment).
- **AzerothCore discussion #4238 — creating custom items, DBC part**
  https://github.com/azerothcore/azerothcore-wotlk/discussions/4238
  Practical WDBX + MPQEditor walkthrough; states plainly that every player needs the patch.
- **AzerothCore discussion #5307 — why spells are stored in DB**
  https://github.com/azerothcore/azerothcore-wotlk/discussions/5307
  The design rationale for `spell_dbc` and its tooling cost.

## Guides (field, check the build)

- **"The truth about custom items and the big red question mark"** —
  https://talk.trinitycore.org/t/guide-the-truth-about-custom-items-and-the-big-red-question-mark/21524
  The three-places model and the exact consequences of a missing `Item.dbc` row.
- **"Placing all your custom items from your DB into Item.dbc (3.3.5)"** —
  https://www.ownedcore.com/forums/world-of-warcraft/world-of-warcraft-emulator-servers/wow-emu-guides-tutorials/395673-arcemu-trinitycore-placing-all-your-custom-items-your-db-item-dbc-3-3-5-a.html
  SQL → CSV → DBC → MPQ pipeline; also the source of the common "uncheck (attributes)" advice.
- **"How to make a Custom Currency 3.3.5"** —
  https://www.ownedcore.com/forums/world-of-warcraft/world-of-warcraft-emulator-servers/wow-emu-guides-tutorials/794318-how-make-custom-currency-3-3-5-a.html
  `ItemExtendedCost.dbc` fields that actually matter.
- **"How To Add Custom Wings (3.3.5)"** —
  https://www.wowmodding.net/topic/988-how-to-add-custom-wings-335/
  The `SpellVisualEffectName → SpellVisualKit → SpellVisual → Spell.dbc` chain in practice.
- **"Creating Your First MPQ Patch" (3.3.5 naming rules)** —
  https://www.wowmodding.net/topic/1460-41-creating-your-first-mpq-patch/
  Numeric-then-alphabetic load order and the `patch-4` starting point.
- **"Custom Item Fix" (WoW.exe patch, 3.3.5.12340 enUS)** —
  https://www.wowmodding.net/files/file/283-wow-335-patcher-custom-item-fix/
  The binary-patch alternative to `Item.dbc`; lists the 8 fields and the
  `DBC.EnforceItemAttributes = 0` requirement.
- **"Custom class in 3.3.5a"** (MPQ priority list as posted by modders) —
  https://www.ownedcore.com/forums/world-of-warcraft/world-of-warcraft-emulator-servers/wow-emu-guides-tutorials/774546-creating-custom-class-3-3-5a.html
  Shows the full chain including speech/locale archives; useful cross-check on load order.

## Tooling

- **WDBX Editor** — https://github.com/WowDevTools/WDBXEditor
- **Stoneharry's Spell Editor** — https://github.com/stoneharry/WoW-Spell-Editor
  ("new spell from existing record", visual-kit copy/paste, DBC ⇄ SQL).
- **MPQEditor** — http://www.zezula.net/en/mpq/download.html
- **mpq-builder (TS/CLI)** — https://github.com/ayoub-khemissi/mpq-builder
  Scriptable MPQ v1 creation — the shape a CI patch build should take.
- **mod-visible-bags** — https://github.com/buildthehomelab/wow-mod-visible-bags
  Reference implementation of a scripted patch builder that emits both the MPQ and `spell_dbc.sql`.
- **mod-mount-autoselect** — https://github.com/cthu1hoo/mod-mount-autoselect
  Ships `Spells.csv` rows + module; good pattern for "tiny patch, real feature".
- **mod-individual-progression** — https://github.com/ZhengPeiRu21/mod-individual-progression
  Optional patch files and `DBC.EnforceItemAttributes = 0` usage.
- **Noggit / Noggit Red** — https://github.com/wowdev/noggit3 · https://gitlab.com/prophecy-rp/noggit-red
- **WoW Blender Studio, MultiConverter** — see the WoTLK Modding tools page.

## Ecosystem knowledge bases

- **awesome-wow-335-dev** — https://github.com/haha2345/awesome-wow-335-dev
  Bilingual (zh/en) knowledge base with a *system-ownership map* (which layer should own a
  feature), toolchain notes, asset porting, AddOn/DLL guidance, and case studies with explicit
  validation levels (`REFERENCE` / `VALIDATED_LOCAL` / `REAL_CLIENT_ACCEPTED` / …). Its
  "choose the owning layer first" principle is the same one this document uses.
- **awesome-wow-335-clients** — https://github.com/haha2345/awesome-wow-335-clients
  Client frameworks/HD packs (TSWoW, Project Reforged) and what layer each one extends.
- **AzerothCore wiki** — https://www.azerothcore.org/wiki/
- **TrinityCore 3.3.5 wiki/doxygen** — https://trinitycore.info/
- **WoTLK Modding Wiki** — https://wotlkdev.github.io/wiki/
- **emudevs / model-changing / wowmodding** — long-running modding communities; search before
  asking, most 3.3.5a questions are 10+ years old and answered.

---

## Things explicitly *not* verified here

- Exact `ItemSet.dbc` field count beyond the first 10 item slots (cores read 10; the file may
  carry 17). Confirm in WDBX for your build.
- `ItemExtendedCost.dbc` field count in 3.3.5 (honor/arena/item pairs + rating); the layout used
  by the cores is in `DBCStructure.h::ItemExtendedCostEntry`.
- Per-core support for `creature_template.scale` and other convenience columns (AzerothCore and
  TrinityCore differ).
- Whether any given retro-port `.dbc` pack is field-compatible with 12340. Always check with
  `dbc_tool.py info` and by comparing a known row against your stock table.
- Behaviour of the WoW.exe "Custom Item Fix" beyond the reported enUS 12340 build.
