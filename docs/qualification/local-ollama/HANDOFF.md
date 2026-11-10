# Conflux local inference qualification

**Handoff verdict** — Arena can resume. The live Ollama path passed for one bounded task at Conflux `e5a1c761f612a28ddccee037261a6071379787a4` (PR #7 merged). Record Arena handoff item 2 as qualified for this configuration. Keep the broader provider and deployment checklist items open.

## Verified run

- The Conflux gateway ran in production mode in a temporary Docker Compose project. It used the repository Compose bridge and `host.docker.internal`. A temporary relay accepted only the qualification container's matching `/api/chat` request and forwarded it to the existing loopback-only Ollama service. No provider key or model download was needed.
- The image used the verified Conflux source with the cached `python:3.12-slim` base, which ran Python 3.12.14. The repository's Python 3.11 base was absent from the local cache. The Compose override supplied an isolated image tag, an ephemeral loopback port, and a disabled automatic restart policy.
- Ollama `0.40.15` ran the installed `phi4-mini:3.8b-q4_K_M`, digest `78fad5d182a7c33065e153a5f8ba210754207ba9d91973f57dffa7f487363753`. The prompt asked for a clause from the opening paragraph of Meridian's README at `3282944eeb4265d9a07caac0dbf8502528bccce0`. The returned clause matched after ignoring final punctuation. The published evidence replaces completion text with its hash and records the source, excerpt, and prompt hashes.
- `POST /v1/tasks` returned `202`. Task `T-05967764E35F44368E06EC85CA61B9F3` reached `COMPLETED` with one successful worker attempt and one committed receipt. The relay captured 72 input and 9 output tokens directly from Ollama. The response content hash and 81 total tokens matched the receipt and project metrics. The call was unpriced in Conflux.
- The gateway restarted against the same named volume and acquired a new loopback port. The committed receipt survived. Resubmitting the same request and idempotency key returned `200`, `duplicate: true`, and the original task ID. Metrics were identical and the relay recorded one inference dispatch in total. The audit chain verified across 11 events.
- The temporary container, network, volume, and tagged image were removed. All 133 existing tests passed separately on host Python 3.12.3 with loopback enabled. The test run emitted the multithreaded `fork` warning.

## Retained findings and next work

- The initial Granite 3B run reached the real provider. Ollama consumed 87 input plus 256 output tokens, reached its length limit, and returned an empty visible completion. Conflux rejected the task after 343 tokens were already consumed against a 256-token task budget. The router passes the remaining total allowance as the provider output cap and checks combined usage after the response. Treat this as post-response enforcement until input accounting can reserve space before dispatch.
- The first Phi-4 Mini run completed and recorded matching usage. Its restart check timed out because the harness reused the initial ephemeral port. The passing run rediscovered the port and verified the new address, stored receipt, metrics, and deduplication.
- Hosted provider and billed-cost qualification, provider-side cancellation, authoritative context limits, concurrent deployment load, independently initialized workers, and real service-side idempotency remain open. Gateway resubmission deduplication covers the same completed Conflux task. The test did not involve external side effects.
- The separate OpenAI-compatible usage fixture finding remains available for Arena. Empty, null, and partial usage can be marked as complete provider-reported accounting. This run exercised the Ollama adapter.

## Evidence and reproduction

- `evidence-qualified.json` contains the passing run.
- `evidence-attempt-1.json` contains the Granite budget failure.
- `evidence-phi4-mini.json` contains the first Phi-4 Mini run with the harness restart timeout.
- `qualify.py` uses `source-blobs.json` to check the input Conflux snapshot before building and running. Use a separate Conflux checkout at the tested commit, since the checklist changed in this handoff. On a Linux host with Docker Compose, the indicated local model, and both source trees, run `python3 qualify.py CONFLUX_SOURCE MERIDIAN_REPO NEW_EVIDENCE_JSON` from this directory. The default is Phi-4 Mini. To reproduce the initial configuration, pass `--model granite4.2:3b`.
- `SHA256SUMS` seals the handoff files for transfer.
