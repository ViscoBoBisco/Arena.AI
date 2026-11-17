# 10 — Novel alterations: using the surface as a language

[09](09-alteration-catalog.md) lists each lever and what it is *for*. This chapter is about what it can
also *do* — mechanisms used for something they were never designed for.

Every case here follows from six properties of the 3.3.5a pair. They are the reason "no client patch" is a
design space rather than a limitation:

| # | Escape hatch | Why it generalises |
|---|---|---|
| 1 | **Text is free** | Any string the server sends renders verbatim; the client ships the fonts, not the words |
| 2 | **The UI is data** | Gossip menus, the quest log, map pins and tooltips are graphs of rows, not compiled screens |
| 3 | **IDs are selectors** | Pointing at an existing ID selects existing client behaviour: cameras, rituals, devices, sounds |
| 4 | **Conditions are logic** | AND/OR/NOT over game state, evaluated per player, everywhere a row is read |
| 5 | **SmartAI is an event loop** | Timers, triggers and actions on any creature or object, no compilation |
| 6 | **The addon channel is a socket** | Arbitrary strings both ways between client Lua and server code |

The new levers live in the catalog under layer `novel` — 25 of them:

```bash
python3 tools/catalog.py query --layer novel
python3 tools/catalog.py show novel.keypad-input
```

---

## 1. Text delivery: the client is a content management system

Long-form writing is the cheapest custom content in the game, because none of it is client-side.

**Books (`novel.readable-books`).** `page_text` is `(ID, Text, NextPageID)`: a linked list of pages. Point an
item's `PageText` at the first page and right-clicking the item opens the stock reader, with page-turning
arrows and a parchment background. Chain twenty pages and you have a short story, a server manual, a
newspaper, an in-character contract — delivered by one row per page.

**Plaques and signs (`novel.plaque-text`).** GameObject type `9` (TEXT) reads `Data0` as a `page_text` id, so
any sign, banner, gravestone or book-on-a-pedestal display can become readable.

**Gossip as a document (`novel.gossip-app`).** `npc_text` holds a page of prose above the options; `ActionMenuID`
lets an option open *another* menu. That is a page graph, which means a readable document with a table of
contents and "back" links.

**The quest log as prose (`novel.quest-log-hud`).** Objectives text is per-quest and renders in the tracker;
quest descriptions render in the log pane. A "quest" that is actually a lore chapter reads naturally.

**Localisation (`novel.localized-content`).** `page_text_locale`, `creature_text_locale`,
`quest_template_locale`, `gossip_menu_option_locale` and `acore_string`/`module_string` exist precisely so
server-side text can follow the client's language.

Nothing above needs an MPQ. A player on a stock client sees professionally-rendered text you wrote this
afternoon — the one category where a single author can out-produce a whole art team.

---

## 2. Interface without an interface

The client has no scripting API for arbitrary UI... except the addon channel, and no *data-driven* UI
except the ones Blizzard made. Both are more general than they look.

### 2.1 Gossip as an application

An application needs four things: screens, navigation, input, and state. Gossip has all four.

- **Screens** — `npc_text` (prose) + `gossip_menu_option` rows (buttons).
- **Navigation** — `OptionType` decides behaviour, `ActionMenuID` navigates to another menu.
- **Input** — `BoxText` opens a text box, `BoxCoded` is the *code the player must type*, `BoxMoney` accepts an
  amount. This is the one most modders never use: a gossip option can ask the player for a password or a sum
  of money, in a stock dialog.
- **State** — `conditions` per row, so options appear, grey out or vanish as the player changes.

`novel.keypad-input` is the sharpest version: a vault whose code is a gossip option with `BoxCoded`. No addon,
no patch, and it *feels* like a custom interface because the player is typing into a box.

### 2.2 The quest log as a dashboard

Players always have the objective tracker on screen. Up to 25 quests, each with numbered objectives and
counters, persisted per character, plus map pins (`novel.map-annotations`, via `quest_poi`).

That is a heads-up display you did not have to build:

| You want to show | Model it as |
|---|---|
| "Season score: 12 / 50" | one quest, one objective with a count |
| "Daily checklist: 3 of 5" | one quest, five objectives |
| "Find the four vaults" | four quests, each with a `quest_poi` pin |
| "Your faction is winning" | a quest whose objective text the server rewrote (`quest_template` reload) |

The trick is naming: a quest titled *"Ashen Vault: 12 of 50 seals"* reads as a scoreboard, not as a chore.

### 2.3 The addon channel as a socket

`novel.addon-rpc` is the only way to get a genuinely new interface with no client patch. The shape:

