-- Two Cities — rollback.
DELETE FROM smart_scripts  WHERE entryorguid IN (900700, 900701);
DELETE FROM creature_text  WHERE CreatureID IN (900700, 900701);
DELETE FROM spell_area     WHERE spell = 59074 AND area = 331;
DELETE FROM creature       WHERE guid BETWEEN 9007001 AND 9007002;
DELETE FROM creature_template_model WHERE CreatureID IN (900700, 900701);
DELETE FROM creature_template       WHERE entry IN (900700, 900701);
