# 19 — Source-bound engineering handoff bundle

This handoff is generated from the active checkout, not a remembered branch or an old PR. It is a local evidence inventory and next-step packet; it is **not** an installer, release archive, database migration, or gameplay claim.

## Current integrated campaign cycle (2026-11-17 UTC)

- Active branch: `arena/16f4e1cf-arena-ai`, based on merged PR #35 commit `eaf196711b8740b49a29209b03c765923bac4346`. Public AzerothCore pin: `6ffb62abb867dabb258de6813ff5d06d6b011543`.
- P0-01b: hosted MySQL 8.4 run `44422708326` passed for all seven generated tables, foreign collision atomic rollback, modified-row rollback refusal, abrupt child death after five table writes, same-request recovery/replay, and source/schema/code/receipt-bound v3 attestation. `foundry qualify` reports the World DB gate PASS for the synthetic fixture while the release remains BLOCKED. This is not a populated AzerothCore World DB proof: the fixture imports the seven exact DDLs, no base rows, and the initializer refuses any non-empty database.
- Portable Hole: PR #36's hosted run `44425827305` exposed an InnoDB deadlock in competing reservations. The current branch changes all three mutation paths to create/lock the balance row before receipt insertion and aligns the disposable harness. New tests cover same-request concurrency and lock-order invariants. Current branch CI passes: disposable MySQL 8.4 run `44429665849`, pinned-core compile `44429665907`, and Python CI `44429665913`. The ledger remains virtual and character-private. The read-only `BAG` query reports AzerothCore's server-derived carried count for one eligible commodity and excludes bank holdings. The addon has a ten-item per-character local pin list with deduplication/cap tests, plus `NEED` to keep the bag gap separate from available virtual balance (the latter is explicitly not craftable from this view). Pins are convenience state only, never server authority. `make check` passes with `NEED` included (363 tests, 22 skips); the system Python lacks optional Lua/MySQL dependencies, while the isolated Lua 5.1 suite passes all six addon/static behavior checks. Latest PR checks pass: disposable MySQL `44431756572`, pinned compile `44431756542`, and Python/Lua CI `44431756556`. These remain synthetic SQL/compile proofs, not a booted server or client test.
- No production DB, server boot, real client, private Campaign Party, profession reward integration, or live gameplay was tested. Exact pinned-core inspection found `Player::SaveToDB(CharacterDatabaseTransaction, ...)` can append inventory writes to a Characters transaction, but `Item`/inventory state is mutated while that transaction is assembled and `DirectCommitTransaction` has no success result for compensation. Thus physical deposit/withdraw is not safe to expose until failure recovery for in-memory and durable state is demonstrated. PR #37 is the current-branch continuation; earlier PR #36 remains open.
- Safety: no production changes or destructive SQL. World schema setup refuses non-empty schemas and never drops tables. Portable Hole validates stackable unlimited item templates and uses only the session-derived character GUID; GM/server credits are virtual and do not mutate inventory.

## Build a bundle

From the repository root, choose a new destination outside the checkout:

```sh
python3 tools/build_local_handoff.py --out /tmp/arena-local-handoff
```

The command refuses an existing destination and refuses paths inside the repository. By default it runs `make check`, verifies the checked-in Frost Armory preview package, and confirms the source Frost Armory spec fails validation because declared recipes have no executable emitter. `--skip-tests` records that checks were not assessed rather than making a success claim. The bundle contains the exact Git branch/HEAD/worktree state, changed-source SHA-256 inventory, command logs and exit results, evidence matrix, unresolved asset provenance, explicit world-row ownership ledger state, local-runtime checklists, Argent foundation model, and per-file manifest hashes.

Only this repository's source and documentation are copied. No DBC, MPQ, client, DB, account/character data, runtime binary, or credential is bundled. The output is marked `installable: false` and `release_authorized: false`.

## Current safety and evidence boundaries

- World operations have a transactional ownership ledger for explicit disposable InnoDB schemas, with collision refusal and full-row rollback guards. Hosted CI passed the exact seven-table fixture and v3 attestation through the database gate, but this does not import core rows, assess existing World-ID collisions on populated DBs, or authorize package release. The gate must remain scoped to exact per-package evidence; a full pinned World baseline and restored-database acceptance is still outstanding. No production writes are permitted.
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
