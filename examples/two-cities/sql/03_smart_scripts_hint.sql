-- Two Cities (3/3) — the tell.
-- Two NPCs at one spot are indistinguishable only if nothing betrays them. Give each phase's
-- version one line of speech, so the player can see that the world changed and not just the NPC.
-- smart_scripts columns are listed in examples/ashen-vault/sql/04_smart_scripts.sql.
--   source_type 0 = creature, event_type 1 = OOC_LOS (a player came into line of sight),
--   action_type 1 = TALK (action_param1 = groupid in creature_text).
-- Patch cost: none.

DELETE FROM smart_scripts WHERE entryorguid IN (900700, 900701);
DELETE FROM creature_text  WHERE CreatureID IN (900700, 900701);

-- creature_text columns (data/sql/base/db_world/creature_text.sql): CreatureID, GroupID, ID, Text,
-- Type, Language, Probability, Emote, Duration, Sound, BroadcastTextId, TextRange, comment.
INSERT INTO creature_text
  (CreatureID, GroupID, ID, Text, Type, Language, Probability, Emote, Duration, Sound, BroadcastTextId, TextRange)
VALUES
  (900700, 0, 0, 'The district is quiet. It always was, before.', 12, 0, 100, 0, 0, 0, 0, 0),
  (900701, 0, 0, 'You came back. The door remembers you.',          12, 0, 100, 0, 0, 0, 0, 0);

INSERT INTO smart_scripts
  (entryorguid, source_type, id, link, event_type, event_phase_mask, event_chance, event_flags,
   event_param1, event_param2, event_param3, event_param4, event_param5, event_param6,
   action_type, action_param1, action_param2, action_param3, action_param4, action_param5, action_param6,
   target_type, target_param1, target_param2, target_param3, target_param4,
   target_x, target_y, target_z, target_o, comment)
VALUES
  (900700, 0, 0, 0, 1, 0, 100, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0,
   'Two Cities - Vael (before) greets on line of sight'),
  (900701, 0, 0, 0, 1, 0, 100, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0,
   'Two Cities - Vael (after) greets on line of sight');
