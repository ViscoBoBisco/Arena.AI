# 13 — Campaign Party adapter contract v1

Campaign Party is a separate project; Arena does not assume access to its source or invent its implementation details. This versioned boundary is an offline admission/dry-run contract, not a gameplay control plane. AzerothCore remains authoritative for persistent gameplay, Playerbots owns bot execution, Campaign Party owns account/member selection, permissions and command routing, and addon code is presentation/player intent only.

## Contract surface

The executable Python contract is `foundry/adapter.py`; `FakeHost` is the independently testable reference host. Contract version is `1.0.0`.

- `capabilities() -> object`: `{adapter_version, world_db, characters_db, addon_channel, playerbots, ahbot, material_exchange, mpq_patch, party_commands}`. Boolean capabilities describe host claims; an absent `mpq_patch` capability is treated as unavailable.
- `snapshot() -> object`: `{adapter_version, schema_version, reserved_ids, dbc_hashes, mpq_inventory, applied}`. `snapshot_hash()` hashes a stable JSON representation. The current fake host is in-memory and accepts constructor-provided snapshot data; it does not inspect a real DB, DBC tree or MPQ.
- `admit(intent) -> receipt`: checks the intent kind, idempotency key, required patch capability, reserved-ID collisions, schema version and explicitly unverified references. It does not run SQL or execute gameplay.
- `apply(intent) -> receipt`: repeats admission on the host side before recording an install/rollback in fake state. A repeated idempotency key returns the stored receipt. The fake host's “apply” is bookkeeping only.

An intent has this shape:

```json
{
  "idempotency_key": "frost-armory-1.0.0",
  "kind": "dry_run",
  "allow_unverified": false,
  "payload": {
    "patch_cost": "none (server-side only)",
    "schema_version": "1.0.0",
    "ids": {"item_template": [901000]},
    "unverified": []
  }
}
```

Supported kinds: `install_package`, `rollback_package`, `dry_run`. Stable error codes: `CAPABILITY_MISSING`, `ID_COLLISION`, `SCHEMA_MISMATCH`, `ALREADY_APPLIED` (reserved for future richer implementations), `VALIDATION_FAILED`, `NOT_ASSESSED`, `UNKNOWN_INTENT`. Successful `admit` returns `status: admitted`; successful fake `apply` returns `status: applied`; a replay has `replayed: true`.

## Admission semantics and safety

- Missing idempotency key is rejected. Replays use the first stored receipt.
- Installs are rejected if any package ID collides with the host's reserved set. Rollback may release named IDs only if currently reserved.
- A package indicating DBC/MPQ work is rejected unless the host advertises `mpq_patch`; this protects an unknown existing custom archive such as `Data/patch-4.MPQ` from implicit replacement.
- Declared schema version must match the host snapshot.
- Unverified asset references are rejected as `NOT_ASSESSED` unless explicitly allowed. Allowance is visible and does not elevate evidence.
- Admission receipts bind to a snapshot hash, so a real adapter should re-check the baseline immediately before execution. FakeHost does not implement concurrent-change protection, ownership attribution, SQL transactionality or real rollback.

## What a real Campaign Party binding must provide

A future baseline adapter should ingest, without mutation, exact AzerothCore/core revision and DB schemas; client/server DBC hashes; existing MPQ names, hashes and ownership; module loader/API interfaces; reserved ID ranges; addon/channel availability; and capabilities for the existing inventory, professions/Material Exchange, secondary spell systems, party orchestration and Playerbots. It must report missing/ambiguous data as not assessed, reject collisions, produce a deterministic plan, and never replace the target's authorities. Installation and rollback require explicit operator confirmation and transactional/recovery behavior appropriate to the target deployment.

## Tests and proof

`tests/test_foundry_adapter.py` exercises acceptance, install, rollback, idempotent replay and negative cases for collision, absent MPQ capability, schema mismatch, missing idempotency key, unknown intent and unverified assets. These are `ARENA-SYNTHETIC`; they do not establish Campaign Party admission or runtime behavior. `CAMPAIGN-ADMISSION` and `CAMPAIGN-LIVE` remain pending until actual downstream artifacts and the real client/Playerbots are supplied.

## Limitations and compatibility risks

This contract is an initial seam, not a finalized Campaign Party API. It currently does not model selected-member identity, party snapshots, permission checks, command intents, server-side gameplay receipts, database migration state or Playerbot capabilities beyond boolean flags. The in-memory fake host's ID reservation model is intentionally small and does not prove safe ownership boundaries for shared IDs. Version changes must be explicit and tested; downstream-specific bindings should be separate implementations conforming to this contract, not hidden assumptions in the compiler.
