-- Explicit account-to-account authorization for read-only shared vault views.
-- Writes, reservations, pins, and receipts remain character-scoped.
CREATE TABLE IF NOT EXISTS `portable_hole_vault_access` (
  `owner_account_id` int unsigned NOT NULL,
  `viewer_account_id` int unsigned NOT NULL,
  `granted_by_guid` int unsigned NOT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`owner_account_id`, `viewer_account_id`),
  KEY `idx_portable_hole_vault_viewer` (`viewer_account_id`, `owner_account_id`),
  CONSTRAINT `portable_hole_vault_no_self_share_chk` CHECK (`owner_account_id` <> `viewer_account_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
