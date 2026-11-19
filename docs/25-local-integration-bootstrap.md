# Four-module local integration bootstrap and acceptance

This is the repository-checkout entry point for the merged four-module package. `tools/local_gameplay.py` depends on the full repository (`make`, generators, tests, pinned-core metadata), so it is **not standalone** and must be run from a clean checkout at the revision being qualified. The generated handoff is separately verifiable with its standalone `verify.py` and dependency checker. It creates a fresh workspace outside this checkout, audits the package, runs repository checks, generates and verifies a source bundle, and—when prerequisites exist—clones the exact public AzerothCore pin into that workspace, compiles all four modules together, links `worldserver`, and records hashes and logs. It never copies a module into an existing Campaign Party/private checkout.

## One-command qualification

**Local agent's first command:** from the exact source checkout, run the read-only preflight before choosing a build or touching local assets:

```sh
python3 tools/local_gameplay.py preflight
```

Review `mysql_client_api.status` and its Ubuntu guidance; do not begin the pinned build unless the compile/link probe passes. From a clean checkout, choose a new path outside the repository. The default run is read-only with respect to databases and client files; build and runtime steps are gated by prerequisites and explicit options. Before any expensive core build, the preflight verifies Oracle MySQL 8.4 by version and by compiling/linking the exact required API probe; an installed MariaDB development package is rejected.

```sh
set -eu
OUT="${TMPDIR:-/tmp}/arena-local-$(date -u +%Y%m%dT%H%M%SZ)"
test ! -e "$OUT"
python3 tools/local_gameplay.py qualify --workspace "$OUT"
cat "$OUT/QUALIFICATION.md"
```

A retry can reuse the marked workspace; it verifies the checkout snapshot, core pin, bundle files, CI evidence, isolated core, module trees, and any cached executable before resuming. A mismatch exits unsuccessfully instead of silently rebuilding over it:

```sh
python3 tools/local_gameplay.py qualify --workspace "$OUT" --resume
```

To transfer exact-revision hosted evidence, download the integration artifact for the same `HEAD` and pass its JSON file. The builder checks the evidence schema, exact source/core revisions, pinned MySQL API record, all four generated module-tree hashes, linked binary hash, and version command; the final `qualification.json` includes evidence, bundle/migration hashes, gate results, and known limits.

```sh
HEAD="$(git rev-parse HEAD)"
CI_DIR="${TMPDIR:-/tmp}/arena-ci-$HEAD"
mkdir -p "$CI_DIR"
gh run download "$CI_RUN_ID" --name "arena-four-module-integration-$HEAD" --dir "$CI_DIR"
OUT="${TMPDIR:-/tmp}/arena-local-$HEAD"
python3 tools/local_gameplay.py qualify --workspace "$OUT" \
  --ci-evidence "$CI_DIR/integration-evidence.json"
```

`qualify` runs `make check`, audits package/source/migrations/register, creates and self-verifies `handoff/`, and records a readable report, JSON evidence, logs and ZIP. It attempts the pinned build only when `git`, CMake, C++, and the MySQL 8.4 API probe pass. Use `--require-build` when the local build itself is a required gate; otherwise missing local build prerequisites are `NOT_ASSESSED`, not failures. Any operation explicitly requested with database/client/boot flags that is blocked or fails returns nonzero. CMake then checks Boost, OpenSSL, and remaining upstream libraries. The pin is `foundry/core-pins.json`; the clone is isolated and module paths are hash-checked on resume. Build flags match `.github/workflows/azerothcore-compile.yml`.

Read-only inventory can be run independently:

```sh
python3 tools/local_gameplay.py preflight --data-dir /path/to/server/data --test-client /path/to/disposable/client
python3 tools/local_gameplay.py audit
```

The preflight output distinguishes compiler/build tools, MySQL client/development tools and API status, server `dbc`, `maps`, `vmaps`, `mmaps`, and a client executable. Client version is deliberately reported as operator-verified; executable presence is not proof of build 12340. On Ubuntu 24.04 amd64, install the CI-compatible client by configuring Oracle's `noble mysql-8.4-lts` repo and installing `libmysqlclient-dev=8.4.13-1ubuntu24.04`; when the probe is missing/incompatible, its `mysql_client_api.install_guidance` field contains the exact key/repository/apt commands and remaining CMake dependencies. Rerun preflight; do not proceed if the compile/link probe is not PASS.

## Explicit disposable database migration path

The module migration gate is opt-in. It creates a new, UUID-named schema with the `arena_disposable_` prefix on **localhost only**, refuses collisions (no `IF NOT EXISTS`), writes a run/core provenance marker, and applies exactly the ordered migration list in `handoff/bundle.json`. It then creates a SQL dump backup. The schema contains module migration tables only; it is not a complete AzerothCore Characters database and must not be used to claim boot/runtime qualification.

The migration path uses the MySQL/MariaDB command-line client and does not require PyMySQL; PyMySQL is optional for separate DB-API tests. Set local disposable-server credentials without placing secrets in command arguments or logs:

```sh
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

This integration workspace does not provide CMake, Oracle MySQL 8.4 development headers/client, a local disposable DB, full pinned-core source, server DBC/maps/vmaps/mmaps, or a build-12340 client. A local `c++` alone cannot qualify a core build. The script's preflight reports the MySQL API probe and all other prerequisites before any expensive build; optional missing gates remain `NOT_ASSESSED`, while failed explicitly requested operations return nonzero. Do not claim a boot/client pass from CI build or database tests. Private Campaign Party/Playerbots qualification and exact-client interaction remain local-only.

Physical item custody remains disabled. No bootstrap operation mutates live inventory or materializes virtual balances. Recovery means restoring owned client/config backups or restoring a verified SQL dump to a **new** disposable schema, never destructive rollback of player data.
