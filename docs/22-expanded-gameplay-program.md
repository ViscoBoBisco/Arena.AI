# 22 — Expanded gameplay architecture and implementation program

This chapter and [`designs/gameplay-systems-register.json`](../designs/gameplay-systems-register.json)
are a current source-oriented program, not evidence that proposed gameplay already exists. The
register is the machine-readable authority for feature owners, loops, evidence, maturity, gaps, and
next milestones. Update it in the same PR that changes a feature's implementation state.

## Re-inspected baseline

The active branch began at `186dcdf73b0063db26177654982a7642c7786b29`, the merge commit for PR #32.
PR #32 (`f75d3b086c18c5f3694660ba65552bc32fcb1365`) changed the Foundry delivery contract: a DBC
delta is a non-installable intermediate, client and server materializations are separately required,
artifact lifecycle is recomputed from package contents, and FakeHost adapter v1.2 validates its
synthetic lifecycle claims. It did not produce full DBC files, authenticate external proof, boot a
server, qualify a client, or integrate private Campaign Party.

## Unified ownership model

| Domain | Authoritative owner | Arena's role | Boundary that must remain explicit |
|---|---|---|---|
| Real characters, item instances, spells/auras, loot, NPC combat and persistence | AzerothCore | Generate content and source-bound qualification; provide reference models | Addon payloads never authorize state; preserve core item lifecycle |
| Selected character/Altbot, roster, party permissions, deployment and orchestration | Campaign Party (separate/private) | Define versioned transport-neutral contracts and test doubles | No invented private APIs or assumed source/baseline |
| Bot movement, combat and tactics | Playerbots | Develop explicitly versioned strategy capability contracts | Bots are not NPC squad simulation and new powers are not automatically bot-supported |
| Entitlements, loadouts, provenance, operation receipts and audit | Each runtime subsystem owns its domain state; shared envelope can correlate operations | Specify common identity/version/receipt vocabulary and generate artifacts | Shared metadata does not transfer authority across domains or databases |
| Portable Hole | Executable character-private virtual commodity ledger, reservations, receipts, addon UI and GM/server credit in `server/mod-portable-hole` | Qualify MySQL/module in CI; next add recoverable physical item custody and explicit revocable ACL adapters | No physical item deposit/withdrawal, account/party sharing, or profession reward integration is claimed; never infer ownership from addon data or bypass the core item object model |
| Runic Codex | Future runtime module; AzerothCore executes effects | Define typed effects, unlock sources, packaging and qualification | Native glyphs remain separate; no guessed DBC rows, assets or unqualified class effects |
| Faction Operations | Future module; AzerothCore retains native reputation and hostility | Define authority/accord packages and tests | Native reputation, faction authority, personal accords and FactionTemplate hostility are separate |
| Arena Foundry | Arena repository | Declarative specs, source-aware IDs, generators, packages, qualification and handoff | A generated artifact is not installed gameplay; maturity labels follow evidence only |

Portable Hole should become the authorized resource custody layer used by professions, expedition
provisioning and faction supply. Runic Codex consumes source-aware unlock/provenance and exposes
active powers through a versioned loadout. Equipment mastery links real item-instance identity to
bounded progress, not a template ID. Faction Operations supplies scoped benefits and deployments.
Campaign Party chooses and authorizes members and actions; Playerbots execute compatible bot
strategies; AzerothCore performs and persists real game state. Each integration is a versioned,
permission-checked adapter with exact receipts and independent disable/recovery behavior.

## Current implementation/evidence matrix

The complete row-by-row state is in the JSON register. Current bounded maturity:

- **Implemented, bounded:** Foundry specification, asset-index, package, qualification, offline FakeHost adapter, and the exact-pinned disposable World ownership/receipt attestation path from PR #35. That World proof qualifies only its synthetic fixture; it is not a populated-World or release qualification.
- **Synthetic executable slice, qualification still open:** Portable Hole character-private virtual balances, reservations, GM/server credits, exact receipts, a native AzerothCore PlayerScript/addon surface, and a disposable-MySQL crash/concurrency test contract. Hosted run `44425827305` found a real competing-reservation InnoDB deadlock; the current branch orders balance-row locking before receipt insertion and adds a same-request concurrency case, but this repair must pass hosted MySQL and pinned-core compilation. It does not move physical items, share account/party ACLs, connect profession rewards, or claim gameplay.
- **Synthetic/design-only or blocked:** faction-operation typed foundation; Frontier Operations state oracle; Runic Codex; Equipment Mastery; Catalytic Crafting's actual recipe operations; Party Build Architect; source-bound DBC materialization; and public-core full boot. The historical Frost Armory preview is not a playable package.
- **External/unavailable:** build-12340 client exercise and private Campaign Party integration. No
  current source checkout or contract establishes either.
- **Compile evidence is not runtime evidence:** prior PR #32 checks reported pinned public-core
  module compilation and a table-scoped disposable Characters DB proof. Neither means full-core
  boot, live gameplay, Playerbot support, client acceptance or Campaign Party admission.

## Prioritized, separable PR sequence

