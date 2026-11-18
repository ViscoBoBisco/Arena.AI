# Frontier Operations: synthetic reference model

This prototype turns one gameplay idea into a small, testable state contract: **The Surveyor's Last Route**, a short open-zone survey, rescue, beacon-defense, and outcome-choice operation. The candidate setting is Un'Goro Crater, but no exact site, creature, object, display, quest, or item has been verified against build-12340 data.

## What is implemented

`model.py` is an in-memory Python oracle for one character's operation. It exercises:

- three survey clues in any order;
- rescue gated on all clues;
- two distinct beacon objectives gated on rescue;
- one deterministic, mutually exclusive outcome choice;
- optimistic revision checks and idempotent successful-request replay;
- rejection of unknown/duplicate objectives and conflicting request-ID reuse.

The keys such as `site-a` and `beacon-a` are abstract test labels, **not game IDs**. The model creates no quest, item, currency, database row, addon UI, or game-server behavior.

## Run the synthetic tests

From the repository root:

```sh
python3 -m unittest discover -s tests -p 'test_frontier_operations_model.py' -v
```

## Explicit non-claims

This is not production persistence: it has no database transaction, uniqueness constraint, concurrent writer protection, authentication, party ownership, reward grant, or inventory integration. Its deterministic snapshot/restore helper replays a synthetic event log to simulate a restart; it does not prove crash-safe commits or durable storage. It is not compiled against AzerothCore and does not exercise a client, Playerbots, or Campaign Party. It must not be installed on a server.

The next real implementation requires local source-backed asset selection, downstream ownership/schema review, transactional server-side persistence, migration and rollback, pinned-core compile evidence, and acceptance with actual client/server/Playerbots/party behavior. See [`designs/frontier-operations.json`](../../designs/frontier-operations.json) and [`docs/16-frontier-operations.md`](../../docs/16-frontier-operations.md).
