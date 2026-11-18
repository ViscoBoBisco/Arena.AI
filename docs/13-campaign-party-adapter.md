# 13 — Campaign Party adapter contract v1.2

Campaign Party is a separate project; Arena does not assume access to its source or invent its implementation details. This versioned boundary is an offline admission/dry-run contract, not a gameplay control plane. AzerothCore remains authoritative for persistent gameplay, Playerbots owns bot execution, Campaign Party owns account/member selection, permissions and command routing, and addon code is presentation/player intent only.

## Contract surface

The executable Python contract is `foundry/adapter.py`; `FakeHost` is the independently testable reference host. Contract version is `1.2.0`; v1.2 adds a byte-derived artifact lifecycle inventory (`generated`, `available`, `qualified`, `requires_install`, `installed`) and separates a canonical DBC delta intermediate from the installable client/server DBC outputs. v1.1 added structured requirements and removed admission decisions based on display-only `patch_cost`.

- `capabilities() -> object`: `{adapter_version, world_db, characters_db, addon_channel, server_module, config_activation, client_server_dbc_parity, mpq_patch, playerbots, ahbot, material_exchange, party_commands}`. Boolean capabilities describe host claims; absent required capabilities are unavailable. DBC parity additionally requires equal client/server hashes for every declared table.
- `snapshot() -> object`: `{adapter_version, schema_version, reserved_ids, dbc_hashes, mpq_inventory, installed_artifacts, applied}`. `snapshot_hash()` hashes a stable JSON representation; DBC hashes use `{client: {table: sha256}, server: {table: sha256}}`. The current fake host is in-memory and accepts constructor-provided snapshot data; it does not inspect a real DB, DBC tree or MPQ.
- `admit(intent) -> receipt`: checks the intent kind, idempotency key, structured delivery requirements, required host capabilities and artifact presence, reserved-ID collisions, schema version and explicitly unverified references. It does not run SQL or execute gameplay.
- `apply(intent) -> receipt`: repeats admission on the host side before recording an install/rollback in fake state. A repeated idempotency key returns the stored receipt. The fake host's “apply” is bookkeeping only.

An intent has this shape:

```json
{
  "idempotency_key": "frost-armory-1.0.0",
  "kind": "dry_run",
  "allow_unverified": false,
  "payload": {
    "patch_cost": "none (display summary only)",
    "schema_version": "1.0.0",
    "ids": {"item_template": [901000]},
    "unverified": [],
    "feature_spec": {"spec_version": 1, "version": "1.0.0",
                     "items": [{"key": "blade"}]},
    "delivery_requirements": {
      "schema": "arena-delivery-requirements/1",
      "artifacts": [
        {"kind": "world_sql", "version": "1.0.0", "requires_install": true,
         "trust": "generated_unqualified", "activation": "apply_after_schema_qualification"},
        {"kind": "data_migration", "version": "1.0.0", "requires_install": true,
         "trust": "generated_unqualified", "activation": "apply_after_backup_and_review"}
      ],
      "capabilities": [
        {"name": "world_db", "version": "adapter:1.2.0", "required": true,
         "trust": "host_claim_and_snapshot_required", "activation": "admission_gate"}
      ]
    },
    "generated_artifacts": [{"kind": "world_sql"}, {"kind": "data_migration"}],
    "available_artifacts": [{"kind": "world_sql"}, {"kind": "data_migration"}],
    "artifact_inventory": [
      {"identity": "world_sql", "kind": "world_sql", "generated": true,
       "available": true, "qualified": false, "requires_install": true, "installed": false},
      {"identity": "data_migration", "kind": "data_migration", "generated": true,
       "available": true, "qualified": false, "requires_install": true, "installed": false}
    ]
  }
}
```

