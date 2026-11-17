-- The Ashen Vault (1/5) — creature templates
-- Illustrative. Column names follow AzerothCore WotLK world DB (checked 2026-11).
-- Replace every value marked TODO(verify) with an ID you confirmed in your own client data.
-- Our entry range: 900100-900199 (see docs/04 §4, ID-range policy).

SET @WARDEN   := 900100;   -- friendly gate NPC, gossip/script
SET @SENTINEL := 900101;   -- two trash constructs
SET @KEEPER   := 900110;   -- boss
SET @ADD      := 900111;   -- boss add

-- TODO(verify): existing CreatureDisplayInfo.dbc ids that fit the fiction.
SET @DISP_WARDEN   := 24954;
SET @DISP_SENTINEL := 24955;
SET @DISP_KEEPER   := 26718;
SET @DISP_ADD      := 24956;

DELETE FROM creature_template_model WHERE CreatureID IN (@WARDEN, @SENTINEL, @KEEPER, @ADD);
DELETE FROM creature_template       WHERE entry      IN (@WARDEN, @SENTINEL, @KEEPER, @ADD);

INSERT INTO creature_template
  (entry, name, subname, minlevel, maxlevel, exp, faction, npcflag, speed_walk, speed_run,
   detection_range, rank, dmgschool, unit_class, unit_flags, unit_flags2, dynamicflags,
   family, type, type_flags, lootid, mingold, maxgold, AIName, MovementType,
   HealthModifier, ManaModifier, ArmorModifier, DamageModifier, VerifiedBuild)
VALUES
  -- A friendly NPC: npcflag 1 (gossip) is what makes the client offer a gossip window at all.
  (@WARDEN,   'Vault Warden',   'Keeper of the Ashen Seal', 80, 80, 2, 35, 1,
   1.0, 1.14286, 20, 0, 0, 1, 0, 2048, 0, 0, 7, 0, 0, 0, 0, '', 0,
   1, 1, 1, 1, 12340),
  -- Trash: rank 1 = elite. faction 16 is hostile to both factions.
  (@SENTINEL, 'Ashen Sentinel', 'Vault Guardian',          80, 80, 2, 16, 0,
   1.0, 1.14286, 25, 1, 0, 1, 0, 2048, 0, 0, 9, 0, 0, 0, 0, 'SmartAI', 0,
   3, 1, 1, 1, 12340),
  -- Boss: lootid points at creature_loot_template.Entry (see 05_loot.sql).
  (@KEEPER,   'Ashen Vault Keeper', 'The Seal Unbroken',   80, 80, 2, 16, 0,
   1.0, 1.14286, 40, 1, 0, 1, 0, 2048, 0, 0, 9, 0, @KEEPER, 0, 0, 'SmartAI', 0,
   14, 1, 1, 2, 12340),
  -- Adds: no loot, no faction noise.
  (@ADD,      'Ashen Remnant',  '',                        80, 80, 2, 16, 0,
   1.0, 1.14286, 30, 0, 0, 1, 0, 2048, 0, 0, 9, 0, 0, 0, 0, 'SmartAI', 0,
   1, 1, 1, 1, 12340);

-- Appearance lives in its own table in current AzerothCore (creature_template has no modelid
-- columns). Probability 1 on a single row = always this model.
INSERT INTO creature_template_model
  (CreatureID, Idx, CreatureDisplayID, DisplayScale, Probability, VerifiedBuild)
VALUES
  (@WARDEN,   0, @DISP_WARDEN,   1.0, 1, 12340),
  (@SENTINEL, 0, @DISP_SENTINEL, 1.0, 1, 12340),
  (@KEEPER,   0, @DISP_KEEPER,   1.2, 1, 12340),
  (@ADD,      0, @DISP_ADD,      0.8, 1, 12340);
