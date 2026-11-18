-- Vault Terminal: Characters database migration.
-- One transaction records each request outcome and applies at most one increment.
CREATE TABLE IF NOT EXISTS `vault_terminal_claims` (
  `guid` int unsigned NOT NULL COMMENT 'AzerothCore character low GUID',
  `total_claims` int unsigned NOT NULL DEFAULT 0 COMMENT 'server-authoritative total',
  `last_claim_day` date DEFAULT NULL COMMENT 'database-server day of the last claim',
  `last_request_id` varchar(64) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
  PRIMARY KEY (`guid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Preserve an installation using the previous one-row-per-day schema. Migrate its full
-- claim count and latest day to the new one-row-per-character counter, retaining the old
-- daily rows in a backup table. Each prepared statement is a single DDL/DML operation.
SET @arena_counter_legacy_shape = (
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'vault_terminal_claims' AND column_name = 'claim_day') > 0
  AND (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'vault_terminal_claims' AND column_name = 'total_claims') = 0
  AND (SELECT COUNT(*) FROM information_schema.tables
   WHERE table_schema = DATABASE() AND table_name = 'vault_terminal_claims_legacy_v1') = 0
);
SET @arena_counter_stage_sql = IF(@arena_counter_legacy_shape,
  'CREATE TABLE IF NOT EXISTS `vault_terminal_claims_stage_v2` (
     `guid` int unsigned NOT NULL,
     `total_claims` int unsigned NOT NULL DEFAULT 0,
     `last_claim_day` date DEFAULT NULL,
     `last_request_id` varchar(64) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
     PRIMARY KEY (`guid`)
   ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4', 'SELECT 1');
PREPARE arena_counter_stage_stmt FROM @arena_counter_stage_sql;
EXECUTE arena_counter_stage_stmt;
DEALLOCATE PREPARE arena_counter_stage_stmt;

SET @arena_counter_copy_sql = IF(@arena_counter_legacy_shape,
  'INSERT INTO `vault_terminal_claims_stage_v2` (guid, total_claims, last_claim_day, last_request_id)
   SELECT guid, COUNT(*), MAX(claim_day), NULL FROM `vault_terminal_claims` GROUP BY guid
   ON DUPLICATE KEY UPDATE total_claims = VALUES(total_claims),
     last_claim_day = VALUES(last_claim_day), last_request_id = NULL', 'SELECT 1');
PREPARE arena_counter_copy_stmt FROM @arena_counter_copy_sql;
EXECUTE arena_counter_copy_stmt;
DEALLOCATE PREPARE arena_counter_copy_stmt;

SET @arena_counter_rename_sql = IF(@arena_counter_legacy_shape,
  'RENAME TABLE `vault_terminal_claims` TO `vault_terminal_claims_legacy_v1`, `vault_terminal_claims_stage_v2` TO `vault_terminal_claims`', 'SELECT 1');
PREPARE arena_counter_rename_stmt FROM @arena_counter_rename_sql;
EXECUTE arena_counter_rename_stmt;
DEALLOCATE PREPARE arena_counter_rename_stmt;

CREATE TABLE IF NOT EXISTS `vault_terminal_claims_requests` (
  `guid` int unsigned NOT NULL,
  `request_id` varchar(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `status` varchar(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `total_claims` int unsigned NOT NULL DEFAULT 0,
  `claim_day` date NOT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`guid`, `request_id`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
