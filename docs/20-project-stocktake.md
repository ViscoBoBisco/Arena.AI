# 20 — Project stocktake and coverage map

This is the source-bound inventory for the repository-wide completeness pass. It describes what is
present, what its current evidence supports, and what remains outside the workspace. It is not a
claim that every design is implemented or that locally unavailable game/runtime behaviour has been
qualified. Recheck paths and commands against the active checkout before relying on this snapshot.

## How to read the status labels

- **Implemented / locally checked** — executable repository code or structured content exists and
  its stated local checks pass. This does not imply compatibility with an external client/server.
- **Documented / bounded** — the contract, procedure, or analysis exists; external execution is
  absent or intentionally deferred.
- **Design / synthetic** — a proposal or in-memory model, not installable gameplay.
- **Blocked / partial** — a clear safety or completeness gap prevents a valid build, release, or
  stronger claim.
- **External qualification required** — needs a matching build-12340 client, pinned or target core,
  disposable database, runtime, or downstream fork not available as an ordinary local test fixture.

## Repository map and leaf coverage

| Area / leaf | Present coverage | State and remaining work |
|---|---|---|
| `README.md` overview, quickstart, constraints, layout, chapter index | Project scope, tooling commands, trust boundary, and historical context | Locally maintained; quickstart uses Vault Terminal and must be treated as a handoff build, not gameplay install. Keep claims synchronized with CI and generated evidence. |
| `docs/01` data model | Client/server ownership, DBC, MPQ, caches, overlays | Documented; exact core/version behavior still needs source pin and local target verification. |
| `docs/02` integration matrix | Feature surfaces, patch needs, relevant tables | Documented decision aid; retain concrete traces and avoid claiming every combination is runtime-proven. |
| `docs/03` recipes | Reusable feature recipes | Documented; cross-link to example/spec and qualify each recipe before calling it deployable. |
| `docs/04` patch/release | Archive, parity, manifest, release and rollback | Tool-backed locally; actual client patch acceptance/launcher deployment remains external. |
| `docs/05` failure modes | Symptom/cause/fix guidance | Documented; grow from reproducible repo defects and clearly sourced field/core cases. |
| `docs/06` asset reuse | Existing-asset design patterns | Documented; individual stock IDs require local DBC/client provenance. |
| `docs/07` sources | Source-backed references and explicit unknowns | Documented; review source pins and links as relevant; do not treat a public source as proof for private forks. |
| `docs/08` integration mechanics | Boot/data loading, protocols, traces and consistency joints | Source analysis; runtime/client observations remain separate. |
| `docs/09` catalog guide | Catalog model and six layers | Backed by machine-readable catalog; keep prose/examples aligned with catalog schema. |
| `docs/10` novel alterations | Design patterns using existing surfaces | Design/documentation; per-feature playability is not implied. |
| `docs/11` testing ladder | Local test tiers and discovered defects | Documented; the current `make check` result is recorded below, not a permanent pass guarantee. |
| `docs/12` Foundry | Specs, emitters, packaging and qualification limits | Implemented with deliberate fail-closed boundaries; World DB qualification blocked pending durable row ownership and recovery. |
| `docs/13` Campaign Party adapter | Offline adapter contract and fake-host semantics | Implemented/synthetic at contract level; real downstream API/fork integration unqualified. |
| `docs/14` Azeroth Unbound | Source-backed world-scale feasibility | Design analysis; no integrated campaign implementation. |
| `docs/15` catalytic crafting | Server-authoritative design, hooks, first-slice contract | Design-only; hooks and negative-path qualification are not a playable feature. |
| `docs/16` Frontier Operations | Bounded mission contract and model | Design plus synthetic model; no installable in-game feature. |
| `docs/17` Vault Terminal client acceptance | Manual build-12340 client checklist | Procedure only; actual compatible client run not assessed here. |
| `docs/18` runtime handoff | Safe order for local server/client qualification | Procedure only; booted runtime and gameplay remain not assessed. |
| `docs/19` engineering handoff | Source-bound local evidence bundle | Generator exists; checked-in generated bundle may lag HEAD and must be regenerated before reuse. |
| `docs/20` stocktake | This area-by-area baseline | Present; update as coverage changes. |
| `docs/21` systematic plan | Ordered completion sequence and exit checks | Present; progress is tracked by follow-up edits and test evidence. |
| `catalog/alterations.json` | 110 entries, six layers, four patch kinds | Validator passes. Distribution: policy 15, content 32, mechanics 15, systems 8, presentation 15, novel 25; delivery kinds: none 90, DBC rows 10, client files 6, assets 4. Entries should retain effect, verification, notes, surfaces and documentation refs. Validator now rejects malformed JSON shapes/types and non-validation queries refuse invalid catalogs. The pre-audit snapshot had 24 entries without optional notes and 70 with one listed verification instruction; these counts are review queues, not automatic defects. All 110 catalog entries now include a limitation/evidence note. The policy, content and mechanics slices have all been reviewed for safer, more scoped verification prompts; systems and presentation notes now capture implementation dependencies and external acceptance boundaries. Thirty-six entries still have one listed verification instruction. Per-entry factual/source review remains in progress. |
| `tools/catalog.py`, `tests/test_catalog.py` | Validate, list/query/show/surfaces/matrix/stats and catalog tests | Locally checked. Audit per-entry coverage and query outputs as catalog evolves. |
| DBC tools: `tools/dbc_tool.py`, `tools/preflight.sh`, `tools/manifest.py`, `tools/selftest.py` | Inspect/validate DBCs, references, free IDs, parity, manifests, synthetic fixtures | Locally checked against generated fixtures; real client/server data and patch archives remain user-supplied qualification inputs. |
| Foundry schema/IDs/assets: `foundry/spec.py`, `ids.py`, `assets.py` | Spec checks, allocation and local asset inventory/provenance | Implemented; only locally supplied data can verify stock IDs. |
| Foundry generation: `generate.py`, `dbc_writer.py` | Supported outputs and DBC writer | Partial by design: unsupported feature surfaces must fail emitter coverage, not disappear from a package. Add emitters only with schema and round-trip tests. |
| Foundry package lifecycle: `package.py`, `candidate.py`, `maturity.py`, `qualification.py` | Deterministic packaging, verification, candidate export and evidence/maturity gates | Locally tested; release publication now shares the atomic no-replace directory guard with builds, so concurrent destination creation cannot overwrite operator data. A handoff candidate is not release authorization. |
| Foundry adapter: `adapter.py` | Offline admission, replay, parity and refusal semantics | Synthetic contract tests pass; real Campaign Party/Playerbot host integration requires the actual downstream fork. |
| DB qualifications: `db_qualification.py`, `tools/foundry_db_qualify.py`, `foundry/qualifications/` | Fail-closed World qualification and module evidence contracts | World SQL currently refuses qualification because no durable ownership journal exists. Characters proof is table/request scoped and does not establish full runtime/gameplay. |
| `specs/vault-terminal.json` | Server-fed interface spec | Validation passes locally. Build/compile/Characters evidence has bounded scope; client acceptance and live gameplay need separate runs. |
| `specs/frost-armory.json` | Foundry pipeline exercise with recipe declaration | Fresh validation fails emitter coverage. This is intentional fail-closed behavior; checked-in `packages/frost-armory/` is historical preview only, not a newly buildable/installable package. |
| `packages/frost-armory/` | Generated preview and evidence metadata | Historical artifact can lag source/HEAD. Inspect manifest and evidence before use; never override current emitter failure. |
| `examples/INDEX.md` and seven example directories | Ashen Vault, Fair-Weather Market, Reliquary, Ritual Gate, Season Dials, Two Cities, Vault Terminal | Structural contracts, SQL rollback/ID tests and model checks exist. No example is thereby proven against a live disposable AzerothCore DB or gameplay runtime; verify assets locally. |
| `designs/azeroth-unbound.json` | World-scale campaign design | Design only; no implementation claim. |
| `designs/catalytic-crafting.json` | Crafting design contract | Design only; hooks/transaction behavior need real core qualification. |
| `designs/frontier-operations.json` and `prototypes/frontier-operations/` | Mission design and in-memory state oracle | Synthetic only; do not package as game content. |
| `designs/argent-faction-operations-foundation.json`, `foundry/faction_ops.py` | Typed faction/reputation/hostility foundation | Design scaffolding; stock IDs unresolved and no playable accord. Preserve type separation and null provenance. |
| `tests/` | 26 unittest modules, helper fixtures; catalog, docs, tools, Foundry, designs, examples and hostile-input behavior | Latest `make check`: selftest passed; 324 tests ran, 1 skipped, all other tests passed. This validates repository behavior only. Add tests alongside every implementation change. |
| `.github/workflows/ci.yml` | Python/repository checks | CI evidence is revision-specific; inspect latest run before claiming current remote status. |
| `.github/workflows/azerothcore-compile.yml` | Pinned-core module compile | Compile scope only; not boot/runtime/gameplay or private fork compatibility. |
| `.github/workflows/azerothcore-db-qualification.yml` | Disposable Characters DB qualification | A previous run failed while parsing redirected validation output; no pass claim from that run. Diagnose command output and preserve exact revision-bound evidence before relying on it. |
| `handoff/arena-local-handoff-*` | Generated evidence packet | Snapshot may lag the current HEAD; regenerate with `tools/build_local_handoff.py` before distribution. |
| Root/tool/test conventions | Python stdlib test suite and Makefile | `make check` is the local regression gate; check CLI help and JSON/schema compatibility in relevant changes. |

