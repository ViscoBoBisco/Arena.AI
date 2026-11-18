"""Reference implementation of the VaultTerminal v2 protocol (see ../PROTOCOL.md).

This file is the *executable* form of the specification: the same framing, the same opcodes, the same
rules, and the same rank ladder as the addon and the C++ module — expressed once, in Python, so the
tests can hold all three to it. It is not shipped to a server; the module is. It exists because a
protocol that is only prose drifts from its implementations, and a protocol with an executable model
cannot: either the model and the peer agree, or a test fails.

Two audiences:

* the model itself (`Session`), used by the tests to replay conformance vectors, fuzz payloads and
  move a clock across midnight;
* `constant_mismatches(peer)`, used to prove that the *checking* is capable of failing.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import date

PREFIX = "VT1"
VERSION = 2
MAX_BYTES = 255
TARGET_SEALS = 50
CLAIMS_PER_DAY = 1
POLL_SECONDS = 5
TIMEOUT_SECONDS = 10

# Descending: first threshold the seal count reaches wins. Mirrors `VT_RANKS` in the C++ module.
RANKS = ((50, "Vaultmaster"), (25, "Keeper"), (10, "Adept"), (0, "Initiate"))

ERRORS = frozenset({"BUSY", "COOLDOWN", "VERSION", "DENIED", "INTERNAL", "REQUEST"})
CLIENT_OPCODES = frozenset({"HELLO", "STATE", "CLAIM", "PING"})
SERVER_OPCODES = frozenset({"S", "OK", "ERR", "BYE", "RCPT"})

_OPCODE = re.compile(r"^[A-Z]{1,8}$")
_FIELD = re.compile(r"^[\x20-\x7e]{0,64}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def rank_for(seals: int) -> str:
    for threshold, name in RANKS:
        if seals >= threshold:
            return name
    return RANKS[-1][1]


class FrameError(ValueError):
    """A frame that the protocol forbids us to build or read."""


def build(opcode: str, *fields) -> str:
    """Encode one message. Raises rather than emitting something the cap or grammar forbids."""
    if not _OPCODE.match(opcode):
        raise FrameError(f"opcode {opcode!r} is not 1-8 upper-case letters")
    parts = [PREFIX, opcode, *(str(f) for f in fields)]
    for part in parts[1:]:
        if "\t" in part or "\n" in part or "\r" in part:
            raise FrameError(f"field {part!r} contains a separator")
        if not _FIELD.match(part):
            raise FrameError(f"field {part!r} is not printable ASCII within 64 bytes")
    payload = "\t".join(parts)
    if len(payload) > MAX_BYTES:
        raise FrameError(f"payload is {len(payload)} bytes; the cap is {MAX_BYTES}")
    return payload


def parse(payload: str):
    """Return (opcode, fields) if this is one of our messages, otherwise None.

    None also covers everything the transport already refuses (too long, wrong prefix, no opcode):
    the caller cannot tell those apart, and does not need to — all three are silence.
    """
    if not isinstance(payload, str) or len(payload) > MAX_BYTES:
        return None
    if not payload.startswith(PREFIX + "\t"):
        return None
    head, _, rest = payload[len(PREFIX) + 1:].partition("\t")
    if not _OPCODE.match(head):
        return None
    return head, (rest.split("\t") if rest else [])


@dataclass(frozen=True)
class Receipt:
    request_id: str
    status: str
    total_claims: int
    claim_day: date


@dataclass
class Store:
    """Serializable persistent character state plus durable request receipts.

    The lock models one InnoDB row lock per character. A response is derived
    from the immutable receipt for that request, never from a later state read.
    """

    claims: dict = field(default_factory=dict)          # guid -> (count, last_day)
    receipts: dict = field(default_factory=dict)        # (guid, request_id) -> Receipt
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    fail_before_commit: bool = False
    fail_after_update: bool = False

    def process_claim(self, guid, request_id: str, day: date, target: int) -> Receipt:
        if not _REQUEST_ID.fullmatch(request_id):
            raise ValueError("invalid request id")
        key = (guid, request_id)
        with self.lock:
            saved = self.receipts.get(key)
            if saved is not None:
                return saved
            if self.fail_before_commit:
                self.fail_before_commit = False
                raise RuntimeError("injected database failure before transaction")

            previous = self.claims.get(guid, (0, None))
            total, last_day = previous
            if total >= target:
                status = "DENIED_TARGET"
            elif last_day == day:
                status = "DENIED_COOLDOWN"
            else:
                total += 1
                last_day = day
                status = "GRANTED"
                self.claims[guid] = (total, last_day)

            if self.fail_after_update:
                self.fail_after_update = False
                self.claims[guid] = previous
                raise RuntimeError("injected database failure after counter update")

            receipt = Receipt(request_id, status, total, day)
            self.receipts[key] = receipt
            return receipt

    def history(self, guid) -> list:
        total, _last_day = self.claims.get(guid, (0, None))
        return [None] * total

    def claims_on(self, guid, day: date) -> int:
        _total, last_day = self.claims.get(guid, (0, None))
        return int(last_day == day)


@dataclass
class Session:
    """A disposable connection over shared persistent state and request receipts."""

    today: date
    store: Store = field(default_factory=Store)
    guid: int = 1
    seals: int = 0
    disabled: bool = False

    @property
    def total_seals(self) -> int:
        return self.seals + self.store.claims.get(self.guid, (0, None))[0]

    @property
    def claims_today(self) -> int:
        return self.store.claims_on(self.guid, self.today)

    def snapshot(self) -> str:
        left = max(0, CLAIMS_PER_DAY - self.claims_today)
        return build("S", self.total_seals, TARGET_SEALS, left, rank_for(self.total_seals),
                     "1" if left else "0")

    def advance_to(self, day: date) -> None:
        self.today = day

    def handle(self, payload: str) -> list:
        parsed = parse(payload)
        if parsed is None:
            return []
        opcode, fields = parsed

        if self.disabled:
            if opcode in ("HELLO", "STATE", "PING", "CLAIM"):
                return [build("BYE", "server is under maintenance")]
            return []

        if opcode == "HELLO":
            if len(fields) < 1 or fields[0] != str(VERSION):
                return [self._err("VERSION", "client protocol is not supported")]
            return [self.snapshot()]
        if opcode == "STATE":
            return [self.snapshot()]
        if opcode == "CLAIM":
            if len(fields) != 1 or not _REQUEST_ID.fullmatch(fields[0]):
                return [self._err("REQUEST", "claim request id is invalid")]
            request_id = fields[0]
            try:
                receipt = self.store.process_claim(
                    self.guid, request_id, self.today, max(0, TARGET_SEALS - self.seals))
            except RuntimeError:
                return [build("RCPT", request_id, "RETRY", self.total_seals)]
            replies = [build("RCPT", receipt.request_id, receipt.status,
                             self.seals + receipt.total_claims)]
            if receipt.status != "RETRY":
                replies.append(self.snapshot())
            return replies
        if opcode == "PING":
            return [build("OK")]
        return []

    @staticmethod
    def _err(code: str, text: str) -> str:
        assert code in ERRORS or code == "REQUEST", code
        return build("ERR", code, text)


# --------------------------------------------------------------------------- self-check
def constant_mismatches(peer: dict) -> list:
    """Compare a peer's advertised constants with ours; empty list means agreement.

    Kept here (rather than in the test) so the test can prove the comparison detects a lie:
    feed it a deliberately wrong prefix and it must complain.
    """
    expected = {
        "prefix": PREFIX,
        "version": str(VERSION),
        "max_bytes": MAX_BYTES,
        "target_seals": TARGET_SEALS,
        "claims_per_day": CLAIMS_PER_DAY,
        "ranks": {seals: name for seals, name in RANKS},
    }
    problems = []
    for key, want in expected.items():
        have = peer.get(key)
        if key == "ranks" and have is not None:
            have = {int(k): v for k, v in (have.items() if isinstance(have, dict) else have)}
        if have != want:
            problems.append(f"{key}: model has {want!r}, peer has {have!r}")
    return problems


# --------------------------------------------------------------------------- transcript demo
def main() -> int:
    """`python3 harness/vt_model.py` — print the conversation PROTOCOL.md shows, byte for byte."""
    import datetime
    session = Session(today=datetime.date(2026, 11, 17), seals=12)
    script = [
        ("client", "VT1\tHELLO\t2\tdemo-1.0.0"),
        ("client", "VT1\tSTATE"),
        ("client", "VT1\tCLAIM\t20261117-1-1000"),
        ("client", "VT1\tCLAIM\t20261117-2-2000"),
        ("client", "VT1\tPING"),
        ("client", "VT1\tFUTURE\tfrom-a-newer-build"),
    ]
    for who, payload in script:
        print(f"{who:>6}  {payload!r}")
        for reply in session.handle(payload):
            print(f"{'server':>6}  {reply!r}   ({len(reply)} bytes)")
    session.advance_to(datetime.date(2026, 11, 18))
    print(f"{'server':>6}  <midnight>")
    for reply in session.handle("VT1\tCLAIM\t20261118-3-3000"):
        print(f"{'server':>6}  {reply!r}   ({len(reply)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
