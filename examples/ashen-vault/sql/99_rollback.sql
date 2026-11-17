-- The Ashen Vault — rollback
-- Deletes by pinned entry/guid range only. Nothing outside 900100-900299 / 9001001-9002003 is touched,
-- which is the practical argument for a reserved ID range even when you never ship a client patch.

-- behaviour first (cheap), then spawns, then templates
DELETE FROM smart_scripts            WHERE entryorguid IN (900100, 900101, 900110, 900111) AND source_type = 0;
DELETE FROM creature_loot_template   WHERE Entry IN (900100, 900101, 900110, 900111);

DELETE FROM creature                 WHERE guid BETWEEN 9001001 AND 9001006;
DELETE FROM gameobject               WHERE guid BETWEEN 9002001 AND 9002003;

DELETE FROM creature_template_model  WHERE CreatureID IN (900100, 900101, 900110, 900111);
DELETE FROM creature_template        WHERE entry      IN (900100, 900101, 900110, 900111);

DELETE FROM gameobject_template      WHERE entry      IN (900200, 900201);
