-- The Ashen Vault (2/5) — creature spawns
-- Illustrative. Replace the base coordinates with the output of .gps while standing in the cave.
-- Spawn guids are pinned (9001001+) so re-running this file is idempotent and rollback is exact.

SET @MAP := 1;               -- Ashenvale: always-present terrain, no patch, no instance needed
SET @X   := 3290.0;          -- TODO(verify): .gps
SET @Y   := -1220.0;
SET @Z   := 91.0;

DELETE FROM creature WHERE guid BETWEEN 9001001 AND 9001006;

INSERT INTO creature
  (guid, id1, map, spawnMask, phaseMask, position_x, position_y, position_z, orientation,
   spawntimesecs, wander_distance, curhealth, curmana, MovementType)
VALUES
  -- Gate NPC outside the door, facing it (-pi/2 radiants ≈ west).
  (9001001, 900100, @MAP, 1, 1, @X,        @Y,        @Z,        -1.5708, 300, 0, 0, 0, 0),
  -- Two sentinels flanking the inner corridor.
  (9001002, 900101, @MAP, 1, 1, @X + 6.0,  @Y + 3.0,  @Z,         3.1416, 300, 3, 0, 0, 1),
  (9001003, 900101, @MAP, 1, 1, @X + 6.0,  @Y - 3.0,  @Z,         0.0,    300, 3, 0, 0, 1),
  -- The keeper, at the far end of the vault.
  (9001004, 900110, @MAP, 1, 1, @X + 22.0, @Y,        @Z,         3.1416, 300, 0, 0, 0, 0),
  -- Two more trash pulls in the antechamber.
  (9001005, 900101, @MAP, 1, 1, @X + 14.0, @Y + 6.0,  @Z,         3.1416, 300, 3, 0, 0, 1),
  (9001006, 900101, @MAP, 1, 1, @X + 14.0, @Y - 6.0,  @Z,         3.1416, 300, 3, 0, 0, 1);
