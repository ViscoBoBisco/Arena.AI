-- The Fair-Weather Market (1/3) — a shop that exists for twelve hours a week.
-- game_event columns, with their real units (data/sql/base/db_world/game_event.sql):
--   eventEntry  TINYINT UNSIGNED -> 0-255. Not a 900xxx id: the ID-range policy has a per-table
--               ceiling and this table's is 255. Pick from the high end of the free range.
--   start_time / end_time   absolute window the event may ever run inside
--   occurence   MINUTES between starts (not seconds)
--   length      MINUTES the event lasts
--   holiday     client-side HolidayId -> Holiday.dbc; 0 = not a holiday (patch-free)
--   world_event 0 normal / 1 world event
--   announce    0 silent, 1 announce, 2 follow the config
-- Patch cost: none. The client learns an event is active from the server.

SET @EVENT := 240;
SET @VENDOR := 900600;
SET @DISP_VENDOR := 24954;   -- TODO(verify): an existing CreatureDisplayInfo row

DELETE FROM game_event          WHERE eventEntry = @EVENT;
DELETE FROM game_event_creature WHERE eventEntry = @EVENT;
DELETE FROM creature_template   WHERE entry = @VENDOR;
DELETE FROM creature_template_model WHERE CreatureID = @VENDOR;
DELETE FROM creature            WHERE guid = 9006001;
DELETE FROM npc_vendor          WHERE entry = @VENDOR;
DELETE FROM conditions          WHERE SourceTypeOrReferenceId = 23 AND SourceGroup = @VENDOR;

-- Weekly: every 10080 minutes (7 days), for 720 minutes (12 hours), inside a two-year window.
INSERT INTO game_event
  (eventEntry, start_time, end_time, occurence, length, holiday, holidayStage, description, world_event, announce)
VALUES
  (@EVENT, '2026-01-01 00:00:00', '2030-01-01 00:00:00', 10080, 720, 0, 0,
   'Fair-Weather Market: a travelling vendor opens for twelve hours a week', 0, 1);

-- The vendor. npcflag 128 = VENDOR is what makes the client open a merchant window.
INSERT INTO creature_template
  (entry, name, subname, minlevel, maxlevel, exp, faction, npcflag, speed_walk, speed_run,
   detection_range, rank, dmgschool, unit_class, unit_flags, unit_flags2, dynamicflags,
   family, type, type_flags, lootid, mingold, maxgold, AIName, MovementType,
   HealthModifier, ManaModifier, ArmorModifier, DamageModifier, VerifiedBuild)
VALUES
  (@VENDOR, 'Sella Fairweather', 'Travelling Merchant', 80, 80, 2, 35, 128, 1.0, 1.14286,
   20, 0, 0, 1, 0, 2048, 0, 0, 7, 0, 0, 0, 0, '', 0, 1, 1, 1, 1, 12340);

INSERT INTO creature_template_model
  (CreatureID, Idx, CreatureDisplayID, DisplayScale, Probability, VerifiedBuild)
VALUES (@VENDOR, 0, @DISP_VENDOR, 1.0, 1, 12340);

-- The spawn exists always; the event just makes it visible. Putting the spawn in the event table
-- instead would also work, but then the NPC teleports rather than walking in and out of the fiction.
INSERT INTO creature
  (guid, id1, map, spawnMask, phaseMask, position_x, position_y, position_z, orientation,
   spawntimesecs, wander_distance, curhealth, curmana, MovementType)
VALUES
  (9006001, @VENDOR, 0, 1, 1, -9455.0, 64.0, 56.0, 0.0, 300, 0, 0, 0, 0);

INSERT INTO game_event_creature (eventEntry, guid) VALUES (@EVENT, 9006001);
