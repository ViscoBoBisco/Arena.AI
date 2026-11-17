-- The Fair-Weather Market (2/3) — stock, and three ways to gate a row.
-- npc_vendor: (entry, slot, item, maxcount, incrtime, ExtendedCost, VerifiedBuild)
--   maxcount 0 = unlimited; ExtendedCost points at ItemExtendedCost.dbc (a client table — avoid it
--   if you want to stay patch-free).
-- Conditions on vendor rows use SourceTypeOrReferenceId 23 (CONDITION_SOURCE_TYPE_NPC_VENDOR,
-- ConditionMgr.h), SourceGroup = the vendor's entry, SourceEntry = the item's entry.

SET @VENDOR := 900600;

DELETE FROM npc_vendor WHERE entry = @VENDOR;
DELETE FROM conditions WHERE SourceTypeOrReferenceId = 23 AND SourceGroup = @VENDOR;

-- TODO(verify): existing item entries. Consumables are the safest stock: no client patch, no
-- ItemExtendedCost, no custom economy.
SET @BREAD  := 4544;
SET @WATER  := 159;
SET @JERKY  := 117;
SET @MARKER := 4056;   -- a veteran-only trinket, existing in Item.dbc

INSERT INTO npc_vendor (entry, slot, item, maxcount, incrtime, ExtendedCost, VerifiedBuild) VALUES
  (@VENDOR, 0, @BREAD,  0, 0, 0, 12340),
  (@VENDOR, 1, @WATER,  0, 0, 0, 12340),
  (@VENDOR, 2, @JERKY,  0, 0, 0, 12340),
  (@VENDOR, 3, @MARKER, 1, 3600, 0, 12340);   -- one per hour, no extended cost

-- Three predicates, three ConditionType values (ConditionMgr.h):
--   8  CONDITION_QUESTREWARDED   quest_id
--   5  CONDITION_REPUTATION_RANK faction_id, rankMask (1 hated .. 128 exalted)
--   12 CONDITION_ACTIVE_EVENT    event_id
-- Rows that share an ElseGroup are OR-ed; different groups are AND-ed.

-- TODO(verify): a quest your realm actually rewards, and a faction id from Faction.dbc.
SET @QUEST   := 13189;
SET @FACTION := 1106;

-- The trinket needs EITHER the quest rewarded OR Revered with the faction: two rows, one group.
INSERT INTO conditions
  (SourceTypeOrReferenceId, SourceGroup, SourceEntry, SourceId, ElseGroup, ConditionTypeOrReference,
   ConditionTarget, ConditionValue1, ConditionValue2, ConditionValue3, NegativeCondition,
   ErrorType, ErrorTextId, ScriptName, Comment)
VALUES
  (23, @VENDOR, @MARKER, 0, 1, 8,  0, @QUEST,   0,   0, 0, 0, 0, '', 'Market: veteran stock if attuned'),
  (23, @VENDOR, @MARKER, 0, 1, 5,  0, @FACTION, 64,  0, 0, 0, 0, '', 'Market: or Revered with the faction'),
  -- The same item is additionally required to be an event day (AND: its own group).
  (23, @VENDOR, @MARKER, 0, 2, 12, 0, 240,     0,   0, 0, 0, 0, '', 'Market: only while the market runs');

-- Reading this back: "stock the trinket if (attuned OR Revered) AND the market is open". Four rows.
