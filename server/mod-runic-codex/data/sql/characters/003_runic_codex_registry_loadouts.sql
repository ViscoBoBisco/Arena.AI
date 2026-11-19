-- Registry-backed discoveries, two equipment slots, and saved character loadouts.
ALTER TABLE `runic_codex_powers`
  ADD COLUMN `equipped_slot` tinyint unsigned DEFAULT NULL AFTER `equipped`;
UPDATE `runic_codex_powers` SET `equipped_slot`=0 WHERE `equipped`=1;
ALTER TABLE `runic_codex_powers`
  ADD UNIQUE KEY `runic_codex_equipped_slot_unique` (`guid`,`equipped_slot`),
  ADD CONSTRAINT `runic_codex_equipped_slot_chk`
    CHECK (`equipped_slot` IS NULL OR `equipped_slot` < 2),
  ADD CONSTRAINT `runic_codex_equipped_slot_state_chk`
    CHECK ((`equipped`=0 AND `equipped_slot` IS NULL) OR (`equipped`=1 AND `equipped_slot` IS NOT NULL));

CREATE TABLE IF NOT EXISTS `runic_codex_discoveries` (
  `guid` int unsigned NOT NULL,
  `power_key` varchar(32) NOT NULL,
  `item_entry` int unsigned NOT NULL,
  `used_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`guid`, `power_key`),
  KEY `runic_codex_discovery_item_idx` (`item_entry`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
INSERT IGNORE INTO `runic_codex_discoveries` (`guid`,`power_key`,`item_entry`,`used_at`)
  SELECT `guid`,`power_key`,`item_entry`,`used_at` FROM `runic_codex_inscriptions`;

CREATE TABLE IF NOT EXISTS `runic_codex_loadouts` (
  `guid` int unsigned NOT NULL,
  `loadout_name` varchar(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`guid`, `loadout_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `runic_codex_loadout_powers` (
  `guid` int unsigned NOT NULL,
  `loadout_name` varchar(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `slot_index` tinyint unsigned NOT NULL,
  `power_key` varchar(32) NOT NULL,
  PRIMARY KEY (`guid`, `loadout_name`, `slot_index`),
  UNIQUE KEY `runic_codex_loadout_power_unique` (`guid`, `loadout_name`, `power_key`),
  CONSTRAINT `runic_codex_loadout_slot_chk` CHECK (`slot_index` < 2),
  CONSTRAINT `runic_codex_loadout_fk` FOREIGN KEY (`guid`,`loadout_name`)
    REFERENCES `runic_codex_loadouts` (`guid`,`loadout_name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
