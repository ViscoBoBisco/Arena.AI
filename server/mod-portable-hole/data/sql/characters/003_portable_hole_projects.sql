-- Shared, account-authorized project budgets over virtual commodity balances only.
-- These rows never contain physical Item instances and cannot materialize/craft items.
CREATE TABLE IF NOT EXISTS `portable_hole_projects` (
  `owner_account_id` int unsigned NOT NULL,
  `project_key` varchar(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `item_entry` int unsigned NOT NULL,
  `target_amount` bigint unsigned NOT NULL,
  `reserved_amount` bigint unsigned NOT NULL DEFAULT 0,
  `status` varchar(12) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'OPEN',
  `created_by_guid` int unsigned NOT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`owner_account_id`,`project_key`),
  KEY `idx_portable_hole_project_item` (`owner_account_id`,`item_entry`,`status`),
  CONSTRAINT `portable_hole_project_target_chk` CHECK (`target_amount` > 0),
  CONSTRAINT `portable_hole_project_reserved_chk` CHECK (`reserved_amount` <= `target_amount`),
  CONSTRAINT `portable_hole_project_status_chk` CHECK (`status` IN ('OPEN','CLOSED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `portable_hole_project_access` (
  `owner_account_id` int unsigned NOT NULL,
  `project_key` varchar(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `member_account_id` int unsigned NOT NULL,
  `role` varchar(12) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `granted_by_guid` int unsigned NOT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`owner_account_id`,`project_key`,`member_account_id`),
  KEY `idx_portable_hole_project_member` (`member_account_id`,`owner_account_id`,`project_key`),
  CONSTRAINT `portable_hole_project_access_fk` FOREIGN KEY (`owner_account_id`,`project_key`)
    REFERENCES `portable_hole_projects` (`owner_account_id`,`project_key`) ON DELETE CASCADE,
  CONSTRAINT `portable_hole_project_access_no_self_chk`
    CHECK (`owner_account_id` <> `member_account_id`),
  CONSTRAINT `portable_hole_project_access_role_chk` CHECK (`role` IN ('VIEW','CONTRIBUTE'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `portable_hole_project_reservations` (
  `owner_account_id` int unsigned NOT NULL,
  `project_key` varchar(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `request_id` varchar(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `contributor_account_id` int unsigned NOT NULL,
  `guid` int unsigned NOT NULL,
  `item_entry` int unsigned NOT NULL,
  `amount` bigint unsigned NOT NULL,
  `state` varchar(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'PENDING',
  `release_request_id` varchar(64) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`owner_account_id`,`project_key`,`request_id`),
  KEY `idx_portable_hole_project_contributor` (`contributor_account_id`,`guid`,`state`),
  CONSTRAINT `portable_hole_project_reservation_fk` FOREIGN KEY (`owner_account_id`,`project_key`)
    REFERENCES `portable_hole_projects` (`owner_account_id`,`project_key`) ON DELETE CASCADE,
  CONSTRAINT `portable_hole_project_reservation_state_chk`
    CHECK (`state` IN ('PENDING','HELD','DENIED','RELEASED')),
  CONSTRAINT `portable_hole_project_reservation_release_chk`
    CHECK ((`state` = 'RELEASED' AND `release_request_id` IS NOT NULL)
        OR (`state` <> 'RELEASED' AND `release_request_id` IS NULL)),
  CONSTRAINT `portable_hole_project_reservation_amount_chk` CHECK (`amount` > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
