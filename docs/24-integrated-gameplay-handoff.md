# Integrated gameplay handoff bundle

Build the reproducible source bundle into a new directory outside the checkout:

```sh
python3 tools/build_gameplay_handoff.py --out /tmp/arena-gameplay-handoff
python3 /tmp/arena-gameplay-handoff/verify.py
```

The bundle contains the three maintained AzerothCore modules and addons, the generated Vault Terminal
module/addon/config, all additive Characters SQL migrations in a machine-readable order, Vault Terminal
source evidence, focused qualification tests, dependency checks, install and disable/rollback steps,
source/core hashes, and exact private Campaign Party/Playerbots/build-12340 acceptance requirements.
The builder refuses overwrite and paths inside the repository. It does not copy client files,
credentials, live DBs, or server binaries. `bundle.json` explicitly records that production readiness
and physical item custody are false and the combined boot/client gate is not assessed.

The public pinned workflow now additionally links the complete `worldserver` with the four modules
installed together and executes the binary's version command. This is an integrated compile/linkage
qualification only. A live boot still requires disposable World and Characters databases, the pinned
core's full data/maps/vmaps inputs and core configuration; representative gameplay and addon protocol
checks require a booted server and a legal build-12340 client. This sandbox has no CMake, container
runtime, MySQL/MariaDB, AzerothCore data bundle or WoW client, so it cannot claim those results.

See generated `INSTALL.md`, `ROLLBACK.md`, `DEPENDENCIES.md`, and `PRIVATE-QUALIFICATION.md` for
operator steps and the stop conditions. The package is for local disposable integration, not
production installation.
