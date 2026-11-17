# 02 — Feature integration matrix

For each feature type: **does it need a client patch?** What tables are involved, what the
minimum viable implementation is, and where it usually goes wrong. Recipes for the starred
items are in [03-recipes.md](03-recipes.md).

Legend: **No** = server DB/scripts only. **Rows** = client patch containing only DBC rows that
reference existing client assets. **Assets** = client patch containing new M2/BLP/WMO/maps.
**Code** = client binary/DLL work.

---

## Summary

| Feature | Client patch | Server side | New art needed |
|---|---|---|---|
| Creature / NPC (new stats, AI, spawns) | **No** | `creature_template`, `smart_scripts`, `creature`, `creature_addon` | no — reuse `modelid` |
| New creature *look* variant of existing model | **No** if you reuse a display ID; **Rows** (`CreatureDisplayInfo.dbc`) if you need a different texture-set/scale | same | no — points at existing M2 |
| Game object | **No** (existing display) | `gameobject_template`, `gameobject` | no |
| Quest, quest chain, quest text | **No** | `quest_template`, `quest_*`, `page_text`, `conditions` | no |
| Gossip / NPC text / vendor / trainer | **No** | `npc_text`, `gossip_menu*`, `npc_vendor`, `creature_trainer`, `trainer_spell` | no |
| Loot, conditions, events, phasing | **No** | `*_loot_template`, `conditions`, `game_event*`, `phase_*` | no |
| Instance/dungeon in an existing map | **No** | `instance_template`, spawns, `areatrigger_*`, `access_requirement` | no |
| **New item ID** | **Rows** — `Item.dbc` | `item_template` | no if display reused |
| **New item look** | **Rows** — `ItemDisplayInfo.dbc` | `item_template.displayid` | no — reuse `ModelName`/`ModelTexture`/`InventoryIcon` paths |
| Item set + set bonuses | **Rows** — `ItemSet.dbc` | `item_template.itemset`, set bonus spells | no |
| Gem / enchant | **Rows** — `GemProperties.dbc`, `SpellItemEnchantment.dbc` (+ `Spell.dbc`) | `item_template`, `spell_item_enchantment` overlay if needed | no |
| **Visible spell the player casts** | **Rows** — `Spell.dbc` + satellites | `Spell.dbc` copy, optional `spell_dbc` | no |
| Invisible/serverside mechanic spell | **No** | `spell_dbc` (+ script) | no |
| Spell visual / cosmetic aura (wings, glow, trail) | **Rows** — `SpellVisual*` chain (+ `Spell.dbc`) | copy of the same DBCs | no — reuse existing kits |
| **Mount** | **Rows** — `Spell.dbc` (mount aura) [+ `Item.dbc` if it comes from an item] | `creature_template` for the mount body | no — reuse creature display |
| Hunter pet / non-combat companion | **Rows** for a new summon spell; **No** for a new pet family member using an existing summon | `creature_template` | no |
| **Vendor currency (token, badge)** | **Rows** — `ItemExtendedCost.dbc` | `npc_vendor`, item as token | no |
| Title | **Rows** — `CharTitles.dbc` | `CharTitles` DB / achievement | no |
| Achievement | **Rows** — `Achievement.dbc`, `Achievement_Criteria.dbc` | `achievement_*` tables | no |
| Talent / glyph / spellbook layout | **Rows** — `Talent.dbc`, `TalentTab.dbc`, `GlyphProperties.dbc`, `SkillLineAbility.dbc` | trainer/learn logic | no |
| Reputation faction | **Rows** — `Faction.dbc` + `FactionTemplate.dbc` (name/icon) | `creature_onkill_reputation`, etc. | no |
| Area/zone name, subzone | **Rows** — `AreaTable.dbc` | `areatable_dbc` overlay, spawns | no |
| Taxi route | **Rows** — `TaxiPath.dbc`, `TaxiPathNode.dbc` | `taxi_path` overlay | no |
| Battleground / arena | **No** for mechanics; **Rows** (`BattlemasterList.dbc`, `Map.dbc`) if it must appear in the PvP UI as its own entry | `battleground_template`, `game_graveyard*` | no |
| **New continent/map** | **Assets** — ADT/WDT (+ `Map.dbc`, `AreaTable.dbc`) | `map_extractor`/`vmap`/`mmap` rerun | **yes** |
| New class / race / item class / profession | **Code + Rows**, hardcoded client paths | deep core work | usually yes |
| Custom UI / panels / mini-games | **No** — ship an addon | addon messages or custom opcodes | no |
| Custom account-wide systems (prestige, currencies, collections) | **No** | C++ module + addon | no |

