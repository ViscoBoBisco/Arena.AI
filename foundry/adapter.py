"""Campaign Party adapter contract v1 + fake integration host.

The contract is the stable seam between Arena (offline content engineering)
and Campaign Party (the downstream runtime). Arena never imports Campaign
Party code; it speaks this contract, and the fake host below exercises the
whole contract without the downstream source.

Contract summary (docs/13):
  capabilities()  -> what the host can do (world DB, characters DB, addon
                     channel, playerbots, ahbot, material exchange, MPQ...)
  snapshot()      -> {schema_version, reserved_ids, dbc_hashes, mpq, applied}
  admit(intent)   -> receipt or structured error. Intents carry an
                     idempotency key; replays return the original receipt.
  apply(intent)   -> only after admission; server-side revalidation happens
                     inside the host, never trusting the caller.

Error codes are stable strings: CAPABILITY_MISSING, ID_COLLISION,
SCHEMA_MISMATCH, ALREADY_APPLIED, VALIDATION_FAILED, NOT_ASSESSED,
UNKNOWN_INTENT.
"""

from __future__ import annotations

import hashlib
import json
from typing import Dict, List, Optional

from . import ADAPTER_VERSION

REQUIRED_CAPABILITIES = ("world_db", "characters_db")


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


class FakeHost:
    """In-memory Campaign Party stand-in. Records everything; decides nothing
    about gameplay — it only enforces the contract."""

    def __init__(self, *, capabilities=None, schema_version: str = "1.0.0",
                 reserved_ids=None, dbc_hashes=None, mpq_inventory=None):
        self._capabilities = dict(capabilities or {
            "world_db": True, "characters_db": True, "addon_channel": True,
            "playerbots": True, "ahbot": True, "material_exchange": True,
            "mpq_patch": False, "party_commands": True,
        })
        self._schema_version = schema_version
        self._reserved = {k: set(v) for k, v in (reserved_ids or {}).items()}
        self._dbc_hashes = dict(dbc_hashes or {})
        self._mpq = list(mpq_inventory or ["patch-4.MPQ"])
        self._applied: Dict[str, dict] = {}   # idempotency_key -> receipt
        self._admitted: List[dict] = []

    # -- contract surface --------------------------------------------------
    def capabilities(self) -> dict:
        return {"adapter_version": ADAPTER_VERSION, **self._capabilities}

    def snapshot(self) -> dict:
        return {
            "adapter_version": ADAPTER_VERSION,
            "schema_version": self._schema_version,
            "reserved_ids": {k: sorted(v) for k, v in self._reserved.items()},
            "dbc_hashes": dict(self._dbc_hashes),
            "mpq_inventory": list(self._mpq),
            "applied": sorted(self._applied),
        }

    def snapshot_hash(self) -> str:
        return _hash(self.snapshot())

    def admit(self, intent: dict) -> dict:
        """Validate an intent without applying it. Returns a receipt."""
        key = intent.get("idempotency_key")
        if not key:
            return self._error("VALIDATION_FAILED", "intent needs an idempotency_key")
        if key in self._applied:
            return {**self._applied[key], "replayed": True}
        kind = intent.get("kind")
        if kind not in ("install_package", "rollback_package", "dry_run"):
            return self._error("UNKNOWN_INTENT", f"unknown intent kind {kind!r}")
        payload = intent.get("payload") or {}
        # capability gate: a package needing an MPQ patch cannot be admitted
        # by a host that cannot merge patches (e.g. patch-4.MPQ is owned).
        if payload.get("patch_cost", "none").startswith("dbc-rows") \
                and not self._capabilities.get("mpq_patch"):
            return self._error("CAPABILITY_MISSING",
                               "package needs an MPQ/DBC patch; host cannot merge patches")
        # Collision checks apply to installs. Rollback is allowed to release
        # exactly the identifiers it names, but may not silently free IDs the
        # host never reserved for this installation.
        for table, ids in (payload.get("ids") or {}).items():
            reserved = self._reserved.get(table, set())
            if kind == "install_package":
                clash = set(ids) & reserved
                if clash:
                    return self._error("ID_COLLISION",
                                       f"{table}: ids already reserved: {sorted(clash)[:5]}")
            elif kind == "rollback_package":
                missing = set(ids) - reserved
                if missing:
                    return self._error("VALIDATION_FAILED",
                                       f"{table}: rollback names unreserved ids: {sorted(missing)[:5]}")
        # schema gate
        if payload.get("schema_version") not in (None, self._schema_version):
            return self._error("SCHEMA_MISMATCH",
                               f"package targets schema {payload['schema_version']}, "
                               f"host is {self._schema_version}")
        # unverified assets are never admitted silently
        if payload.get("unverified") and not intent.get("allow_unverified"):
            return self._error("NOT_ASSESSED",
                               f"{len(payload['unverified'])} unverified asset(s); "
                               "pass allow_unverified explicitly")
        receipt = {
            "status": "admitted",
            "idempotency_key": key,
            "kind": kind,
            "snapshot_hash": self.snapshot_hash(),
            "adapter_version": ADAPTER_VERSION,
        }
        self._admitted.append(receipt)
        return receipt

    def apply(self, intent: dict) -> dict:
        """Apply a previously admitted intent (idempotent)."""
        key = intent.get("idempotency_key")
        if key in self._applied:
            return {**self._applied[key], "replayed": True}
        receipt = self.admit(intent)
        if receipt.get("status") != "admitted":
            return receipt
        if intent.get("kind") == "install_package":
            for table, ids in (intent.get("payload") or {}).get("ids", {}).items():
                self._reserved.setdefault(table, set()).update(ids)
        elif intent.get("kind") == "rollback_package":
            for table, ids in (intent.get("payload") or {}).get("ids", {}).items():
                self._reserved.get(table, set()).difference_update(ids)
        final = {**receipt, "status": "applied",
                 "snapshot_hash": self.snapshot_hash()}
        self._applied[key] = final
        return final

    # -- helpers ------------------------------------------------------------
    def _error(self, code: str, message: str) -> dict:
        return {"status": "error", "code": code, "message": message,
                "adapter_version": ADAPTER_VERSION}

    @property
    def admitted(self) -> List[dict]:
        return list(self._admitted)
