# Arena.AI

## Unified Agentic Intelligence Platform — core build

A working implementation of the Phase 1–2 vertical slice from the architecture
manual in [`docs/unified-agentic-system/`](docs/unified-agentic-system/):

```
unified/
  contracts.py      # canonical task envelope, tool call/receipt, state machine (manual 5.1-5.4)
  ledger.py         # durable SQLite task ledger: tasks, steps, attempts, committed receipts
  policy.py         # grant-based admission + pre-execution authorization (manual 3.3)
  broker.py         # capability broker, typed receipts, native sandboxed tools
  adapters.py       # scrapling.fetch (allowlist), openbb.query, codegraph.index/find, rea.inspect
  model_router.py   # bounded inference queue + token budget (manual 3.6)
  role_registry.py  # immutable versioned specialist profiles (manual 3.5)
  automation.py     # signed n8n-style callbacks (HMAC+timestamp+replay) + triggers (manual 4.4)
  planner.py        # plan(TaskEnvelope) -> StepDAG (AgenticSeek adapter stand-in)
  engine.py         # canonical path: gateway -> ledger -> plan -> broker -> evidence -> verifier
  verifier.py       # evidence-based result assembly with conflicts/failures surfaced
  gateway.py        # HTTP API: /v1/tasks, /v1/tools, /v1/roles, /v1/automation/*
  server.py         # entry point
cli.py              # CLI gateway client
tests/
  test_acceptance.py  # 9 tests from the manual's section 5.10 acceptance suite
  test_expansion.py   # 13 tests for adapters, model router, roles, automation
```

### Run

```bash
python3 -m unified.server --port 8080        # gateway on 0.0.0.0:8080
python3 -m unittest discover -s tests -v     # acceptance suite
```

### Try it

```bash
curl -X POST localhost:8080/v1/tasks -d '{"project_id":"demo","idempotency_key":"k1","request":{"intent":"echo","instructions":"hello"},"permissions":[]}'
curl localhost:8080/v1/tools
```

Idempotent submission, policy denial, dependency ordering, restart/receipt
recovery, cancellation, artifact hashing, prompt-injection resistance,
conflict surfacing and typed tool failures are all covered by the tests.

## Documentation

- **Unified Agentic Intelligence Platform — Architecture Manual** in
  [`docs/unified-agentic-system/`](docs/unified-agentic-system/). Start with
  `Unified_Agentic_System_Manual.html` (fully self-contained, works offline).
