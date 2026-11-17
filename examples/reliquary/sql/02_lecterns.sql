-- The Reliquary (2/2) — the objects that serve the pages.
-- type 9 GAMEOBJECT_TYPE_TEXT reads Data0 = pageID, Data1 = language -> Languages.dbc,
-- Data2 = pageMaterial -> PageTextMaterial.dbc, Data3 = allowMounted (GameObjectData.h).
-- Reusing existing display rows is what keeps this patch-free.

SET @JOURNAL := 900401;    -- reads page 900400 (three pages)
SET @PLAQUE  := 900402;    -- reads page 900410 (one page)
SET @LEDGER  := 900403;    -- reads page 900420 (two pages)

-- TODO(verify): existing GameObjectDisplayInfo.dbc rows — a lectern, a wall plaque, a bookshelf.
SET @DISP_JOURNAL := 7630;
SET @DISP_PLAQUE  := 366;
SET @DISP_LEDGER  := 359;

DELETE FROM gameobject_template WHERE entry IN (@JOURNAL, @PLAQUE, @LEDGER);
DELETE FROM gameobject          WHERE guid BETWEEN 9004101 AND 9004103;

INSERT INTO gameobject_template (entry, type, displayId, name, size, Data0, Data1, Data2, Data3, VerifiedBuild)
VALUES
  -- language 0 = LANG_UNIVERSAL (everyone reads it); 1 would restrict it to a language the
  -- client already has, which is a neat way to gate lore behind a language skill.
  (@JOURNAL, 9, @DISP_JOURNAL, 'Field Journal',  1.0, 900400, 0, 1, 0, 12340),
  (@PLAQUE,  9, @DISP_PLAQUE,  'Reliquary Plaque', 1.0, 900410, 0, 1, 0, 12340),
  (@LEDGER,  9, @DISP_LEDGER,  'Ledger of the Ninth Door', 1.0, 900420, 0, 1, 0, 12340);

-- Three lecterns in a row, inside the Ashen Vault's anteroom (map 1).
INSERT INTO gameobject
  (guid, id, map, spawnMask, phaseMask, position_x, position_y, position_z, orientation,
   rotation0, rotation1, rotation2, rotation3, spawntimesecs, animprogress, state, VerifiedBuild)
VALUES
  (9004101, @JOURNAL, 1, 1, 1, 3288.5, -1222.0, 91.0, 1.5708, 0, 0, 0, 1, 300, 100, 1, 12340),
  (9004102, @PLAQUE,  1, 1, 1, 3291.0, -1218.0, 91.0, 3.1416, 0, 0, 0, 1, 300, 100, 1, 12340),
  (9004103, @LEDGER,  1, 1, 1, 3286.0, -1226.0, 91.0, 0.0,    0, 0, 0, 1, 300, 100, 1, 12340);
