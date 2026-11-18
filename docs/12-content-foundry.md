# 12 — Content Foundry: specification, build, and evidence

The Foundry is the first reusable compiler slice in Arena. It is a local-first Python package that takes a versioned JSON feature specification and emits a deterministic handoff bundle. It does **not** require or distribute WoW client files. Supplied DBC extracts and inventory metadata remain local inputs.

## Current pipeline

```text
JSON spec v1 -> validate/plan -> build preview -> verify integrity -> qualify -> release copy
```

The phases are intentionally independent. `build` never marks a bundle installable. `verify` checks package bytes only and does not upgrade evidence. `qualify` writes a separate manifest-bound report with a complete, delivery-derived gate set and embeds the raw attestation snapshot; `release` recomputes policy from the package and that snapshot, rejects report-only edits, and atomically copies only a qualified package, then rechecks the staged copy. A release copy is not an install or gameplay claim.

Qualification fails closed on missing manifest-bound attestations. Artifact maturity is now a structured matrix (`arena-artifact-maturity/1`): required behaviors are fixed by artifact kind and the inventory is derived from the delivery contract. Qualification evaluates package bytes and relevant DB/compile/asset evidence; free-form `generated_scope` prose is informational only and cannot promote or demote maturity. Missing or unknown artifact kinds and missing behavior records fail closed. Reports and external attestation JSON remain user-writable records, not cryptographic signatures. Recomputing policy prevents report-only forgery but does not authenticate an attestation author or prove that a referenced external check ran. Any maturity item supported only by user-supplied JSON remains `EXTERNAL_UNTRUSTED`; this local release command is not an independently trusted release authorization. Downstream operators must validate provenance independently.

## Disposable database migration check

