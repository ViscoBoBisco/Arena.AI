# Runic Codex: registry-backed spell powers

Runic Codex now has a reusable, server-owned power registry in `src/RunicPowerRegistry.h`. Each
entry declares its discovery item and item spell, prerequisite rank chain, affected spell ranks,
refund tuning and slot cost. It currently includes six powers:

- **Arcane Echo**: use the stock Tome of Polymorph: Turtle (item 22739; item spell 28271; 15% Gahz'ranka loot), and know an Arcane Missiles rank. While equipped, a fully paid cast refunds 10% of mana cost, capped at 100.
- **Aegis Echo**: use the stock Codex of Holy Word: Shield III (item 3122; item spell 600; quest 3121 reward), and know a Power Word: Shield rank. While equipped, a fully paid Power Word: Shield cast refunds 5%, capped at 60.
- **Umbral Echo**: use the stock Grimoire of Shadow Bolt X (item 21281; item spell 25307; AQ reference loot 34024), and know a Shadow Bolt rank. While equipped, a fully paid Shadow Bolt cast refunds 5%, capped at 75.
- **Arcane Ward**: the Polymorph: Turtle tome and an Arcane Missiles rank unlock an 8% incoming-spell damage reduction capped at 300.
- **Verdant Ward**: the same tome and a Rejuvenation rank unlock an 8% healing-received bonus capped at 300.
- **Shadow Ward**: use the stock Grimoire of Shadow Ward IV (item 22891; item spell 28610; reference loot 30171 from Ragnaros/Nefarian), and know a Shadow Ward rank. While equipped, it reduces Shadow-school spell damage by 6%, capped at 200; it stacks with other eligible equipped powers and does not reduce Fire/Frost damage.

Mana refunds measure mana actually spent after cast and run server-side; their percentage and cap can be
adjusted with `RunicCodex.<power_key>.RefundPercent` and `RefundCap`. Damage reduction and healing
bonuses use their respective per-power percent/cap settings. The implementation caps percentages at
100, caps mitigation/refunds to configured amounts, and applies the Shadow Ward school filter only to
incoming spell damage whose native `SpellInfo::SchoolMask` includes Shadow. Powers do not trigger a
second spell or trust client identity. Each item use and native spell prerequisite can happen in either
order. The original WotLK glyph system is untouched. The stock IDs, spell chains, loot/quest evidence
and exact source pin are recorded in the registry comments and validated against the pinned SQL in CI.

Players can equip unlocked powers into either of two character-bound slots and save/apply named
loadouts. The server validates registry keys, unlock state, slot bounds and saved-loadout powers; the
addon only submits requests. `003_runic_codex_registry_loadouts.sql` migrates old inscription evidence
forward, preserves existing entitlement rows, adds slot state and creates per-character loadout tables.
Apply `001`, `002`, then `003` in order. The first two migrations remain unchanged for existing installs.

This is source, database and pinned-compile evidence, not yet booted-server or live-client gameplay
qualification. Existing aura, item custody and native glyph behavior require the exact local runtime
checklist in the integration handoff.