| PR slice | Outcome and acceptance boundary |
|---|---|
| **P0-01a World ownership** | InnoDB-only data transactions; per-database advisory lock; manifest/version-bound row journal; complete inserted-row snapshots; request receipts; collision, re-apply, wrong owner, manifest drift, user modification, and concurrency tests. Raw rollback SQL remains refused. |
| **P0-01b World DB qualification** | Only after 01a: use disposable MySQL initialized from the exact pinned World schema; interruption/reconnect tests; source/schema/manifest-bound proof. Do not re-enable the old qualification gate until this passes. |
| **P0-02 DBC materialization** | Start with source-verified Item.dbc; write new client/server paths, preserve unrelated records and fields, round-trip and validate references, prove parity, separately stage MPQ. Unknown tables including Spell.dbc stay blocked. |
| **P0-03 artifact/proof trust** | Derive presence from verified bytes and authenticate qualification provenance; reject stale or replayed reports. FakeHost remains an explicitly synthetic test. |
| **P0-04 public runtime attempt** | Opt-in full public-pinned AzerothCore build/module-load/boot/bounded request in isolated CI where resources permit; report missing data explicitly. Private fork and client gates stay separate. |
| **P1-01 shared contracts** | Versioned namespaced identity, permission revision, source provenance, immutable request receipt and audit envelope, with each domain retaining its own authority. Demonstrate one isolated server path. |
| **P1-02 Runic Codex vertical** | One source-verified existing token and acquisition, durable permanent learn, addon listing/equip, one genuinely changed spell behavior, relog/duplicate/recursion safety. No asset ID is assumed. |
| **P1-03 Portable Hole commodity slice** | ACL-scoped virtual 64-bit commodity balance, exact request receipts, paged/search listing, reserved amounts, concurrency/crash disposable-MySQL coverage; no unique item restoration claim. |
| **P1-04 reagent overlay/pins** | Read-only bag versus authorized shared counts, shortages and grounded Closest Available; Withdraw Missing is a later explicitly authorized operation. |
| **P1-05 Argent benefit** | Verify native faction identity, persist authority independently of reputation, deliver one tangible benefit without global hostility or fake party membership. |
| **P2 verticals** | Separate PRs for unique-item custody, mastery/relic, squads, Frontier mission, actual crafting, Build Architect, discovery atlas and expedition planning. Each must show executable tests and its own limits. |

## P0-01a World ownership: current contract

`foundry/world_ownership.py` is the first independent implementation slice. The service accepts an
integrity-checked package, requires its World SQL to match current spec-derived output, and only
supports the known pinned-table insert allowlist. It creates two Arena-owned InnoDB side tables; it
does not alter AzerothCore tables. A stable request ID binds a retry to its original action and
manifest. The service serializes Arena operations with a schema-specific MySQL advisory lock and
wraps target inserts/deletes, row journal changes and operation receipts in one InnoDB transaction.
It refuses non-InnoDB targets, foreign collisions, other package/version/manifest owners, and any
rollback whose complete current row snapshot differs from the snapshot captured at apply. Schema
DDL is the separate idempotent bootstrap; MySQL implicit DDL commits are not misrepresented as part
of the row transaction.

The package CLI is intentionally limited to explicitly confirmed
`arena_foundry_disposable_*` schemas. Install the optional pinned connector for that command with
`python3 -m pip install -r requirements-world-qualification.txt`.
The ordinary generated `sql/99_rollback.sql` guard and existing World ARENA-DB qualification remain
closed: this new service is not a reason to apply legacy raw SQL. The disposable MySQL 8.4 integration passed on source `aa2016076e2e68df60555d38ab507a3550683b38` (run `44412436391`). Its synthetic schema uses only the tested `item_template` slice and includes a dropped client connection after the first insert followed by same-request recovery. Exact AzerothCore schema initialization, server-process crash/restart recovery and manifest-bound World attestation remain P0-01b work.
Changed-package upgrades currently refuse; a separately designed reversible upgrade protocol is
required rather than interpreting changed output as ownership.

Example (disposable database only; protect the option file with mode 0600):

```bash
python3 -m pip install PyMySQL==1.1.1
python3 tools/foundry_world_operate.py apply /tmp/checked-package \
  --database arena_foundry_disposable_world_test \
  --defaults-extra-file /secure/local-mysql.cnf \
  --request-id apply-v1-2026-01 --confirm-disposable
python3 tools/foundry_world_operate.py rollback /tmp/checked-package \
  --database arena_foundry_disposable_world_test \
  --defaults-extra-file /secure/local-mysql.cnf \
  --request-id rollback-v1-2026-01 --confirm-disposable
```

Never run this flow against production, a shared DB or valuable data. On any exception the DML
transaction rolls back; preserve the error and inspect the disposable journal before retrying with
the same request ID. No raw `DELETE-by-ID` recovery is authorized.

## Acceptance and handoff discipline

For each follow-on PR record exact base/head SHA, file/schema changes, local and CI tests, evidence
level, compatibility target, user-visible acceptance, adversarial cases, migration/disable/recovery,
nonclaims, and the next independent slice. Regenerate handoff material from the actual current HEAD;
never copy an old PR's report as fresh evidence. Keep legal game data, secrets, private player data,
client extracts and MPQ binaries out of Git. Research on other custom servers must record source,
feature, verified technical precedent, compatibility and licensing; design inspiration does not
license assets/code or establish WotLK support.
