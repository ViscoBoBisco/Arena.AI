-- The Ashen Vault (3/5) — game objects
-- Illustrative. Replace display ids with existing GameObjectDisplayInfo.dbc rows.
-- Data0..Data23 are a union: each `type` reads them with different meanings (GameObjectData.h).
--   type 0  DOOR      Data0 = startOpen, Data1 = lockId -> Lock.dbc, Data2 = autoCloseTime
--   type 5  GENERIC   Data0 = floatingTooltip, Data1 = highlight

SET @DOOR    := 900200;   -- type 0 = door
SET @BRAZIER := 900201;   -- type 5 = generic (no data semantics)

SET @DISP_DOOR    := 3831;   -- TODO(verify): existing door display
SET @DISP_BRAZIER := 6886;   -- TODO(verify): existing brazier display

DELETE FROM gameobject_template WHERE entry IN (@DOOR, @BRAZIER);
DELETE FROM gameobject          WHERE guid BETWEEN 9002001 AND 9002003;

INSERT INTO gameobject_template (entry, type, displayId, name, size, Data0, Data1)
VALUES
  -- Door: closed at spawn, unlocked (lockId 0) because the gate is scripted, not keyed.
  (@DOOR,    0, @DISP_DOOR,    'Ashen Vault Door', 1.0, 0, 0),
  -- Generic: decorative only, no interaction, no tooltip.
  (@BRAZIER, 5, @DISP_BRAZIER, 'Ashen Brazier',    1.0, 0, 0);

INSERT INTO gameobject
  (guid, id, map, spawnMask, phaseMask, position_x, position_y, position_z, orientation,
   rotation0, rotation1, rotation2, rotation3, spawntimesecs, animprogress, state)
VALUES
  (9002001, @DOOR,    1, 1, 1, 3290.0, -1220.0, 91.0, -1.5708, 0, 0, 0, 1, 300, 100, 1),
  (9002002, @BRAZIER, 1, 1, 1, 3296.0, -1215.0, 91.0,  0.0,    0, 0, 0, 1, 300, 100, 1),
  (9002003, @BRAZIER, 1, 1, 1, 3296.0, -1225.0, 91.0,  0.0,    0, 0, 0, 1, 300, 100, 1);
