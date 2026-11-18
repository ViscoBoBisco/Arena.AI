# Vault Terminal — 3.3.5a client acceptance checklist

This is a manual handoff for an operator with a legal WoW 3.3.5a / build-12340 client and a disposable pinned AzerothCore server. It supplements, but is not replaced by, generated-output tests and the Lua 5.1 API-shim test. No game client is included in this repository. A Lua shim pass is not a client qualification.

## Preconditions

- [ ] Record client build from the login screen; it must be **12340**. Do not change the target version to make another client pass.
- [ ] Record the exact AzerothCore commit, module build ID, world/Characters schema versions, and the package/spec SHA-256 values used for this run.
- [ ] Use an isolated disposable server and Characters database. Do not run claims or migration checks against production or shared player data.
- [ ] From the repository root, build and verify the exact package under test with `python3 tools/foundry.py build specs/vault-terminal.json --out /tmp/vault-terminal`, then `python3 tools/foundry.py verify /tmp/vault-terminal`.
- [ ] Install `/tmp/vault-terminal/server/mod-vault-terminal/` into the core's `modules/`, compile and load it. The generated config is `/tmp/vault-terminal/conf/vault-terminal.conf`; its exact setting is `VaultTerminal.Enable = 1` (case-sensitive), matching `specs/vault-terminal.json` and the C++ reader. Do not use `vault_terminal.Enable`.
- [ ] Apply `/tmp/vault-terminal/sql/characters/vault_terminal_claims.sql` to the disposable Characters database. On an older per-day schema, verify rows are aggregated into the current `vault_terminal_claims` counter and preserved in `vault_terminal_claims_legacy_v1`; the durable receipt table is `vault_terminal_claims_requests`.
- [ ] Enable addon chat transport in the host (`AddonChannel = 1`). Preserve the counter, receipt table, and any legacy backup during disable/uninstall so character history and retry protection remain available.
- [ ] Copy only the generated `VaultTerminal` addon folder to the test client's `Interface/AddOns/`. Do not copy a client or proprietary client data into this repository.
- [ ] Enable Lua error display with `/console scriptErrors 1`, then `/reload`. Confirm the addon is enabled in the AddOns list and its TOC shows `## Interface: 30300`.

## Interaction checks

For each check, record the client build, character GUID (in private test notes only), server log excerpt, and before/after SQL state. Redact account names and credentials.

- [ ] Run `/vt`; the panel opens, sends `VT1<TAB>HELLO<TAB>2<TAB>1.0.0` (protocol version plus addon version) over a self-WHISPER, and shows server-derived count, rank, and daily availability.
- [ ] Close and reopen with `/vt`; repeat with `/vaultterminal`. The existing panel is reused, one event handler remains registered, and each opening requests a fresh snapshot. If a claim ID is still pending locally, the addon resubmits that same ID after HELLO to recover its durable receipt; it must not mint a new ID merely because the snapshot now shows no claim available.
- [ ] Click Claim once. Record the sent request ID, one `vault_terminal_claims` increment, and the matching `vault_terminal_claims_requests` receipt (`GRANTED`, request ID, resulting total), followed by the refreshed `S` snapshot. The addon never grants an item or currency.
- [ ] Replay the same request ID using the approved test harness: it must return the original receipt without another increment. A distinct same-day request ID must receive a truthful `DENIED_COOLDOWN` receipt. The normal UI may disable Claim after its authoritative snapshot reports no claim available.
- [ ] Restart the worldserver and relog/reopen. Count, rank, same-day cooldown, and the previously committed request receipt must match the retained Characters data.
- [ ] With the feature disabled on a disposable server, reopen the panel. It shows the server's disabled/BYE response and permits no claim; after re-enable, Refresh can recover and fetch a new snapshot.
- [ ] Disconnect or suppress the server response. The panel reports timeout, retains no stale actionable state, and Refresh recovers after connectivity returns.
- [ ] Verify there are no Lua errors, broken frames, duplicate handler effects, or chat-channel errors during repeated close/open/reload cycles.

## Negative and boundary checks

- [ ] Using an approved test harness or a controlled server test seam, send wrong prefix, wrong sender, wrong distribution, unsupported version, malformed fields, non-ASCII data, unknown opcodes, and frames over 255 bytes. None may alter Characters state. Do not use a retail or production character for packet tests.
- [ ] Race two distinct test sessions for one character/day. At most one receives `GRANTED`; each response's request ID and receipt status must match that request, the other receives `DENIED_COOLDOWN`, and the counter increments at most once. The SQL-level cap alone is not proof that each response is truthful.
- [ ] With a disposable clock fixture, test the server-defined day boundary and target cap. A new day grants at most one additional claim; reaching the target never increments past it.
- [ ] Confirm migration reapply is harmless, including an upgrade from legacy per-day rows. Disable/rollback retains `vault_terminal_claims`, `vault_terminal_claims_requests`, and `vault_terminal_claims_legacy_v1` when present. Do not drop player-state tables as part of feature disable.

## Result classification

Record each item as `PASS`, `FAIL`, or `NOT_ASSESSED`, attach raw redacted logs and SQL observations, and identify who ran the test. Only checks actually performed by a compatible client count as client evidence. CI compilation, disposable DB tests, static API scans, and a Lua 5.1 shim remain their respective narrower evidence; they do not establish client or Campaign Party qualification.
