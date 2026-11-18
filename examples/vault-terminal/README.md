# The Vault Terminal — a server-fed interface with no client patch

The counterpart to [`ashen-vault`](../ashen-vault/README.md). Where that example proves you can add
*content* without touching the client, this one proves you can add an **interface**.

A player types `/vt` and a movable panel opens showing their seals, their rank, and whether today's
claim is available. The numbers come from the server in real time. Nothing is installed into the MPQ,
no DBC row exists for it, and no launcher is involved — because a 3.3.5a interface is an addon, and an
addon is a folder.

| Piece | Where it lives | Needs a client patch? |
|---|---|---|
| The panel (Lua, XML-free) | `Interface/AddOns/VaultTerminal/` | no — a folder |
| The truth (seals, cooldown, rank, receipts) | server module + Characters tables `vault_terminal_claims` and `vault_terminal_claims_requests` | no |
| The channel | `AddonChannel = 1` in `worldserver.conf` | no |

| | |
|---|---|
| **Patch cost** | none — the client-side piece is an addon folder (catalog kind `client-files`), not an MPQ |
| **Reused client assets** | default UI textures and font paths, and the chat channel itself |
| **Clock** | 1–2 — a server module plus an addon folder, no archive |

## What is here

```
PROTOCOL.md                          the wire format: framing, opcodes, limits, rules, vectors
harness/vt_model.py                  the same protocol as executable Python: replay, fuzz, clock
addon/VaultTerminal/VaultTerminal.toc
addon/VaultTerminal/VaultTerminal.lua   the panel: request, render, degrade
server/mod-vault-terminal/            reference module: hook, parse, reply, storage
sql/01_module_table.sql               characters DB: counter + durable request receipt tables
sql/02_terminal_npc.sql               world DB: the NPC that explains /vt (and a keypad option)
sql/99_rollback.sql                   removes the world objects and names the drop
```

## Install

1. **Addon** — copy `addon/VaultTerminal/` into `Interface/AddOns/VaultTerminal/` on the client and
   enable it at the character screen. No MPQ rebuild; the folder is the install.
2. **Config** — `AddonChannel = 1` in `worldserver.conf` (it gates the whole channel; without it the
   server never sees the message).
3. **Database** — run `sql/01_module_table.sql` against the **characters** DB, `sql/02_terminal_npc.sql`
   against the **world** DB. Replace the `TODO(verify)` display ID with one that exists in your
   `CreatureDisplayInfo.dbc`.
4. **Module** — install `server/mod-vault-terminal/` into `modules/`, enable modules in CMake, and rebuild.
   Its request transaction uses the pinned AzerothCore synchronous Characters-database APIs; generated-source
   compile and disposable-MySQL transaction qualification are separate release gates.
5. **Restart** the worldserver (config and module load) and log in.

## What the player sees

```
        Vault Terminal
  Seals    12 / 50
  Rank     Adept
  Claim    a seal is available (1 left today)
  [ Claim seal ]
```

Clicking **Claim seal** sends a claim intent with a persisted request ID. The server serializes the
counter update and immutable receipt in one transaction, then answers `RCPT` for that request plus a
fresh snapshot. A repeated request ID replays its original receipt; a timeout or addon reopen retries
the same ID rather than risking a duplicate claim. A distinct same-day request receives a
`DENIED_COOLDOWN` receipt.

If the server never answers (module missing, channel off, restart in progress), the panel goes to
`—` and says **"no server response"** after ten seconds. That honesty is a protocol rule, not a
politeness: a panel showing stale or invented numbers is worse than a panel that admits it is blind
(`PROTOCOL.md` rule 6).

## Acceptance checks

| Check | How |
|---|---|
| The panel opens | `/vt` — window appears, can be dragged, closes on `/vt` |
| The handshake happened | server log shows no unknown-command noise; panel fills within a second |
| State is server truth | change the row count in `vault_terminal_claims`, `/vt` again — the number moves |
| The action works | click Claim → matching `GRANTED` receipt and snapshot; seals increments by one |
| The action is guarded | submit a new same-day request → `DENIED_COOLDOWN`, seals unchanged |
| Request retries are safe | replay the same request ID, including after timeout/reopen → original receipt; no second increment |
| Concurrent requests are serialized | race distinct request IDs → at most one daily grant and each request gets its own durable outcome |
| Degradation works | stop the worldserver, click Claim → panel shows `—` and "no server response" |
| Version refusal works | set `PROTOCOL_VERSION` to `9` in the Lua → server answers `ERR VERSION`, panel says so |
| Nobody can spoof it | `SendAddonMessage` with a hand-written `VT1\tCLAIM\\tforged-id` from a macro → still one claim per day |
| No client files changed | `git status` in your patch repo is empty; `Interface/AddOns` is not in the MPQ |
| The protocol is well-formed offline | `python3 harness/vt_model.py` prints the conversation in `PROTOCOL.md`; `make test-master` fuzzes it |

## Where this stops being free — and what would need a patch

The client's interface is Lua running in a sandbox, and addons are how Blizzard itself authored the
default UI. Shipping a folder is not a modification of the client; it is the client's own extension
mechanism. The server side is a `PlayerScript` hook plus a chat packet, both stock APIs.

What *would* need a patch: a panel that must exist for players who refuse to install addons (there is
no way to force-load Lua into the client from the server), a new font or texture, or a new minimap
icon drawn from art that does not exist. Everything else in the table above is free.

## Security notes

- Treat every field from the client as hostile. `CLAIM` carries only an opaque, validated request ID;
  the server decides what a claim means and persists a receipt bound to the character and that ID.
- Never let the addon compute a reward, a price, or a cooldown; render what you are told.
- The prefix is namespaced (`VT1`) so the core's own `AzerothCore\t` command channel is untouched —
  driving that channel from an addon executes commands with the player's security, which is a very
  different thing from this protocol.
- 255 bytes per message is a hard cap enforced by the core; the reference client refuses to send
  larger payloads rather than letting the core drop them silently.

## Rollback

`sql/99_rollback.sql` removes the world objects by pinned ID (`900300`, spawn `9003001`). Disable the
module and remove the addon; retain the counter and receipt tables to preserve progress and request
replay protection. If migrated from the earlier daily-row schema, retain its `_legacy_v1` backup too.
Purging any of these requires a separate reviewed action after backup. Nothing else in the game
references these assets, which is the practical benefit of a reserved ID range
([docs/04 §4](../../docs/04-patching-toolchain-and-release.md)).
