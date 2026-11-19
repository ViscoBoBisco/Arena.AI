-- Durable discovery step: the player must use the source-verified tome and know an Arcane Missiles rank.
CREATE TABLE IF NOT EXISTS `runic_codex_inscriptions` (
  `guid` int unsigned NOT NULL,
  `power_key` varchar(32) NOT NULL,
  `item_entry` int unsigned NOT NULL,
  `used_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`guid`, `power_key`),
  CONSTRAINT `runic_codex_inscriptions_power_chk` CHECK (`power_key` = 'arcane_echo'),
  CONSTRAINT `runic_codex_inscriptions_item_chk` CHECK (`item_entry` = 22739)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
