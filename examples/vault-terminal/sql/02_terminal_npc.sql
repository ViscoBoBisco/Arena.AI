-- VaultTerminal (2/3) — the NPC that hands out the addon (WORLD database)
-- Gossip text and options are server-sent strings: no client files involved.
-- Replace the TODO(verify) display id with an existing CreatureDisplayInfo row.

SET @ENTRY := 900300;
SET @DISP  := 24954;    -- TODO(verify): a mechanical / console-looking creature display

DELETE FROM creature_template_model WHERE CreatureID = @ENTRY;
DELETE FROM creature_template       WHERE entry      = @ENTRY;
DELETE FROM creature                WHERE guid      = 9003001;
DELETE FROM gossip_menu_option      WHERE MenuID     = @ENTRY;
DELETE FROM gossip_menu             WHERE MenuID     = @ENTRY;
DELETE FROM npc_text                WHERE ID         = @ENTRY;

INSERT INTO creature_template
  (entry, name, subname, gossip_menu_id, minlevel, maxlevel, exp, faction, npcflag,
   speed_walk, speed_run, detection_range, rank, dmgschool, unit_class, unit_flags, unit_flags2,
   dynamicflags, family, type, type_flags, lootid, mingold, maxgold, AIName, MovementType,
   HealthModifier, ManaModifier, ArmorModifier, DamageModifier, VerifiedBuild)
VALUES
  (@ENTRY, 'Vault Terminal', 'Ashen Vault Network', @ENTRY, 80, 80, 2, 35, 1,
   1.0, 1.14286, 20, 0, 0, 1, 0, 2048,
   0, 0, 9, 0, 0, 0, 0, '', 0,
   1, 1, 1, 1, 12340);

INSERT INTO creature_template_model
  (CreatureID, Idx, CreatureDisplayID, DisplayScale, Probability, VerifiedBuild)
VALUES (@ENTRY, 0, @DISP, 1.0, 1, 12340);

INSERT INTO npc_text (ID, text0_0, text0_1, BroadcastTextID0, lang0, Probability0)
VALUES (@ENTRY,
        'The terminal hums. A panel of seals glows behind scratched glass.$B$B"Interface available. Type /vt to open the terminal."',
        'The terminal hums. A panel of seals glows behind scratched glass.$B$B"Interface available. Type /vt to open the terminal."',
        0, 0, 1);

INSERT INTO gossip_menu (MenuID, TextID) VALUES (@ENTRY, @ENTRY);

INSERT INTO gossip_menu_option
  (MenuID, OptionID, OptionIcon, OptionText, OptionBroadcastTextID, OptionType, OptionNpcFlag,
   ActionMenuID, ActionPoiID, BoxCoded, BoxMoney, BoxText, BoxBroadcastTextID, VerifiedBuild)
VALUES
  -- OptionType 0 = plain gossip text: the option simply closes the window after the text.
  (@ENTRY, 0, 0, 'How do I use the terminal?', 0, 0, 1, 0, 0, 0, 0, NULL, 0, 12340),
  -- A keypad-style option: BoxText is the prompt, BoxCoded is the code the player must type.
  -- Handled by a gossip script in your core (see docs/10 §2.1); harmless to ship unused.
  (@ENTRY, 1, 0, 'Enter an access code', 0, 0, 1, 0, 0, 4711, 0,
   'Type the access code printed on your seal:', 0, 12340);

INSERT INTO creature
  (guid, id1, map, spawnMask, phaseMask, position_x, position_y, position_z, orientation,
   spawntimesecs, wander_distance, curhealth, curmana, MovementType)
VALUES
  (9003001, @ENTRY, 1, 1, 1, 3290.0, -1220.0, 91.0, 3.1416, 300, 0, 0, 0, 0);
