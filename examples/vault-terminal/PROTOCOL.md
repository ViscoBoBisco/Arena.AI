# VaultTerminal protocol, version 1

A request/response protocol between a 3.3.5a addon and the server, carried on the addon chat channel.
It exists to prove one thing: **a live custom interface needs no client patch, no DBC row and no MPQ.**

## Transport

| Property | Value |
|---|---|
| Channel | addon chat (`LANG_ADDON`, message type `CHAT_MSG_ADDON`) |
| Client → server | `SendAddonMessage("VT1", payload, "WHISPER", UnitName("player"))` — a self-whisper |
| Server → client | `SMSG_MESSAGECHAT` built as `CHAT_MSG_WHISPER`, `LANG_ADDON`, sender = receiver = the player |
| Maximum payload | **255 bytes** hard cap (the core drops longer addon messages) |
| Encoding | ASCII; `\t` separates fields, no field contains a tab |
| Framing | one message = one record; no continuation, no binary |

The server side of that table is exactly what the core does for its own addon protocol —
`AddonChannelCommandHandler::Send()` builds `CHAT_MSG_WHISPER`/`LANG_ADDON` with the player as both
sender and receiver. A module copies the mechanism, not the prefix.

## Prefix collision

AzerothCore already speaks on this channel with the prefix `AzerothCore\t` and opcodes
`p` (ping), `h` (human-readable command), `i` (command), replying with `a`/`o`/`f`/`m`. Its parser
**only** claims messages that start with that exact 12-byte prefix — everything else falls through to
normal chat handling.

Therefore: **pick a prefix the core does not own, and claim it in your own hook.** This example uses
`VT1`. Never send `AzerothCore\t…` unless you are deliberately driving the core's command channel.

## Grammar

```
message   := prefix TAB opcode [ TAB field ]*
prefix    := "VT1"
opcode    := 1*8 ALPHA
field     := 1*64 printable ASCII      ; no TAB, no CR, no LF
```

Client → server:

| Opcode | Fields | Meaning | Server must answer with |
|---|---|---|---|
| `HELLO` | protocol version, addon version | handshake; the client announces itself | `S` (current state) |
| `STATE` | — | poll: "where am I?" | `S` |
| `CLAIM` | — | action: "take today's seal" | `OK` or `ERR` |
| `PING` | — | keepalive / RTT probe | `OK` with an empty body |

Server → client:

| Opcode | Fields | Meaning |
|---|---|---|
| `S` | seals, target, claimsLeft, rank, flag | state snapshot; `flag` is `1` when a claim is available |
| `OK` | text (may be empty) | the action succeeded; `text` is safe to show |
| `ERR` | code, text | the action was refused; `code` is machine-readable |
| `BYE` | text | server is closing the session (maintenance, disable switch) |

Error codes: `BUSY`, `COOLDOWN`, `VERSION`, `DENIED`, `INTERNAL`. Clients must not invent codes; they
display `text` and log `code`.

## Rules

1. **The server is the only source of truth.** The client never computes `claimsLeft` or `rank`; it
   renders what `S` said, and says so when it has heard nothing.
2. **Unknown opcodes are ignored, silently.** Both sides must survive a peer that is one version ahead.
   This is what makes the protocol upgradable without a client reinstall.
3. **Version mismatch is refused, not guessed.** If `HELLO` carries a version the server does not
   implement, it answers `ERR VERSION` and stops. The client shows "client out of date".
4. **Actions are idempotent or explicitly refused.** `CLAIM` twice in one day yields `ERR COOLDOWN`;
   the client must not treat that as a failure state.
5. **Never request more than you need.** `STATE` is cheap; polling it every frame is abuse. The
   reference client polls once on login and then on demand, never faster than every 5 seconds.
6. **Degrade visibly.** No answer within 10 seconds → the panel shows `—` and "no server response",
   and keeps working as a plain window. A blank panel looks like a bug; an honest `—` looks like a
   server that is down, which is what it is.

## Conformance vectors

Requesting:

```
VT1\tHELLO\t1\tdemo-1.0.0        (22 bytes)
VT1\tSTATE                       (9 bytes)
VT1\tCLAIM                       (9 bytes)
```

Answering:

```
VT1\tS\t12\t50\t1\tAdept\t1    (21 bytes)
VT1\tOK\tseal claimed            (19 bytes)
VT1\tERR\tCOOLDOWN\talready claimed today   (38 bytes)
```

The worst case is a full `S` line: `VT1\tS\t` + 3 × 10 digits + rank (≤16) + flag, comfortably under
the 255-byte cap. Batching is unnecessary at this size; if your records grow, send several `S` messages
with a sequence number rather than one long line.

## What this costs

| Piece | Where it lives | Client patch |
|---|---|---|
| Panel, keybinds, formatting | addon folder (`Interface/AddOns/VaultTerminal`) | none — a folder, not an MPQ |
| Truth (seals, cooldown, rank) | server module + character DB | none |
| Channel availability | `AddonChannel = 1` in `worldserver.conf` | none |

That is the entire cost: one folder, one module, one config line. The panel looks and behaves like part
of the interface because, from the client's point of view, it is — addons are how the interface is
built in the first place.
