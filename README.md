# Conflux

> A unified agentic intelligence platform that transforms objectives into coordinated, resource-efficient, independently verifiable execution across AI models, specialist agents, software tools, data systems, and persistent workflows, under centralized governance and durable task custody.

## Current implementation

Conflux is the project implementation of the architecture described in
[`docs/unified-agentic-system/`](docs/unified-agentic-system/). The prioritized
remaining development goals are tracked in the
[Conflux development checklist](docs/DEVELOPMENT_CHECKLIST.md):

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
  memory.py         # access-scoped conversation memory (separate from ledger/evidence)
  gateway.py        # HTTP API: /v1/tasks, /v1/tools, /v1/roles, /v1/metrics,
                    #   /v1/memory/{project}, SSE /v1/tasks/{id}/events/stream,
                    #   /v1/automation/trigger|callbacks, approve/reject
  server.py         # entry point
cli.py              # CLI gateway client
tests/
  test_acceptance.py          # acceptance cases from manual section 5.10
  test_expansion.py           # adapter, model router, role and automation tests
  test_approvals_streaming.py # durable approvals, memory, metrics, hash checks
  test_workflows.py           # 4 e2e tests: ch.4 research flow, budget limits, impact trace
  test_scheduler.py           # 2 tests: wave-parallel DAG execution, crash recovery
  test_async_api.py           # 3 tests: 202 async submit + poll, sync mode, artifact download
  test_security_delivery.py   # 3 tests: prompt-injection quarantine, graph staleness, signed webhook delivery
  test_tenant_workflows.py    # tenant workspace isolation, traversal, ch.4.2 repo-change flow
  test_investigate_metrics.py # 3 tests: ch.4.3 static investigation, IOC extraction, metrics
  test_qualification.py       # Phase-6 qualification: all four ch.4 workflows + trace + typed dataset
  test_health.py              # liveness/readiness, no-write probes, response headers
  test_production_config.py   # fail-closed production auth, mounted secret files, web allowlist
  test_ui_console.py          # console contracts, task grants, metrics/memory scope
  test_infra_listing.py       # 2 tests: compose service-links parsing, scoped paginated task list
  test_auth_ratelimit.py      # 5 tests: 401/403 auth, 429 token bucket
  test_audit_dlq.py           # 4 tests: hash-chained audit tamper detection, SSRF, 4xx/5xx DLQ
```

Run the stdlib-only suite with `python3 -m unittest discover -s tests`; CI also
compiles the package, checks the UI script, builds the production image, validates
Compose, and runs a container-level production smoke test using mounted test
secrets. Docker must be installed to run the container/Compose portion locally.

Ledger events are SHA-256 hash-chained (`GET /v1/audit/verify`); webhook delivery
blocks non-HTTPS and private/metadata IPs (SSRF) and routes 4xx immediately /
5xx after retries to a dead-letter queue.
```

The gateway requires `Authorization: Bearer <api-key>` on all `/v1/*` routes
(401 missing, 403 invalid, 429 + `Retry-After` over the token-bucket limit);
`/healthz`, `/readyz` and the UI stay open. `demo.sh` shows a full live run.
Configure API keys as a JSON object mapping bearer tokens to project IDs and
tool grants (for example `{"token-a":{"projects":["project-a"],"grants":["repo.read"]}}`). Local runs can use `UNIFIED_API_KEYS`; production Compose uses `UNIFIED_API_KEYS_FILE` mounted from a protected secret file. Either explicit configuration disables the built-in wildcard `dev-key`, and omitted grants mean no tool permissions. Approval and cancellation also require explicit `task.approve` and `task.cancel` grants.

The production Compose profile sets `UNIFIED_ENV=production` and fails startup
unless API keys have explicit non-wildcard project scopes and a non-placeholder
callback secret is configured. Compose mounts secrets from protected files (the
`.env` file contains paths only); prepare them as described in `.env.example`.
`.dockerignore` excludes local state, secret files, tests and VCS data from the
image build context. Compose publishes only on loopback for a TLS-terminating
reverse proxy, bounds container memory/CPU/processes, and drops Linux
capabilities.

