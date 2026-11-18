"""Sequential synthetic oracle for the Frontier Operations design.

This is deliberately not a database adapter, game-server module, party API, or
production persistence layer. It exists to exercise transition and retry rules
before any integration is attempted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


SITES = frozenset({"site-a", "site-b", "site-c"})
BEACONS = frozenset({"beacon-a", "beacon-b"})
OUTCOMES = frozenset({"restore-route", "archive-findings"})


class OperationError(ValueError):
    """An intent violates the operation contract."""


@dataclass(frozen=True)
class Receipt:
    request_id: str
    revision: int
    action: str
    argument: Optional[str]
    state: str


@dataclass
class FrontierOperation:
    """In-memory, sequential state machine for one character's operation."""

    state: str = "SURVEY"
    revision: int = 0
    sites: set[str] = field(default_factory=set)
    rescued: bool = False
    beacons: set[str] = field(default_factory=set)
    outcome: Optional[str] = None
    _receipts: Dict[str, Tuple[str, Optional[str], Receipt]] = field(default_factory=dict)

    def snapshot(self) -> dict:
        """Return a deterministic test fixture, not a trusted persistence format."""
        ordered = sorted(self._receipts.values(), key=lambda row: row[2].revision)
        return {
            "schema": "frontier-operation-model-snapshot/1",
            "state": self.state,
            "revision": self.revision,
            "sites": sorted(self.sites),
            "rescued": self.rescued,
            "beacons": sorted(self.beacons),
            "outcome": self.outcome,
            "receipts": [
                {
                    "request_id": receipt.request_id,
                    "revision": receipt.revision,
                    "action": receipt.action,
                    "argument": receipt.argument,
                    "state": receipt.state,
                }
                for _, _, receipt in ordered
            ],
        }

    @classmethod
    def restore(cls, snapshot: dict) -> "FrontierOperation":
        """Rebuild from a model-generated event log and reject inconsistent data.

        This is a synthetic restart test helper. A production service must use
        authenticated AzerothCore-owned persistence and transactional recovery,
        not accept a user-writable JSON snapshot as authority.
        """
        expected_keys = {
            "schema", "state", "revision", "sites", "rescued", "beacons",
            "outcome", "receipts",
        }
        if not isinstance(snapshot, dict) or set(snapshot) != expected_keys:
            raise OperationError("invalid snapshot shape")
        if snapshot["schema"] != "frontier-operation-model-snapshot/1":
            raise OperationError("unsupported snapshot schema")
        if (not isinstance(snapshot["revision"], int)
                or isinstance(snapshot["revision"], bool)
                or snapshot["revision"] < 0):
            raise OperationError("invalid snapshot revision")
        if not isinstance(snapshot["state"], str) or not isinstance(snapshot["rescued"], bool):
            raise OperationError("invalid snapshot state fields")
        if (not isinstance(snapshot["sites"], list)
                or not all(isinstance(value, str) for value in snapshot["sites"])
                or not isinstance(snapshot["beacons"], list)
                or not all(isinstance(value, str) for value in snapshot["beacons"])):
            raise OperationError("invalid snapshot objective lists")
        if snapshot["outcome"] is not None and not isinstance(snapshot["outcome"], str):
            raise OperationError("invalid snapshot outcome")
        if not isinstance(snapshot["receipts"], list):
            raise OperationError("invalid snapshot receipts")

        restored = cls()
        receipt_keys = {"request_id", "revision", "action", "argument", "state"}
        for index, raw in enumerate(snapshot["receipts"], start=1):
            if not isinstance(raw, dict) or set(raw) != receipt_keys:
                raise OperationError("invalid snapshot receipt")
            if (not isinstance(raw["revision"], int)
                    or isinstance(raw["revision"], bool)
                    or raw["revision"] != index):
                raise OperationError("non-contiguous snapshot revisions")
            if (not isinstance(raw["request_id"], str) or not raw["request_id"]
                    or not isinstance(raw["action"], str)
                    or (raw["argument"] is not None and not isinstance(raw["argument"], str))
                    or not isinstance(raw["state"], str)):
                raise OperationError("invalid snapshot receipt fields")
            actual = restored.apply(
                request_id=raw["request_id"],
                expected_revision=restored.revision,
                action=raw["action"],
                argument=raw["argument"],
            )
            expected = Receipt(**raw)
            if actual != expected:
                raise OperationError("snapshot receipt does not match replayed state")

        expected_state = {
            "state": restored.state,
            "revision": restored.revision,
            "sites": sorted(restored.sites),
            "rescued": restored.rescued,
            "beacons": sorted(restored.beacons),
            "outcome": restored.outcome,
        }
        if any(snapshot[key] != value for key, value in expected_state.items()):
            raise OperationError("snapshot summary does not match replayed receipts")
        return restored

    def apply(
        self,
        *,
        request_id: str,
        expected_revision: int,
        action: str,
        argument: Optional[str] = None,
    ) -> Receipt:
        """Apply one intent, or replay an identical successful request.

        Production must put the revision check, mutation, receipt/idempotency
        record, and any reward grant in one AzerothCore-owned DB transaction.
        This in-memory method does not model concurrency or crash recovery.
        """
        if not request_id:
            raise OperationError("request_id is required")

        prior = self._receipts.get(request_id)
        fingerprint = (action, argument)
        if prior is not None:
            old_action, old_argument, receipt = prior
            if (old_action, old_argument) != fingerprint:
                raise OperationError("request_id was already used for a different intent")
            return receipt

        if expected_revision != self.revision:
            raise OperationError("stale operation revision")

        next_state = self.state
        if action == "record_site":
            if self.state != "SURVEY":
                raise OperationError("survey is not active")
            if argument not in SITES:
                raise OperationError("unknown survey site")
            if argument in self.sites:
                raise OperationError("survey site already recorded")
            if len(self.sites) == len(SITES) - 1:
                next_state = "RESCUE"
        elif action == "rescue_surveyor":
            if self.state != "RESCUE" or len(self.sites) != len(SITES):
                raise OperationError("all survey clues are required before rescue")
            if argument is not None:
                raise OperationError("rescue action takes no argument")
            next_state = "DEFENSE"
        elif action == "activate_beacon":
            if self.state != "DEFENSE" or not self.rescued:
                raise OperationError("surveyor rescue is required before defense")
            if argument not in BEACONS:
                raise OperationError("unknown beacon")
            if argument in self.beacons:
                raise OperationError("beacon already activated")
            if len(self.beacons) == len(BEACONS) - 1:
                next_state = "OUTCOME"
        elif action == "choose_outcome":
            if self.state == "COMPLETE":
                raise OperationError("outcome is already finalized")
            if self.state != "OUTCOME" or len(self.beacons) != len(BEACONS):
                raise OperationError("both beacon proofs are required before the outcome")
            if argument not in OUTCOMES:
                raise OperationError("unknown outcome")
            next_state = "COMPLETE"
        else:
            raise OperationError("unknown operation action")

        # Mutate only after every validation has succeeded.
        if action == "record_site":
            self.sites.add(argument)  # type: ignore[arg-type]
        elif action == "rescue_surveyor":
            self.rescued = True
        elif action == "activate_beacon":
            self.beacons.add(argument)  # type: ignore[arg-type]
        elif action == "choose_outcome":
            self.outcome = argument

        self.state = next_state
        self.revision += 1
        receipt = Receipt(
            request_id=request_id,
            revision=self.revision,
            action=action,
            argument=argument,
            state=self.state,
        )
        self._receipts[request_id] = (action, argument, receipt)
        return receipt