| Direction | Mechanism | Constraint |
|---|---|---|
| server → UI | `SMSG_MESSAGECHAT` with `LANG_ADDON`, prefix-tagged | 255 characters per message |
| UI → server | `CMSG_MESSAGECHAT` with `LANG_ADDON` | same cap; rate limits apply |
| discovery | addon `.toc` + `AddonHandler` bookkeeping | needs `AddonChannel = 1` |

Because both directions exist, you can implement request **and** response: the addon asks "what is my rank",
the server answers with a compact record, the addon renders it. That is a client-server application inside a
chat channel — the same primitive Blizzard used for its own addon comms.

Two rules keep it honest: put all truth on the server (the addon is a view, never a source), and degrade
gracefully when answers stop (a panel that shows "—" beats one that shows stale numbers).

> **Worked example:** [`examples/vault-terminal/`](../examples/vault-terminal/README.md) implements this
> end to end — `PROTOCOL.md` for the wire format, a 3.3.5a addon for the panel, a reference module using
> `PlayerScript::OnPlayerBeforeSendChatMessage` and the core's own `ChatHandler::BuildChatPacket`
> send path, SQL for the NPC and the module's storage, and `harness/vt_model.py` — the protocol as an
> executable model the tests replay, fuzz and clock-shift. Nothing in it ships an MPQ.

**`novel.telemetry`** is the same channel pointed the other way: the client reports what only it can see
(zone, spec, group size, whether your addon is installed). Cheap instrumentation for tuning content, and the
only way to know which of your hooks players actually trigger.

---

## 3. Space, time and bodies

### 3.1 Two places at the same coordinates

Phasing (`novel.phasing-scenes`) is a bitmask on spawns plus conditions: the same spot holds different
creatures and objects for different players. You cannot move terrain, but you can replace everything standing
on it — which is most of what "the world changed" means. Combine with `disables` and you can retire a
version of an area without deleting it.

### 3.2 Cutscenes on demand

GameObject type `13` (CAMERA) takes `Data1` as a `CinematicSequences` id. The client already contains those
camera paths — the same ones the stock game plays on first visit to a capital. Clicking an object can therefore
play a real cinematic: an intro for your dungeon, a "memory" in a haunted house, a death scene for a boss
(`novel.camera-cutscene`).

### 3.3 Hazards, devices and gates without spells

The gameobject union is a small toolbox most content authors never open:

| Type | Fields that matter | What it buys |
|---|---|---|
| `6` TRAP | `spellId`, `type`, `cooldown`, `startDelay`, `stealthed` | proximity effects, landmines, silent tripwires (`novel.trap-devices`) |
| `12` AREADAMAGE | `radius`, `damageMin/Max`, `damageSchool` | standing somewhere hurts, cannot be dispelled (`novel.hazard-zones`) |
| `18` SUMMONING_RITUAL | `reqParticipants`, `spellId` | a gate that needs a group, with the stock ritual circle (`novel.ritual-gate`) |
| `21` GUARDPOST | `creatureID`, `charges` | objects that spawn defenders, wave after wave (`novel.guardpost-spawner`) |
| `22` SPELLCASTER | `spellId`, `charges`, `partyOnly` | buff stations, vending machines, teleport pads (`novel.device-spellcaster`) |

Stack traps + guardposts + SmartAI and you have a tower-defence event with no code (`novel.smartai-event-bus`).
That is the general pattern: **the client renders consequences; the server only has to choose them.**

---

## 4. Logic without code

### 4.1 Conditions are a predicate language

`novel.conditions-algebra`: rows in one `ElseGroup` are OR, different groups are AND, and
`NegativeCondition` is NOT. Add quest, item, aura, reputation, level, achievement, skill and event types and
you can express most gating you would otherwise write code for:

```
(quest A done AND NOT has item B) OR reputation(exalted) OR event(active)
```

Once you see rows as clauses, "we need a module for that" becomes "we need three rows".

### 4.2 Feature flags and kill switches

`novel.feature-flags`: `disables` turns off spells, quests, maps and battlegrounds with the client's own
refusal messaging, and `game_event` schedules anything. Together: staged launches, seasonal rollover,
emergency off-switches, and A/B states — without deleting a row.

### 4.3 Quests as a state machine

`novel.quest-state-machine`: quest state is per character, permanent, and visible. A chain is a state machine
whose states the player can read. That makes quests the cheapest *persistent per-player* storage in the
schema — and the only one with a UI for free.

### 4.4 SmartAI as the world's event loop

`novel.smartai-event-bus`: timers, aggro, death, health thresholds, summons, emotes and phases. Most "we need
custom code" ideas are four rows. The API is numeric enums from `SmartScriptMgr.h` — see
[03](03-recipes.md) for the workflow and `examples/ashen-vault/sql/04_smart_scripts.sql` for a working file.

