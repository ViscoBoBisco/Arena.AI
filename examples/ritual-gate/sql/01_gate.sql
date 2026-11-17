-- The Vigil Gate (1/2) — a game object that needs three people, not one.
-- type 18 GAMEOBJECT_TYPE_SUMMONING_RITUAL (GameObjectData.h, GOData.summoningRitual):
--   Data0 reqParticipants        how many must be in the ritual circle
--   Data1 spellId                cast at completion -> Spell.dbc (must already exist)
--   Data2 animSpell              looping visual spell while the ritual runs
--   Data3 ritualPersistent       1 = the object survives the ritual
--   Data4 casterTargetSpell      cast on the *caster* at completion
--   Data5 casterTargetSpellTargets  0 = caster only, 1 = caster's group
--   Data6 castersGrouped         require the participants to be in one group
--   Data7 ritualNoTargetCheck    skip the line-of-sight/range check on each participant
-- Entry range: 900500-900599. Patch cost: none — the core runs the ritual, the client draws it.

SET @GATE  := 900500;
SET @DOOR  := 900501;

-- TODO(verify): existing display rows. A stone doorway and a glowing seal fit the fiction.
SET @DISP_GATE := 7670;
SET @DISP_DOOR := 3831;

-- TODO(verify): a stock 3.3.5a spell the client knows. 698 is Ritual of Summoning; its effect is
-- visible and self-explanatory. Replace it with whatever your gate should actually do — the rule is
-- only that the ID exists client-side, or the client cannot render the cast.
SET @RITUAL_SPELL := 698;
SET @ANIM_SPELL   := 0;      -- 0 = no looping visual; set a stock aura for a circle effect

DELETE FROM gameobject_template WHERE entry IN (@GATE, @DOOR);
DELETE FROM gameobject          WHERE guid BETWEEN 9005001 AND 9005002;

INSERT INTO gameobject_template
  (entry, type, displayId, name, size, Data0, Data1, Data2, Data3, Data4, Data5, Data6, Data7, VerifiedBuild)
VALUES
  (@GATE, 18, @DISP_GATE, 'Vigil Gate', 1.0, 3, @RITUAL_SPELL, @ANIM_SPELL, 0, 0, 0, 1, 0, 12340),
  -- A plain door in front of it, so the ritual has something to open in the fiction.
  (@DOOR, 0,  @DISP_DOOR, 'Vigil Door', 1.0, 0, 0, 0, 0, 0, 0, 0, 0, 12340);

-- Two objects, same spot, facing the room (map 1, Ashenvale).
INSERT INTO gameobject
  (guid, id, map, spawnMask, phaseMask, position_x, position_y, position_z, orientation,
   rotation0, rotation1, rotation2, rotation3, spawntimesecs, animprogress, state, VerifiedBuild)
VALUES
  (9005001, @GATE, 1, 1, 1, 3300.0, -1220.0, 91.0, 3.1416, 0, 0, 0, 1, 300, 100, 1, 12340),
  (9005002, @DOOR, 1, 1, 1, 3299.0, -1220.0, 91.0, 3.1416, 0, 0, 0, 1, 300, 100, 1, 12340);