---

## 1. Creatures and NPCs — the cheapest content in the game

**Client patch: none.** `creature_template` + `creature` (spawn) + `creature_addon` +
`creature_equip_template` + `creature_template_movement`, plus `smart_scripts` for behaviour.

The only client-visible input is `creature_template.modelid`, which must be an existing
`CreatureDisplayInfo.dbc` ID (thousands of them, ~59k in a full 3.3.5a set). Everything else —
level, faction, damage, loot, AI, gossip, vendor lists, trainer lists, quest givers — is
server data sent to the client in packets.

Reuse patterns that produce "new" monsters without a single byte of art:

- Recolour/reskin by picking a different display ID of the same skeleton.
- Rescale via the display's `CreatureModelScale`, or a `creature_template` scale field if your
  core has one **(verify per core)**; auras with `SPELL_AURA_MOD_SCALE` (239) also work.
- Gear it up with `creature_equip_template`, whose item entries are `Item.dbc` rows — including
  the "texture-only" item rows Blizzard used purely for NPC equipment.
- Add auras/visuals with `creature_addon.auras` (server-side, client renders any known spell
  visual).
- Give it a mount, a pet, a vehicle seat, a summon, or an `npcflag` behaviour set.

Traps: unknown `modelid` → invisible creature (and the core logs a spawn error); display IDs
that exist on retail but not in the 3.3.5a client; `scale` fields on some cores are ignored for
players' mounts.

## 2. Game objects

Same story. `gameobject_template` + `gameobject` + `gameobject_addon`. `displayId` must exist in
`GameObjectDisplayInfo.dbc` (≈117k in a full set). `data0..data23` are type-specific (chest
loot, door, button, quest giver, spell caster, trap, transport, etc.); getting the `type` right
matters more than the model. **No patch.**

## 3. Quests — fully server-driven, including all text

**Client patch: none.** Titles, objectives, details and reward text all travel in
`SMSG_QUEST_QUERY_RESPONSE`, sourced from `quest_template`, `quest_objectives`/
`quest_template_addon` (schema varies by core version), `page_text`, `quest_poi`, `quest_offer_reward`.

You can build a complete 3.3.5a-quality questline, with custom lore text, on top of existing
NPCs, items, areas and spells — no patch. The only things that can leak client data are rewards
that *are* client content (a new item ID, a new spell, a new title).

Traps: `QuestFlags`/`SpecialFlags` mistakes make the quest auto-complete, invisible, or
un-turn-in-able; `RequiredNpcOrGo` referencing a non-existent spawn; `RewardItemId` pointing at
an item not in `Item.dbc` (red `?` in the reward pane); `LogTitle` over ~200 chars silently
truncated on some UI paths.

## 4. Gossip, NPC text, vendors, trainers

**No patch** — with one asterisk: `trainer_spell` can only teach spells the client knows (see §9).
Vendors can sell any item the client knows. Gossip menus (`gossip_menu`, `gossip_menu_option`,
`npc_text`, `npc_text_locale`), conditions (`conditions` with `ConditionTypeOrReference`), and
`SMSG_NPC_TEXT_UPDATE`/`SMSG_GOSSIP_MESSAGE` are all server-authored.

Custom "conversation trees", lore books, and multi-page interactive NPCs are essentially free —
this is where most custom-server narrative actually lives.

## 5. Items — the classic case, and where the red `?` comes from

