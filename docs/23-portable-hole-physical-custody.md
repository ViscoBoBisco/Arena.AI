# 23 — Portable Hole physical-item custody gate

**Status: design-only.** This document records a source inspection and an acceptance contract; it does
not implement deposits, withdrawals, item snapshots, or recovery. The shipped Portable Hole code
continues to manipulate only its virtual commodity ledger. Do not enable or claim physical custody
until every gate below has executable tests against the exact runtime core and disposable Characters
DB.

## Why a compile is not enough

Inspection was performed against the pinned public AzerothCore source revision
`6ffb62abb867dabb258de6813ff5d06d6b011543` (also the revision used by the module compile workflow):

- `src/server/game/Entities/Player/PlayerStorage.cpp`, `Player::SaveToDB`, appends
  `_SaveInventory(trans)` and many other save operations to a caller-provided Characters transaction.
- The same file's `_SaveInventory` walks `m_itemUpdateQueue`, appends inventory and item-instance
  writes, changes item states, and clears the queue while constructing the transaction. Those
  in-memory dirty-state changes happen before the database worker reports commit success.
- `src/server/game/Entities/Item/Item.cpp`, `Item::SaveToDB`, appends `item_instance` statements and
  changes the item's state to unchanged while constructing the transaction. Its `ITEM_REMOVED`
  branch deletes the `Item` object even when a caller supplied the transaction (only the commit call
  is conditional on transaction ownership).
- `src/server/database/Database/DatabaseWorkerPool.cpp`, `AsyncCommitTransaction`, returns a
  `TransactionCallback` backed by a transaction future; `DirectCommitTransaction` returns `void`.
  The async result is a useful primitive, but it does not by itself reconcile the already-mutated
  live Player/Item save queues on failure. The callback must be consumed in a safe core context.

These details rule out treating `Player::SaveToDB` plus an unchecked direct commit as a safe custody
primitive. They also mean that a successful module compile and a standalone SQL ledger test do not
qualify physical inventory transfer.

## Required invariant

For one physical-item transfer, every observable state must resolve to exactly one durable owner:

1. Before transfer, AzerothCore inventory owns the exact `Item` instance; Portable Hole does not.
2. After a committed deposit, Portable Hole custody owns that same instance and it cannot also be
   found, equipped, mailed, auctioned, traded, or used by its former owner.
3. After a committed withdrawal, the authorized recipient's inventory owns the same instance and
   the custody record is no longer withdrawable.
4. A timeout, disconnect, worker failure, process crash, retry, duplicate request, or competing
   request must never create a second live/durable copy or destroy the only copy. An uncertain
   outcome is locked and reconciled from authoritative persistence; it is never guessed from the
   addon response.

Template entry is not identity. A custody implementation must preserve the exact item GUID and
all supported instance state, not recreate a similar-looking item from `item_template`.

## Minimum protocol gates (all required)

### A. Define the eligible item subset

Start with a deliberately small allowlist, specified by policy and validated server-side. Reject
unsupported item kinds and states rather than silently dropping state. The acceptance suite must
cover item GUID uniqueness, count/stack, owner and binding rules, creator/gift creator, charges,
duration, flags, enchantments, random property, durability, played time, text/wrapping, and any other
state or auxiliary table used by the exact pinned core. If the implementation cannot preserve a
field or auxiliary relation exactly, that item is ineligible. Never issue ad hoc SQL against core
inventory tables to make a transfer appear atomic.

### B. Durable transfer identity and idempotency

Use a server-derived owner, exact item GUID, immutable request fingerprint, monotonic custody state,
and a unique transfer receipt. A repeated request with the same ID and payload returns the original
receipt; reuse with different parameters is rejected. Concurrent deposit/withdraw attempts on one
item serialize on a stable lock. The item GUID must be unique across both core inventory and custody
storage. Authorization, custody owner, and permission revision are checked on every transition.

### C. Atomic persistence

Prove whether the exact deployed schema and AzerothCore APIs permit the core inventory/item-row
changes and module custody/receipt changes to share one Characters InnoDB transaction. The
transaction must either commit all rows or none. Lock order must be documented and tested under
same-item and distinct-item races. Cross-database writes, a second ledger transaction, and a sequence
of independent save calls are not atomic substitutes.

### D. Live in-memory reconciliation

The request must run on the proper Player context, reserve/lock the instance against normal gameplay
and other inventory operations, and establish what happens to save queues and object ownership
before sending a transaction. An async commit callback must be consumed in a core-safe context. On
success, release the operation lock only after the live state agrees with committed persistence. On
failure or uncertainty, ordinary autosave/logout must not persist a state that disagrees with the
last committed rows. Demonstrate an exact reconciliation/reload path, or fail closed and prevent the
player session from saving inconsistent inventory until safe recovery is completed.

Do not assume a callback, local compensating operation, or process restart alone solves the
in-memory problem: prove that subsequent core saves cannot resurrect a removed item, erase a retained
item, or duplicate a restored item.

### E. Crash and adversarial qualification

On a disposable, exact-schema Characters database and a booted exact-pinned core, inject failure at
each boundary: before mutation, after in-memory removal/addition, during transaction execution, after
server commit but before receipt delivery, and during callback/reconciliation. Restart and relog,
then compare item GUID and complete state across inventory, mail/trade/auction-related locations,
module custody rows, and receipts. Also test duplicate and concurrent requests, bag-full withdrawal,
logout/disconnect, owner deletion, permission revocation, invalid/stale state, and replay after an
uncertain response. Preserve evidence tied to exact core commit, schema digest, module source, and
fixture. A mocked unit test or SQL-only harness is useful but insufficient for this gate.

## Stop conditions and safe next step

Until A–E pass, keep `DEPOSIT`/`WITHDRAW` absent from the client protocol and server handlers, and do
not connect profession rewards, crafting, party sharing, or account sharing to physical custody.
The next safe implementation step is a disposable core probe that observes transaction completion
and save-queue behavior under injected commit failure, without moving production or player items.
If that probe cannot establish a recoverable callback/reconciliation boundary, stop and request a
core-level hook or API designed for transactional item transfer rather than working around it in a
module.

The current safe interface is still the virtual commodity ledger plus read-only `BAG` and `NEED`
queries. `NEED` reports carried amount, bag gap, and virtual available amount as separate values; it
does not authorize or perform withdrawal and does not assert that virtual stock is craftable.
