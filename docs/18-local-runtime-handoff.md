# 18 — Local runtime handoff: Vault Terminal on a real server and client

This runbook is for the local agent/operator who has access to the actual AzerothCore tree, disposable databases, and a legal running WoW 3.3.5a client. It separates what this repository has already established from the runtime work that still needs a real environment. Use the existing [client acceptance checklist](17-vault-terminal-client-acceptance.md) as the detailed UI/protocol test sheet; this document orders the work, defines safe preconditions, and specifies the evidence to return.

## Handoff status and bounded target

- Repository source for this handoff: merge commit `24591313f5c0b59a0ab1b472f7c3b0bea2e432fd` (PR #28). The local runtime operator must record the checked-out SHA and whether there are any later reviewed changes before testing.
- Public AzerothCore source pin: `6ffb62abb867dabb258de6813ff5d06d6b011543`. This is the generated module's compile/DB schema pin, not a private Campaign Party or Playerbots-fork compatibility claim.
- Feature under runtime test: generated Vault Terminal v2 (`specs/vault-terminal.json`), with an addon folder, a server module, config, and a Characters migration. It does not require a client patch or custom DBC/MPQ data.
- Repository checks already run here: `make check` passed (310 tests; one environment-dependent test skipped); the Frontier Operations model suite passed (11 tests); the Vault Terminal model transcript ran; the Vault Terminal spec validated and a 19-file package built and verified.
- Merge-commit CI passed: Python CI run `44373371640`, pinned module compile run `44373371701`, and disposable DB qualification run `44373371554`. A later PR CI run (`44374875745`) also compiled the current generated module against the pinned core.
- The first local package build exposed a stale checked-in compile-qualification source hash: the compile itself had passed in CI, but fail-closed packaging therefore marked `ARENA-COMPILE` pending. That record was refreshed to the then-current `VaultTerminal.cpp` SHA-256 `6ab24c1c9b90530a330c8165b9a7dd9b75957fde49b9a9cffd56be764f91b998`, bound to run `44374875745`, and regression-tested against packaged sources. Subsequent source review found the handler accepted addon frames on non-WHISPER distributions despite the documented self-WHISPER-only protocol. The generator now consumes and rejects such frames; until this changed source passes pinned-core CI and the canonical record is refreshed, a package correctly reports `ARENA-COMPILE` pending. Neither compile result is a complete server build or boot.
- The source-to-handoff cross-check also caught an incomplete HELLO example: the actual client sends both protocol and addon versions. The operator checklist now states `VT1<TAB>HELLO<TAB>2<TAB>1.0.0`, and its regression test compares that against the generated addon and module parser.
- This workspace had no CMake, MySQL/MariaDB, AzerothCore checkout, WoW data, or game client. No server or client runtime test has been performed here.

The objective of this handoff is to produce trustworthy, redacted `PASS` / `FAIL` / `NOT_ASSESSED` results for Vault Terminal on an isolated server and build-12340 client. Do not expand the test to Faction Operations, Frontier Operations, Playerbots, or Campaign Party gameplay: no installable implementation for those features is included in this package.

## Safety gates — stop if any are missing

1. Use a disposable server instance and disposable World and Characters databases. Never migrate or test claims on production, shared-player, or the only copy of a server.
2. Before any schema or module change, record the target server's exact core/fork revision, module/build configuration, schema versions, and the existing server state. Take and verify restorable DB backups. If the target is a private Campaign Party / Playerbots fork, preserve it and test it separately from the public pin; do not replace it with upstream.
3. Inventory the existing `modules/`, enabled modules, configuration include/load conventions, database tables, and existing local changes. Do not overwrite unknown files or archives. Build/install into a dedicated disposable clone or a new build directory.
4. Keep credentials out of commands, logs, evidence, and chat. Use a permission-restricted DB client option file. Redact account/character identity in shareable reports; retain any unredacted GUID correlation only in private local notes.
5. If target revision, schema, table ownership, backup recovery, or module loading is ambiguous, stop and report `NOT_ASSESSED`. Do not guess IDs, bypass a failed migration, or “fix” an unknown MPQ.

## Ordered execution plan

### 1. Freeze and capture the test baseline

In the repository checkout under test:

```sh
git status --short
git rev-parse HEAD
git log -1 --format='%H %cs %s'
python3 --version
```

Record the clean/dirty state and exact source SHA. On the server side, independently record `git -C <core-source> rev-parse HEAD`, core/fork identity, CMake cache/module mode, compiler version, World/Characters schema version, and the actual client build from the login screen. Capture hashes of the exact spec and later generated package manifest. Do not label a private fork as the public source pin.

Make a database backup and demonstrate that it can be read/restored into a separate disposable database before proceeding. Snapshot the initial module/config files and the two table names used by this feature (`vault_terminal_claims`, `vault_terminal_claims_requests`). Preserve any existing rows and any legacy `vault_terminal_claims_legacy_v1` table.

### 2. Regenerate and verify the exact handoff bundle

Use a new, nonexistent output directory so the package builder cannot collide with or replace an earlier artifact:

```sh
OUT="/tmp/vault-terminal-$(date -u +%Y%m%dT%H%M%SZ)"
test ! -e "$OUT"
python3 tools/foundry.py validate specs/vault-terminal.json
python3 tools/foundry.py build specs/vault-terminal.json --out "$OUT"
python3 tools/foundry.py verify "$OUT"
sha256sum specs/vault-terminal.json "$OUT/manifest.json" "$OUT/evidence.json"
```

Record `OUT` and all hashes. Check the manifest before installing. The generated package should contain, at minimum:

- `server/mod-vault-terminal/` — generated module source and module config template;
- `conf/vault-terminal.conf` — exact key `VaultTerminal.Enable = 1`;
- `sql/characters/vault_terminal_claims.sql` — additive migration, including the request-receipt ledger and legacy daily-row migration path;
- `sql/characters/99_vault-terminal_rollback.sql` — intentionally non-destructive; it retains player state;
- `addon/VaultTerminal/` — addon `.toc` and Lua, with `## Interface: 30300`;
- `protocol/vault-terminal.json` and `evidence.json`.

Do not use the obsolete key `vault_terminal.Enable`. Follow the target checkout's established module-config loading convention and verify from startup behavior that `VaultTerminal.Enable` was actually read; file presence alone is not proof. This feature depends on core addon transport being enabled (`AddonChannel = 1`).

### 3. Qualify the target source/build without disturbing it

Test the public pin in its own clean source/build tree when available. Test a private fork only in a separate tree and report the fork SHA separately. Copy the generated module into the disposable core's `modules/` path; never copy over an unknown pre-existing module. Configure a dedicated build directory using the target's documented build options. The CI compile job's CMake baseline is available in `.github/workflows/azerothcore-compile.yml` (`MODULES=static`, tools disabled, and PCH disabled); it is a targeted translation-unit/linkage gate, not a recipe for claiming a complete server build.

For runtime qualification, build the actual `worldserver` (and `authserver` if the disposable environment needs it) from the target checkout, inspect the build output for errors, and record the executable/build identity. Verify the module is included by the target loader, starts without registration/config errors, and can be disabled and re-enabled through the target's real config path. Do not infer successful module loading from compilation alone. If enabling the module requires a local source patch or a different core API, retain that patch and classify public-pin and private-fork results independently.

### 4. Exercise database migration only on disposable Characters data

Create a separate disposable Characters database initialized from the target server's matching baseline/schema. Capture the table inventory before applying anything. Apply the exact generated `sql/characters/vault_terminal_claims.sql` using the operator's normal MySQL client and protected credentials; record client/server versions, exit status, and sanitized output. Inspect resulting table engines, columns, keys, and indexes. Confirm both counter and receipt tables are InnoDB and keyed as the generated migration expects.

Run the migration a second time and verify it is harmless. For legacy-upgrade coverage, use a separate synthetic disposable schema containing the old per-day `vault_terminal_claims` shape and representative synthetic rows. Verify the current aggregate count/latest day are correct and the old rows survive in `vault_terminal_claims_legacy_v1`. Never improvise this on a real character database. Capture before/after schema and row counts; do not include real identities in the report.

Rollback in this feature means **disable the module and remove the addon**, while retaining `vault_terminal_claims`, `vault_terminal_claims_requests`, and any legacy backup. The generated rollback SQL deliberately does not drop player data. If a disposable database must be reset, restore/drop only that disposable database from its recorded baseline as a separate operator action; do not treat destructive table deletion as feature rollback.

### 5. Install and boot in the isolated runtime

Install only the verified package outputs into the disposable server and client locations, following the target's module/config conventions. Enable `AddonChannel = 1`. Apply only the Characters migration in this step; no World SQL or NPC is needed for the generated Vault Terminal feature. Make sure the chosen test character and database are disposable.

Boot/restart the `worldserver`. Record startup logs proving the module registered and its config state was loaded, plus any addon-channel errors. Log in with the build-12340 client and confirm the test client is using the addon folder from this exact package. Do not proceed if startup/module/config state is uncertain.

### 6. Run protocol and persistence checks

Use `docs/17-vault-terminal-client-acceptance.md` as the authoritative itemized checklist. At minimum, run these distinct paths and retain before/after SQL observations:

| Case | Required observation |
|---|---|
| `/vt`, `/vaultterminal`, close/reopen, reload | One reusable panel/handler; fresh server snapshot; no Lua errors; Interface 30300 addon enabled. |
| One claim | One matching request ID receives `GRANTED`; counter increments once; durable row in `vault_terminal_claims_requests` has the same status/ID/result; addon displays the subsequent server snapshot. |
| Same request ID replay after response loss/reopen | Original receipt returned; no second increment. Use a controlled test seam/harness for replay; do not mint a replacement ID just to make the test pass. |
| Distinct same-day request | `DENIED_COOLDOWN` receipt attributed to that request; counter unchanged. |
| Concurrent distinct requests for one character/day | Controlled two-request race: at most one `GRANTED`, each response/receipt attributed to its own request, at most one counter increment. A single-client UI click is not a concurrency test. |
| Restart/relog | Counter, rank, cooldown, and old receipt agree with retained Characters rows. |
| Feature disabled | `HELLO` receives the expected `BYE`; no state mutation. Re-enable and confirm a fresh snapshot. |
| Timeout / server unavailable | UI shows no actionable stale state, reports no response, and refresh recovers after service returns. |
| Hostile frames | Wrong prefix/sender/distribution/version, malformed/non-ASCII fields, unknown opcode, and over-limit frames do not mutate either table. Use a controlled harness, never production. |
| Target/day boundary and migration | Target cannot be exceeded; server-defined day transition permits at most one new grant; migration reapply/legacy preservation checks pass. |

For a sanitized DB observation, use the actual disposable GUID privately, then redact it from shared output:

```sql
SELECT guid, total_claims, last_claim_day, last_request_id
FROM vault_terminal_claims WHERE guid = <DISPOSABLE_GUID>;

SELECT request_id, status, total_claims, claim_day, created_at
FROM vault_terminal_claims_requests
WHERE guid = <DISPOSABLE_GUID>
ORDER BY created_at, request_id;
```

A single UI path cannot prove request replay, a database lock/race, or hostile packet behavior. If no controlled way to issue those tests against the live module exists, explicitly mark only those cases `NOT_ASSESSED`; do not infer them from Python-model or disposable-MySQL results.

### 7. Return a provenance-bound evidence packet

Keep raw evidence in a private, access-controlled local directory, not in this repository. Return a sanitized summary with:

- repository SHA and package manifest/spec SHA-256;
- core repository/fork name and exact commit; build system, compiler, generated target and result;
- client build (must be `12340`), addon Interface value, test date/time (UTC), and operator;
- World/Characters schema identities and MySQL version; backup and restore verification status;
- module/config load evidence; migration/reapply/legacy/retention outcomes;
- per-case `PASS`, `FAIL`, or `NOT_ASSESSED`, including request IDs only if sanitized and useful, before/after table counts, and relevant redacted logs/screenshots;
- known deviations, failed tests, untested paths, and exact blockers.

Do not attach credentials, account names, unredacted character GUIDs, proprietary client data, or an MPQ. Do not commit local runtime artifacts. A successful public-pinned run and a successful private fork run are separate evidence records. Client, server, Playerbot, and Campaign Party claims stay separate; nothing here upgrades the latter two without their own live test.

## Acceptance / stop criteria

The handoff is complete only when the evidence packet distinguishes every tested case and preserves raw provenance. A `PASS` means the named observation was performed against the stated actual source/database/client—not merely that a local test, model, CI compile, or generated package passed. If a case cannot be run, return it as `NOT_ASSESSED` with the concrete blocker. Any data mismatch, unexplained mutation, module crash, cross-character effect, or unexpected global faction/PvP behavior is an immediate stop; preserve the disposable state and logs rather than repeatedly retrying.

After this runtime report, the next implementation-planning gate is a local legal-client asset audit and source/schema ownership decision for the Argent Faction Operations pilot. No faction IDs/assets, accord mechanics, squad templates, or Campaign Party capabilities should be inferred from the Vault Terminal run.
