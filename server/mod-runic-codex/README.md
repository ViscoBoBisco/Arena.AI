# Runic Codex: Arcane Echo vertical

This executable first power uses AzerothCore's pinned WotLK Arcane Missiles spell chain as its
source event. Learning any of the server-recognized Arcane Missiles ranks automatically and
idempotently discovers the character-bound `Arcane Echo`; the addon can inspect and equip/unequip
that entitlement. While equipped, a paid Arcane Missiles cast that has actually spent mana refunds floor(10%) of its
mana cost, capped at 100 mana. The effect is a bounded `ModifyPower` after the spell cost is paid; it does
not cast recursively, change spell data, or accept client-supplied character identity.

Install `data/sql/characters/001_runic_codex.sql`, the AzerothCore module, and addon. The module
checks the same spell-known state at login so characters who knew the spell before installation
also discover the power. Entitlements and equip state are keyed to the session-derived character
guid; no account sharing is implied. The addon can only request state or equip state: it cannot
unlock itself.

Source basis for the discovery list: pinned AzerothCore `6ffb62abb867dabb258de6813ff5d06d6b011543`,
`src/server/scripts/Spells/spell_mage.cpp` (Arcane Missiles rank 1 is 5143) and
`src/server/game/Spells/SpellInfoCorrections.cpp` (complete Arcane Missiles rank spell chain).
The source pin plus `OnPlayerLearnSpell`, `OnPlayerSpellCast`, and `OnPlayerUpdate` are used by the
module. Pinned-core compilation is not a booted runtime or client qualification.
