# 12 — Content Foundry: specification, build, and evidence

The Foundry is the first reusable compiler slice in Arena. It is a local-first Python package that takes a versioned JSON feature specification and emits a deterministic handoff bundle. It does **not** require or distribute WoW client files. Supplied DBC extracts and inventory metadata remain local inputs.

## Current pipeline

```text
JSON spec v1 -> schema/reference/ID checks -> plan -> SQL, C++, Lua, config, DBC delta
            -> package manifest + compatibility dry-run + evidence -> verify
```

Commands (all use exit codes 0 success, 1 validation/integrity failure, 2 usage or input error):

```sh
python3 tools/foundry.py validate specs/frost-armory.json
python3 tools/foundry.py plan specs/frost-armory.json
python3 tools/foundry.py assets --dbc-dir /path/to/local/dbc --query frost
python3 tools/foundry.py assets --inventory local-assets.json --query frost --kind item_display
python3 tools/foundry.py build specs/frost-armory.json --out /tmp/frost-armory --allow-unverified
python3 tools/foundry.py verify /tmp/frost-armory
```

`validate` is fail-closed. Only `PASS` qualifies. Missing required database/client evidence is never silently treated as success: checks can be `FAIL`, `SKIPPED`, or `NOT_ASSESSED`. `--allow-unverified` does not convert an unknown asset into a verified one; it explicitly allows building a handoff preview, records the `SKIPPED` verdict and leaves the overall qualification unsuccessful. Without the flag, the build is blocked. The allowance is a development escape hatch, not a release approval.

## Spec v1 contract

A spec has `spec_version: 1`, a lowercase hyphenated `name`, semver `version`, title, inclusive `id_range`, optional dependency declarations, and object arrays (`items`, `spells`, `quests`, `creatures`, `gameobjects`, `vendors`, `recipes`, `events`) plus addon/module/config and at least three acceptance statements. Object references use stable `key` values. The validator rejects unknown top-level fields, malformed IDs, duplicate keys, dangling vendor/recipe/quest references, IDs outside table bounds and unknown world tables. `game_event.eventEntry` is constrained to its own 0–255 table limit rather than inheriting the spec's custom-ID window.

A client asset reference can be an integer (meaning the author asserts it is known) or an explicit object such as `{"value": 41179, "verified": false}`. For release-grade work, use a locally supplied DBC extract or source-backed inventory and record evidence for every referenced display, model, icon and visual. An integer alone is an author assertion, not independent verification. The current v1 schema validates a useful common subset; it is not yet a complete grammar for every field in every WoW content family.

ID allocation is deterministic and table-aware. Explicit IDs are pre-reserved so an earlier automatic choice cannot steal a later explicit ID; automatic IDs skip IDs already used in their table and any supplied baseline. Most tables draw from the spec's inclusive custom range; narrow tables such as `game_event` allocate within their own numeric domain. Duplicate explicit IDs within one table and exhausted windows fail rather than reusing an ID. Separate tables may use the same integer because their keys are table-scoped. Vendor stock and recipe definitions are relationships, not rows with their own primary IDs; the allocator does not invent IDs for them.

The CLI accepts a read-only reservation snapshot using `--baseline`:

```json
{
  "schema": "arena-id-baseline/1",
  "reserved_ids": {"item_template": [901000, 901001], "creature_template": [901000]}
}
```

Pass the same baseline to `plan`, `validate`, and `build`. Packages include `id-baseline.json`, an `id-allocation.json` table/key mapping, and the baseline SHA-256 in both allocation metadata and the manifest. This is a reproducible snapshot contract, not a live Campaign Party scanner or a shared transactional registry; concurrent builds still require coordination and refreshed source snapshots.

## Artifacts and their status

`build` writes the exact normalized spec, SQL migration and rollback, optional Item.dbc delta, module skeleton, 3.3.5a addon skeleton, configuration, README, manifest, evidence and fake-host compatibility receipt. Content files are deterministic; timestamps are opt-in. `verify` hashes listed files and reports drift. The bundle never creates or overwrites an MPQ, and never presumes ownership of `Data/patch-4.MPQ`.

The generated artifacts are **not all production implementations** yet. SQL generation currently covers a narrow subset and is not schema-qualified against a disposable AzerothCore database. Crafting entries are explanatory comments, not executable profession recipes; the emitted C++ module is a registration skeleton, not the requested gameplay; addon Lua is a presentation placeholder; spells are server-overlay name rows rather than a cloned, effect-complete spell; DBC delta generation supports only the current narrow Item.dbc form and no MPQ assembly. These boundaries are intentional and visible: do not install or describe them as a completed gameplay feature. The tool proves specification-to-bundle mechanics, not that all acceptance statements pass.

