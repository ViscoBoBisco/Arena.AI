# 19 — Source-bound engineering handoff bundle

This handoff is generated from the active checkout, not a remembered branch or an old PR. It is a local evidence inventory and next-step packet; it is **not** an installer, release archive, database migration, or gameplay claim.

## Build a bundle

From the repository root, choose a new destination outside the checkout:

```sh
python3 tools/build_local_handoff.py --out /tmp/arena-local-handoff
```

The command refuses an existing destination and refuses paths inside the repository. By default it runs `make check`, verifies the checked-in Frost Armory preview package, and confirms the source Frost Armory spec fails validation because declared recipes have no executable emitter. `--skip-tests` records that checks were not assessed rather than making a success claim. The bundle contains the exact Git branch/HEAD/worktree state, changed-source SHA-256 inventory, command logs and exit results, evidence matrix, unresolved asset provenance, explicit world-row ownership ledger state, local-runtime checklists, Argent foundation model, and per-file manifest hashes.

Only this repository's source and documentation are copied. No DBC, MPQ, client, DB, account/character data, runtime binary, or credential is bundled. The output is marked `installable: false` and `release_authorized: false`.

## Current safety and evidence boundaries

- World migrations now emit plain `INSERT` statements and never delete/replace pre-existing rows. A collision errors; migrations are not re-runnable. No durable World-row ownership ledger exists, so generated rollback deliberately raises an error and the World DB qualifier refuses before any database query. Partial application recovery and World `ARENA-DB` evidence remain blocked.
- Any spec declaring `gameobjects`, `recipes`, unsupported DBC tables, or `Spell.dbc` rows fails emitter coverage instead of silently shipping incomplete artifacts. Frost Armory's checked-in spec remains a design/preview input and cannot be freshly packaged until crafting is executable or the declaration is explicitly removed from that feature.
- `designs/argent-faction-operations-foundation.json` is typed design scaffolding only. `Faction.dbc` reputation and `FactionTemplate.dbc` hostility refs have distinct types and remain explicitly unresolved (`null`). It does not globally rewrite hostility, grant NPCs player-group membership, or implement an accord.
- The public AzerothCore pin is a compatibility reference only. It does not qualify private Campaign Party/Playerbot forks. Booted server, client 3.3.5a, Playerbot, and Campaign Party evidence must each be collected independently.

## Local-only acceptance, when resources are available

1. Record exact repo, core/fork, compiler, DB schema and client build identities; keep public-pin and private-fork runs separate.
2. Use a fresh disposable server and World/Characters databases; prove backup restoration before any migration. Do not use production, populated shared DBs, or unknown client archives.
3. Resolve stock IDs only from legally supplied local DBC data; record table name, row ID and file hash. `Faction.dbc` and `FactionTemplate.dbc` are not interchangeable.
4. For the World SQL redesign, first implement a durable ownership journal with fail-closed collision, interruption, and rollback semantics. Verify supported storage engines and MySQL/MariaDB behavior; do not claim DDL atomicity. Until then, do not apply World migrations and do not run the refusal guard expecting data cleanup.
5. Qualify table/schema correctness against the exact pinned source and actual disposable DB. Keep synthetic unit tests distinct from DB qualification.
6. Only after source/build/module/config qualification, boot a disposable server. Then test a legal build-12340 client separately. Playerbot and Campaign Party tests need their real target forks and APIs.
7. Return redacted command outputs, hashes, schema/build identities, per-case PASS/FAIL/NOT_ASSESSED, blockers, and exact source revisions. Keep credentials and proprietary client data local.

See `docs/17-vault-terminal-client-acceptance.md` and `docs/18-local-runtime-handoff.md` for the bounded Vault Terminal protocol/client and local runtime procedures.
