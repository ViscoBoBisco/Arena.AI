# 16 — Frontier Operations: a first playable-content contract

This turns the broad open-world discussion into one deliberately small vertical: **The Surveyor's Last Route**, an on-demand mission in a candidate existing zone. The point is to make a large space support a compact story and varied play—not to fill it with more enemies, add a daily, or require new art.

The machine-readable contract is [`designs/frontier-operations.json`](../designs/frontier-operations.json). The Python state machine under [`prototypes/frontier-operations/`](../prototypes/frontier-operations/) is a synthetic oracle for transition rules only. **Neither artifact is a Foundry spec or installable gameplay.**

## The mission

1. Start when ready; no holiday calendar or scheduled queue is required.
2. Find three survey sites in any order. Each is a one-time investigation, not a kill counter.
3. Use the gathered clues to locate and rescue a missing surveyor.
4. Activate two field beacons in a bounded defense. The objective ends when both are handled; no wall-clock wait or arbitrary wave quota.
5. Choose whether to restore a route or archive the findings. The choice is deterministic and final for that operation.

Un'Goro Crater is only a **setting candidate**. No exact coordinates, quest, creatures, gameobjects, displays, sound, item, or reward IDs are asserted. They must be selected from locally supplied 3.3.5a data and checked against the target schema and content baseline. If Un'Goro lacks a suitable stock site layout, choose another large outdoor zone; do not force the setting.

## Why this is a useful first slice

- It uses an existing outdoor map and can be authored with existing client assets if local verification finds suitable ones.
- Investigation, rescue, and a two-objective defense feel different without custom maps, vehicle tech, or broad level-scaling changes.
- Optional order during surveying makes the space navigable without turning one route into a hard lock.
- It has a clear start and finish and works toward the project's solo/small-party goal.
- Its state contract can later support a race, caravan, survey, or event-flavored mission, while the concrete operation remains small.
- The final choice can change follow-up content, but the first slice does not attempt global world-state mutation.

### Adapt established events later, not as a dependency

Once the operation contract has real server persistence and acceptance, it can be reused to make existing events more playable rather than merely scheduled. A Darkmoon-flavored challenge circuit could use the same bounded start/finish and deterministic choice pattern for a race, target challenge, or puzzle. A Midsummer-flavored defense could reuse the objective flow with verified event assets. These are **follow-up candidates**, not included in this vertical; event calendar behavior, stock event assets, cross-player visibility, and reward ownership all need separate review. The operation itself stays available year-round and never gates story progression on the real-world calendar.

## State and authority contract

The reference state sequence is:

```text
SURVEY --(3 unique clues, any order)--> RESCUE
   --(rescue proof)--> DEFENSE
   --(2 unique beacon proofs)--> OUTCOME
   --(one deterministic choice)--> COMPLETE
```

The Python model additionally exercises request IDs, expected revisions, and a synthetic snapshot/restore replay for restart-style tests. It is deliberately sequential and in-memory. That replay does not prove durable persistence, crash-safe commits, concurrency safety, or atomic reward delivery. For a real implementation, the server must commit objective state, idempotency receipt, state revision, and any resulting reward together in AzerothCore-owned persistence. Concurrent requests must not award the same clue or completion twice. If the target core cannot provide the required transaction semantics through an agreed mechanism, do not ship a pretend-atomic version; redesign the operation around a safe server-owned transaction.

Ownership boundaries remain strict:

- **AzerothCore:** validates objective proximity/eligibility, owns operation persistence and rewards, and remains authoritative for quests, items, skills, and inventory.
- **Playerbots:** owns bot pathing, combat, and execution. Content must not assume bots can reach an unverified spot or interact with an object until tested.
- **Campaign Party:** owns party identity, membership, permissions, and routing. This design defines no party API, database schema, or IDs.
- **Addon (if any):** presents progress and submits intent only. Client messages are untrusted and cannot award a clue or reward.

The prototype is character-scoped. Party credit, joining/leaving behavior, bot participation, and simultaneous character reward eligibility remain undecided integration work—not inferred from the model.