An item exists in **three** places ([TrinityCore: the truth about custom items](https://talk.trinitycore.org/t/guide-the-truth-about-custom-items-and-the-big-red-question-mark/21524)):

| Place | Table | Consequence if missing |
|---|---|---|
| Server DB | `item_template` | item does not exist |
| Server DBC | `Item.dbc` in `DataDir/dbc` (or `item_dbc` overlay) | core logs "Item (Entry: X) does not exist in item.dbc!" and may reject/misbehave; with `DBC.EnforceItemAttributes=1` mismatches are errors |
| Client DBC | `Item.dbc` in the MPQ patch | **red question mark**, cannot equip, cannot drag to action bar, cannot be enchanted, weapons are not weapons |

`Item.dbc` in 3.3.5a has **exactly 8 fields** — `ID, ClassID, SubclassID,
SoundOverrideSubclassID, Material, DisplayInfoID, InventoryType, SheatheType` (AzerothCore
`DBCfmt.h`: `Itemfmt[] = "niiiiiii"`, `DBCStructure.h::ItemEntry`). Many old forum guides list a
"Quality" column — **they are wrong**; quality lives only in `item_template`. Copying such a
CSV into a DBC is a guaranteed field-count crash.

The icon is *not* in `Item.dbc`; it comes from `ItemDisplayInfo.dbc` (field `InventoryIcon`), so
`item_template.displayid` selects appearance **and** icon together
([TrinityCore: item_template](https://trinitycore.info/database/335/world/item_template)).

Reuse-only strategy (recommended): **do not create new item IDs unless you must.** Reusing an
existing `entry` and rewriting `item_template` gives you new stats, new names, new prices, new
sockets, new set membership — with zero client risk. `DBC.EnforceItemAttributes = 0` lets you
deviate from the client's class/subclass/slot, at the cost of the client's own opinion
(equip rules, weapon requirements, animation).

## 6. Item appearance (new look without new art)

**Rows**: add an `ItemDisplayInfo.dbc` row whose `ModelName`/`ModelTexture` point at **existing**
M2/BLP paths, and whose `InventoryIcon` points at an existing icon name. Then set
`item_template.displayid` to that new ID. 3.3.5 layout (from WoWDBDefs, build range
3.0.1.8303–3.3.5.12340):

```
ID, ModelName[2], ModelTexture[2], InventoryIcon[2], GeosetGroup[3], Flags,
SpellVisualID, GroupSoundIndex, HelmetGeosetVisID[2], Texture[8], ItemVisual, ParticleColorID
```

`GeosetGroup` decides sleeve/robe/boot variants; `HelmetGeosetVis` controls hair/ears hidden by
helmets. Copy a row of a *similar* item and change only the model/texture/icon strings — that is
how recolours and "new" armour sets are made without touching a 3D program.

Transmog-style servers do not need this at all: they send a different `displayId` for an
equipped item at runtime (that is what `mod-transmog` and friends do), which works because the
client can render any display ID it already knows.

## 7. Item sets

`ItemSet.dbc` holds name, up to 17 item IDs, up to 8 set-bonus spell IDs + thresholds, and a
required skill. `item_template.itemset` points into it. **Client patch required if the client
must show the set block in the tooltip** — the documented note is blunt: *"You can not make up
new item sets. Item sets are defined in ItemSet.dbc"*
([TrinityCore: item_template](https://trinitycore.info/database/335/world/item_template)).

Server-side, the bonuses themselves are spells (which may be `spell_dbc`-only, i.e. invisible —
the client just won't show the tooltip block).

## 8. Gems, enchants, sockets

- Socket colours/count and `socketBonus` are `item_template` + `SpellItemEnchantment.dbc`.
- A new gem needs `GemProperties.dbc` + `SpellItemEnchantment.dbc` rows (client), plus the gem
  item itself (`Item.dbc`).
- A new enchant needs `SpellItemEnchantment.dbc` (client) and a spell that applies it
  (`SPELL_EFFECT_ENCHANT_ITEM` = 53, `ENCHANT_ITEM_TEMPORARY` = 54; misc value A = enchantment ID).
- Socket bonuses are enchantments too, gated by `SpellItemEnchantmentCondition.dbc`.

All "rows", no art: the gem icon is an existing icon name; the enchant effect is an existing
stat type + amount.

## 9. Spells — visible vs. invisible

Split every spell you want into one of two buckets:

**Invisible / serverside** (hidden auras, trigger spells, internal cooldowns, scaling helpers,
"marker" spells a script casts): **no client patch**. Add a row to `spell_dbc`
([AzerothCore wiki](https://github.com/azerothcore/wiki/blob/master/docs/spell_dbc.md)) with a
free ID, or better, reuse an existing dummy spell and drive it from a script. The client will
not show an icon, name or tooltip — because it has never heard of the spell. That is fine when
nobody is supposed to see it. It is *not* fine for anything a player must click.

**Visible** (castable, in the spellbook, on an item, on a trainer, an aura players read):
**client patch.** `Spell.dbc` row plus whatever satellites the row points at:
`SpellIcon.dbc` (icon), `SpellCastTimes.dbc`, `SpellDuration.dbc`, `SpellRange.dbc`,
`SpellRadius.dbc`, `SpellCategories.dbc`, `SpellVisual.dbc`, `SpellDescriptionVariables.dbc`.
3.3.5a `Spell.dbc` has 234 fields and exactly **three** effect/aura slots; `Name_Lang_*` and
`Description_Lang_*` are 16 locale string slots (only the first—enUS—is normally used).

Practical construction method: **clone an existing spell that already behaves the way you want**
(same effect + aura + targeting), give it a new ID, then change name, icon, visual, numbers and
cost. Editing effect semantics from scratch is where spells break; cloning keeps attribute flags
consistent. Stoneharry's Spell Editor builds its "new spell from existing record" and
visual-kit copy features around exactly this.

Traps: unknown `SpellIconID` (invisible icon), `SpellVisualID = 0` (spell works, no effect
appears), `DurationIndex`/`RangeIndex`/`CastTimeIndex` pointing at non-existent rows, a
`SchoolMask` that disagrees with the visual, attribute flags that make the spell unusable
(`SPELL_ATTR0_...` / passive / hidden), and the client's `Spell.dbc` disagreeing with the
server's (server casts fine, client refuses, or vice versa).

## 10. Spell visuals, cosmetic auras, wings, glows

**Rows**, no art. The chain is:

```
Spell.dbc.SpellVisualID  ->  SpellVisual.dbc  ->  SpellVisualKit.dbc
                                                 ->  SpellVisualEffectName.dbc (M2 path + scale)
                                                 ->  SpellVisualKitModelAttach.dbc (attachment point)
```

You can build a brand-new effect purely by *recombining existing kits, models and attachment
points* ([Grindi's wings recipe](https://www.wowmodding.net/topic/988-how-to-add-custom-wings-335/),
[Spell Editor's kit copy/paste](https://github.com/stoneharry/WoW-Spell-Editor)). Typical result:
a permanent "wings"/"aura" spell = `SPELL_EFFECT_APPLY_AURA` (6) + `SPELL_AURA_DUMMY` (4) with a
`SpellVisual` that attaches an existing wing M2 to an existing attachment point, plus
`Attributes` for death-persistence and `DurationIndex` = -1.

## 11. Mounts

Mount = a spell with `SPELL_EFFECT_APPLY_AURA` + `SPELL_AURA_MOUNTED` (78) whose **EffectMiscValueA
is a creature entry** ([AzerothCore spell aura reference](https://www.azerothcore.org/wiki/spell-aura-reference)),
plus a `creature_template` row whose `modelid` is the existing mount display you want. Optionally
wrap it in an item (`Item.dbc` + `item_template`) and a trainer/quest/reputation gate.

So a "new mount" is: server creature row (no patch) + client `Spell.dbc` row (patch) [+ `Item.dbc`
if itemised]. Speed is governed by the aura/`SPELL_AURA_MOUNTED` handler and riding skill, not by
the creature. `mod-mount-autoselect` is a good real example of shipping a handful of `Spells.csv`
rows into `Spell.dbc` alongside a module ([repo](https://github.com/cthu1hoo/mod-mount-autoselect)).

## 12. Vendors and custom currencies

Token currencies are just items. To make a vendor *show* "10 × Token" instead of gold, you need
an `ItemExtendedCost.dbc` row (client + server): ID, then the required item IDs/counts and/or
honor/arena points ([guide](https://www.ownedcore.com/forums/world-of-warcraft/world-of-warcraft-emulator-servers/wow-emu-guides-tutorials/794318-how-make-custom-currency-3-3-5-a.html)).
Then `npc_vendor.ExtendedCost` references it. Without the client row the vendor UI cannot render
the cost.

## 13. Titles, achievements, talents, glyphs, reputations, areas, taxi

All of these are **client metadata**: the client will not display a title it has no
`CharTitles.dbc` row for, will not evaluate an achievement it has no `Achievement.dbc` row for,
will not show a talent in the talent pane without `Talent.dbc`, and will not name a zone without
`AreaTable.dbc`. The *logic* can be server-only; the *label* cannot.

They are all "rows" patches, and they are all fine to skip in a first release: server-side
progression without UI labels is a worse product than fewer features that look native.

## 14. Instances in existing maps

**No patch.** A "custom dungeon" can be built in an unused corner of an existing map (a cave, an
instanced area, a closed-off zone) using `instance_template` (entry → map), spawns, doors,
`areatrigger_*`, `access_requirement`, `instance_encounters`, and SmartAI/C++ boss scripts.
Difficulty variants use `SpawnMode`/`difficulty` columns. The client already knows the terrain,
so nothing needs to be patched. This is the standard trick for custom raids on 3.3.5a servers.

A **new** map is the expensive path: Noggit/adtcreator → ADT/WDT → `Map.dbc`/`AreaTable.dbc`
rows → re-extract server maps/vmaps/mmaps → client patch with the map files. Weeks, not days,
and every client patch becomes large.

## 15. Classes and races

Technically possible, practically a project of its own. The client hardcodes a lot
(`ChrClasses.dbc`, `ChrRaces.dbc`, `CharBaseInfo.dbc`, `CharStartOutfit.dbc`, `SkillLine.dbc`,
`SkillLineAbility.dbc`, `Talent*.dbc`, `Spell.dbc`, `playercreateinfo*`, plus hardcoded class
counts in the executable). Published custom-class efforts exist ("Creating a custom class in
3.3.5a" guides, Ascension's classless system, Duskhaven's custom classes), and all of them are
essentially *forked clients* plus heavily modified cores.

The reusable alternative that gives 80% of the feel for 5% of the cost: **keep the nine classes
and add "specs"** — new talents/passives/spells layered onto existing classes via trainer gates,
`skill_line_ability`, and server-side rules; plus cosmetic identity (title, glyphs, item sets,
spell visuals).

## 16. Custom systems and UI

The high-leverage pattern on 3.3.5a is: **authority in a server module, presentation in an
addon.**

- Server: C++ module (AzerothCore `mod-*` with `ScriptMgr` hooks) or Lua (`mod-eluna` /
  `mod-ale`; note ALE is **not** source-compatible with Eluna —
  [kadeshar/mod-eluna](https://github.com/kadeshar/mod-eluna)).
- Transport: addon messages — `SendAddonMessage("PREFIX", payload, "WHISPER", UnitName("player"))`,
  a self-whisper, ~255 bytes per message; chunk longer payloads. 3.3.5a has **no** prefix
  registration API (`RegisterAddonMessagePrefix` arrived in 5.x), so the server must claim your
  prefix in its own hook. Or a custom opcode, if you are willing to patch the client. Worked
  example: [`examples/vault-terminal`](../examples/vault-terminal/README.md).
- Client: a plain addon that renders panels, currency counters, collection browsers, etc.

Nothing here needs an MPQ patch, and addons can be shipped independently of the client patch.
This is how transmog UIs, "collection" browsers, prestige shops, and custom talent panels are
usually built on 3.3.5a.