Production workspaces and code graphs are namespaced per project; workspace
browsing requires `repo.read`. Existing unscoped workspace files/indexes are not
auto-assigned: migrate or reindex them per project before rollout. The container
runs as UID 10001; ensure an existing named volume's `/data` is writable by that
UID. Runtime metrics are project-scoped; global audit verification requires
`ops.read`, and memory reads/writes require `memory.read` / `memory.write`.
Outbound fetching remains disabled unless `UNIFIED_WEB_ALLOWLIST` explicitly
names audited HTTPS hosts; fetches reject redirects, non-public DNS results,
and responses over 2 MiB. Health probes are read-only, responses include
`no-store` and browser security headers. Request bodies are capped at 1 MiB,
and task/event history reads are SQL-bounded.
```

`codegraph.service_links` (14 tools) parses compose `depends_on` into service
edges; `GET /v1/tasks?limit=&offset=` is project-scoped and paginated. The zero-build
operator console at `/` provides guided task forms for every planner intent,
Chapter 4 workflow presets, tenant-scoped task/approval inspection, workspace
browsing, capabilities and memory, metrics, and audit-chain verification.
```

`demo.sh` runs a full live walkthrough (health → tools → async submit → poll →
evidence citations → metrics → idempotent dedupe). `/healthz` and `/readyz`
follow the liveness/readiness split; compose secrets come from `.env`
(see `.env.example`).
```

`codegraph.trace` gives cycle-safe transitive upstream callers (13 tools);
`openbb.query` accepts a `dataset_uri` artifact for a schema-validated typed path
when provider extensions are absent (units/metadata preserved, never fabricated).
```

`investigate` runs the ch. 4.3 flow (static-only `rea.inspect` with IOC/entropy
triage → optional graph correlation; runtime observation stays unauthorized).
`GET /v1/metrics/summary` exposes dashboard metrics: success rate, tool error
rate by tool, p50/p95 latency, token usage.
```

Multi-project isolation: every task read requires a matching `X-Project-Id`
header (403 on cross-project access). The verifier applies the verifier role's
rubric as deterministic checks; `repo_change` runs the ch. 4.2 flow
(index → impact → approved sandboxed write → verified report).
```

Security/delivery: fetched content is quarantined as `untrusted-data` evidence
with numbered citation handles (`GET /v1/tasks/{id}/evidence`), `codegraph.freshness`
reports index staleness, and completed results can be delivered to a signed
webhook (`delivery.webhook_url`, HMAC-SHA256 + timestamp).
```

Tasks run asynchronously by default (`POST /v1/tasks` → `202 Accepted` +
`status_url`; use `?sync=1` for synchronous execution). Artifacts download at
`/v1/artifacts/{task}/{step}/{name}` with an `X-Sha256` integrity header.
`Dockerfile` + `docker-compose.yml` provide the hardened single-host deployment
(gateway is the only published port; state on a named volume).
```

The scheduler executes independent ready DAG steps in parallel waves (tool
parallelism; model calls serialize on the shared router queue) and reconciles
interrupted tasks at startup, reusing committed receipts so side effects are
never repeated.
unified/ui/index.html       # zero-build web UI (vanilla JS + SSE) served at /
```

Hard per-task budgets (`tool_calls`, `time_seconds`) are enforced at step level
and fail with a typed `budget_exceeded` receipt. Open `http://localhost:8080/`
for the web UI.

Side-effecting tool steps (e.g. `native.fs.write`) pause the run in a durable
`WAITING_APPROVAL` state and execute exactly once after `POST .../approve`;
`POST .../reject` cancels. Step IDs are deterministic per task, so committed
receipts and approvals survive restarts and resumes.

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

- **Conflux — Unified Agentic Intelligence Platform Manual** in
  [`docs/unified-agentic-system/`](docs/unified-agentic-system/). Start with
  `Unified_Agentic_System_Manual.html` (fully self-contained, works offline).
