# 19 — Source-bound engineering handoff bundle

This handoff is generated from the active checkout, not a remembered branch or an old PR. It is a local evidence inventory and next-step packet; it is **not** an installer, release archive, database migration, or gameplay claim.

## Current P0-01b continuation (2026-11-17 UTC)

- Active branch: `arena/64ea3704-arena-ai`; base/source HEAD before this working diff: `6f7c369b11cdd7bd4e1bdec72f9d87af120e27bd` (merged PR #33). Pinned public AzerothCore source: `6ffb62abb867dabb258de6813ff5d06d6b011543`.
- This change adds an empty-disposable-schema initializer which hashes and extracts the seven exact pinned World table DDLs, checks runtime InnoDB engines and the generator's primary-key identities, plus an opt-in integration test that abruptly terminates a subprocess after its first item insert and retries after MySQL recovers the transaction.
- Local evidence: `make check` passed (349 tests, 12 skips) before the final documentation/register-only edits; focused schema/register tests passed (7 tests, with the two MySQL integration tests skipped because no local disposable MySQL service was configured); pinned upstream sparse-checkout/revision/source-hash and DDL extraction checks passed. Hosted disposable-MySQL execution is still pending.
- Status: `SYNTHETIC` only; the World `ARENA-DB` gate remains blocked until hosted exact-schema and process-recovery checks pass, all supported generated tables are exercised, and a durable source/schema/receipt-bound qualification attestation is implemented. No production DB, populated base data, server boot, client, private Campaign Party, gameplay, or live qualification was tested.
- Safety: no production changes or destructive SQL; initialization refuses a non-empty schema and never drops tables. The CI fixture writes only to a new `arena_foundry_disposable_*` database. Rollback remains constrained by the ownership ledger and fail-closed full-row checks.

## Build a bundle

From the repository root, choose a new destination outside the checkout:

```sh
python3 tools/build_local_handoff.py --out /tmp/arena-local-handoff
```

The command refuses an existing destination and refuses paths inside the repository. By default it runs `make check`, verifies the checked-in Frost Armory preview package, and confirms the source Frost Armory spec fails validation because declared recipes have no executable emitter. `--skip-tests` records that checks were not assessed rather than making a success claim. The bundle contains the exact Git branch/HEAD/worktree state, changed-source SHA-256 inventory, command logs and exit results, evidence matrix, unresolved asset provenance, explicit world-row ownership ledger state, local-runtime checklists, Argent foundation model, and per-file manifest hashes.

Only this repository's source and documentation are copied. No DBC, MPQ, client, DB, account/character data, runtime binary, or credential is bundled. The output is marked `installable: false` and `release_authorized: false`.

## Current safety and evidence boundaries

- World operations have a transactional ownership ledger for explicit disposable InnoDB schemas, with collision refusal and full-row rollback guards. Existing CI covers synthetic `item_template` DDL only. A new CI path fetches the exact pinned AzerothCore commit, initializes the seven generated-table DDLs in a fresh empty disposable schema, records source/DDL/runtime-key hashes, and kills a worker process after its first item insert to exercise MySQL rollback and same-request restart recovery. This path is not qualified until its hosted MySQL run passes; it does not import base data, cover every generated table, create a durable qualification attestation, or lift the World `ARENA-DB` gate. No production writes are permitted.
- Any spec declaring `gameobjects`, `recipes`, unsupported DBC tables, or `Spell.dbc` rows fails emitter coverage instead of silently shipping incomplete artifacts. Frost Armory's checked-in spec remains a design/preview input and cannot be freshly packaged until crafting is executable or the declaration is explicitly removed from that feature.
- `designs/argent-faction-operations-foundation.json` is typed design scaffolding only. `Faction.dbc` reputation and `FactionTemplate.dbc` hostility refs have distinct types and remain explicitly unresolved (`null`). It does not globally rewrite hostility, grant NPCs player-group membership, or implement an accord.
- The public AzerothCore pin is a compatibility reference only. It does not qualify private Campaign Party/Playerbot forks. Booted server, client 3.3.5a, Playerbot, and Campaign Party evidence must each be collected independently.

## Local-only acceptance, when resources are available

1. Record exact repo, core/fork, compiler, DB schema and client build identities; keep public-pin and private-fork runs separate.
2. Use a fresh disposable server and World/Characters databases; prove backup restoration before any migration. Do not use production, populated shared DBs, or unknown client archives.
3. Resolve stock IDs only from legally supplied local DBC data; record table name, row ID and file hash. `Faction.dbc` and `FactionTemplate.dbc` are not interchangeable.
4. For World SQL, retain the durable ownership journal and fail-closed collision/rollback rules; do not claim DDL atomicity. Run the exact-pinned-schema disposable integration and abrupt process-death test, then extend it to every supported generated table and the supported server versions. Do not use production, populated shared DBs, or the refusal guard as a cleanup tool.
5. Bind any eventual qualification attestation to the source revision, source/DDL/runtime-schema hashes, operation receipt and recovery checks. Until those checks pass, the World `ARENA-DB` gate remains blocked; synthetic fixtures are not database qualification.
6. Only after source/build/module/config qualification, boot a disposable server. Then test a legal build-12340 client separately. Playerbot and Campaign Party tests need their real target forks and APIs.
7. Return redacted command outputs, hashes, schema/build identities, per-case PASS/FAIL/NOT_ASSESSED, blockers, and exact source revisions. Keep credentials and proprietary client data local.

See `docs/17-vault-terminal-client-acceptance.md` and `docs/18-local-runtime-handoff.md` for the bounded Vault Terminal protocol/client and local runtime procedures.
