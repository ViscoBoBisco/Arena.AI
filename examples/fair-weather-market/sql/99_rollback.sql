-- The Fair-Weather Market — rollback.
DELETE FROM conditions          WHERE SourceTypeOrReferenceId = 23 AND SourceGroup = 900600;
DELETE FROM npc_vendor          WHERE entry = 900600;
DELETE FROM game_event_creature WHERE eventEntry = 240;
DELETE FROM creature            WHERE guid = 9006001;
DELETE FROM creature_template_model WHERE CreatureID = 900600;
DELETE FROM creature_template   WHERE entry = 900600;
DELETE FROM game_event          WHERE eventEntry = 240;
