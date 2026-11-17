# WotLK 3.3.5a custom content — mechanics and toolchain

Everything needed to add **custom features, items, spells, creatures, quests and systems** to a
World of Warcraft: Wrath of the Lich King (build **12340**) private server, built strictly from
assets and mechanics **the client already ships**.

Three things live here:

1. **A knowledge base** (`docs/`) — how the client and server actually consume data, what needs a
   client patch and what does not, recipes, failure modes, and the code-level mechanics.
2. **A catalog and a toolchain** (`catalog/`, `tools/`) — 110 alterations with their player-visible
   effects and patch cost, queryable; a DBC inspector/validator, a one-command preflight gate, and a
   patch manifest generator/verifier. No game files required to use or test any of it.
3. **Seven worked examples** — see [`examples/INDEX.md`](examples/INDEX.md) for the standard each one
   meets and the ID ranges they own. A dungeon, a server-fed interface, a book system, a
   three-person gate, a scheduled shop, a phasing pair, and a config-only season: every one loads on
   a stock world DB, every one is verifiable in game, every one needs **no client patch**.

> **The constraint that carries the value:** build custom content out of parts the client already
> has. The 3.3.5a client ships ~59k creature displays, ~117k game-object displays, six figures of
> item display rows, tens of thousands of spells and thousands of spell visuals. A server-driven
> design built from those needs **no client patch at all** for most features, and a *tiny* patch
> (a few hundred bytes of DBC rows, never models) for the rest.

---

## Quickstart

```bash
make check                     # selftest + all three test tiers (~15 s, no game files)
./tools/preflight.sh dbc-client/ dbc-server/     # gate a patch before packing
python3 tools/catalog.py query --no-mpq            # what needs no client patch at all
python3 tools/dbc_tool.py info Item.dbc
python3 tools/manifest.py make --staging patch/ --version 2026.11.1 --out manifest.json
python3 tools/manifest.py verify --manifest manifest.json --staging patch/
```

Nothing here needs a WoW client, a database, or Windows. The DBC tooling is stdlib Python; its
test suite generates its own fixtures, so you can validate the validator before trusting it.

---

## The six rules that prevent most of the pain

1. **Every piece of content lives in two or three places at once.** Items live in `item_template`
   (server DB) and `Item.dbc` (server *and* client); spells in `Spell.dbc` (both sides) and
   optionally `spell_dbc` (server overlay). Forgetting one copy is the #1 cause of "works for me".
   → [docs/01](docs/01-client-and-server-data-model.md)
2. **The client owns appearance and self-validation; the server owns state and mechanics.** If the
   client cannot resolve an ID it draws a red `?`, refuses to treat an item as a weapon, or drops a
   spell icon — no server code fixes that. → [docs/08 §3](docs/08-how-integration-actually-works.md)
3. **Decide which layer owns a feature before writing code.** Panel → addon; sink → SQL; new weapon
   *class* → client patch; new boss → none. → [docs/02](docs/02-feature-integration-matrix.md)
4. **A client patch is a release artifact, not a development step.** Versioned, reproducible,
   checksummed, and gated by a launcher. → [docs/04](docs/04-patching-toolchain-and-release.md)
5. **Client and server DBC sets must be byte-identical** for every table both sides read. Drift is
   silent — the server starts fine and quietly drops data. → `tools/preflight.sh`
6. **Reuse IDs, not just models.** The cheapest "new" item is an existing `Item.dbc` row with new
   `item_template` stats; the cheapest "new" spell visual is an existing kit behind a new
   `SpellVisual`. → [docs/06](docs/06-design-playbook-existing-assets.md)

---

## Layout

```
README.md                     this page
docs/                         the knowledge base (01–11)
catalog/alterations.json      110 levers: what you can change, what players see, patch cost
tools/
  catalog.py                  query the catalog (layers, clocks, surfaces, patch kinds)
  dbc_tool.py                 inspect/validate DBCs: header, duplicates, free IDs, refs, drift
  preflight.sh                release gate: structure + parity + referential integrity
  manifest.py                 build/verify a manifest (sha256 per table, patch hash, version)
  selftest.py                 synthetic fixtures for dbc_tool.py
examples/INDEX.md             the "full example" contract: seven parts, enforced by tests
examples/ashen-vault/         a zero-client-patch dungeon: spec → SQL → acceptance → rollback
examples/vault-terminal/      a zero-client-patch interface: protocol + model + addon + module + SQL
examples/reliquary/           books as content: page_text chains on type-9 objects
examples/ritual-gate/         a door that needs three people: GO type 18, reqParticipants
examples/fair-weather-market/ a scheduled shop with per-player stock: event + vendor + conditions
examples/two-cities/          phasing: two NPCs, one spot, phaseMask + spell_area
examples/season-dials/        a balance pass with no data: two config presets
tests/                        three test tiers: intern, engineer, master (stdlib only)
Makefile                      `make check`, `make preflight CLIENT=… SERVER=…`, `make manifest`
```

