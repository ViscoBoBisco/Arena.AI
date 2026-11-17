-- The Ashen Vault (4/5) — behaviour (SmartAI)
-- Illustrative. SmartAI runs entirely server-side: the client only learns about it indirectly,
-- through SMSG_AURA_UPDATE / SMSG_SPELL_START / emote and summon updates (see docs/08 §4).
-- Enum values below were read from SmartScriptMgr.h (SmartEvent / SmartAction / SmartTarget).

SET @KEEPER := 900110;

DELETE FROM smart_scripts WHERE entryorguid = @KEEPER AND source_type = 0;

INSERT INTO smart_scripts
  (entryorguid, source_type, id, link, event_type, event_phase_mask, event_chance, event_flags,
   event_param1, event_param2, event_param3, event_param4, event_param5, event_param6,
   action_type, action_param1, action_param2, action_param3, action_param4, action_param5, action_param6,
   target_type, target_param1, target_param2, target_param3, target_param4,
   target_x, target_y, target_z, target_o, comment)
VALUES
  -- 4 = SMART_EVENT_AGGRO. 5 = SMART_ACTION_PLAY_EMOTE, 5 = EMOTE_ONESHOT_EXCLAMATION.
  (@KEEPER, 0, 0, 0, 4, 0, 100, 0,
   0, 0, 0, 0, 0, 0,
   5, 5, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0,
   'Ashen Vault Keeper - aggro: exclamation emote'),

  -- 2 = SMART_EVENT_HEALTH_PCT (param1 = min %, param2 = max %). Phase 1 so it happens once per phase.
  -- 11 = SMART_ACTION_CAST, param1 = an existing Spell.dbc id (server casts; client already knows it).
  (@KEEPER, 0, 1, 0, 2, 1, 100, 0,
   50, 51, 0, 0, 0, 0,
   11, 26662, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0,
   'Ashen Vault Keeper - at 50%: enrage (reuse an existing enrage spell)'),

  -- 22 = SMART_ACTION_SET_EVENT_PHASE: move to phase 2 so row 1 cannot fire again.
  (@KEEPER, 0, 2, 0, 2, 1, 100, 0,
   50, 51, 0, 0, 0, 0,
   22, 2, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0,
   'Ashen Vault Keeper - enter phase 2'),

  -- 12 = SMART_ACTION_SUMMON_CREATURE (param1 entry, param2 summon type, param3 ms, param4 attack invoker).
  (@KEEPER, 0, 3, 0, 2, 2, 100, 0,
   25, 26, 0, 0, 0, 0,
   12, 900111, 8, 60000, 1, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0,
   'Ashen Vault Keeper - at 25% (phase 2): summon an Ashen Remnant');

-- 6 = SMART_EVENT_DEATH. 5 = PLAY_EMOTE, 15 = EMOTE_ONESHOT_ROAR (cosmetic; safe if your build
-- maps it differently). Add SMART_ACTION_TALK (1) + a creature_text row if you want real lines.
INSERT INTO smart_scripts
  (entryorguid, source_type, id, link, event_type, event_phase_mask, event_chance, event_flags,
   event_param1, event_param2, event_param3, event_param4, event_param5, event_param6,
   action_type, action_param1, action_param2, action_param3, action_param4, action_param5, action_param6,
   target_type, target_param1, target_param2, target_param3, target_param4,
   target_x, target_y, target_z, target_o, comment)
VALUES
  (@KEEPER, 0, 4, 0, 6, 0, 100, 0,
   0, 0, 0, 0, 0, 0,
   5, 15, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0,
   'Ashen Vault Keeper - death: roar emote');