## Evidence ladder

| Level | What earns it | Frost Armory status on this branch |
|---|---|---|
| `ARENA-STATIC` | schema, structural and repository checks | Spec structure checks run; stock asset verification remains `NOT_ASSESSED` |
| `ARENA-SYNTHETIC` | generated fixtures, negative tests, fake-host behavior | 64 Foundry-focused tests and all 213 repository tests pass in this branch; generated bundle does not attest test execution |
| `ARENA-DB` | apply and rollback on a disposable compatible DB | Pending; no MySQL/AzerothCore DB in this environment |
| `ARENA-COMPILE` | module compile/API check against pinned compatible AzerothCore | Pending; generated module is a skeleton and is not compiled |
| `ARENA-PACK` | reproducible package and independent integrity verification | `foundry verify packages/frost-armory` passes in this workspace; package evidence marks verification as an external required step, not a self-claim |
| `CAMPAIGN-ADMISSION` | actual Campaign Party baseline accepts without conflicts | Pending; target repo/baseline unavailable |
| `CAMPAIGN-LIVE` | real 3.3.5a client and Playerbots execute feature | Pending; no downstream runtime |

`ARENA-PACK` does not imply DB migration safety, module compilation, valid stock asset IDs, or live functionality. The nine asset references in `specs/frost-armory.json` are explicitly unverified. A package with `allow_unverified: true` is a preview/handoff draft and is not release-qualified.

## Traceable task ledger

Acceptance conditions are deliberately explicit; `done` means the contract is implemented and tested at the stated level, not that Campaign Party has accepted it.

| Track | Work item | State / acceptance condition |
|---|---|---|
| A | Fail-closed spec, ID/table bounds, visible verdicts | **Partial** — synthetic validation and negative tests; client/server parity, full DBC references, schema and pinned-core checks remain |
| B | Local DBC/inventory ingest, deterministic keyword search and patch-cost query | **Partial** — implemented subset; semantic relationships, MPQ inventory parser and broad DBC coverage remain |
| C | Versioned JSON spec, key references, ID allocation and preview | **Partial** — spec v1 common subset and snapshot-aware ID allocation; transactional cross-package registry, migration tooling and all content grammars remain |
| D | Deterministic bundle, rollback, manifest verification, lossless DBC delta writer | **Partial** — narrow writer and bundle; conflict-aware merge, MPQ assembly and cache invalidation remain |
| E | Shared item/spell/equipment factories | **Not done** — current generated records are not a level-scaling/effect-complete factory |
| F | Quest, world, dungeon and encounter factories | **Not done** — no complete five-member instance or encounter state machine |
| G | Profession graph, Material Exchange interface and AHBot simulator | **Not done** — recipes are currently descriptive only; downstream economy remains authoritative |
| H | Versioned party/Playerbot contracts and test doubles | **Partial** — package admission fake host only; no party state/command contract or bot behavior simulation |
| I | Static/synthetic qualification and negative tests | **Partial** — synthetic suite done; disposable DB, compile, addon runtime, installer fault/rollback tests remain |
| J | CLI create/inspect/plan/build/verify/compare/explain | **Partial** — validate/plan/build/verify/assets/explain and imported baseline snapshots available; compare, GUI and live baseline discovery remain |
| K | Nine cohesive production showcases | **Not done** — Frost Armory is a compiler exercise, not a production-ready showcase; eight others not built |
| L | Campaign Party contract, fake host and handoff | **Partial** — adapter v1 fake host exercises dry-run/admission/idempotency gates; actual baseline admission and complete handoff checker remain |

## Compatibility and risk

- Target is 3.3.5a build 12340 / AzerothCore WotLK, but no real Campaign Party schema or core revision has been pinned by this package. The checked-in example package carries an empty baseline snapshot; it has not been collision-checked against Campaign Party.
- A DBC row requires parity between client and server copies. Current package generation does not prove that parity.
- Unknown stock IDs may render incorrectly or fail lookup. This spec marks these unknown; do not treat the placeholder numbers as stock-verified.
- SQL syntax/column compatibility is not proven by text-level tests. Apply only to a disposable matching database after `ARENA-DB` qualification exists.
- Generated C++ is uncompiled, and generated addon behavior is not client-runtime tested.
- The existing patch archive is outside Foundry's write path; any future patch merger must use a new path, detect collisions and refuse to overwrite unknown archives.

See [13 — Campaign Party adapter](13-campaign-party-adapter.md) for the external contract and its limits.
