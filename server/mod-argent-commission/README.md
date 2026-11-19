# Argent Commission

A character who reaches Honored with native Argent Crusade faction 1106 can claim one durable
commission. AzerothCore core data identifies spell 57819 as Argent Champion and its aura handler
sets the player's championing faction to 1106. The module persists the entitlement in the Characters
DB and applies that existing core spell at claim and login. It never edits reputation, faction
standing, FactionTemplate hostility, or party membership. In addition to championing, an earned
commission provides Argent Field Dressing: while the character remains at least Honored, each
credited creature kill restores a configurable percentage of maximum health, capped by configuration
and never exceeding missing health. Defaults are 2% / 150 at Honored and an upgraded 3% / 250 at
Exalted. The values are read from `conf/mod-argent-commission.conf.dist`; the server still enforces
the durable entitlement and live reputation rank, regardless of client input.
The benefit is checked server-side against the live native reputation rank and the durable earned
entitlement. It does not run on player kills, alter hostility, or grant reputation. A client can
request a claim but cannot supply a character GUID or an asserted reputation value.

Apply `data/sql/characters/001_argent_commission.sql`, load the module, and install the
`ArgentCommission` addon. Use `/argentcommission`; the server enforces the Honored threshold and
idempotently stores the earned entitlement. Spell/faction identity source evidence is the exact
public AzerothCore pin `6ffb62abb867dabb258de6813ff5d06d6b011543`,
`src/server/game/Spells/Auras/SpellAuraEffects.cpp` (57819 -> faction 1106, Argent Crusade).
This is compile/source evidence only until exercised in a booted game with real DBC data.