---

## 5. Economy and protocol

- **Mail as a queue (`novel.mail-queue`).** The only channel that delivers while the player is offline.
  Story episodes, compensation, daily gifts, payouts. Mail carries items, money and text.
- **Items as game pieces (`novel.items-as-cards`).** The trade window, mail and the auction house are already
  a protocol for exchanging discrete assets; items are the pieces. Random suffixes
  (`item_enchantment_template`) give you variance without new item ids.
- **A shop that changes per player (`novel.dynamic-vendor`).** Conditions on `npc_vendor` rows are evaluated
  per player, so one merchant is an entire tier system.
- **Reputation as a resource.** Standing gates vendors, quests and loot through conditions; with
  `reputation_reward_rate` and spillover you can make it behave like a currency without adding one.

---

## 6. Two structural repurposings

**Class-less characters (`novel.classless-progression`).** `playercreateinfo_spell_custom`,
`playercreateinfo_action`, `playercreateinfo_item` and `playercreateinfo_skills` decide what a new character
knows. Grant an unusual kit and progression becomes content-driven. The client's class UI stays as it is —
you are changing what the character *can do*, not what the game thinks a class *is* (talents and trainers will
still expect the normal frame; see [05](05-failure-modes.md) for the mismatches).

**Instances as persistent state (`novel.instance-state-machine`).** `instance_encounters` records what the
group has done; `dungeon_access_template` / `dungeon_access_requirements` gate entry. That is enough to make a
custom dungeon a campaign: wings that unlock, a boss that only appears after the third encounter, a lockout
that means something.

---

## 7. What makes a novel use safe

Novelty fails in specific ways. Four rules keep it from becoming a support queue:

1. **Never depend on a client id that does not exist.** Every camera, sound, spell, display and cinematic id
   must be one you verified. `tools/dbc_tool.py ids`, `.lookup`, and a test character are the instruments.
2. **Keep state on the server.** The addon is a view. Anything the client believes can be wrong, stale or
   forged; anything the server believes survives a `/reload`.
3. **Design the degraded state.** Assume the addon is missing, the quest is abandoned, the phase is wrong, the
   message was dropped. The player should see a plain, working game, not a broken one.
4. **Verify on a fresh character, then on a GM.** Caches, phases and GM flags hide most novel-use bugs
   ([05](05-failure-modes.md)).

| Symptom | Usual cause |
|---|---|
| Book opens to an empty page | `PageText` points at a `page_text` id that does not exist |
| Plaque does nothing when clicked | GO type is not `9`, or `Data0` is not the page id |
| Gossip option missing for one player | condition fails — check the clause, not the UI |
| Panel blank but the server logs traffic | addon prefix mismatch, or `AddonChannel = 0` |
| Camera plays, then input is stuck | the sequence ended without returning control |
| Quest tracker shows `0/0` | objective text present but no objective row to count |
| Guardpost spawns nothing | `charges` is 0, which is "none", not "unlimited" |

---

## 8. Worked design: the Vault Terminal

One NPC, no patch, four novel levers stacked:

| Screen | Implementation | Novel lever |
|---|---|---|
| Greeting + "what is this place" | `npc_text` page | text delivery |
| "Enter access code" | gossip option with `BoxCoded` | `novel.keypad-input` |
| "Claim today's seal (3 left)" | gossip option gated by a `conditions` clause on an item count | `novel.conditions-algebra` |
| "Progress: 12 of 50 seals" | a quest in the tracker, objective updated by script | `novel.quest-log-hud` |
| "Where are the other vaults?" | `quest_poi` pins on the world map | `novel.map-annotations` |
| Optional live leaderboard | addon panel fed by `SendAddonMessage` | `novel.addon-rpc` |

The only client-side artefact is the optional addon; everything else is rows. The player experiences a terminal
with a keypad, a daily claim, a progress bar and map markers — none of which the client was programmed to have.

---

## 9. Adding your own

The catalog is the project's memory. When you invent something, record it:

```bash
python3 tools/catalog.py validate        # shape, prefixes, allowlists, doc references
python3 -m unittest discover -s tests    # includes doc-vs-catalog agreement
```

New `table:` and `conf:` surfaces are checked against `catalog/world_tables.txt` and
`catalog/config_keys.txt`, which are captured inventories from the core's own schema and config files. If
your lever names a table that does not exist, validation fails — that is deliberate: it is how invented
schema stops at the door.

The `effect` field is the one that matters. Write what a player notices, in one sentence, or the entry is
not yet knowledge.
