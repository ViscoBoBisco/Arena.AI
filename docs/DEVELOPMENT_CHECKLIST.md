# Conflux Development Checklist

> A unified agentic intelligence platform that transforms objectives into coordinated, resource-efficient, independently verifiable execution across AI models, specialist agents, software tools, data systems, and persistent workflows, under centralized governance and durable task custody.

This is the consolidated development direction for Conflux. Items are grouped by priority; unchecked items remain goals to be implemented and qualified.

## P0 — Core execution integrity

- [x] **1. Harden task custody and state transitions.** Establish authoritative task identities, versioned plans, atomic state transitions, durable step and attempt records, and correct handling of partial execution and concurrent workers.
- [x] **2. Guarantee safe execution recovery.** Record operation identity before dispatch; reconcile interrupted side effects, reuse committed receipts, implement appropriate idempotency, and never blindly repeat actions with unknown outcomes.
- [x] **3. Enforce execution budgets and cancellation.** Enforce tool/model-call, cumulative token, wall-clock, per-engine concurrency, CPU, RLIMIT_AS address-space ceilings, and external-request limits. Run capability tools and model calls in supervised POSIX workers; propagate cancellation/deadlines, persist CPU usage, and preserve ambiguous side effects for recovery. Worker RSS high-water observations are measured separately where available. These resource controls do not provide filesystem, network, credential, privilege, or process isolation and must not be described as a security sandbox; per-engine concurrency is not a deployment-wide cap.
- [ ] **4. Correct usage accounting and telemetry.** Partially implemented: dispatch boundaries, retry/failure/unknown counts, elapsed time, worker CPU, measured peak RSS, the separate RLIMIT_AS ceiling, actual-versus-estimated token source, provider-call counts, configured rate-card estimates, and explicitly uncertain token/cost/effect counts are persisted or reconstructed from durable receipts/attempts across restarts. The local Ollama run matched provider token counts to the committed receipt and metrics after restart. Still required: qualify other live providers, capture vendor-billed costs where exposed, expose all fields cleanly in operator UI, and verify throughput/resource semantics under real deployment load. Never label a rate-card estimate as provider-billed actual cost.

### Deferred recovery qualification (items 1–2 remain accepted)

- [x] **Structured recovery reporting.** Return one durable outcome per task (`completed`, `resumed`, `still_unresolved`, `already_running`, or `recovery_failed`) and retain errors for inspection; never report unresolved tasks as recovered successes.
- [x] **Forced process-termination qualification.** A separate supervised tool worker is terminated after a simulated durable external side effect and measurable CPU work; the attempt remains `UNKNOWN`/`RECOVERY_REQUIRED`, CPU usage remains ledgered, and recovery does not dispatch a duplicate.
- [ ] **Adapter-specific idempotency contracts.** Document and qualify each side-effecting adapter's real service-side idempotency and reconciliation guarantees across crashes, timeouts, and network partitions; do not infer these guarantees from the generic broker key propagation test. Generic key propagation remains insufficient evidence.
- [ ] **Worker start-method qualification.** Gateway requests run on HTTP worker threads while capability workers currently use `fork`; audit inherited-lock/library state and qualify a safer independently initialized worker topology before treating this as deployment-ready. Resource limits are not a security sandbox. Measured blocker: the broker is not picklable (native tool handlers are local closures), so `spawn` or `forkserver` needs a worker that rebuilds its registry from serializable configuration. Python 3.12 warns on `fork` in multithreaded processes; the gateway test run still emits that warning.

## P1 — Intelligence, planning, and verification

- [ ] **5. Integrate actual AI inference.** A provider-independent completion interface supports configured OpenAI-compatible endpoints and Ollama alongside an explicitly simulated development provider. Provider-reported usage and configured rate-card estimates are distinguished, and production rejects simulated inference. Live OpenAI-compatible qualification, provider/model discovery, authoritative context-window negotiation, streaming, and deployment cancellation behavior remain open.
  - [x] **Bounded live Ollama qualification.** A production-mode Compose gateway completed one Phi-4 Mini task, retained its receipt and usage after restart, and deduplicated the same submission without a second inference call. See the [qualification handoff](qualification/local-ollama/HANDOFF.md) for the exact configuration and the observed token-budget overrun.
