# 05 — Failure modes: symptom → cause → fix

Almost every problem in this space is one of four things: **a missing copy of the data**, **a
shadowed file**, **a stale cache**, or **a stale client**.

---

## Fast triage table

| Symptom | Most likely cause | First check |
|---|---|---|
| Item is a red `?` in bags/vendor | Client `Item.dbc` has no row (or your patch is shadowed, or cache) | Is the patch the winning archive for the player's locale? Is `Cache/` deleted? |
| Item equips, but abilities say "requires a melee weapon" | Client `Item.dbc` class/subclass/inventory type wrong (or absent) | Compare row against a real weapon row |
| Item can't be dragged to the action bar / can't be enchanted | Same as above — the client does not consider it a real item | same |
| Wrong icon or invisible model | `item_template.displayid` points at a `ItemDisplayInfo` row that does not exist, or `InventoryIcon` names a missing icon | `dbc_tool.py refs Item.dbc --field 5 --must-exist ItemDisplayInfo.dbc` |
| Server log: `Item (Entry: X) does not exist in item.dbc!` | Server's `DataDir/dbc/Item.dbc` (and `item_dbc`) lacks the row, or class/subclass mismatch with `DBC.EnforceItemAttributes=1` | Add to server DBC or `item_dbc`; or set the flag to 0 deliberately |
| Client crashes on login / character select | A patched DBC is structurally invalid: field count, string block, duplicate ID, or a row from another build | Run `dbc_tool.py info` on every patched table; bisect by reverting one table at a time |
| Client crashes when opening a specific panel | Satellite table missing rows for something the panel iterates (talents, sets, achievements, area names) | Revert the table that matches the panel; add complete rows |
| Spell trains, shows in book, does nothing | `SpellVisualID = 0` (no effect) or effect/aura/target fields wrong, or passives/hidden attributes inherited from the template | Compare field-by-field with the spell you cloned |
| Spell casts but the client shows nothing in the aura bar | Client does not know the spell (serverside `spell_dbc` only) | Move it into `Spell.dbc` |
| Spell works for you, not for players (or vice versa) | Client `Spell.dbc` ≠ server `Spell.dbc` | `dbc_tool.py verify-pair` |
| Tooltip numbers are wrong/old | Server overrides via `spell_dbc`/`item_template` while the client shows DBC values | Decide which side owns the number; align them |
| Trainer doesn't list the spell | Spell not in client `Spell.dbc`; missing `trainer_spell`; skill/`SkillLineAbility` mismatch; `ReqLevel`/`ReqSkillRank` unmet | `.lookup spell`, then check trainer tables |
| Mount item does nothing / no mount appears | `SPELL_AURA_MOUNTED` misc value must be a **creature entry**, and that creature must have a valid `modelid` | Check both rows |
| Vendor shows no price / purchase fails | `ItemExtendedCost.dbc` row missing client-side | Ship it in the same patch |
| Set bonus not applying | `ItemSet.dbc` row missing/threshold columns wrong, or `item_template.itemset` not set | Check the row and each item |
| Creature/GO is invisible | `modelid`/`displayId` not in the client's display table | Pick an existing display ID |
| Quest reward shows a red `?` | Reward item ID not in client `Item.dbc` | Either add the item properly or use an existing item |
| "It worked yesterday" | `Cache/` or a second patch file shadowing yours | List `Data/` and `Data/<locale>/` for foreign `patch-*` |
| Server refuses to start: `has N field(s) (expected M)` | Server DBC from a different build than the core expects | Re-extract from the matching client |

---

## 1. The red question mark (the genre's mascot)

Chain of causation, in order of likelihood:

