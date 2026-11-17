-- The Vigil Gate — rollback. Loot first, then the objects that point at it.
DELETE FROM gameobject_loot_template WHERE Entry = 900510;
DELETE FROM gameobject               WHERE guid BETWEEN 9005001 AND 9005002 OR guid = 9005010;
DELETE FROM gameobject_template      WHERE entry IN (900500, 900501, 900510);