- [ ] **6. Develop intelligent model routing.** Select models by capability, task complexity, context, modality, resource availability, latency, and cost. Support controlled fallback and GPU-aware inference admission.
- [ ] **7. Implement adaptive task planning.** Translate arbitrary objectives into validated dependency graphs rather than relying exclusively on hardcoded intents. Support replanning without silently increasing permissions or altering previous execution records.
- [ ] **8. Implement genuine inter-step data flow.** Make each step's typed outputs, artifacts, and evidence available to dependent steps. Resolve references securely and avoid unnecessary duplication of large inputs.
- [ ] **9. Operationalize specialist-agent collaboration.** Make specialist profiles usable for task assignment, delegated work, review, and synthesis. Coordinate shared models and context without unnecessarily spawning redundant autonomous agents.
- [ ] **10. Build comprehensive evidence provenance.** Preserve captured source identity, timestamps, hashes, precise locators, transformation history, artifact references, and relationships between source material and derived findings.
- [ ] **11. Separate observations, interpretations, and conclusions.** Maintain explicit distinctions between retrieved evidence, deterministic calculations, model inference, and final claims so information cannot silently acquire greater authority through repeated summarization.
- [ ] **12. Implement independent verification.** Replace hardcoded success assertions with actual source checking, artifact hash validation, schema checks, calculations, test execution, contradiction detection, and task-specific review.
- [ ] **13. Make verified completion authoritative.** Require validation before marking work complete. Represent partial success, failed checks, unsupported capabilities, and inconclusive findings explicitly.

## P2 — Integrations, memory, and operations

- [ ] **14. Strengthen the capability broker.** Define stable typed contracts for inputs, outputs, permissions, side effects, resource needs, availability, cancellation, idempotency, and version identity. Discover actual installed capabilities dynamically.
- [ ] **15. Complete the seven upstream integrations.** Implement functional AgenticSeek planning, Scrapling acquisition, OpenBB data providers, n8n workflows, Agency Agents specialist profiles, codebase-memory-mcp source intelligence, and REA software investigation. Prefer supported upstream interfaces over unnecessary rewrites.
- [ ] **16. Expand persistent memory and retrieval.** Separate task, conversation, project, operational, and reusable knowledge. Support scoped retrieval, provenance, freshness, invalidation, retention, and efficient context assembly.
- [ ] **17. Develop resource-aware concurrent scheduling.** Coordinate independent I/O, CPU, model, and specialist operations through bounded queues, dependency-aware scheduling, capacity reservations, priorities, and backpressure.
- [ ] **18. Optimize computational efficiency.** Introduce deterministic preprocessing, reusable artifacts, version-aware caching, incremental indexing, compact model context, and measured offloading to specialized tools.
- [ ] **19. Make automation and delivery durable.** Persist schedules or workflow execution references, callback state, delivery attempts, retries, acknowledgments, and dead-letter queues. Reconcile duplicate events and failed external actions.

## P3 — Security, product, and readiness

- [ ] **20. Complete security and isolation.** Enforce least privilege, tenant separation, credential protection, scoped file access, sandboxing, network egress restrictions, SSRF defenses, prompt-injection boundaries, and independent audit integrity.
- [ ] **21. Expand the operator interface.** Provide plan inspection, dependency visualization, real-time progress, model/tool activity, approvals, cancellation, artifact navigation, evidence inspection, provider health, and understandable error reporting.
- [ ] **22. Build observability and operational controls.** Add searchable execution traces, structured metrics, alerts, health checks, incident diagnostics, capacity monitoring, and clear separation between development and production operation.
- [ ] **23. Establish comprehensive qualification.** Test real integrations, end-to-end workflows, crash recovery, duplicate execution, adversarial inputs, tenant isolation, verification failures, resource overload, performance, and regressions. Track readiness per capability.
- [ ] **24. Prepare sustainable deployment and maintenance.** Provide reproducible builds, pinned dependencies, migrations, backups, restoration tests, secret rotation, upgrade procedures, release management, and upstream licensing review.
