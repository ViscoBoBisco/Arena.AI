# Four-module local integration bootstrap and acceptance

This is the single supported local entry point for the merged four-module package. It creates a fresh workspace outside this checkout, audits the package, runs repository checks, generates and verifies a source bundle, and—when prerequisites exist—clones the exact public AzerothCore pin into that workspace, compiles all four modules together, links `worldserver`, and records hashes and logs. It never copies a module into an existing Campaign Party/private checkout.

## One-command qualification

From a clean checkout, choose a new path outside the repository. The default run is read-only with respect to databases and client files; build and runtime steps are gated by prerequisites and explicit options.

```sh
set -eu
OUT="${TMPDIR:-/tmp}/arena-local-$(date -u +%Y%m%dT%H%M%SZ)"
test ! -e "$OUT"
python3 tools/local_gameplay.py qualify --workspace "$OUT"
cat "$OUT/QUALIFICATION.md"
```

`qualify` runs `make check`, a package/source/migration/feature-register audit, creates and self-verifies `handoff/`, and writes `qualification.json`, readable `QUALIFICATION.md`, per-step logs, a ZIP of the generated handoff, and (if compilation completes) `local-build-evidence.json`. It attempts the full pinned build only if `git`, CMake, and a C++ compiler are present. CMake's configure step is authoritative for Boost, Oracle MySQL development headers, OpenSSL, and the remaining upstream libraries. The core pin is `foundry/core-pins.json`; the script clones the public core into a new, tool-owned workspace and refuses to overwrite module paths. Build flags match `.github/workflows/azerothcore-compile.yml`.

Read-only inventory can be run independently:

```sh
python3 tools/local_gameplay.py preflight --data-dir /path/to/server/data --test-client /path/to/disposable/client
python3 tools/local_gameplay.py audit
```

The preflight output distinguishes compiler/build tools, MySQL client/development tools, server `dbc`, `maps`, `vmaps`, `mmaps`, and a client executable. Client version is deliberately reported as operator-verified; executable presence is not proof of build 12340.

## Explicit disposable database migration path

The module migration gate is opt-in. It creates a new, UUID-named schema with the `arena_disposable_` prefix on **localhost only**, refuses collisions (no `IF NOT EXISTS`), writes a run/core provenance marker, and applies exactly the ordered migration list in `handoff/bundle.json`. It then creates a SQL dump backup. The schema contains module migration tables only; it is not a complete AzerothCore Characters database and must not be used to claim boot/runtime qualification.

Install the optional connector and set local disposable-server credentials without placing secrets in command arguments or logs:

```sh
python3 -m pip install -r requirements-world-qualification.txt
export ARENA_DISPOSABLE_MYSQL_HOST=127.0.0.1
export ARENA_DISPOSABLE_MYSQL_PORT=3306
export ARENA_DISPOSABLE_MYSQL_USER=arena_test_admin
read -rsp 'Disposable MySQL password: ' ARENA_DISPOSABLE_MYSQL_PASSWORD; export ARENA_DISPOSABLE_MYSQL_PASSWORD; echo
OUT="${TMPDIR:-/tmp}/arena-local-$(date -u +%Y%m%dT%H%M%SZ)"
python3 tools/local_gameplay.py qualify --workspace "$OUT" --create-disposable-db
unset ARENA_DISPOSABLE_MYSQL_PASSWORD
```

The account must be able to create new databases and apply schema DDL. The tool never drops schemas. On failure it retains any newly created schema and reports its name for inspection. `recover-db` restores the saved dump to another new `arena_disposable_*` database; the original is preserved:

```sh
python3 tools/local_gameplay.py recover-db --workspace "$OUT" --restore-to-new-disposable-db
```

Do not use production or shared database credentials. To boot AzerothCore, separately initialize complete disposable auth/world/characters schemas from the pinned core's own SQL. The three DB names in the copied runtime config must all use the local host and `arena_disposable_` prefix. The migration-only schema above does not meet that requirement by itself.

## Optional bounded public-server boot

