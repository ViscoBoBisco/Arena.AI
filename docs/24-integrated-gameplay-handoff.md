# Integrated gameplay handoff bundle

Use the repeatable, guarded local bootstrap to run the audit, repository checks, source bundle generation, prerequisite inventory, and (where tools exist) an isolated pinned-core build. It writes a readable qualification report and integrity-checked source handoff outside the checkout:

```sh
OUT="${TMPDIR:-/tmp}/arena-local-$(date -u +%Y%m%dT%H%M%SZ)"
test ! -e "$OUT"
python3 tools/local_gameplay.py qualify --workspace "$OUT"
cat "$OUT/QUALIFICATION.md"
```

For the source-only bundle builder directly:

```sh
python3 tools/build_gameplay_handoff.py --out /tmp/arena-gameplay-handoff
python3 /tmp/arena-gameplay-handoff/verify.py
```

See `docs/25-local-integration-bootstrap.md` for explicit disposable database creation, guarded test-client addon installation, bounded public boot attempt, disable and recovery commands.

The bundle contains the three maintained AzerothCore modules and addons, the generated Vault Terminal
module/addon/config, all additive Characters SQL migrations in a machine-readable order, source evidence,
focused qualification tests (including shared-project race/recovery, preparation planning, new Runic
powers and Argent Medic policy), dependency checks, install and disable/rollback steps, source/core
hashes, and exact private Campaign Party/Playerbots/build-12340 acceptance requirements.
The builder refuses overwrite and paths inside the repository. It does not copy client files,
credentials, live DBs, or server binaries. `bundle.json` explicitly records that production readiness
and physical item custody are false and the combined boot/client gate is not assessed.

The public pinned workflow links the complete `worldserver` with all four modules installed together,
executes the binary version command, and emits an evidence JSON containing the source commit, exact
AzerothCore pin, MySQL client/package versions, hashes of all four module trees, and the linked binary.
On a passing run it builds an integrity-checked handoff containing that evidence. A separate disposable
MySQL workflow records migration/ledger test evidence, including Portable Hole project reservations.
These qualify compilation/linkage and database contracts, not a live gameplay loop. A live boot still
requires verified maps/DBC/vmaps/mmaps inputs, world/auth/characters data and runtime configuration;
login and addon protocol checks require a booted server and legal build-12340 client. Those inputs are
not available in this workspace, so boot/login/live gameplay and private Campaign Party qualification
remain explicitly unassessed.

See generated `INSTALL.md`, `ROLLBACK.md`, `DEPENDENCIES.md`, and `PRIVATE-QUALIFICATION.md` for
operator steps and the stop conditions. The package is for local disposable integration, not
production installation.
