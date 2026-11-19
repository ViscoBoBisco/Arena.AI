# Argent Commission

A character who reaches Honored with native Argent Crusade faction 1106 can claim one durable
commission. AzerothCore core data identifies spell 57819 as Argent Champion and its aura handler
sets the player's championing faction to 1106. The module persists the entitlement in the Characters
DB and applies that existing core spell at claim and login. It never edits reputation, faction
standing, FactionTemplate hostility, or party membership. A client can request a claim but cannot
supply a character GUID or an asserted reputation value.

Apply `data/sql/characters/001_argent_commission.sql`, load the module, and install the
`ArgentCommission` addon. Use `/argentcommission`; the server enforces the Honored threshold and
idempotently stores the earned entitlement. Spell/faction identity source evidence is the exact
public AzerothCore pin `6ffb62abb867dabb258de6813ff5d06d6b011543`,
`src/server/game/Spells/Auras/SpellAuraEffects.cpp` (57819 -> faction 1106, Argent Crusade).
This is compile/source evidence only until exercised in a booted game with real DBC data.
