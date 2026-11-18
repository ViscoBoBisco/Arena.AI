# mod-vault-terminal

Generated AzerothCore server module for the [Vault Terminal example](../../README.md). The module consumes `VT1` addon frames, serializes each claim in a Characters-database transaction, records a request-keyed receipt, and sends the receipt for the submitted request rather than inferring success from a later counter read.

Install this directory under `modules/`, enable modules in CMake, and rebuild against the pinned AzerothCore revision in `foundry/core-pins.json`. `src/mod-vault-terminal_loader.cpp` exposes the `Addmod_vault_terminalScripts()` loader symbol and `src/mod-vault-terminal.h` declares the module registration entry point. Keep `AddonChannel = 1` in `worldserver.conf`.

The SQL migration is `../../sql/01_module_table.sql`. It creates the current counter and receipt tables. If it finds the previous per-day claim-table shape, it aggregates the old rows into the new per-character counter and renames the old table to `vault_terminal_claims_legacy_v1`; the old daily rows are retained. The migration is intended to be repeatable. The generated compile gate and disposable-MySQL qualification are separate checks and do not qualify a running server or live gameplay.
