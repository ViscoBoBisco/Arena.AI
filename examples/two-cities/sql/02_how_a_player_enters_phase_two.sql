-- Two Cities (2/3) — how a player *gets* phase 2.
-- `spell_area` applies an aura spell to anyone standing in an area whose quest conditions pass.
-- If that aura is a phase aura, the player's phase mask changes and the server simply stops sending
-- the objects of the other phase. Columns (data/sql/base/db_world/spell_area.sql):
--   spell, area, quest_start, quest_end, aura_spell, racemask, gender, autocast,
--   quest_start_status, quest_end_status
-- Patch cost: none — the spell already exists client-side, and phases are server-side filtering.

-- TODO(verify): an existing spell with a PHASE effect. Do NOT invent one: the client must be able
-- to render the aura, and the effect type is what makes it phasing rather than a buff.
SET @PHASE_SPELL := 59074;

-- TODO(verify): the area id (not the zone id) that contains the district, from AreaTable.dbc.
SET @AREA := 331;

DELETE FROM spell_area WHERE spell = @PHASE_SPELL AND area = @AREA;

-- While the quest is taken and not yet rewarded, the phase aura applies.
INSERT INTO spell_area
  (spell, area, quest_start, quest_end, aura_spell, racemask, gender, autocast,
   quest_start_status, quest_end_status)
VALUES
  (@PHASE_SPELL, @AREA, 0, 0, 0, 0, 2, 1, 64, 11);

-- status 64 = QUEST_STATUS_REWARDED; 11 = NOT_TAKEN(1) | COMPLETE(2) | IN_PROGRESS(8). Setting
-- quest_start/quest_end to 0 makes the row apply unconditionally in the area, which is the cleanest
-- way to demo phasing: two players in the same spot, one aura, two worlds.