1. **Client has no `Item.dbc` row for the ID.** This is not cosmetic: the item cannot be
   equipped properly, cannot go on an action bar, cannot be enchanted, and is not a weapon for
   spell requirements ([TrinityCore thread](https://talk.trinitycore.org/t/guide-the-truth-about-custom-items-and-the-big-red-question-mark/21524)).
2. **The row exists, but in a patch the client does not load.** Classic on non-English clients:
   `Data/patch-4.MPQ` loses to `Data/ruRU/patch-ruRU-3.MPQ`.
3. **The client cached the item** before the patch (`Cache/ItemCache.wdb`).
4. **`DisplayInfoID` is wrong**, so even a valid row renders the default/`?` icon.

Diagnosis in 30 seconds: `.additem <id>`, then look at the tooltip. If the item has a *name* but
no icon, it is (1) or (4). If it has neither name nor icon, the server does not know it either.
If it works for the dev and not for a player, it is (2) or (3).

There is also a **client-side bypass**: patching `Wow.exe` so it does not consult `Item.dbc` for
custom IDs exists for 3.3.5.12340 enUS
([Custom Item Fix](https://www.wowmodding.net/files/file/283-wow-335-patcher-custom-item-fix/)),
and requires `DBC.EnforceItemAttributes = 0`. It removes the row-patch step entirely, at the
cost of running a patched executable — decide deliberately.

## 2. "It's an item, but it's not a *weapon*"

The client has its own opinion about what an item is, from `Item.dbc` (`ClassID`, `SubclassID`,
`InventoryType`, `SheatheType`). If those disagree with `item_template`, the server thinks it is a
sword and the client thinks it is a grey blob: no swing animation, no weapon-requirement
satisfied, no action bar.

`DBC.EnforceItemAttributes` (AzerothCore, default `1`) exists precisely to catch this: it logs and
refuses DB values that contradict the DBC. Turning it to `0` silences the complaint; it does not
change the client's mind. Fix the `Item.dbc` row.

## 3. Structurally invalid DBC → client crash

The client is far less forgiving than the server. Causes, in order:

- **Field count mismatch** — a CSV import with an extra/missing column, or a row from another
  build. `dbc_tool.py info` reports `recordSize != fieldCount*4` and truncation.
- **String block corruption** — hand-edited names, offsets past the block, missing NUL
  terminators. Re-edit in WDBX rather than patching bytes.
- **Duplicate IDs** — `dbc_tool.py info` flags duplicates in field 0.
- **Unsorted rows** in tables the client binary-searches (tolerant in most cases; not worth the
  risk).
- **Non-enUS locale slots** pointing outside the string block: the client ignores them, but some
  cores assert on them. A real core had to be patched to tolerate 7,029 such strings
  ([CoA PR #1484](https://github.com/jealous-sound/azerothcore-wotlk-coa/pull/1484)).

Crash-bisect procedure: revert to the stock table, confirm the crash is gone, add your rows back
in halves. Keep `dbc-src/` pristine so this is a one-minute operation.

## 4. Server won't start / silently drops content

- `dbc exists, and has 13 field(s) (expected 12). Extracted file might be from wrong client
  version or a database-update has been forgotten.` — the server's DBC set does not match the
  core's expected layout ([AzerothCore: common errors](https://www.azerothcore.org/wiki/common-errors)).
- **Silent drops** are worse: invalid display IDs, missing extended costs, missing limit
  categories. The core logs a line per problem, but startup scrolls past it. Grep the log:
  `Invalid displayId`, `does not exist in item.dbc`, `Table 'X' is empty`, `has N field(s)`.

## 5. "Works on my machine"

The complete list of ways two clients differ while the server is identical:

1. different `patch-*.MPQ` set (old version, extra mod, renamed patch);
2. different locale folder routing;
3. cached WDB data;
4. different addons (some addons cache item/spell data in `SavedVariables`);
5. Windows filesystem case sensitivity vs. a path spelled with the wrong case (matters when you
   build patches on Linux).

The launcher + checksum + addon version handshake in
[04 §7](04-patching-toolchain-and-release.md#7-launcher--verification) exists to collapse this
list to "you are on version X or you are not playing".

## 6. In-game debugging toolkit

| Need | Command / tool |
|---|---|
| Add an item to test a row | `.additem <id> [count]` |
| Learn a spell | `.learn <id>` (server must know it) |
| Find an ID | `.lookup item <name>`, `.lookup spell <name>`, `.lookup creature <name>` |
| Inspect what the server thinks | `.server info`, `.debug ...`, core logs at `Logger.*` debug level |
| See packet-level behaviour | core packet logs; client-side sniffer if you must |
| Inspect a model/texture path exists | MPQEditor, search the path in merged mode |
| Preview a creature display | WoW Model Viewer; or spawn a critter with that display |
| Verify a DBC quickly | `python3 tools/dbc_tool.py info|refs|verify-pair` |

## 7. Anti-patterns that produce chronic bugs

- **Editing the server's `dbc/` folder by hand as the primary workflow.** Regenerate it from
  source instead; hand edits get lost on the next extraction.
- **Adding rows to `Spell.dbc` from a retro-port pack without checking the field count.** The
  pack is usually from another build.
- **Using `spell_dbc` to change numbers players can read.** Tooltips then lie forever.
- **Reusing custom IDs after a redesign.** Old items in banks/mail become new items.
- **Shipping multiple patch files.** One patch, one version, always.
- **Skipping the cache purge "because it's just a test".** Then debugging a ghost for an hour.
