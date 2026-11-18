# 21 — Systematic completion plan

This plan turns the stocktake in [20](20-project-stocktake.md) into an ordered, repository-wide
coverage pass. It is deliberately evidence-first: locally achievable improvements proceed now;
work that needs a real client, target core, disposable database, or downstream fork remains an
explicit qualification task rather than an invented result.

## Operating method

For every leaf (chapter, catalog entry, tool behavior, spec field, generated artifact, example,
design component, test gate, and workflow):

1. State its purpose and owner (client, server, addon, database, tool, or design-only).
2. Link the source/contract and the implementation that supports it.
3. Add the smallest useful reproducible local test or structured validation.
4. State the evidence boundary and required external acceptance case.
5. Make unsafe/unsupported/missing inputs fail closed.
6. Run the narrow check, then `make check`; update docs/evidence and this plan in the same change.

A chapter or component is not “complete” merely because it has prose. It is complete to the
workspace’s limit when its scope is clear, references resolve, local claims have a regression check,
known external work is actionable, and no unsupported claim is presented as proven.

## Prioritized work sequence

| Priority | Workstream | Actions and exit condition |
|---|---|---|
| P0 — trust and reproducibility | Keep the overview, package labels, evidence, and latest source in sync. | Quickstarts use a passing spec; historical artifacts are labeled; handoff is regenerated from current source; CI failures are diagnosed, not inferred. Exit: `make check` passes, package validation behavior is described accurately, and handoff source revision matches checkout. |
| P1 — contracts and executable surfaces | Review all Foundry spec fields, generator surfaces, package lifecycle, adapters, DBC writer, IDs, and evidence gates. | For each accepted field, test acceptance and adversarial/refusal paths; for unsupported surfaces, assert fail-closed behavior. Exit: no declared content silently vanishes; build/verify are deterministic; qualification gates cannot be borrowed across changed inputs. |
| P2 — catalog completeness | Walk every one of 110 entries, grouped by the six layers; verify unique ID, real surface, player-visible effect, practical check, patch kind, notes and doc refs. | Fix omissions with source/test support, not speculation. Exit: validator passes, docs cover all entries, and representative queries for each layer/delivery kind are tested. |
| P3 — knowledge-base chapter audit | For chapters 01–21, check title, linked sources, terminology, command accuracy, cross-links, and explicit evidence level. | Add missing examples and failure modes where supported; eliminate stale/duplicated claims. Exit: every chapter has an owner/scope, link checks pass, and unverified behaviors are marked. |
| P4 — examples and specs | Review all seven examples and both specs from declaration through emitted output, rollback, IDs, dependency/provenance and acceptance. | Keep Vault Terminal as the current locally valid example; retain Frost’s expected emitter failure until implementation or an intentional scope change with evidence. Exit: each example’s seven-part contract remains tested, every SQL mutation has a safe strategy, and unsupported content cannot package. |
| P5 — design/prototype maturity | Inventory every design component, identify assumptions, interfaces, state transitions, failure paths and promotion gate. | Keep Azeroth Unbound/Catalytic Crafting/Frontier Operations/Argent foundation distinct from installable content. Exit: each design has an explicit next proof and is not accidentally treated as a Foundry input. |
| P6 — external qualification | Complete only with target resources and revision-bound evidence. | World DB ownership journal and recovery first; then disposable DB qualification; pinned-core compile; boot/runtime; legal build-12340 client acceptance; then Playerbot/Campaign Party target-fork checks. Exit: results, hashes, environment, exact cases and failures are documented independently. |

## Immediate backlog, ordered by safety and leverage

### 1. Baseline claim correction and discoverability — started

- The README quickstart now uses `specs/vault-terminal.json`, whose static validation passes, rather
  than presenting Frost’s intentionally unsupported recipe as the routine build example.
- The README now labels the Frost bundle as historical and non-installable and distinguishes a
  Vault Terminal handoff bundle from gameplay installation/qualification.