## The knowledge base

| Chapter | What it answers |
|---|---|
| [01 — Data model](docs/01-client-and-server-data-model.md) | Where data lives: MPQ load order, DBC layout, WDB cache, `DataDir/dbc`, `*_dbc` SQL overlays, who is authoritative for what |
| [02 — Integration matrix](docs/02-feature-integration-matrix.md) | Feature by feature: server-only vs. needs a patch, which tables, which traps |
| [03 — Recipes](docs/03-recipes.md) | Item, item look, spell, cosmetic aura, mount, serverside spell, set, gem/enchant, currency, dungeon, addon+server system |
| [04 — Patching & release](docs/04-patching-toolchain-and-release.md) | MPQ build mechanics, patch naming and **per-locale load order**, toolchain, ID policy, CI gate, launcher, rollback |
| [05 — Failure modes](docs/05-failure-modes.md) | Symptom → cause → fix, for the errors you will actually hit |
| [06 — Design playbook](docs/06-design-playbook-existing-assets.md) | Designing new content out of existing assets: reuse patterns, effort ladder, content pipeline |
| [07 — Sources](docs/07-sources.md) | Annotated primary + field sources, core code paths, and an explicit "not verified" list |
| [08 — How it actually works](docs/08-how-integration-actually-works.md) | The mechanics: boot sequence, DBC loading and SQL overlays in code, four runtime protocols, end-to-end traces, ten lossy joints, the consistency contract |
| [09 — Alteration catalog](docs/09-alteration-catalog.md) | **What you can change and what players actually see**: the reflection model, six layers, three clocks, 110 levers, six patch-free compositions, and what will never show without a patch |
| [10 — Novel alterations](docs/10-novel-alterations.md) | **Using the surface as a language**: the six escape hatches, books as a CMS, gossip as an app, the quest log as a HUD, the addon channel as a socket, gameobject types as devices, and the safety rules for clever content |
| [11 — Testing ladder](docs/11-testing-ladder.md) | What an intern, an engineer and a master engineer each test — and the thirteen real defects those tiers found in this project's own tooling, docs and claims |

## Glossary

| Word | Meaning here |
|---|---|
| **DBC** | Blizzard's fixed-width client table format (`WDBC`). Read by the client, and (a copy) by the server. |
| **MPQ** | Blizzard's archive format. Custom content is delivered by adding a higher-priority archive, never by editing Blizzard's. |
| **Patch** | A custom MPQ named `patch-N.MPQ` / `patch-L.MPQ` that shadows files from lower-priority archives. Also used loosely for "a client release". |
| **DBC row** | One record in such a table; the unit of client-side custom content. |
| **Overlay** | An AzerothCore `*_dbc` SQL table whose rows are applied on top of the loaded DBC file (add or replace by ID). |
| **Serverside** | Content the server knows and the client does not (invisible mechanics). |
| **Display ID** | An ID into a presentation table (`ItemDisplayInfo`, `CreatureDisplayInfo`, `GameObjectDisplayInfo`) — usually the only thing the client needs in order to *draw* something. |
| **Cache / WDB** | `Cache/*.wdb`: the client's copy of server-sent item/creature/quest data. Goes stale after DB/DBC changes. |

## Status and trust level

This is a synthesis of public format documentation, core source, and field reports — not a lab
report. Claims that came from code are marked `[core]` with the file and symbol; client behaviour
is marked `[client]` because the client is closed-source. Anything version- or core-specific is
flagged **(verify)**, and [07](docs/07-sources.md) ends with an explicit list of what was *not*
verified.

## Repository history

This repository previously contained **Conflux**, an agentic-execution prototype that was archived
read-only on 2026-11-11 and continues in a private repository (`ViscoBoBisco/MOC`) along with its
design documentation, qualification record and source. On 2026-11-17 the contents were replaced
with this project. The old tree is still reachable in git history (`898a5cf` and earlier) — nothing
was destroyed, only superseded.
