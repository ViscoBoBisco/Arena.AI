# 04 — Patching, toolchain and release discipline

A client patch is a **release artifact**. The projects that stay healthy treat it like software:
one source of truth, one build command, one version number, one manifest, one checksum, and a
launcher that refuses to start on a mismatch. The projects that do not, end up with players on
three different `patch-4.MPQ` builds and bug reports that cannot be reproduced.

---

## 1. MPQ build mechanics

| Setting | 3.3.5a value | Notes |
|---|---|---|
| Format | **MPQ 1.0** (`FormatVersion = 0`) | v2 also loads on WotLK, v3/v4 do not |
| Sector size shift | **3** (4096 bytes) | StormLib quirk; MPQEditor default |
| `(attributes)` | **do not create** | old guides are right about this; it is not needed and can confuse |
| `(listfile)` | optional | useful for your own tooling; some servers strip it to hinder ripping |
| Hash table size | power of two, ≥ number of files | MPQEditor's "maximum files" box |
| Internal paths | backslashes, case as the client asks | `DBFilesClient\Item.dbc` |
| Compression | zlib is fine | DBCs are small; compression barely matters |

Directory inside the archive: **exactly `DBFilesClient`**, with the DBC files directly inside.

Minimum content of a working patch:

```
patch-4.MPQ
  DBFilesClient\Item.dbc
  DBFilesClient\Spell.dbc
  DBFilesClient\ItemDisplayInfo.dbc
  ... (only the tables you changed)
```

**Do not** repack all 200+ DBCs "just in case". Every extra file is a file that can drift from
the server's set and a file that will shadow a future stock fix. Patch only what you changed.

## 2. Naming and load order (reprise, plus the traps)

- Start at `patch-4.MPQ` (stock uses `patch.MPQ`, `patch-2`, `patch-3`).
- Numeric (`patch-4..9`) load before alphabetic (`patch-A..Z`); later wins.
- **English clients:** `Data/patch-*.MPQ` outranks `Data/enUS/patch-enUS-*.MPQ`.
- **Russian/Chinese clients:** locale archives outrank `Data/` — you must ship
  `Data/<locale>/patch-<locale>-4.MPQ`.
- Other servers' launchers already occupy letters (custom servers commonly claim `patch-Z` or
  `patch-A..H`). If your players might have leftovers from another server, pick a letter and
  *document it*, or have the launcher detect and warn on foreign patches.
- `patch-9.MPQ` → `patch-A.MPQ` is the safe progression when you need to out-rank a competing
  mod; renaming a patch is a client-visible change, so announce it.
- Never rely on "it worked on my machine" for ordering: verify by extracting your DBC **in merged
  mode** from the players' exact archive set and comparing to `dbc-client/` (see §5).

## 3. Data-editing hygiene

1. **Never edit `dbc-src/`.** Extract once, treat it as read-only, generate `dbc-client/` and
   `dbc-server/` from it.