## Synthetic reference model

Run its targeted tests with:

```sh
python3 -m unittest discover -s tests -p 'test_frontier_operations_model.py' -v
```

The tests check out-of-order site completion, prerequisites, duplicate prevention, deterministic outcome, successful retry replay, conflicting request IDs, and stale revisions. Keys such as `site-a` are synthetic labels, not game IDs. See the prototype README for the non-claims. Passing these tests is `ARENA-SYNTHETIC` evidence only.

## Implementation and acceptance gates

| Step | Required work | Evidence / gate |
|---|---|---|
| 1. Content audit | Select stock locations, objects, NPCs, text, and any reward representation from supplied local data; check coordinates, phase visibility, and interactions. | `ARENA-STATIC`; asset IDs remain `NOT_ASSESSED` until source-backed locally. |
| 2. Scope + schema | Agree per-character vs. party ownership and joining/leaving policy with the real downstream contract; inspect target migrations, reserved IDs, install order, conflicts, and rollback. | `CAMPAIGN-ADMISSION` remains pending until the actual baseline is available. |
| 3. Server state | Implement persistence, authorization, proximity/eligibility checks, revision/idempotency handling, and reward transaction without client authority. | Synthetic tests plus negative tests for duplicate, concurrent, restart, replay, and invalid-intent cases. |
| 4. Content package | Generate/author only after target IDs and schemas are reviewed; include migration, rollback, ownership manifest, compatibility and acceptance instructions. | Disposable DB apply/reapply/rollback; non-overwriting package publication. |
| 5. Runtime | Compile against the exact compatible core; test a clean 3.3.5a client, solo, duo, bots, late join/leave, death, disconnect, restart, and inventory failure. | `ARENA-COMPILE`, then separate client/server/Playerbot/Campaign Party acceptance. |
| 6. Live qualification | Verify the complete mission and reward flow on the downstream runtime with provenance and reviewed data. | Only then may the applicable production-live gate pass. |

The design's acceptance list is intentionally stricter than “the quest completes”: three clues must be distinct and order-independent; rescue and defense prerequisites cannot be skipped; retries cannot duplicate progress; the choice cannot be rewritten; and no unverified item or currency is granted. A rollback must remove only this feature's rows and state. It must never overwrite or replace a user-owned MPQ.

## Backlog enabled by the same pattern

After this operation is qualified, candidate reuses are:

- **Caravan:** select a route and protect a moving supply objective; fail safely without a required real-time escort wait.
- **Survey/race:** checkpoint course or timed mastery result in a large flat area; best-time record is optional, not a required grind.
- **Field research:** observe a behavior and choose what evidence to bring back, not a hundred-kill bar.
- **Town defense:** start on demand and end when a finite set of objectives is resolved.
- **Darkmoon/Midsummer remix:** borrow an existing event's theme/assets after local verification, but avoid calendar-only access and don't change global event state for personal progress.
- **Outpost restoration:** finite steps that unlock a contact or mission route; defer persistent shared-world phase changes until ownership and rollback are solved.

These are opportunities for reuse, not commitments or confirmed compatibility claims. High-risk global world scaling, custom maps, flight unlocks, and wholesale raid retuning are intentionally outside the first slice.

## Current evidence

| Claim | Status |
|---|---|
| Mission concept, boundaries, and state transitions documented | **PASS — design review only** |
| Python reference state-machine tests | **Synthetic only**; run the targeted unittest command above |
| Verified build-12340 stock asset IDs and positions | **NOT_ASSESSED** |
| Foundry-generated or installable gameplay package | **NOT_IMPLEMENTED** |
| AzerothCore module, schema migration, rollback and DB proof | **NOT_ASSESSED** |
| Client addon/runtime | **NOT_ASSESSED** |
| Playerbot movement/interaction and Campaign Party admission | **NOT_ASSESSED** |
| Live gameplay / production qualification | **NOT_ASSESSED** |

The next meaningful engineering step is the local stock-content audit and target ownership/schema decision—not inventing SQL IDs or presenting the synthetic model as a playable feature.
