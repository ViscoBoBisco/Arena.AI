# Runic Codex: registry-backed spell powers

Runic Codex now has a reusable, server-owned power registry in `src/RunicPowerRegistry.h`. Each
entry declares its discovery item and item spell, prerequisite rank chain, affected spell ranks,
refund tuning and slot cost. It currently includes two powers:

- **Arcane Echo**: use the stock Tome of Polymorph: Turtle (item 22739; item spell 28271; 15% Gahz'ranka loot), and know an Arcane Missiles rank. While equipped, a fully paid cast refunds 10% of mana cost, capped at 100.
- **Aegis Echo**: use the stock Codex of Holy Word: Shield III (item 3122; item spell 600; quest 3121 reward), and know a Power Word: Shield rank. While equipped, a fully paid Power Word: Shield cast refunds 5%, capped at 60.

Both effects measure mana actually spent after cast and run server-side; their percentage and cap can be
adjusted with `RunicCodex.<power_key>.RefundPercent` and `RefundCap` in
`conf/mod-runic-codex.conf.dist` (percent and refund remain bounded to 100). They do not trigger a second
spell or trust client identity. The item use and spell prerequisite can happen in either order. The original WotLK
glyph system is untouched. The stock IDs, spell chains, loot/quest evidence and exact source pin are
recorded in the registry comments and exercised by source-contract tests.

Players can equip unlocked powers into either of two character-bound slots and save/apply named
loadouts. The server validates registry keys, unlock state, slot bounds and saved-loadout powers; the
addon only submits requests. `003_runic_codex_registry_loadouts.sql` migrates old inscription evidence
forward, preserves existing entitlement rows, adds slot state and creates per-character loadout tables.
Apply `001`, `002`, then `003` in order. The first two migrations remain unchanged for existing installs.

This is source, database and pinned-compile evidence, not yet booted-server or live-client gameplay
qualification. Existing aura, item custody and native glyph behavior require the exact local runtime
checklist in the integration handoff.
