# 11 — The testing ladder: intern, engineer, master

This project's tools make claims — "safe to pack", "no drift", "this lever needs no patch". Claims are
only worth what their tests are. The suite is therefore split into three tiers, each asking a harder
question than the last, and each finding a different *kind* of defect:

| Tier | Question | Finds | Runtime |
| **1 — intern** | *does it run?* | crashes, typos, undecodable files, dead links | ~1 s (10 tests) |
| **2 — engineer** | *does it behave at the edges?* | inconsistent exit codes, off-by-one ranges, silent skips | ~3 s (30 tests) |
| **3 — master** | *is the test capable of failing?* | decorative checks, unsafe writes, invented schema, scale limits | ~5 s (18 tests) |

```bash
make check          # all three tiers + the tool selftest
make test-intern    # tier 1
make test-engineer  # tier 2
make test-master    # tier 3
```

---

## Tier 1 — what an intern tests

Breadth, speed, zero insight required. If a file does not parse or a command does not start, nothing
above this tier matters.

- every tool answers `--help` with exit 0 (`catalog.py`, `dbc_tool.py`, `manifest.py`)
- `preflight.sh` with no arguments prints usage and exits 2 (not a traceback)
- `selftest.py` passes
- every `.md` decodes as UTF-8, starts with an `H1`, ends with a newline
- every `.json` in the repo parses
- the README names every top-level directory; the Makefile has every documented target
- the catalog has titles, effects, and a non-trivial number of entries
- every CLI subcommand runs and prints something
- a minimal, valid DBC is accepted

Finding: **none of these are deep, and that is the point.** Tier 1's real value is that it is *cheap
enough to run constantly*, so it never becomes the reason a change is not tested.

## Tier 2 — what a middling engineer tests

Now boundaries, contracts, and agreement between components.

**Boundaries.** Zero-record files are valid; a truncated string block, a record size inconsistent with
the field count, a bad magic, and a zero-field header are all rejected; negative IDs are legal;
duplicate IDs are reported and fail the check but do not crash.

**One exit-code taxonomy, applied everywhere.** 0 = ok, 1 = a check failed, 2 = usage or IO. The tier
asserts each case, including the ones that used to be muddled:

| Case | Code |
|---|---|
| missing file, a directory path, unreadable file | 2 |
| corrupt or truncated DBC, duplicate IDs, dangling refs, drift | 1 |
| inverted range, out-of-range field, missing argument | 2 |

**Range and set semantics.** Free-ID windows are the exact complement of used IDs inside the requested
range, inclusive at both ends; a fully used range fails; an untouched range reports the whole range.

**Cross-tool agreement.** `query --layer X --json` must equal the JSON filtered by hand; `--no-mpq`
must exclude every `dbc-rows`/`assets` entry; `verify-pair` must fail on value drift *and* on shape
drift; `manifest verify` must distinguish MISSING, EXTRA and CHANGED in one run.

**Manifest edges.** dotfiles skipped, nested paths kept, Unicode names preserved, POSIX separators,
empty staging is an error, no `.tmp` litter after success.

**Preflight semantics.** No tables → usage error; a server directory that does not exist → usage error
(a typo must never silently skip the parity check); a table the server does not have → warning, not
failure (overlays are legal); drift and dangling references → failure.

### Findings from this tier

1. `free --from 100 --to 10` returned **0** and printed a negative count. Now a usage error (2).
2. `free` printed ranges only when *some* IDs were used, and a bare summary otherwise. Now always ranges —
   a checker that changes shape depending on data is a checker nobody trusts.
3. `info <directory>` reported a **check failure** (1) instead of an IO error (2), because the parser
   wrapped every `OSError` in the same exception. Split into `DbcIOError`.
4. The catalog loader rejected a UTF-8 BOM — which is what a Windows editor writes by default. Now
   read as `utf-8-sig`.

## Tier 3 — what a master engineer tests

The question changes from "does it work" to **"is the test capable of failing?"**

### 3.1 Mutation testing of the validators

A checker that cannot fail is decoration. So the suite injects known defects and demands they be caught.
For the catalog's validator, fourteen mutations, each of which must produce a problem:

