-- The Vigil Gate (2/2) — what the ritual reveals.
-- A chest (type 3). GOData.chest (GameObjectData.h):
--   Data0 lockId -> Lock.dbc, Data1 lootId -> gameobject_loot_template, Data2 chestRestockTime
--   (minutes), Data3 consumable, Data4 minRestockTime, Data5 maxRestockTime ...
-- lootId 0 means "no loot"; we point it at the chest's own entry, which is the usual convention.

SET @CACHE := 900510;
-- TODO(verify): existing display row for a chest or reliquary box.
SET @DISP_CACHE := 259;

DELETE FROM gameobject_template     WHERE entry = @CACHE;
DELETE FROM gameobject              WHERE guid  = 9005010;
DELETE FROM gameobject_loot_template WHERE Entry = @CACHE;

INSERT INTO gameobject_template (entry, type, displayId, name, size, Data0, Data1, Data2, Data3, VerifiedBuild)
VALUES
  -- lockId 0 = unlocked: who may stand here is decided by the ritual and the door, not by a key.
  (@CACHE, 3, @DISP_CACHE, 'Vigil Cache', 1.0, 0, @CACHE, 0, 0, 12340);

INSERT INTO gameobject
  (guid, id, map, spawnMask, phaseMask, position_x, position_y, position_z, orientation,
   rotation0, rotation1, rotation2, rotation3, spawntimesecs, animprogress, state, VerifiedBuild)
VALUES
  (9005010, @CACHE, 1, 1, 1, 3302.0, -1224.0, 91.0, 0.0, 0, 0, 0, 1, 300, 100, 1, 12340);

-- The chest's loot. Columns are identical to creature_loot_template (Entry, Item, Reference,
-- Chance, QuestRequired, LootMode, GroupId, MinCount, MaxCount, Comment).
INSERT INTO gameobject_loot_template
  (Entry, Item, Reference, Chance, QuestRequired, LootMode, GroupId, MinCount, MaxCount, Comment)
VALUES
  -- TODO(verify): existing item entries that suit your realm. Chance 0 + Reference 0 means
  -- "always"; Chance 100 would mean 100%.
  (@CACHE, 43573, 0, 0,   0, 1, 1, 1, 1, 'Vigil Gate - group 1: a tear of bitter anguish'),
  (@CACHE, 36909, 0, 100, 0, 1, 1, 1, 3, 'Vigil Gate - group 1: cobalt ore, sometimes');
