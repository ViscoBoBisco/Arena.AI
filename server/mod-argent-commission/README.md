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

A commissioned, currently Honored character can also use **Request medic** near a living stock
Argent Medic (creature 16284). The server verifies the persistent commission, native reputation,
server-observed distance (10 yards), and native Cure Disease spell (28133), then asks that existing
NPC to cast the spell. The existing SmartAI/gossip registration is untouched. Requests have a
process-local cooldown clamped to 30–3600 seconds (default 60); it resets on server restart and is
not a persistent entitlement or a claim of live runtime qualification.

Apply `data/sql/characters/001_argent_commission.sql`, load the module, and install the
`ArgentCommission` addon. Use `/argentcommission`; the server enforces the Honored threshold and
idempotently stores the earned entitlement. Spell/faction and NPC source evidence is the exact
public AzerothCore pin `6ffb62abb867dabb258de6813ff5d06d6b011543`: `SpellAuraEffects.cpp`
(57819 -> faction 1106), `creature_template.sql` (16284 Argent Medic), and `smart_scripts.sql`
(28133 Cure Disease gossip action). This is source and compile evidence only until exercised in a
booted game with real DBC/data and a compatible client.