Each artifact requirement has `kind`, `version`, `trust`, `activation`, `requires_install`, and an optional table name; each capability requirement has `name`, `version`, `required`, `trust`, and `activation`. Package payloads carry lifecycle records and redundant generated/available labels; the fake host checks label consistency and refuses requirements the inventory marks unavailable, but it does not receive package bytes and cannot authenticate caller claims. Package qualification independently recomputes byte presence and lifecycle inventory from the manifest-verified bundle. `qualified` is distinct from file presence, and package metadata cannot claim `installed` state. A canonical `dbc_delta` is generated/available only as an intermediate (`requires_install: false`); client and server DBC outputs remain required install artifacts and must both be materialized and parity-qualified. Missing artifacts, missing capabilities, forged/stale labels and absent/mismatched DBC hashes fail closed. `patch_cost` remains a human-readable summary only.

Supported kinds: `install_package`, `rollback_package`, `dry_run`. Stable error codes: `CAPABILITY_MISSING`, `DBC_PARITY_MISSING`, `ARTIFACT_MISSING`, `REQUIREMENTS_INVALID`, `ID_COLLISION`, `SCHEMA_MISMATCH`, `ALREADY_APPLIED` (reserved for future richer implementations), `VALIDATION_FAILED`, `NOT_ASSESSED`, `UNKNOWN_INTENT`. Successful `admit` returns `status: admitted`; successful fake `apply` returns `status: applied`; a replay has `replayed: true`.

## Admission semantics and safety

- Missing idempotency key is rejected. Replays use the first stored receipt.
- Installs are rejected if any package ID collides with the host's reserved set. Rollback may release named IDs only if currently reserved.
- Admission ignores `patch_cost`; it consumes `delivery_requirements` and verifies required artifact entries, host capability claims, and declared client/server DBC hash parity. A mixed addon + DBC package still requires `mpq_patch`, even if its human summary mentions addon files first. This protects unknown archives such as `Data/patch-4.MPQ` from implicit replacement.
- Declared schema version must match the host snapshot.
- Unverified asset references are rejected as `NOT_ASSESSED` unless explicitly allowed. Allowance is visible and does not elevate evidence.
- Admission receipts bind to a snapshot hash, so a real adapter should re-check the baseline immediately before execution. FakeHost does not implement concurrent-change protection, ownership attribution, SQL transactionality or real rollback.

## What a real Campaign Party binding must provide

A future baseline adapter should ingest, without mutation, exact AzerothCore/core revision and DB schemas; client/server DBC hashes; existing MPQ names, hashes and ownership; module loader/API interfaces; reserved ID ranges; addon/channel availability; and capabilities for the existing inventory, professions/Material Exchange, secondary spell systems, party orchestration and Playerbots. It must report missing/ambiguous data as not assessed, reject collisions, produce a deterministic plan, and never replace the target's authorities. Installation and rollback require explicit operator confirmation and transactional/recovery behavior appropriate to the target deployment.

## Tests and proof

`tests/test_foundry_adapter.py` exercises acceptance, install, rollback, idempotent replay, duplicate/forged artifact labels, qualified Item.dbc, mixed addon+DBC, refusal of a DBC delta when either materialized side is absent, and refusal of unsupported Spell.dbc/two-table declarations; it also covers collision, absent MPQ merge capability, mismatched parity, schema mismatch, missing idempotency key, unknown intent and unverified assets. `tests/test_foundry_delivery.py` covers addon+Item.dbc, addon+Spell.dbc, multiple DBC tables, the non-installable delta contract and mixed SQL+MPQ requirements. These are `ARENA-SYNTHETIC`; they do not establish Campaign Party admission or runtime behavior. `CAMPAIGN-ADMISSION` and `CAMPAIGN-LIVE` remain pending until actual downstream artifacts and the real client/Playerbots are supplied.

## Limitations and compatibility risks

This contract is an initial seam, not a finalized Campaign Party API. It currently does not model selected-member identity, party snapshots, permission checks, command intents, server-side gameplay receipts, database migration state or Playerbot capabilities beyond boolean flags. The in-memory fake host's ID reservation model is intentionally small and does not prove safe ownership boundaries for shared IDs. Version changes must be explicit and tested; downstream-specific bindings should be separate implementations conforming to this contract, not hidden assumptions in the compiler.
