"""Campaign Party adapter contract v1.1 + fake integration host.

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

Error codes are stable strings: CAPABILITY_MISSING, DBC_PARITY_MISSING,
ARTIFACT_MISSING, REQUIREMENTS_INVALID, ID_COLLISION, SCHEMA_MISMATCH,
ALREADY_APPLIED, VALIDATION_FAILED, NOT_ASSESSED, UNKNOWN_INTENT.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Dict, List, Optional

from . import ADAPTER_VERSION

REQUIRED_CAPABILITIES = ("world_db", "characters_db")
DELIVERY_SCHEMA = "arena-delivery-requirements/1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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
            "mpq_patch": False, "client_server_dbc_parity": False,
            "server_module": True, "config_activation": True,
            "party_commands": True,
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
        # Delivery is a structured contract; labels such as patch_cost are
        # presentation only and are never consulted for admission.
        requirements = payload.get("delivery_requirements")
        feature_spec = payload.get("feature_spec")
        if (not isinstance(requirements, dict)
                or requirements.get("schema") != DELIVERY_SCHEMA
                or not isinstance(requirements.get("artifacts"), list)
                or not isinstance(requirements.get("capabilities"), list)
                or not isinstance(feature_spec, dict)):
            return self._error("REQUIREMENTS_INVALID",
                               "payload needs feature_spec and arena-delivery-requirements/1")
        from .spec import delivery_requirements
        if delivery_requirements(feature_spec) != requirements:
            return self._error("REQUIREMENTS_INVALID",
                               "delivery requirements do not match the embedded feature spec")
        for capability in requirements["capabilities"]:
            if (not isinstance(capability, dict)
                    or not isinstance(capability.get("name"), str)
                    or not isinstance(capability.get("required"), bool)
                    or not isinstance(capability.get("version"), str)
                    or not isinstance(capability.get("trust"), str)
                    or not isinstance(capability.get("activation"), str)):
                return self._error("REQUIREMENTS_INVALID", "malformed capability requirement")
            if capability["required"] and not self._capabilities.get(capability["name"], False):
                return self._error("CAPABILITY_MISSING",
                                   f"host lacks required capability {capability['name']}")
            if capability["required"] and capability["name"] == "client_server_dbc_parity":
                dbc_artifacts = [entry for entry in requirements["artifacts"]
                                 if isinstance(entry, dict)
                                 and entry.get("kind") in ("client_dbc", "server_dbc")]
                tables = {entry.get("table") for entry in dbc_artifacts
                          if isinstance(entry.get("table"), str)}
                client = self._dbc_hashes.get("client", {})
                server = self._dbc_hashes.get("server", {})
                if (not tables or not isinstance(client, dict) or not isinstance(server, dict)
                        or any(table not in client or table not in server
                               or not isinstance(client[table], str)
                               or not _SHA256_RE.fullmatch(client[table])
                               or client[table] != server[table]
                               for table in tables)):
                    return self._error("DBC_PARITY_MISSING",
                                       "required client/server DBC hashes are absent or differ")
        available = payload.get("available_artifacts")
        if not isinstance(available, list):
            return self._error("REQUIREMENTS_INVALID", "available_artifacts must be a list")
        available_keys = set()
        for entry in available:
            if (not isinstance(entry, dict)
                    or not isinstance(entry.get("kind"), str)
                    or (entry.get("table") is not None
                        and not isinstance(entry.get("table"), str))):
                return self._error("REQUIREMENTS_INVALID", "malformed available artifact")
            available_keys.add((entry["kind"], entry.get("table")))
        for artifact in requirements["artifacts"]:
            if (not isinstance(artifact, dict)
                    or not isinstance(artifact.get("kind"), str)
                    or not isinstance(artifact.get("version"), str)
                    or not isinstance(artifact.get("trust"), str)
                    or not isinstance(artifact.get("activation"), str)
                    or (artifact.get("table") is not None
                        and not isinstance(artifact.get("table"), str))):
                return self._error("REQUIREMENTS_INVALID", "malformed artifact requirement")
            if (artifact["kind"], artifact.get("table")) not in available_keys:
                return self._error("ARTIFACT_MISSING",
                                   f"required artifact missing: {artifact['kind']}"
                                   + (f" ({artifact['table']})" if artifact.get("table") else ""))
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
