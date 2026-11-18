# 19 — Source-bound engineering handoff bundle

This handoff is generated from the active checkout, not a remembered branch or an old PR. It is a local evidence inventory and next-step packet; it is **not** an installer, release archive, database migration, or gameplay claim.

## Current P0-01b continuation (2026-11-17 UTC)

- Active branch: `arena/c787bf02-arena-ai`; base/source HEAD: `fa56dea97a2e4eb4b4db50869b45fb02376ea3d3` (PR #34 merge). Pinned public AzerothCore source: `6ffb62abb867dabb258de6813ff5d06d6b011543`.
- This cycle extends the prior item-only crash fixture to all seven generated tables, a late foreign collision, full-row edit rollback refusal, and abrupt worker death after the vendor insert with five tables already mutated in the uncommitted transaction. It adds an owned-world qualifier and v3 attestation binding the package, pinned source hashes, observed schema, source-code hashes, recovery proof, and durable operation receipts; CI then runs `foundry qualify` and checks that only the World DB gate passes while release remains blocked.
- Local evidence: `make check` passed (351 tests, 14 skips); all four MySQL integration tests are skipped locally because no disposable MySQL service is available. Workflow YAML and inline shell syntax pass. Hosted MySQL execution of this expanded integration/attestation pipeline is pending.
- Status remains `SYNTHETIC`; do not promote the World gate until hosted CI proves the expanded acceptance end to end. No base data, production DB, server boot, client, private Campaign Party, gameplay, or live qualification was tested.
- Safety: no production changes or destructive SQL. Exact-schema initialization refuses non-empty schemas and never drops tables. The fixture writes only to an explicitly named disposable schema; any partial DDL failure requires discarding that disposable schema. Rollback remains ledger-bound and refuses changed or foreign rows.

## Build a bundle

From the repository root, choose a new destination outside the checkout:

```sh
python3 tools/build_local_handoff.py --out /tmp/arena-local-handoff
```

The command refuses an existing destination and refuses paths inside the repository. By default it runs `make check`, verifies the checked-in Frost Armory preview package, and confirms the source Frost Armory spec fails validation because declared recipes have no executable emitter. `--skip-tests` records that checks were not assessed rather than making a success claim. The bundle contains the exact Git branch/HEAD/worktree state, changed-source SHA-256 inventory, command logs and exit results, evidence matrix, unresolved asset provenance, explicit world-row ownership ledger state, local-runtime checklists, Argent foundation model, and per-file manifest hashes.

Only this repository's source and documentation are copied. No DBC, MPQ, client, DB, account/character data, runtime binary, or credential is bundled. The output is marked `installable: false` and `release_authorized: false`.

## Current safety and evidence boundaries

- World operations have a transactional ownership ledger for explicit disposable InnoDB schemas, with collision refusal and full-row rollback guards. Earlier hosted CI covered exact pinned DDL plus item-only process recovery; the expanded seven-table apply/collision/crash/restart/receipt-attestation run is pending. Even when green, this evidence imports no base data and qualifies only the explicitly disposable fixture, not a populated World database or release. No production writes are permitted.
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
