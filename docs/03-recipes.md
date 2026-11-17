# 03 — Recipes

Field indices are 0-based and were checked against AzerothCore `master`
(`src/server/shared/DataStores/DBCStructure.h`). **Always confirm column names in your DBC
editor** (WDBX shows the header names from your client build; index drift between patches is
real).

---

## 0. Workspace layout (do this once)

The single most effective habit: keep one source of truth and build everything from it.

```
wow-content/
  dbc-src/            # extracted stock DBCs, never edited in place
  dbc-client/         # the DBCs that go into the patch  (generated)
  dbc-server/         # the DBCs that go into DataDir/dbc (generated)
  sql/                # per-feature .sql files, numbered, idempotent
  patch/              # DBFilesClient/* staging area for MPQ build
  manifest.json       # what is in this release: ids, tables, version
  tools/              # dbc_tool.py, build scripts
```

Extraction: open MPQEditor, select **all** MPQs in `Data/` **and** `Data/<locale>/` in *merged
mode* (asks on open), then extract `DBFilesClient/*.dbc` to `dbc-src/`. Merged mode applies the
client's own override rules, so you get the DBCs the client will actually use.

Then:

- copy `dbc-src/` → `dbc-server/` and point `DataDir` at a folder whose `dbc/` is `dbc-server/`;
- copy `dbc-src/` → `dbc-client/`, apply your row edits, and pack `dbc-client/` into the patch;
- after any change: `tools/dbc_tool.py verify-pair dbc-client/X.dbc dbc-server/X.dbc` must pass
  for every table you touched, and the client cache must be deleted.

ID-range policy (put it in `manifest.json`, enforce it in CI):

| Namespace | Range | Why |
|---|---|---|
| Items | `800000–899999` | above every stock 3.3.5a item (max ~78k) and above common custom ranges |
| Spells | `800000–899999` | stock max ~90k; many servers use 100000+ for serverside spells, so avoid that band |
| Creature entries | `900000–999999` | stock max ~300k |
| Game object entries | `900000–999999` | ditto |
| Creature display IDs | `150000+` | stock ~60k; only if you add `CreatureDisplayInfo` rows |
| Item display IDs | `150000+` | stock ~117k |
| Everything else (`ItemExtendedCost`, `ItemSet`, …) | `800000+` | keep one obvious "custom" band |

Before allocating, prove the range is free:

```bash
python3 tools/dbc_tool.py free dbc-client/Item.dbc  --from 800000 --to 800999
python3 tools/dbc_tool.py free dbc-client/Spell.dbc --from 800000 --to 800999
```

---

## 1. Item with a new ID (the full three-place flow)

Goal: a new weapon entry that is a real weapon to both the client and the server.

**1.1 Server DB row** (`item_template`). Only the fields that matter for a first test:
`entry, class, subclass, name, displayid, Quality, BuyPrice, SellPrice, InventoryType,
ItemLevel, RequiredLevel, dmg_min1, dmg_max1, dmg_type1, delay, bonding, Material, sheath`.

```sql
DELETE FROM item_template WHERE entry = 800000;
INSERT INTO item_template
  (entry, class, subclass, SoundOverrideSubclass, name, displayid, Quality, Flags, FlagsExtra,
   BuyCount, BuyPrice, SellPrice, InventoryType, AllowableClass, AllowableRace, ItemLevel,
   RequiredLevel, MaxCount, stackable, bonding, description, Material, sheath,
   dmg_min1, dmg_max1, dmg_type1, delay, StatsCount)
VALUES
  (800000, 2, 7, -1, 'Blade of the Test Realm', 41179, 4, 0, 0,
   1, 0, 12500, 17, -1, -1, 200,
   80, 1, 1, 1, 'A custom blade that reuses a stock display.', 1, 3,
   300, 450, 0, 2600, 0);
```

`displayid = 41179` is an **existing** `ItemDisplayInfo` row (any stock weapon display works —
substitute one whose model you like); reusing it means the appearance and icon are already correct
in every client.

**1.2 Server DBC.** Either copy the row into `dbc-server/Item.dbc`, or (AzerothCore only) insert
into `item_dbc`:

```sql
DELETE FROM item_dbc WHERE ID = 800000;
INSERT INTO item_dbc (ID, ClassID, SubclassID, Sound_Override_Subclassid, Material,
                      DisplayInfoID, InventoryType, SheatheType)
VALUES (800000, 2, 7, -1, 1, 41179, 17, 3);
```

