# Conflux Development Checklist

> A unified agentic intelligence platform that transforms objectives into coordinated, resource-efficient, independently verifiable execution across AI models, specialist agents, software tools, data systems, and persistent workflows, under centralized governance and durable task custody.

This is the consolidated development direction for Conflux. Items are grouped by priority; unchecked items remain goals to be implemented and qualified.

## P0 — Core execution integrity

- [x] **1. Harden task custody and state transitions.** Establish authoritative task identities, versioned plans, atomic state transitions, durable step and attempt records, and correct handling of partial execution and concurrent workers.
- [x] **2. Guarantee safe execution recovery.** Record operation identity before dispatch; reconcile interrupted side effects, reuse committed receipts, implement appropriate idempotency, and never blindly repeat actions with unknown outcomes.
- [ ] **3. Enforce execution budgets and cancellation.** Apply real limits to tool calls, model calls, tokens, time, concurrency, CPU, memory, and external requests. Support cancellation, timeout propagation, and explicit budget-exceeded results.
- [ ] **4. Correct usage accounting and telemetry.** Preserve actual provider usage through the broker, receipts, ledger, and UI. Measure latency, errors, throughput, resource consumption, and costs, including after restarts.

## P1 — Intelligence, planning, and verification

- [ ] **5. Integrate actual AI inference.** Replace the simulated model with working local and/or hosted models through a provider-independent interface supporting discovery, context limits, streaming, cancellation, errors, and usage reporting.
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