2. **Edit with WDBX Editor** (preferred) or **Stoneharry's Spell Editor** for spells/visuals;
   legacy `MyDBCEditor` still works but is buggier and has no bulk operations
   ([tools list](https://wotlkdev.github.io/wiki/tools/)).
3. **Verify the column header names**, not the indices, when in doubt — WDBX shows the header
   from the build you opened, while forum posts show whatever build the author used.
4. **Copy rows from the same build.** A `Item.dbc` row pasted from a 4.x client, or a `Spell.dbc`
   row from a 5.4.8 retro-port pack, is the classic source of "client crashes on login".
5. **Keep CSVs** (`dbc_tool.py dump`, or WDBX's export) next to the SQL so diffs are reviewable.
6. **Append, don't insert-in-the-middle**, and keep IDs sorted within your custom band. Binary
   search assumptions in both client and core make sorted rows the safe default.
7. **Automate validation** — `tools/dbc_tool.py` covers header/duplicate-ID/dangling-reference/
   client-vs-server-drift checks; run it before every build.

## 4. Toolchain

| Job | Tool | Notes |
|---|---|---|
| MPQ read/write | **MPQEditor** (zezula) | merged mode = the client's view |
| MPQ programmatic | StormLib, `mpqcli`, `mpq-builder` (TS) | for CI-built patches |
| DBC edit | **WDBX Editor** | current best general editor |
| Spell/visual edit | **Spell Editor** (stoneharry) | resolves references, copies visual kits, SQL import/export |
| DBC ⇄ CSV | DBCUtil | for scripted bulk edits |
| SQL | HeidiSQL / MySQL Workbench / **Keira3** | Keira3 is AzerothCore's DB editor |
| Code mods | **TSWoW** | TypeScript framework that generates client patches + server data (different paradigm; see below) |
| Models | WoW Blender Studio, M2Mod, MultiConverter (retro-ports) | only if you break the "existing assets" rule |
| Viewer | WoW Model Viewer r252 | find display IDs / preview M2s |
| Textures | BLPConverter, BLP Lab, Photoshop BLP plugin | icons and skins |
| Maps | Noggit / Noggit Red, adtcreator | new terrain (expensive path) |
| Binary | 010 Editor (templates), Ghidra/IDA | executable patches, field offsets |
| Runtime | ReShade, DXVK, `d3d9.dll` proxies | visual post-processing, no gameplay change |

**TSWoW** deserves a mention because it attacks the exact problem this document cares about: you
describe content in TypeScript and the framework generates the DBC rows, the client patch and the
server data. It is a separate core/framework, not a drop-in for an existing AzerothCore install,
but it is the closest thing to "smooth" tooling in the 3.3.5a ecosystem
([project list entry](https://github.com/haha2345/awesome-wow-335-clients)).

## 5. The preflight gate (make this non-optional)

`tools/preflight.sh` wraps the checks below into one command
(`./tools/preflight.sh dbc-client/ dbc-server/`, exit code 0 = safe to pack). The individual
checks it runs:

```bash
set -e
tables="Item.dbc Spell.dbc ItemDisplayInfo.dbc SpellVisual.dbc SpellVisualKit.dbc \
        SpellIcon.dbc ItemExtendedCost.dbc ItemSet.dbc"

for t in $tables; do
  # structural sanity + duplicate IDs
  python3 tools/dbc_tool.py info "dbc-client/$t"
  # client and server must agree
  python3 tools/dbc_tool.py verify-pair "dbc-client/$t" "dbc-server/$t"
done

# referential integrity for the tables that break most visibly
python3 tools/dbc_tool.py refs dbc-client/Item.dbc  --field 5 --must-exist dbc-client/ItemDisplayInfo.dbc
python3 tools/dbc_tool.py refs dbc-client/Spell.dbc --field 133 --must-exist dbc-client/SpellIcon.dbc
python3 tools/dbc_tool.py refs dbc-client/Spell.dbc --field 131 --must-exist dbc-client/SpellVisual.dbc
```

`verify-pair` catches the highest-cost class of bug on a private server: the client patch and the
server's `DataDir/dbc` set diverging. Real-world evidence that this matters: a server whose DBC
set lacked 59k creature displays and 12.8k extended costs silently dropped item limit categories
from 60,751 items and skipped 1,225 spawns — no error, just missing content
([CoA PR #1484](https://github.com/jealous-sound/azerothcore-wotlk-coa/pull/1484)).

Equally valuable: **re-extract the server DBC set from the client the players actually run**
(including your patch), and diff it against what you ship.

## 6. Manifest and versioning

```json
{
  "content_version": "2026.11.1",
  "client_build": "3.3.5a.12340",
  "patch": { "file": "patch-4.MPQ", "sha256": "<fill after build>", "locale_variant": null },
  "tables": ["Item.dbc", "Spell.dbc", "ItemDisplayInfo.dbc", "SpellIcon.dbc", "ItemExtendedCost.dbc"],
  "id_ranges": { "items": [800000, 899999], "spells": [800000, 899999] },
  "addons": [{ "name": "MyCustomUI", "version": "1.4.2", "interface": 30300 }],
  "requires_cache_purge": true
}
```

Rules:

- **One version number** covers patch + server DB migrations + required addon versions. Players
  should never be able to mix.
- **Publish SHA-256** of the patch. The launcher verifies before launch and refuses otherwise.
- **Bump on every DBC row change**, even one row. "Small change" is how drift starts.
- Keep the built patch in a content-addressed artifact store, not in git (binary blobs bloat
  repos and confuse code review).
- `interface` version for addons on 3.3.5a is **30300**.

## 7. Launcher / verification

The server cannot verify a client's DBCs — 3.3.5a has no manifest-based file check and Warden is
an anti-cheat, not a content validator. So mismatches are detected by *symptoms*, not by the
handshake. Mitigations, in order of value:

1. **Launcher-owned client.** It downloads/patches `Data/patch-4.MPQ`, verifies SHA-256, deletes
   `Cache/`, checks that no foreign `patch-*.MPQ` shadows yours, then launches.
2. **Addon handshake.** Ship a tiny addon that sends `MYSRV|VERSION|2026.11.1` on
   `PLAYER_LOGIN` via an addon message; the server replies with the expected version, and the
   addon prints a loud warning (or the module kicks with a message) if they differ. Catches
   "player kept an old patch" without touching the executable.
3. **Server-side sanity checks.** On login, a module can verify addon presence via
   `GetAddonInfo`-equivalent messages and check for known-bad item IDs in bags. Cheap, limited.
4. **Documentation.** A one-page "install this patch or the server looks broken" with the red-`?`
   screenshot saves hundreds of support tickets.

## 8. Release/rollback checklist

- [ ] `dbc_tool.py` gate passes; `verify-pair` clean for every touched table.
- [ ] Server started on the new `DataDir/dbc` set in staging; log grepped for
      `does not exist in item.dbc`, `has N field(s) (expected M)`, `Invalid displayId`,
      `Table 'X' is empty`.
- [ ] New item/spell/quest/creature tested on a **clean client** (cache deleted, fresh
      `WTF/AddOns` disabled).
- [ ] Old patch replaced, not added next to — one patch file in `Data/`.
- [ ] Launcher SHA-256 updated; addon version bumped; manifest committed.
- [ ] Rollback = restore previous patch + previous `dbc-server/` + revert SQL migration; keep the
      previous patch artifact for at least one release cycle.

## 9. Content-migration discipline

Custom content that touches the client must be **forward-only**. You cannot un-ship an
`Item.dbc` row: existing items in players' banks and mail reference those IDs. So:

- Never reuse a custom ID for different content.
- Never delete a custom row; retire it (`Flags`/`Quality`/vendor removal) instead.
- Keep a changelog of ID → content, forever. This is your only defence against a future dev
  "cleaning up" the DBC.

## 10. Legal and distribution hygiene

This is engineering advice, not legal advice. The practical convention in the ecosystem:

- Do not distribute Blizzard binaries (client, MPQs, extracted assets) as your own product.
- Custom content is usually shipped as **delta patches** built from the user's own client files,
  or as serverside data + a small patch the launcher produces locally.
- DBC rows derived from Blizzard tables are still derived from Blizzard data; many projects keep
  them as *generated artifacts* rather than checked-in files for exactly this reason.
- AzerothCore's FAQ states the project exists for theory-crafting/learning and does not condone
  client modification ([FAQ](https://www.azerothcore.org/wiki/faq)) — keep that separation
  between "the emulator" and "your modded client" clear in your own docs.