- Added chapters 20–21 to the README layout and chapter index; documentation integrity tests discover
  every numbered chapter dynamically, so no test-range change was needed.
- Corrected the Foundry examples in chapter 12 to use Vault Terminal for the happy path, retained an
  explicit Frost emitter-coverage warning, and aligned the pinned-core compile commands with the
  Vault Terminal module rather than the unsupported Frost recipe spec.
- Checks: `make check` PASS (315 tests, one skipped); catalog validation PASS; Vault Terminal
  validate/build/verify PASS (17 files built, 19 payload/metadata files verified); docs integrity
  tests PASS; `git diff --check` PASS. The handoff generator also passed its three declared checks,
  but its `/tmp` output is not the checked-in artifact. These package/handoff round trips prove
  integrity only—not qualification or play.

### 2026-11-17 — catalog validator hardening (P2 started)

- Hardened `tools/catalog.py` validation for wrong top-level/collection/entry shapes, invalid field
  types, boolean clocks, empty surface targets, malformed definitions and duplicate definition IDs.
  Non-validation queries now validate input first and refuse malformed custom catalogs with a clear
  error instead of reaching unsafe render/filter paths.
- Added regression tests for malformed shapes, values and query refusal. Current catalog still
  validates cleanly; all 110 records are unchanged, so this is validator hardening—not yet the
  promised entry-by-entry factual audit.
- Checks: catalog tests PASS (16); tier-master suite PASS (34); `make check` PASS (317 tests, one
  skipped); presentation JSON query returned 15 records; `git diff --check` PASS.
- Tried to retrieve failed workflow run `44392453717` logs again; GitHub's results receiver returned
  `EOF`. The historical JSON parse failure therefore remains undiagnosed; no cause is inferred.

### 2026-11-17 — CLI/lifecycle hardening (P1 started)

- The Foundry lifecycle audit found a publication race: `release_package` preflighted an absent
  destination but used `os.replace`, which could replace a directory created in that interval.
  Release now uses the same atomic no-replace directory publication guard as package builds.
- Added a race-injection regression proving a destination created after preflight, including its
  operator-owned sentinel file, survives and the staged copy is cleaned up. Existing qualified
  release and failure-path tests still pass.
- Hardened catalog loading/validation further: top-level shape, schema version, documented
  definitions, layer clocks, catalog ID syntax, optional note types, UTF-8 errors, and malformed
  custom catalog use. These changes validate data; they do not claim factual verification of all
  catalog surfaces.

- Added the Foundry CLI command-contract table to chapter 12, including read/write scope, exit-code
  semantics and the distinction between plan/build/verify/qualify/release.
- Checks: catalog suite PASS (18 tests); lifecycle suite PASS (9 tests); tier-master suite PASS
  (34 tests); docs integrity suite PASS (6 tests); final `make check` PASS (320 tests, one skipped);
  catalog validation and Vault Terminal spec validation PASS; `git diff --check` PASS.

### 2026-11-17 — policy catalog slice (P2 continued)

- Reviewed all 15 policy records for their listed surfaces, effect, verification prompt, notes
  presence and chapter references; checked declared config/table surfaces through the catalog's
  allowlists. Deep source confirmation for every policy behavior remains open. Added explicit
  limitations for money-drop versus vendor-price effects, reputation multipliers versus
  reward/spillover rows, PvE/PvP corpse behavior, guild-economy test isolation, and reset checks.
- Removed the destructive `.instance unbind all` suggestion from the ordinary verification list;
  replaced it with test-bind/reset-boundary checks and a warning about clearing player lockouts.
- Policy records now all carry notes; catalog metrics are 19 records without notes and 68 with a
  single verification item (both counts still need manual review, not blanket enforcement).