`tools/foundry_db_qualify.py` requires a clean checkout at the exact revision in `foundry/core-pins.json`; proofs record hashes of the touched pinned `data/sql/base/db_world/*.sql` sources. It refuses non-disposable database names and requires explicit confirmation. **Current world-SQL packages are not DB-qualified:** because generated rows have no durable ownership ledger, the qualifier refuses before contacting the database rather than attempting an unsafe rollback. Earlier v2 apply/idempotence/rollback machinery must not be treated as current evidence for these changed emitters. A future ownership-ledger implementation must qualify collision refusal, interrupted apply recovery, rollback ownership, and exact schema behavior before restoring a PASS path. The historical [run 44219922132](https://github.com/ViscoBoBisco/Arena.AI/actions/runs/44219922132) at `6d49045de64c11c619b1d2011dce179f5d81c97b` passed the then-current seven-table identity/idempotence/rollback gate and overall qualification was correctly **BLOCKED**, but it did not collect per-column value checks. CI must pass again with v2 before claiming semantic DB evidence. Even v2 remains table-scoped—not a full populated world/server, full schema or gameplay qualification—and does not qualify stock assets, MPQ delivery, acceptance or live gameplay.

```sh
# First initialize this throwaway schema from the pinned compatible AzerothCore SQL base.
python3 tools/foundry_db_qualify.py packages/frost-armory \
  --database arena_foundry_disposable_frost \
  --core-source /path/to/clean/pinned/azerothcore-wotlk \
  --defaults-extra-file ~/.my.cnf \
  --confirm-disposable \
  --out /tmp/frost-db-attestations.json
python3 tools/foundry.py qualify packages/frost-armory \
  --attestations /tmp/frost-db-attestations.json \
  --out /tmp/frost-qualification.json
```

Never point this command at production or a populated shared database. The credential option file should be user-readable only; credentials are not passed on the command line or copied into the attestation.

Commands (all use exit codes 0 success, 1 validation/integrity failure, 2 usage or input error):

```sh
python3 tools/foundry.py validate specs/frost-armory.json
python3 tools/foundry.py plan specs/frost-armory.json
python3 tools/foundry.py assets --dbc-dir /path/to/local/dbc --query frost
python3 tools/foundry.py assets --inventory local-assets.json --query frost --kind item_display
python3 tools/foundry.py build specs/frost-armory.json --out /tmp/frost-armory --allow-unverified
python3 tools/foundry.py verify /tmp/frost-armory
python3 tools/foundry.py qualify /tmp/frost-armory
python3 tools/foundry.py release /tmp/frost-armory --out /tmp/releases/frost-armory
```

`build` publishes to a new destination only: it assembles in a same-filesystem staging directory and atomically renames into place without replacing an existing path. A collision or write failure leaves the prior destination untouched; move or archive an old package explicitly before rebuilding there. `validate` is fail-closed. Only `PASS` qualifies. Missing required database/client evidence is never silently treated as success: checks can be `FAIL`, `SKIPPED`, or `NOT_ASSESSED`. `--allow-unverified` does not convert an unknown asset into a verified one; it explicitly allows building a handoff preview, records the `SKIPPED` verdict and leaves the overall qualification unsuccessful. Without the flag, the build is blocked. The allowance is a development escape hatch, not a release approval.

## Spec v1 contract

A spec has `spec_version: 1`, a lowercase hyphenated `name`, semver `version`, title, inclusive `id_range`, optional dependency declarations, and object arrays (`items`, `spells`, `quests`, `creatures`, `gameobjects`, `vendors`, `recipes`, `events`) plus addon/module/config, optional table-scoped `delivery` declarations, and at least three acceptance statements. The derived `arena-delivery-requirements/1` contract enumerates required artifacts and host capabilities; `patch_cost` is display-only. Object references use stable `key` values. The validator rejects unknown top-level fields, malformed IDs, duplicate keys, dangling references, IDs outside table bounds and unknown world tables. Declared `gameobjects` and `recipes` currently fail with `emitter-coverage`: their generator surfaces are not executable. `Spell.dbc` and non-Item DBC contracts likewise fail closed until a concrete row emitter exists. `game_event.eventEntry` is constrained to its own 0–255 table limit rather than inheriting the spec's custom-ID window.

A client asset reference may remain an integer or use a legacy object such as `{"value": 41179, "verified": true}`; neither the integer nor the boolean is proof. To qualify a supported reference, extract the relevant DBCs locally, query them, and copy the returned `evidence` object into the reference:

```sh
python3 tools/foundry.py assets --dbc-dir /path/to/local/dbc --query 41179 --kind item_display
python3 tools/foundry.py validate specs/my-feature.json --dbc-dir /path/to/local/dbc
python3 tools/foundry.py build specs/my-feature.json --out /tmp/my-feature --dbc-dir /path/to/local/dbc
```

The evidence binds a table, row ID, and SHA-256 to parsed WDBC bytes. Validation requires the evidence to match the locally parsed row and rechecks the source hash, so changed or absent extracts, wrong-table evidence, and self-asserted `verified: true` fail or remain unassessed. This proves row presence in the supplied bytes; it does not independently certify that those bytes came from an unmodified 3.3.5a client. Inventory JSON remains useful for discovery, but its `verified` fields are shown as claims, do not qualify references, and its patch-cost estimate stays conservative. Packages preserve evidence hashes but do not bundle proprietary DBC extracts. The supported reference-to-table mapping is a useful subset, not yet a complete grammar for every WoW content family.

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

`build` writes the exact normalized spec, SQL migration and guarded rollback, optional Item.dbc delta, module and addon outputs, configuration, README, manifest, evidence and fake-host compatibility receipt. World migrations use plain `INSERT` statements, so a primary-key collision errors instead of deleting or overwriting existing data; re-running after success is unsupported. `sql/99_rollback.sql` raises a deliberate error because matching IDs or row values cannot prove ownership. Do not treat the refusal as an executable rollback. Automatic world cleanup and DB apply/rollback qualification remain blocked until durable ownership tracking is implemented. MySQL DDL atomicity is not assumed. Content files are deterministic; timestamps are opt-in. `verify` hashes listed files and reports drift. The bundle never creates or overwrites an MPQ, and never presumes ownership of `Data/patch-4.MPQ`.

Generated artifacts remain mixed-maturity and do not establish complete gameplay. Legacy non-protocol specs still use a registration-skeleton module and presentation-only addon; crafting entries had been explanatory comments, and specs declaring them now fail closed rather than package that omission. Spells are server-overlay name rows rather than effect-complete spells, and DBC output is narrow Item.dbc delta JSON without MPQ assembly. The checked-in Frost Armory package is retained only as a historical preview artifact: its regenerated World SQL refuses collisions and its rollback refuses untracked ownership, but its recipe declaration means a fresh build is correctly blocked. Vault Terminal is the first generated `arena-counter-terminal/1` path: it emits a Characters ledger migration, server-authoritative daily-counter handler, Interface 30300 addon UI, protocol manifest, and install/rollback notes. Its generated module passed the targeted pinned-core translation-unit/linkage gate, and the Characters schema/query/claim behavior passed a disposable DB run (see the exact CI record below). A booted worldserver feature session and a real build-12340 client have not been qualified; neither compile, DB proof, Lua shim, nor package integrity promotes gameplay or client compatibility to complete. Playerbots and Campaign Party behavior remain unqualified. See the [3.3.5a client acceptance checklist](17-vault-terminal-client-acceptance.md); actual client evidence remains `NOT_ASSESSED` until executed.

## Evidence ladder

| Level | What earns it | Frost Armory status on this branch |
|---|---|---|
| `ARENA-STATIC` | schema, structural and repository checks | Spec structure checks run; stock asset verification remains `NOT_ASSESSED` |
| `ARENA-SYNTHETIC` | generator contracts, fixtures, negative tests, fake-host behavior | `make check` passed 295 tests on this branch, including emitted-addon API scans and a Lua 5.1 shim flow; this does not execute the game client or worldserver feature |
| `ARENA-DB` | apply/rollback plus generated column-value checks on disposable schemas | Frost Armory world proof remains v1 identity-only in run 44219922132; its v2 semantic proof is not yet run. Separately, Vault Terminal's Characters schema/query/claim proof passed in [run 44307329762](https://github.com/ViscoBoBisco/Arena.AI/actions/runs/44307329762). Full worldserver runtime and gameplay remain NOT_ASSESSED. |
| `ARENA-COMPILE` | module compile/API check against pinned compatible AzerothCore | **PASS** for exact Frost Armory source hashes and AzerothCore `6ffb62abb867dabb258de6813ff5d06d6b011543`; [CI run](https://github.com/ViscoBoBisco/Arena.AI/actions/runs/44201459186/job/134456328585). This is a targeted object/linkage check, not a full server build or gameplay qualification. |
| `ARENA-PACK` | reproducible package and independent integrity verification | `foundry verify packages/frost-armory` passes in this workspace; package evidence marks verification as an external required step, not a self-claim |
| `CAMPAIGN-ADMISSION` | actual Campaign Party baseline accepts without conflicts | Pending; target repo/baseline unavailable |
| `CAMPAIGN-LIVE` | real 3.3.5a client and Playerbots execute feature | Pending; no downstream runtime |

`ARENA-PACK` does not imply DB migration safety, module compilation, valid stock asset IDs, or live functionality. The nine asset references in `specs/frost-armory.json` are explicitly unverified. A package with `allow_unverified: true` is a preview/handoff draft and is not release-qualified.

`foundry export-candidate <package> --out <candidate.zip>` creates a deterministic, integrity-checked review/integration archive with a `CANDIDATE.json` envelope. It explicitly says `candidate_only`, `non_authorizing`, `installable: false`, and `release_authorized: false`, and leaves package qualification unchanged. This is not the `release` command and does not synthesize Campaign admission/live evidence.

## Pinned AzerothCore source contract and compile gate

`foundry/core-pins.json` records the AzerothCore WotLK source revision `6ffb62abb867dabb258de6813ff5d06d6b011543` (observed at upstream `master` when pinned). Source inspection at that exact checkout confirmed that CMake collects translation units beneath `modules/<module>/src`, generates a static module loader that invokes `Add<module-name-with-hyphens-replaced-by-underscores>Scripts()`, and that `PlayerScript::OnPlayerLogin(Player*)` exists. The generator now emits a shared header declaring its registration function and includes it from the feature and module-loader translation units. These are inspected source facts, not a compile or runtime result.

A repeatable targeted compiler gate is available after configuring that exact core checkout with the generated module present. The configure step must complete successfully with AzerothCore's native build dependencies installed; it emits `compile_commands.json`. Then run:

```sh
python3 tools/foundry.py build specs/frost-armory.json --out /tmp/frost-armory --allow-unverified
cp -a /tmp/frost-armory/server/mod-frost-armory /path/to/azerothcore/modules/
cmake -S /path/to/azerothcore -B /path/to/azerothcore-build \
  -DWITHOUT_GIT=1 -DNOJEM=1 -DUSE_COREPCH=0 -DUSE_SCRIPTPCH=0 \
  -DTOOLS_BUILD=none -DWITH_WARNINGS=0
python3 tools/compile_generated_module.py \
  --build-dir /path/to/azerothcore-build \
  --core-source /path/to/azerothcore \
  --module mod-frost-armory \
  --registration AddFrostArmoryScripts
```

The helper refuses a different core revision, compiles the generated feature TU, module-local loader and CMake-generated static loader using that build's exact compiler commands, then checks the registration symbols connect with `nm`. Frost Armory's record remains `foundry/qualifications/azerothcore-module.json`. The current generated Vault Terminal source (`VaultTerminal.cpp` SHA-256 `dc23be4e95e2702c5c862d4d2f89a305b8690efd862796b0633a694bfea3b81a`) passed the pinned-source compile/linkage workflow in [run 44378437285](https://github.com/ViscoBoBisco/Arena.AI/actions/runs/44378437285/job/135063773024), against revision `6ffb62abb867dabb258de6813ff5d06d6b011543`; the matching exact-source record is `foundry/qualifications/vault-terminal-azerothcore-module.json`. Earlier the package's fail-closed hash check correctly omitted `ARENA-COMPILE` because that record still described a previous C++ source hash, despite a newer compile run having passed. The record was refreshed, and the package test now requires exact source-hash matching. Source review also found the request hook accepted addon frames carried over non-WHISPER chat distributions; the generated C++ now consumes and rejects any VT1 frame not sent as the documented WHISPER transport. Its new guard passed pinned-core compile/linkage CI. This does not prove a complete server build, database, live client, or runtime behavior. The generated Characters migration/read/claim behavior passed isolated DB qualification in [run 44307329762](https://github.com/ViscoBoBisco/Arena.AI/actions/runs/44307329762). Package generation and qualification require the pinned core revision, canonical checked-in feature record, and every packaged module `.cpp`/`.h` hash to match. These are targeted compile/linkage and disposable DB checks—not a complete AzerothCore build, final module link, booted worldserver feature session, 3.3.5a client run or gameplay qualification. They do not certify live feature behavior.

## Traceable task ledger

Acceptance conditions are deliberately explicit; `done` means the contract is implemented and tested at the stated level, not that Campaign Party has accepted it.

| Track | Work item | State / acceptance condition |
|---|---|---|
| A | Fail-closed spec, ID/table bounds, visible verdicts | **Partial** — structured artifact/capability requirements and client/server hash-parity admission gates are synthetic-tested; full asset provenance, DBC references and schema qualification remain |
| B | Local DBC/inventory ingest, deterministic keyword search and patch-cost query | **Partial** — implemented subset; semantic relationships, MPQ inventory parser and broad DBC coverage remain |
| C | Versioned JSON spec, key references, ID allocation and preview | **Partial** — spec v1 common subset and snapshot-aware ID allocation; transactional cross-package registry, migration tooling and all content grammars remain |
| D | Deterministic bundle, rollback, manifest verification, lossless DBC delta writer | **Partial** — narrow writer and bundle; conflict-aware merge, MPQ assembly and cache invalidation remain |
| E | Shared item/spell/equipment factories | **Not done** — current generated records are not a level-scaling/effect-complete factory |
| F | Quest, world, dungeon and encounter factories | **Not done** — no complete five-member instance or encounter state machine |
| G | Profession graph, Material Exchange interface and AHBot simulator | **Not done** — recipes are currently descriptive only; downstream economy remains authoritative |
| H | Versioned party/Playerbot contracts and test doubles | **Partial** — package admission fake host only; no party state/command contract or bot behavior simulation |
| I | Static/synthetic qualification and negative tests | **Partial** — synthetic suite and pinned-core translation-unit compile/linkage gate done; disposable DB, addon runtime, installer fault/rollback tests remain |
| J | CLI create/inspect/plan/build/verify/compare/explain/candidate export | **Partial** — validate/plan/build/verify/qualify/release/assets/explain and deterministic `export-candidate` available; compare, GUI, live baseline discovery and production integration remain |
| K | Nine cohesive production showcases | **Not done** — Frost Armory is a compiler exercise, not a production-ready showcase; eight others not built |
| L | Campaign Party contract, fake host and handoff | **Partial** — adapter v1.1 fake host exercises structured delivery requirements, DBC hash-parity and idempotency gates; actual downstream baseline admission and complete handoff checker remain |

## Compatibility and risk

- Target is 3.3.5a build 12340 / AzerothCore WotLK, but no real Campaign Party schema or core revision has been pinned by this package. The checked-in example package carries an empty baseline snapshot; it has not been collision-checked against Campaign Party.
- A DBC row requires parity between client and server copies. Current package generation does not prove that parity.
- Unknown stock IDs may render incorrectly or fail lookup. This spec marks these unknown; do not treat the placeholder numbers as stock-verified.
- Historical run 44219922132 passed a then-current seven-table apply/idempotence/rollback check. It is not evidence for current generated SQL: the emitter has since changed to collision-failing INSERTs and an ownership refusal guard, and the world DB qualifier now intentionally refuses before any database access. Current ARENA-DB status is `NOT_ASSESSED`.
- Non-protocol generated C++ remains a registration skeleton. Vault Terminal is generated from `specs/vault-terminal.json`; its exact-source module compile and disposable Characters migration/query/claim checks passed. Booted worldserver behavior, real client/addon interaction, Playerbots, and Campaign admission/live behavior remain unqualified.
- The existing patch archive is outside Foundry's write path; any future patch merger must use a new path, detect collisions and refuse to overwrite unknown archives.

See [13 — Campaign Party adapter](13-campaign-party-adapter.md) for the external contract and its limits.
