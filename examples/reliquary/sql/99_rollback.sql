-- The Reliquary — rollback. Text first, then the objects that referenced it.
DELETE FROM gameobject          WHERE guid BETWEEN 9004101 AND 9004103;
DELETE FROM gameobject_template WHERE entry IN (900401, 900402, 900403);
DELETE FROM page_text           WHERE ID BETWEEN 900400 AND 900429;