If you use `item_dbc`, the server does not need its `Item.dbc` edited — but the **client still
does**. The client is the part that decides a two-hander is a two-hander.

**1.3 Client DBC.** In WDBX Editor, open `dbc-client/Item.dbc`, add one row at the end:

| 0 ID | 1 ClassID | 2 SubclassID | 3 SoundOverrideSubclass | 4 Material | 5 DisplayInfoID | 6 InventoryType | 7 SheatheType |
|---|---|---|---|---|---|---|---|
| 800000 | 2 | 7 | -1 | 1 | 41179 | 17 | 3 |

That is the **entire** 8-field row. `ClassID 2` = weapon, `SubclassID 7` = two-handed sword,
`InventoryType 17` = `INVTYPE_2HWEAPON` (see the table in
[TrinityCore item_template docs](https://trinitycore.info/database/335/world/item_template)),
`SheatheType 3` = on back. Save, then verify:

```bash
python3 tools/dbc_tool.py info dbc-client/Item.dbc
python3 tools/dbc_tool.py refs dbc-client/Item.dbc --field 5 --must-exist dbc-client/ItemDisplayInfo.dbc
```

**1.4 Patch + restart.** Copy `dbc-client/Item.dbc` to `patch/DBFilesClient/Item.dbc`, rebuild
`Data/patch-4.MPQ` (see [04](04-patching-toolchain-and-release.md)), replace
`dbc-server/Item.dbc`, restart the world server, **delete the client `Cache/` folder**, log in,
`.additem 800000`.

**Reuse-only alternative (no patch at all):** pick an existing `entry` whose class/subclass/
inventory type you like, `UPDATE item_template` its name/stats/price/quality, and change item
IDs in drops/vendors/quests. You lose "new ID" bookkeeping and you must accept that the icon and
model are fixed — but zero client risk, zero distribution.

---

## 2. New item look without new art

Add a row to `ItemDisplayInfo.dbc` (25 fields in 3.3.5a;
[WoWDBDefs](https://github.com/wowdev/WoWDBDefs/blob/master/definitions/ItemDisplayInfo.dbd)):

| idx | field | what to put |
|---|---|---|
| 0 | ID | new ID, e.g. `150000` |
| 1–2 | `ModelName[2]` | existing M2 path, e.g. `Item\ObjectComponents\Weapon\Sword_2H_Claymore_A_01.mdx` |
| 3–4 | `ModelTexture[2]` | existing BLP path for the texture variant |
| 5–6 | `InventoryIcon[2]` | existing icon name, e.g. `INV_Sword_04` (client resolves `Interface\Icons\<name>`) |
| 7–9 | `GeosetGroup[3]` | copy from a similar item (sleeves/skirt/boots) |
| 10 | `Flags` | copy |
| 11 | `SpellVisualID` | 0, or an existing visual for glowing weapons |
| 12 | `GroupSoundIndex` | copy |
| 13–14 | `HelmetGeosetVisID[2]` | copy (hides hair under helms) |
| 15–22 | `Texture[8]` | copy / variant BLPs |
| 23 | `ItemVisual` | copy (usually 0) |
| 24 | `ParticleColorID` | copy (usually 0) |

Then `item_template.displayid = 150000`. Copy the *whole row* of a similar item and edit only
the paths — that is how recolours, "new" tier sets, and faction-themed gear are made with no 3D
work. Verify every path exists by checking the client's MPQs (MPQEditor → look up the path), and
that the display ID is unique.

---

## 3. A visible spell (clone-and-retune)

The reliable method is cloning an existing spell with the same *shape* of behaviour.

1. **Pick a template.** e.g. a direct-damage single-target nuke with a projectile; or a
   self-buff aura. Note its ID.
2. **Clone the row** in `Spell.dbc` to a free ID (`800000`). In WDBX: select row → duplicate →
   change ID field (0).
3. **Rename.** `SpellName` (field 136 is the enUS name), `Rank` (153), and optionally the
   `Description` slot (170 for enUS). Use the client's own `$s1`/`$d`/`$m1` substitution tokens
   if you want numbers in the tooltip — but only ones the spell actually has.
4. **Icon.** `SpellIconID` (133) must be an existing `SpellIcon.dbc` row. Check with
   `dbc_tool.py refs Spell.dbc --field 133 --must-exist SpellIcon.dbc`.
5. **Visual.** `SpellVisual[0]` (131) = existing `SpellVisual.dbc` row (copy one from a similar
   spell); `SpellVisual[1]` (132) is the second visual. `0` means "no visual" — the spell will
   work and appear to do nothing.
6. **Numbers.** `EffectBasePoints` (80–82) is **value − 1** (classic WoW quirk): to deal 100
   damage, store 99. `EffectDieSides` (74–76) adds `1..n`. `EffectMiscValue` (110–112) is the
   effect's parameter (school for damage-taken effects, creature for mounts, enchantment for
   enchants…). `Effect` (71–73) is the effect type, `EffectApplyAuraName` (95–97) the aura type
   for `SPELL_EFFECT_APPLY_AURA` (6).
7. **Satellites.** `CastingTimeIndex` (28) → `SpellCastTimes.dbc`; `DurationIndex` (40) →
   `SpellDuration.dbc`; `rangeIndex` (46) → `SpellRange.dbc`; `EffectRadiusIndex` (92–94) →
   `SpellRadius.dbc`; `Category`/`CategoryRecoveryTime` → `SpellCategories.dbc` and the cooldown
   fields. Reuse existing rows for all of these (e.g. "instant", "30 sec", "40 yd").
8. **Make it obtainable.** Simplest: `character_spell` / `.learn 800000`; then
   `trainer_spell` (trainer), `item_template.spellid_1` with `spelltrigger_1=0` (Use:) — that
   requires the item to exist (recipe 1), or a quest reward/`quest_template.RewardSpell`.
9. **Test with a clean cache**, and remember the client must have the same `Spell.dbc` row —
   copy the final file to `dbc-server/` too.

Gotchas that produce "the spell does nothing": passive/hidden attributes set on the cloned
template; `SpellVisualID = 0`; `DurationIndex` 0 on an aura spell; `Targets`/`ImplicitTargetA`
not matching the effect; `SchoolMask` 0; `ProcFlags` left from the template; reagent/cost fields
inherited from a spell you cannot pay for.

---

## 4. A cosmetic aura / wings / glow (no new art)

Chain (all existing rows, only new IDs):

```
new Spell row
  Effect (71)                = 6   (APPLY_AURA)
  EffectApplyAuraName (95)   = 4   (DUMMY)
  EffectImplicitTargetA (86) = 1   (caster)
  DurationIndex (40)         = -1  (permanent)
  Attributes (4)            |= death-persist bit
  SpellVisual[0] (131)       = <new SpellVisual row>

new SpellVisual row
  PrecastKit  -> 0
  CastingKit  -> 0
  ImpactKit   -> 0
  StateKit    -> <new SpellVisualKit row>     # "state" kit = the persistent look
  ...

new SpellVisualKit row
  one of the kit "model" slots -> <new SpellVisualEffectName row>
  (optionally: attach point via SpellVisualKitModelAttach.dbc)

new SpellVisualEffectName row
  Model path = existing wing/aura M2 (e.g. an existing back-attachment spell model)
  Scale / params copied from a similar effect
```

Two practical shortcuts: (a) copy an existing cosmetic spell whole — many already exist
(Shadowform, Ice Barrier, holiday auras) and only need a recolour/re-attach; (b) Stoneharry's
Spell Editor can copy a whole visual kit from an existing spell onto a new spell and re-link it
automatically ([README](https://github.com/stoneharry/WoW-Spell-Editor)).

---

## 5. A mount

Three pieces:

1. **Server:** `creature_template` row (e.g. `900100`) with `modelid` = an existing mount display
   ID and `faction`/`npcflag`/`speed_walk`/`speed_run` set sanely. No patch.
2. **Client+server:** `Spell.dbc` row with `Effect` (71) = `6` (APPLY_AURA),
   `EffectApplyAuraName` (95) = `78` (`SPELL_AURA_MOUNTED`),
   `EffectMiscValue` (110) = **900100** (creature entry, confirmed by
   [AzerothCore's aura reference](https://www.azerothcore.org/wiki/spell-aura-reference)),
   `DurationIndex` (40) = an existing "until cancelled" row, `CastingTimeIndex` = instant,
   `SpellIconID` (133) = an existing mount icon, `SpellVisual` = existing summon visual.
3. **Optional item:** `Item.dbc` + `item_template` with `spellid_1 = <mount spell>`,
   `spelltrigger_1 = 0` (Use), `bonding = 1`, class 15 (`Misc`) / subclass 5 (`Mount`) for a
   non-equippable mount item, or an armour class if you want it in a slot.

Mount speed comes from the riding skill and the mount aura handling, not from the creature's
`speed_run`; `mod-mount-autoselect` shows how to layer zone/skill-based speed with additional
invisible `spell_dbc` auras ([repo](https://github.com/cthu1hoo/mod-mount-autoselect)).

---

## 6. An invisible "serverside" spell (no client patch)

Use for internal mechanics only: hidden stat auras, trigger markers, cooldown carriers, scaling
helpers.

AzerothCore column names (verified against `data/sql/base/db_world/spell_dbc.sql`): the effect
columns are `Effect_1..3`, `EffectBasePoints_1..3`, `EffectAura_1..3` (**not**
`EffectApplyAuraName`), `EffectMiscValue_1..3`, `ImplicitTargetA_1..3`, `EffectRadiusIndex_1..3`,
plus `SpellVisualID_1..2`, `SpellIconID`, `SchoolMask`, `DurationIndex`, `CastingTimeIndex`,
`Name_Lang_enUS`, `Name_Lang_Mask`, `Description_Lang_enUS`.

```sql
-- Minimal hidden aura. Columns not listed get their table defaults (0), NOT the values
-- of any DBC row — so spell out everything the core reads.
DELETE FROM spell_dbc WHERE ID = 900001;
INSERT INTO spell_dbc
  (ID, Attributes, Targets, CastingTimeIndex, DurationIndex, SchoolMask,
   Effect_1, EffectBasePoints_1, EffectAura_1, EffectMiscValue_1, Name_Lang_enUS, Name_Lang_Mask)
VALUES
  (900001, 0x00000040,   -- a "hidden client-side" attribute bit; confirm the bit in SharedDefines.h
   0, 1,               -- Targets 0, CastingTimeIndex 1 = instant
   21,                 -- DurationIndex: an existing SpellDuration.dbc row (look up which)
   1,                  -- physical
   6,                  -- SPELL_EFFECT_APPLY_AURA
   49,                 -- base points = value - 1  => 50
   137,                -- apply-aura type: pick from spell-aura-reference, do not guess
   0,
   'Internal Marker', 14);
```

Two rules:

- The client will not know this spell. Do not put it on a trainer, an item, or a visible aura
  slot; if it must show, it has to go into `Spell.dbc` instead.
- Prefer driving effects from a script (`Player::CastCustomSpell` / `AddAura` with computed
  values) over encoding rules in DBC flags. Scripts are testable; flags are archaeology.

AzerothCore's own convention keeps serverside spells in a high band (100100+ historically) and
its own bug history shows why you should not reuse IDs that the client already defines: overlay
rows that collide with client spell IDs silently change client-defined spells
([CoA PR #1498](https://github.com/jealous-sound/azerothcore-wotlk-coa/pull/1498)).

---

## 7. Item set

`ItemSet.dbc` row: ID `800000`, `name[0]` = "Test Realm Armour", item slots (fields 18–34 in
3.3.5a; the core reads the first 10) = your item IDs, 8 bonus spells (35–42) with their piece
thresholds (43–50), required skill id/rank (51–52) = 0. Then each item gets
`item_template.itemset = 800000`.

Set bonus spells can be `spell_dbc`-only if they are invisible stat auras — the client simply
will not show the "3 pieces" block in the tooltip. If you want the tooltip block, the set must
exist client-side (that is the whole reason for the DBC row) **and** the bonus spells should be
in `Spell.dbc` so their names render.

---

## 8. Gem, enchant, socket bonus (brief)

- Gem: `GemProperties.dbc` row (colour mask, `SpellItemEnchantment` ID) + the
  `SpellItemEnchantment.dbc` row (stat type/value) + the gem item (`Item.dbc` +
  `item_template` with `GemProperties` set).
- Enchant: `SpellItemEnchantment.dbc` row + a spell using `SPELL_EFFECT_ENCHANT_ITEM` (53) or
  `ENCHANT_ITEM_TEMPORARY` (54) with `EffectMiscValue` = enchantment ID; sell it as "Enchant
  Scroll" item or via the enchanting trainer.
- Socket bonus: `item_template.socketBonus` = `SpellItemEnchantment` ID, gated by
  `SpellItemEnchantmentCondition.dbc` rows if you want "only red gems" style rules.

All rows; icons are existing icon names; effects are existing stat types.

---

## 9. Vendor with a custom currency

1. Token item: any item, e.g. `Item.dbc` + `item_template` "Emblem of Testing" (`stackable 200`).
2. `ItemExtendedCost.dbc` row: ID `800000`, then `reqitem[0..4]` (fields 4–8) = token item IDs,
   `reqitemcount[0..4]` (fields 9–13) = amounts, honor/arena fields 1–3 = 0.
3. `npc_vendor` row: `entry`, `item`, `ExtendedCost = 800000`, no money cost.

Without the client `ItemExtendedCost` row the vendor cannot draw the price and the purchase
fails. Ship it in the same patch as the token item.

---

## 10. A custom dungeon wing or boss in an existing map (no patch)

1. Pick an unused interior/area on an existing map (a closed-off zone, an unreachable cave, a
   copy of an unused map ID that already has terrain). Confirm no stock spawns conflict.
2. `instance_template`: map, parent, `script`, `allowMount`.
3. `creature` spawns inside; `gameobject` for doors/chests; `areatrigger_teleport` + a portal GO
   or a gossip teleport to enter/exit.
4. `instance_encounters` (AzerothCore) + a C++/Lua boss script, or `smart_scripts` for a
   simpler encounter. `creature_loot_template`, `reference_loot_template` for rewards.
5. `access_requirement` (level/item/quest/achievement) to gate entry.

Everything here is server data; the client renders the existing terrain and models. This is the
standard, sustainable way to add "custom raids" on 3.3.5a.

---

## 11. Custom UI or a custom system (addon + server)

Server side: a C++ module hooking `ScriptMgr` (AzerothCore modules) or Lua with `mod-eluna` /
`mod-ale`. Client side: a plain addon.

Transport options, cheapest first:

1. **Addon messages** — a self-whisper carrying a tab-separated payload, ≤255 bytes, the server
   replying with the same packet shape. No prefix registration exists in 3.3.5a
   (`RegisterAddonMessagePrefix` is 5.x), so the server claims your prefix itself.
2. **Existing packets repurposed** — e.g. send a gossip menu with computed options, or use
   `SMSG_UI_*` events the addon already listens for.
3. **Custom opcode** — requires a client patch/DLL. Only worth it when the payload is large or
   latency-critical.

Then keep the authority server-side: the addon may *display* "you have 4 tokens", but the
purchase must be validated server-side on the button click (`SendAddonMessage` → module →
re-check → act → reply).

**The whole implementation is worked out in [`examples/vault-terminal`](../examples/vault-terminal/README.md)** —
framing and rules in `PROTOCOL.md`, the 3.3.5a addon, a reference module on
`PlayerScript::OnPlayerBeforeSendChatMessage`, the SQL, and a Python model of the protocol that the
tests use to check all three against each other. Read that instead of re-deriving it here.

---

## 12. Shipping the patch (short version)

```bash
# 0. one-command gate (structure, duplicates, client/server parity, dangling refs)
tools/preflight.sh dbc-client/ dbc-server/ || exit 1

# 1. validate everything before packing
python3 tools/dbc_tool.py info  dbc-client/Item.dbc
python3 tools/dbc_tool.py refs  dbc-client/Item.dbc --field 5 --must-exist dbc-client/ItemDisplayInfo.dbc
python3 tools/dbc_tool.py refs  dbc-client/Spell.dbc --field 133 --must-exist dbc-client/SpellIcon.dbc
for f in dbc-client/*.dbc; do
  python3 tools/dbc_tool.py verify-pair "$f" "dbc-server/$(basename "$f")" || exit 1
done

# 2. stage
rm -rf patch/DBFilesClient && mkdir -p patch/DBFilesClient
cp dbc-client/Item.dbc dbc-client/Spell.dbc dbc-client/ItemDisplayInfo.dbc patch/DBFilesClient/

# 3. pack with MPQEditor (GUI) or StormLib-based CLI, format 1.0, no (attributes)
#    -> Data/patch-4.MPQ   (locale clients: Data/<locale>/patch-<locale>-4.MPQ)

# 4. publish patch + bump version in manifest.json; launcher deletes Cache/ on update
```

See [04](04-patching-toolchain-and-release.md) for the automation and release discipline.
