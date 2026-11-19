# Runic Codex: Arcane Echo vertical

The first executable power uses a loot-to-learn-to-equip loop: the stock `Tome of Polymorph:
Turtle` (item 22739), which drops from Gahz'ranka in the pinned world data, records an inscription.
Using it after learning any server-recognized Arcane Missiles rank unlocks character-bound `Arcane
Echo`; learning the rank after using the tome also unlocks it. Both steps are idempotent and survive
relog. The addon can inspect and equip/unequip the entitlement. While equipped, a paid Arcane
Missiles cast that has actually spent mana refunds floor(10%) of its mana cost, capped at 100 mana.
The effect is a bounded `ModifyPower` after the spell cost is paid; it does not cast recursively,
change spell data, or accept client-supplied character identity.

Install both ordered migrations in `data/sql/characters/`, the AzerothCore module, and addon.
The unlock requires both a server-observed use of item 22739 and a learned Arcane Missiles rank;
either can happen first, and both progress steps are durable and duplicate-safe. Merely knowing the
spell does not auto-unlock the power. Entitlements and equip state are keyed to the session-derived
character guid; no account sharing is implied. The addon can only request state or equip state: it
cannot unlock itself.

Source basis: pinned AzerothCore `6ffb62abb867dabb258de6813ff5d06d6b011543`,
`src/server/scripts/Spells/spell_mage.cpp` (Arcane Missiles rank 1 is 5143),
`src/server/game/Spells/SpellInfoCorrections.cpp` (complete rank chain), and
`data/sql/base/db_world/item_template.sql` plus `creature_loot_template.sql` (item 22739 teaches
spell 28271 and is a 15% Gahz'ranka loot entry). The module observes successful item-spell casts,
spell learning, and paid spell casts through pinned player hooks. Pinned compilation is not a booted
runtime or real-client qualification.