Only request a boot after building the isolated public core and preparing a **separate** data directory with DBC, maps, vmaps, and mmaps, plus complete disposable auth/world/characters databases. Supply a config path; the script copies it into the isolated clone with mode 0600 (the original is read-only), requires its `DataDir` to match the explicit data path, refuses non-local database hosts and schema names without the disposable prefix, runs the generated `worldserver` for a bounded interval, waits for the `World initialized` marker, then stops only that child process and retains its log.

```sh
OUT="${TMPDIR:-/tmp}/arena-local-$(date -u +%Y%m%dT%H%M%SZ)"
python3 tools/local_gameplay.py qualify --workspace "$OUT" \
  --data-dir /path/to/pinned/acore-data \
  --runtime-config /path/to/a/redacted-local-disposable-worldserver.conf \
  --attempt-public-boot --boot-timeout 90
```

Boot is `PASS` only when the real pinned binary reaches the ready marker. Compile, module translation-unit linkage, SQL migrations, or addon syntax checks never promote boot. Even a server boot does not prove a character request path; exercising the four addon protocols and native game effects still requires a real test character and build-12340 client. Keep redacted logs and mark any non-exercised request `NOT_ASSESSED`.

## Test-client addon install, backup, disable, recovery

The script does not write to any client unless the operator supplies a designated test directory, attests build 12340, and passes both explicit write flags. Beforehand, create `ARENA_TEST_CLIENT.txt` in that disposable client root with this exact line:

```text
Arena.AI disposable TEST CLIENT; addon writes explicitly authorized.
```

Then include all of these with the main `qualify` command:

```sh
--test-client /path/to/disposable/build-12340-client \
--client-build 12340 --install-addons --confirm-test-client-write
```

Before replacing each of the four addon directories, the tool creates a hash-verified backup in the external workspace, stages all four deliveries, verifies them, and then swaps them in. If the client is changed afterwards, recovery refuses to overwrite those edits. To restore the pre-install addon state, including any addon folder that existed before the run:

```sh
python3 tools/local_gameplay.py recover-client --workspace "$OUT" \
  --test-client /path/to/disposable/build-12340-client --confirm-test-client-write
```

The new Arena copies are quarantined in the workspace rather than deleted. The script writes no client executable, configuration, cache, SavedVariables, or Campaign Party files.

Four master switches are provided for an isolated server config: `PortableHole.Enable`, `RunicCodex.Enable`, `ArgentCommission.Enable`, and `VaultTerminal.Enable`. To disable all four without deleting data, after qualification run:

```sh
python3 tools/local_gameplay.py disable-modules --workspace "$OUT" \
  --runtime-config "$OUT/core/arena-local-worldserver.conf" \
  --confirm-isolated-config-write
```

The command backs up and hashes the exact isolated config before setting all four keys to `0`. To restore only if no operator edits changed the disabled config:

```sh
python3 tools/local_gameplay.py restore-modules --workspace "$OUT" \
  --confirm-isolated-config-write
```

Never run these operations against a Campaign Party installation. Keep any private fork in a separate untouched directory and manually port only after reviewing its exact revision and local conventions.

## Current workspace findings and stop gates

The recorded merge baseline `850cae42040ac31b9fdcd9f15d0fe91d94bf7f6b` has successful hosted Python, pinned four-module compile/link, and disposable Characters-schema CI runs (`44596610699`, `44596610718`, `44596610873`). Those results apply to that merged commit, not to subsequent uncommitted edits in this worktree. In this Arena workspace, CMake, the SQL client/server, a real pinned-core checkout, disposable DB, server DBC/maps/vmaps/mmaps, and a build-12340 client are not available. The local `c++` alone is insufficient to establish compile/link. Thus a fresh build, public boot, database migration, and live addon requests cannot be qualified here. Run the one-command procedure on a suitable host; the script records gates as `MISSING`, `BLOCKED`, or `NOT_ASSESSED` and never manufactures a runtime `PASS`. Private Campaign Party/Playerbots validation and exact-client interaction remain local-only.

Physical item custody remains disabled. No bootstrap operation mutates live inventory or materializes virtual balances. Recovery means restoring owned client/config backups or restoring a verified SQL dump to a **new** disposable schema, never destructive rollback of player data.
