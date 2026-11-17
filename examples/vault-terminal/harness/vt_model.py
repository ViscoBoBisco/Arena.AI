"""Reference implementation of the VaultTerminal v1 protocol (see ../PROTOCOL.md).

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
from dataclasses import dataclass, field
from datetime import date, timedelta

PREFIX = "VT1"
VERSION = 1
MAX_BYTES = 255
TARGET_SEALS = 50
CLAIMS_PER_DAY = 1
POLL_SECONDS = 5
TIMEOUT_SECONDS = 10

# Descending: first threshold the seal count reaches wins. Mirrors `VT_RANKS` in the C++ module.
RANKS = ((50, "Vaultmaster"), (25, "Keeper"), (10, "Adept"), (0, "Initiate"))

ERRORS = frozenset({"BUSY", "COOLDOWN", "VERSION", "DENIED", "INTERNAL"})
CLIENT_OPCODES = frozenset({"HELLO", "STATE", "CLAIM", "PING"})
SERVER_OPCODES = frozenset({"S", "OK", "ERR", "BYE"})

_OPCODE = re.compile(r"^[A-Z]{1,8}$")
_FIELD = re.compile(r"^[\x20-\x7e]{0,64}$")


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


@dataclass
class Store:
    """The character database: one row per (character, day). Shared by every session of a character.

    This is why a client cannot claim twice by reconnecting: the session is disposable, the rows are
    not — exactly like `PRIMARY KEY (guid, claim_day)` in ../../sql/01_module_table.sql.
    """

    claims: dict = field(default_factory=dict)          # guid -> [date, ...]

    def record(self, guid, day: date) -> None:
        self.claims.setdefault(guid, []).append(day)

    def history(self, guid) -> list:
        return self.claims.get(guid, [])

    def claims_on(self, guid, day: date) -> int:
        return sum(1 for d in self.history(guid) if d == day)


@dataclass
class Session:
    """One connection. All state is read from the store, never cached — like the module's queries."""

    today: date
    store: Store = field(default_factory=Store)
    guid: int = 1
    seals: int = 0                       # seals the character already had (the DB's older rows)
    disabled: bool = False

    # -- state -------------------------------------------------------------------------------
    @property
    def total_seals(self) -> int:
        return self.seals + len(self.store.history(self.guid))

    @property
    def claims_today(self) -> int:
        return self.store.claims_on(self.guid, self.today)

    def snapshot(self) -> str:
        left = max(0, CLAIMS_PER_DAY - self.claims_today)
        return build("S", self.total_seals, TARGET_SEALS, left, rank_for(self.total_seals),
                     "1" if left else "0")

    def claim(self) -> None:
        self.store.record(self.guid, self.today)

    def advance_to(self, day: date) -> None:
        """Midnight. Nothing is recomputed: `claims_today` is a query, so it follows the date."""
        self.today = day

    # -- wire --------------------------------------------------------------------------------
    def handle(self, payload: str) -> list:
        """Feed one inbound message; return the outbound messages, in order (possibly none)."""
        parsed = parse(payload)
        if parsed is None:
            return []                       # not ours, or too long: the transport already dropped it

        opcode, fields = parsed

        if self.disabled:
            # Rule: never go silent. A disabled server says so in the only vocabulary it has.
            if opcode in ("HELLO", "STATE", "PING", "CLAIM"):
                return [build("BYE", "server is under maintenance")]
            return []

        if opcode == "HELLO":
            if len(fields) < 1:
                return [self._err("VERSION", "HELLO without a protocol version")]
            if fields[0] != str(VERSION):
                return [self._err("VERSION", "client protocol is not supported")]
            return [self.snapshot()]
        if opcode == "STATE":
            return [self.snapshot()]
        if opcode == "CLAIM":
            if self.claims_today >= CLAIMS_PER_DAY:
                return [self._err("COOLDOWN", "already claimed today")]
            self.claim()
            return [build("OK", "seal claimed"), self.snapshot()]
        if opcode == "PING":
            return [build("OK")]
        return []                           # unknown opcode: silence, by rule 2

    @staticmethod
    def _err(code: str, text: str) -> str:
        assert code in ERRORS, code
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
        ("client", "VT1\tHELLO\t1\tdemo-1.0.0"),
        ("client", "VT1\tSTATE"),
        ("client", "VT1\tCLAIM"),
        ("client", "VT1\tCLAIM"),
        ("client", "VT1\tPING"),
        ("client", "VT1\tFUTURE\tfrom-a-newer-build"),
    ]
    for who, payload in script:
        print(f"{who:>6}  {payload!r}")
        for reply in session.handle(payload):
            print(f"{'server':>6}  {reply!r}   ({len(reply)} bytes)")
    session.advance_to(datetime.date(2026, 11, 18))
    print(f"{'server':>6}  <midnight>")
    for reply in session.handle("VT1\tCLAIM"):
        print(f"{'server':>6}  {reply!r}   ({len(reply)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
