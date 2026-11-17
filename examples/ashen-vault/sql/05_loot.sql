-- The Ashen Vault (5/5) — loot
-- Illustrative. Every item here must be an item that already exists in the client's Item.dbc —
-- that is the whole point of the example: loot is delivered by the server, but the client can only
-- render items whose display/icon/name it can resolve (docs/01 §client authority).
-- GroupId 1 with three items = one roll of a "pick one of three" group.

SET @KEEPER := 900110;

DELETE FROM creature_loot_template WHERE Entry = @KEEPER;

INSERT INTO creature_loot_template
  (Entry, Item, Reference, Chance, QuestRequired, LootMode, GroupId, MinCount, MaxCount, Comment)
VALUES
  -- TODO(verify): existing item entries. Replace with stock items appropriate to your realm.
  (@KEEPER, 43573, 0, 0,   0, 1, 1, 1, 1, 'Ashen Vault - group 1: tears of bitter anguish'),
  (@KEEPER, 43575, 0, 0,   0, 1, 1, 1, 1, 'Ashen Vault - group 1: worn leather satchel'),
  (@KEEPER, 43576, 0, 0,   0, 1, 1, 1, 1, 'Ashen Vault - group 1: chipped horn'),
  -- Money: independent of the group rolls.
  (@KEEPER, 0,     0, 100, 0, 1, 0, 25, 40, 'Ashen Vault - gold drop (25-40)');
