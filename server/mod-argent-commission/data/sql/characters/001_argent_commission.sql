-- Character entitlement to the core Argent Champion mechanic.
CREATE TABLE IF NOT EXISTS `argent_commission_entitlements` (
  `guid` int unsigned NOT NULL,
  `faction_id` int unsigned NOT NULL,
  `effect_spell` int unsigned NOT NULL,
  `granted_by` varchar(32) NOT NULL,
  `granted_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`guid`),
  CONSTRAINT `argent_commission_faction_chk` CHECK (`faction_id` = 1106),
  CONSTRAINT `argent_commission_spell_chk` CHECK (`effect_spell` = 57819)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
