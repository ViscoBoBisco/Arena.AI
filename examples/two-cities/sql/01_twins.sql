-- Two Cities (1/3) — the same coordinates, twice.
-- Phasing is a per-player filter on server-sent objects: a spawn whose `phaseMask` does not
-- intersect the player's phase is never sent, so it does not exist for that player. The client
-- needs no idea that phasing happened; it just receives a different world.
-- Entry range: 900700-900799. Patch cost: none.

SET @BEFORE := 900700;   -- the quartermaster as the player first meets him
SET @AFTER  := 900701;   -- the same man, after the district changes

-- TODO(verify): existing CreatureDisplayInfo rows — the SAME display for both, on purpose: the
-- fiction is that it is one person, so only his name, gossip and stock differ.
SET @DISP := 24954;

DELETE FROM creature_template_model WHERE CreatureID IN (@BEFORE, @AFTER);
DELETE FROM creature_template       WHERE entry IN (@BEFORE, @AFTER);
DELETE FROM creature                WHERE guid BETWEEN 9007001 AND 9007002;

INSERT INTO creature_template
  (entry, name, subname, minlevel, maxlevel, exp, faction, npcflag, speed_walk, speed_run,
   detection_range, rank, dmgschool, unit_class, unit_flags, unit_flags2, dynamicflags,
   family, type, type_flags, lootid, mingold, maxgold, AIName, MovementType,
   HealthModifier, ManaModifier, ArmorModifier, DamageModifier, VerifiedBuild)
VALUES
  (@BEFORE, 'Quartermaster Vael', 'Before the Ninth Door', 80, 80, 2, 35, 1, 1.0, 1.14286,
   20, 0, 0, 1, 0, 2048, 0, 0, 7, 0, 0, 0, 0, '', 0, 1, 1, 1, 1, 12340),
  (@AFTER,  'Quartermaster Vael', 'After the Ninth Door',  80, 80, 2, 35, 1, 1.0, 1.14286,
   20, 0, 0, 1, 0, 2048, 0, 0, 7, 0, 0, 0, 0, '', 0, 1, 1, 1, 1, 12340);

INSERT INTO creature_template_model
  (CreatureID, Idx, CreatureDisplayID, DisplayScale, Probability, VerifiedBuild)
VALUES (@BEFORE, 0, @DISP, 1.0, 1, 12340),
       (@AFTER,  0, @DISP, 1.0, 1, 12340);

-- Identical x, y, z, orientation. Only phaseMask differs: bit 0 = phase 1, bit 1 = phase 2.
INSERT INTO creature
  (guid, id1, map, spawnMask, phaseMask, position_x, position_y, position_z, orientation,
   spawntimesecs, wander_distance, curhealth, curmana, MovementType)
VALUES
  (9007001, @BEFORE, 1, 1, 1, 3294.0, -1220.0, 91.0, 3.1416, 300, 0, 0, 0, 0),
  (9007002, @AFTER,  1, 1, 2, 3294.0, -1220.0, 91.0, 3.1416, 300, 0, 0, 0, 0);
