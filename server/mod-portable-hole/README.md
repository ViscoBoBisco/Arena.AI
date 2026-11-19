# Portable Hole commodity warehouse module

AzerothCore module + `PH1` addon protocol for a **character-private virtual commodity ledger**. This first executable slice supports authoritative GM credits (for seeding/operations), paged balance reads, reservations, release, and consume. All mutating requests are backed by InnoDB transactions and durable request receipts. The server derives the owning character GUID from the session; client frames cannot select an owner.

## Deliberate scope

- Stores fungible counts keyed by `character GUID + item_template entry`; only stackable, unlimited, non-quest item templates are accepted. Item IDs are checked through AzerothCore's `ObjectMgr`; no stock ID is guessed or included.
- This slice does **not** remove or materialize physical bag items, transport unique Item instances, share an account/party ACL, price resources, or implement profession crafting. GM credit is a privileged server operation, not a player reward mechanism. Trusted modules can call `PortableHoleCreditFromServer` and `PortableHoleConsumeReservation`; a profession/reward adapter must still prove its own item/recipe transaction and cross-system recovery before minting balance or materializing a craft result.
- Reserved amounts remain part of total balance but cannot be reserved twice. `CONSUME` spends one complete active reservation; `RELEASE` returns its reserved amount to available balance. The unique receipt key makes duplicate requests replay the original result and rejects reuse with different parameters.

## Install/test prerequisites

1. Load `sql/characters/001_portable_hole_warehouse.sql` into a disposable Characters DB. It creates only the three module-owned InnoDB tables; it does not drop, migrate, or seed core data.
2. Install the module directory under AzerothCore `modules/`, copy `conf/mod-portable-hole.conf.dist` to the matching runtime config location, and load the `PortableHole` addon from `addon/PortableHole`.
3. The test-only credit operation is accepted only for a GM and only for the GM's own character. Browse valid stackable `item_template` entries from the server's checked-out content; do not put guesses in the config or SQL.

## Protocol

Client frames are bounded ASCII/tab records on `LANG_ADDON` channel `PH1`. Supported opcodes are `HELLO`, `LIST`, `CREDIT` (GM-only), `RESERVE`, `RELEASE`, and `CONSUME`. Request IDs are required for mutations and are bounded safe tokens. `guid` never appears in a request. The server throttles requests, limits page and operation sizes, filters unavailable item templates from output, and returns the receipt read back from the durable table after commit. On database uncertainty, the client gets `RETRY` and must repeat the same request ID.

## Qualification

The repository's disposable MySQL suite tests the same table/transaction/receipt contract for collision, idempotency, competing requests, process death, replay, and rollback. The public-core compile workflow compiles this module against pinned AzerothCore. Neither check is a booted-server/client gameplay qualification. See `docs/22-expanded-gameplay-program.md` for the shared-contract boundaries.