| Mutation | Must be caught because |
|---|---|
| drop a required field | the schema is mandatory |
| unknown layer / wrong id prefix | layers and ids must agree |
| unknown patch kind | delivery kinds are enumerated |
| clock = 7 | there are three clocks |
| empty `surfaces` / malformed `surfaces` | a lever without a surface is a rumour |
| unknown surface kind | `conf:`, `table:`, `dbc:`, `file:`, `addon:`, `code:` only |
| invented table name | `catalog/world_tables.txt` is the schema inventory |
| renamed config key | `catalog/config_keys.txt` is the config inventory |
| duplicate id | ids are keys |
| dangling doc reference | every `docs` ref must resolve to a chapter |
| empty `effect` / non-list `verify` | the entry must describe play |

For the release gate, six mutations of a *passing* fixture: value drift, an invented display ID, a
truncated table, duplicate IDs, a wrong field count, plus a control that must pass. If the control ever
fails, the mutation suite is inverted and worthless.

### 3.2 Fuzzing

150 deterministic pseudo-random buffers (magic-only, header-shaped, pure noise) fed to the parser
in-process: **any** exception other than `DbcError` is a failure, because an unexpected exception is an
unhandled input. A smaller CLI corpus asserts that no command ever prints a traceback and that exit codes
stay in `{0,1,2}`.

### 3.3 Scale

100,000 records parse and window in well under the bound; a 300-file staging directory hashes and
verifies. Scale tests are not about speed for its own sake — they are how you learn that the window
scan is `O(n log n)` rather than `O(range)`, which matters the day someone asks for ID ranges in the
millions.

### 3.4 Guarantees

- the manifest write is **atomic** (temp file + `os.replace`), so a crash cannot leave a half-written
  release descriptor; a failed write is a clean exit 2 with no `.tmp` behind it
- validation is idempotent
- every `table:`/`conf:` surface in the catalog exists in the captured inventories — the *claims* check,
  not just the shape check

### Findings from this tier

1. **The release gate accepted a 7-field `Item.dbc`.** It was self-consistent and identical on both
   sides, so every check passed — but a 7-field Item.dbc is exactly what the client refuses and the
   server logs as "has N field(s) (expected M)". Fix: `dbc_tool.py` now knows verified field counts for
   the tables this project documents (`Item` 8, `ItemDisplayInfo` 25, `Spell` 234, `SpellIcon` 2) and
   fails `info` on a mismatch, which makes `preflight.sh` fail too. Unknown tables are skipped, so it
   never cries wolf.
2. **The catalog contained two invented table names** (`game_graveyard_zone`,
   `player_classlevelstats` — TrinityCore names, not AzerothCore ones). Caught the moment the allowlist
   existed; corrected to `graveyard_zone` and `player_class_stats`.
3. **The manifest writer was not atomic**, and its failure path raised `FileExistsError` as a traceback
   instead of an error message. Both fixed.
4. **Test infrastructure itself was wrong:** importing a tool that uses `@dataclass` via
   `importlib` fails unless the module is registered in `sys.modules` first. A tier-3-only class of bug —
   it lives in the harness, not the product.
5. **A documentation claim was wrong:** the example SQL said a door's `Data0` is its lock ID. Reading
   `GameObjectData.h` shows `Data0 = startOpen`, `Data1 = lockId`. Fixed in
   `examples/ashen-vault/sql/03_gameobjects.sql`. Facts about closed-source behaviour need this
   treatment too — see [07](07-sources.md).

### What tier 3 cannot tell you

Mutation testing proves the checks *fire*; it cannot prove they are the *right* checks. The allowlist
proves a table name exists; it cannot prove the column you named means what you think. Fuzzing proves
the parser does not crash on garbage; it cannot prove it parses valid files correctly — that requires
known-good fixtures from a real client, which is why `selftest.py` carries hand-computed expectations.

The gap that remains is **requirements**: no test can tell you that a lever's `effect` sentence is
honest. That is what the catalog review is for, and why every entry carries a `verify` step a human can
run in game.

---

## Applying the ladder to your own content

The same three questions map onto a server:

| Tier | Your content | The test |
|---|---|---|
| intern | "does it load?" | restart, `.lookup` the entry, look at it |
| engineer | "does it behave at the edges?" | level 1 vs 80, solo vs group, with and without the prerequisite, GM vs player |
| master | "is the verification capable of failing?" | break it on purpose: wrong display ID, missing `Item.dbc` row, server/client drift — does your gate catch it, or does it pass? |

The last row is the one that separates a server that ships from a server that hopes. A gate you have
never seen fail is not a gate; it is a ritual. Make it fail once, on purpose, for every check you rely
on — that is what `tests/test_tier_master.py` does to this project's own tools.