- Added regression assertions for the whole policy slice and the lockout-safe reset check.
  Verification: `make check` PASS (321 tests, one skipped); catalog validation PASS; policy query
  metrics matched the then-current 15/19/68 counts; docs tests PASS (6); `git diff --check` PASS.

### 2026-11-17 — content catalog slice (P2 continued)

- Reviewed the five content records that lacked notes: quest gating, graveyards, battleground/arena,
  mail rewards and weather. Added path-specific limits and test guidance without upgrading static
  claims to live-core/client evidence.
- Replaced the speculative `.bg start?` instruction with a target-core documented test-queue
  procedure. Clarified that GM `.weather` is an immediate control, not proof of zone scheduling.
- All 32 content entries now include notes. Catalog-wide open queues are 14 entries with no notes
  and 64 with a single verification item; continue reviewing by layer, not by count alone.
- Verification: `make check` PASS (322 tests, one skipped); catalog validation PASS; the new content-slice regression PASS; docs integrity PASS (6); `git diff --check` PASS.

### 2026-11-17 — mechanics catalog slice (P2 continued)

- Reviewed the ten mechanics entries that lacked notes, covering cooldown overrides, threat, proc
  conditions, spell-script registration, zone-aura boundaries, teleport destinations, stacking,
  difficulty mapping, waypoint links and pool/linked respawns. Added controlled test procedures and
  evidence limits; corrected a grammar error and narrowed a difficulty claim to modes actually
  supported by the target instance/core.
- Mechanics entries now all include notes. Combined with completed policy/content slices, 4 entries
  still lack optional notes and 55 have a single listed verification step. These are review metrics,
  not proof defects; other mechanics entries still need source-level validation.
- Verification: catalog tests PASS (21); `make check` PASS (323 tests, one skipped); catalog validation PASS; docs integrity PASS (6); `git diff --check` PASS.

### 2026-11-17 — systems and presentation catalog slices (P2 continued)

- Added limitation/evidence notes to the four previously note-less records: dynamic creature scaling, custom loot logic, nameplate/minimap overlays, and new titles. Expanded verification guidance across every systems and presentation entry, adding distinct controlled checks for persistence, permissions, malformed inputs, state boundaries, asset/schema parity and clean-client behavior as appropriate.
- All 110 records now carry a non-empty note; all 23 systems/presentation records have at least two verification checks. Thirty-six entries elsewhere in the catalog still have one listed verification instruction; this is a review queue, not a completion score or automatic defect count.
- Verification: catalog tests PASS (22); `make check` PASS (324 tests, one skipped); catalog validation PASS; docs integrity PASS (6); all 110 records have notes and all systems/presentation records have two or more checks; `git diff --check` PASS.

### 2. Read-only source/evidence audit of all Foundry paths

Inspect `foundry/spec.py`, `generate.py`, `dbc_writer.py`, `package.py`, `candidate.py`,
`maturity.py`, `qualification.py`, `db_qualification.py`, `adapter.py`, `ids.py`, and their CLIs.
For each CLI verb (`validate`, `plan`, `build`, `verify`, `qualify`, `release`, `export-candidate`,
`assets`, `explain`), record accepted inputs, side effects, refusal behavior and evidence output.
Resolve the World DB ownership design before any attempt to enable writes or rollback. Inspect
Characters workflow redirected output directly; a JSON parse error is not a diagnosis. Exit only
when failure modes and evidence claims have exact tests and current CI result references.

### 3. Catalog line-by-line audit

Use the catalog’s own `list`, `show`, `surfaces`, `matrix` and `stats` commands. Group the work by
policy (15), content (32), mechanics (15), systems (8), presentation (15), novel (25). The first
mechanical pass counted 24 entries with no optional `notes` and 70 with a single `verify` item; these
are prioritization signals, not auto-fail criteria. For each record, independently answer: does the
surface exist in the cited schema/config/source; is the player-visible effect causally plausible; is
the verification instruction actionable and controlled; is delivery cost conservative; are
limitations stated; do doc references actually explain it? Use local code or
fixtures where possible and mark core/client-dependent assertions for external verification. Do not
bulk-rewrite entries from category names alone.

