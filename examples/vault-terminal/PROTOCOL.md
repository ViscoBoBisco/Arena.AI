# VaultTerminal protocol, version 2

A request/response protocol between a WoW 3.3.5a addon and the server, carried on the addon chat channel. The client submits intent; the Characters database is authoritative for state and request outcomes.

## Transport and framing

| Property | Value |
|---|---|
| Channel | addon chat (`LANG_ADDON`, `CHAT_MSG_ADDON`) |
| Prefix | `VT1` (AzerothCore's own `AzerothCore\t` prefix is not used) |
| Client → server | `SendAddonMessage("VT1", payload, "WHISPER", UnitName("player"))` |
| Maximum payload | **255 bytes** |
| Encoding | printable ASCII, tab-separated fields |
| Request ID | 1–64 characters from `[A-Za-z0-9_-]`; generated and saved per character |

Each message is one frame. A frame begins `VT1<TAB>opcode`; fields cannot contain tabs, CR, or LF. The addon rate-limits sends and checks the payload size before sending. A persisted pending request ID is reused on retry or addon reopen until its matching receipt is received.

## Client → server

| Opcode | Fields | Meaning |
|---|---|---|
| `HELLO` | protocol version, addon version | Handshake; version is `2`. |
| `STATE` | none | Request current server snapshot. |
| `CLAIM` | request ID | Submit one claim intent. The ID is the idempotency key. |
| `PING` | none | Liveness probe. |

## Server → client

| Opcode | Fields | Meaning |
|---|---|---|
| `S` | total seals, target, claims left today, rank, claim-available flag | Authoritative snapshot. |
| `RCPT` | request ID, status, resulting total | Durable outcome attributed to exactly that request. |
| `ERR` | code, text | Protocol/request-level refusal. |
| `OK` | optional text | Successful non-claim response, such as `PING`. |
| `BYE` | text | Feature disabled or shutting down. |

Receipt statuses include `GRANTED`, `DENIED_COOLDOWN`, `DENIED_TARGET`, and `DENIED_INTERNAL`; `RETRY` means the transaction did not complete (or was rate-limited) and the same request ID may be retried. The client ignores a receipt whose ID does not match its pending request. A matching receipt clears the pending intent; a subsequent `S` frame updates the rendered state. The outcome is never inferred from a post-write counter read.

Protocol error codes include `BUSY`, `COOLDOWN`, `VERSION`, `DENIED`, `INTERNAL`, and `REQUEST`. `COOLDOWN` is retained for compatible server responses; v2 claim outcomes are normally carried by request-attributed `RCPT` receipts.

## Reliability and concurrency

The server serializes a character's claim transaction in InnoDB. It locks/updates the character counter, checks the target and database-server day, and inserts the immutable request receipt in the same transaction. Therefore:

1. A repeated `(character GUID, request ID)` returns the same stored receipt and cannot increment twice.
2. Distinct simultaneous requests compete against the same locked counter; the target cap and daily limit cannot be passed by a race.
3. A failed transaction commits neither the counter change nor a receipt, so retrying with that request ID is safe.
4. The receipt includes the result attributable to the request, not a later value read after another request may have committed.
5. The addon persists a pending request ID in `VaultTerminalDB`; timeout, close, or reopen does not silently create a new intent.

The request ledger is in `vault_terminal_claims_requests`; aggregate character state is in `vault_terminal_claims`. Both are Characters-database data and are retained during rollback to preserve progress and replay protection.

## Rank ladder

| Seals | Rank |
|---|---|
| 0–9 | `Initiate` |
| 10–24 | `Adept` |
| 25–49 | `Keeper` |
| 50+ | `Vaultmaster` |

The server derives rank and available claims. The addon only renders server frames; it never computes rewards, rank, or cooldown.

## Conformance examples

A snapshot (the response to `HELLO` or `STATE`) is 21 bytes in this example:

```text
VT1\tS\t12\t50\t1\tAdept\t1 (21 bytes)
```

Full exchange, with `<TAB>` shown for readability:

```text
client  VT1<TAB>HELLO<TAB>2<TAB>demo-1.0.0
server  VT1<TAB>S<TAB>12<TAB>50<TAB>1<TAB>Adept<TAB>1
client  VT1<TAB>CLAIM<TAB>20261117-1-1000
server  VT1<TAB>RCPT<TAB>20261117-1-1000<TAB>GRANTED<TAB>13
server  VT1<TAB>S<TAB>13<TAB>50<TAB>0<TAB>Adept<TAB>0
```

The executable vectors and hostile-input/race/retry checks are in `harness/vt_model.py` and `tests/test_tier_master.py`. Run `python3 harness/vt_model.py` from the repository root to print the deterministic transcript.
