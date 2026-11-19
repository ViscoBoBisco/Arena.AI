-- Character-bound Runic Codex discovery/equip state. One row is one entitlement.
CREATE TABLE IF NOT EXISTS `runic_codex_powers` (
  `guid` int unsigned NOT NULL,
  `power_key` varchar(32) NOT NULL,
  `unlocked` tinyint unsigned NOT NULL DEFAULT 0,
  `equipped` tinyint unsigned NOT NULL DEFAULT 0,
  `discovered_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`guid`, `power_key`),
  CONSTRAINT `runic_codex_equipped_requires_unlock_chk` CHECK (`equipped` <= `unlocked`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