### 4. Documentation and example audit

Read chapters sequentially against the code and existing tests. Check every command by running its
help or a safe local validation; check every relative link. For examples, trace spec/SQL/module/addon
(if present)/acceptance/rollback and ID allocation. Distinguish SQL parse/shape tests from execution
against the supported database engine. Keep rollback claims conservative where DDL, concurrent
writes, or row ownership is not proven.

### 5. Design component audit

For each design JSON and model, enumerate components and record: intended player experience,
state ownership, authoritative mutation point, persistence, idempotency, concurrency, exploit/abuse
paths, observability, test oracle, and promotion criteria. Keep unresolved stock IDs null with
provenance requirements. A synthetic model can validate its own transitions but cannot establish
core hooks, client behavior, or gameplay balance.

### 6. Target-environment acceptance, only when available

Use the local-only procedures in chapters 17–19. Never run against production or shared populated
DBs. Record exact repository/core/client/schema/compiler identities, redacted outputs and hashes.
Stop at blockers; do not interpret skipped gates as passes. Re-evaluate which external tests can
actually be performed before starting this phase.

## Evidence ledger / current blockers

| Evidence surface | Current state | Next actionable step |
|---|---|---|
| Repository unit/selftest | PASS in latest pass (324 tests, one skipped) | Re-run after each code/docs batch. |
| Catalog structural validation | PASS (110 entries) | Complete per-record evidence/content audit and add tests only for grounded fixes. |
| Vault Terminal static spec validation | PASS | Continue exact-artifact and external acceptance workflow; no install/gameplay inference. |
| Frost Armory current spec | Expected emitter-coverage FAIL | Implement a fully tested recipe emitter and required qualification, or intentionally remove/defer the unsupported declaration. Do not relabel the existing preview. |
| World DB qualification | BLOCKED by no durable row ownership ledger; guard refuses before DB access | Design and test durable ownership, collision handling, interrupted-apply recovery, rollback ownership and engine behavior before restoring any PASS path. |
| Characters DB workflow | Prior run failed parsing redirected validation JSON; cause not established | Capture the validation command stdout/stderr/exit code and JSON bytes as distinct artifacts; fix and rerun on disposable DB only. |
| Pinned-core module compile | Separate compile evidence only; a historical run passed after Frost was removed from compile scope | Recheck current revision’s remote run before making a current claim. |
| build-12340 client acceptance | NOT_ASSESSED | Run chapter 17 against a legally supplied matching client and preserve case-by-case evidence. |
| Booted server runtime/gameplay | NOT_ASSESSED | Follow chapter 18 only after safe source/build/module/config and DB gates. |
| Playerbot / Campaign Party | NOT_ASSESSED on actual target forks | Obtain exact fork/API and run separate integration suites; fake-host tests are not substitutes. |
| Handoff bundle | Generated snapshot may lag HEAD | Regenerate after the planned baseline/doc updates and verify its source revision, manifest and test status. |

## Change tracking convention

For future batches, append a short dated entry below with paths changed, narrow/full commands run,
results, and any external gate still open. Do not mark a workstream complete based on intent alone.

### 2026-11-17 — baseline and first completeness pass

- Read-only inventory established the component map in chapter 20; no external qualification was
  attempted.
- `make check` passed: selftest plus 315 tests, one skipped. Catalog validation and both Foundry
  spec outcomes were checked as recorded in chapter 20.
- Began P0: corrected the README quickstart to build Vault Terminal and clarified the historical,
  non-installable Frost bundle and the handoff-versus-gameplay distinction.
- Remaining in this batch: link chapters 20–21 from the README, run docs/full tests, and inspect
  exact README/test assumptions before calling the documentation pass complete.