## Verified workspace baseline for this stocktake

- `make check`: **PASS** — selftest passed and unittest discovery ran 324 tests (one skipped), including catalog hostile-shape, six-layer note coverage and release no-replace race regressions.
- `python tools/catalog.py validate`: **PASS** — 110 entries, six layers, four patch kinds.
- `python tools/foundry.py validate specs/vault-terminal.json`: **PASS** for declared local validation gates only.
- Vault Terminal `build` + `verify`: **PASS** — built 17 files / 36,569 bytes and verified 19 payload/metadata files. This proves package integrity only, not qualification or play.
- `python tools/foundry.py validate specs/frost-armory.json --allow-unverified`: **FAIL** at `emitter-coverage`, as expected for the unsupported recipe declaration. Asset verification was skipped; other listed static gates passed. Do not interpret this as an asset or release pass.
- `tools/build_local_handoff.py` smoke: **PASS** for its three declared checks (make check, historical package integrity, expected Frost fail-closed result); generated outside the repository in `/tmp`, so it did not refresh the checked-in handoff artifact.
- External evidence not newly assessed by these commands: real client build-12340 acceptance, World DB apply/rollback, full server boot/runtime/gameplay, Playerbot, and Campaign Party integration.

## Cross-cutting completeness rules

1. Every behavior claim should point to an implementation, source, test, or explicit external acceptance case.
2. Every generated artifact should be deterministic, manifest-bound and fail closed on missing/unsupported inputs.
3. Every persistent mutation needs collision, interruption, retry, ownership and rollback semantics; never infer safe rollback from an inverse-looking SQL statement.
4. Specs must account for every declared surface; unsupported declarations are errors, not silently omitted output.
5. Keep source-level, synthetic, compile, DB, client, runtime and gameplay evidence separate and revision-bound.
6. Never copy proprietary client data, secrets, production DB data, or unverified third-party assets into the repository.
