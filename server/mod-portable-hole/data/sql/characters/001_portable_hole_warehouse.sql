-- Portable Hole commodity ledger — Characters DB, InnoDB only.
-- Character-scoped by design. No account, party, or item-instance ownership is inferred.
-- Pins are server-persisted character preferences; pin_state serializes the ten-pin cap.
CREATE TABLE IF NOT EXISTS `portable_hole_pin_state` (
  `guid` int unsigned NOT NULL,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`guid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `portable_hole_pins` (
  `guid` int unsigned NOT NULL,
  `item_entry` int unsigned NOT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`guid`, `item_entry`),
  KEY `idx_portable_hole_pins_order` (`guid`, `created_at`, `item_entry`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS `portable_hole_commodities` (
  `guid` int unsigned NOT NULL,
  `item_entry` int unsigned NOT NULL,
  `balance` bigint unsigned NOT NULL DEFAULT 0,
  `reserved` bigint unsigned NOT NULL DEFAULT 0,
  `last_request_id` varchar(64) DEFAULT NULL,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`guid`, `item_entry`),
  CONSTRAINT `portable_hole_balance_reserved_chk` CHECK (`reserved` <= `balance`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `portable_hole_reservations` (
  `guid` int unsigned NOT NULL,
  `reservation_id` varchar(64) NOT NULL,
  `item_entry` int unsigned NOT NULL,
  `amount` bigint unsigned NOT NULL,
  `state` varchar(16) NOT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`guid`, `reservation_id`),
  KEY `idx_portable_hole_reservation_item` (`guid`, `item_entry`, `state`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `portable_hole_receipts` (
  `guid` int unsigned NOT NULL,
  `request_id` varchar(64) NOT NULL,
  `fingerprint` varchar(256) NOT NULL,
  `action` varchar(16) NOT NULL,
  `status` varchar(24) NOT NULL,
  `item_entry` int unsigned NOT NULL,
  `amount` bigint unsigned NOT NULL,
  `reservation_id` varchar(64) DEFAULT NULL,
  `source` varchar(24) NOT NULL,
  `actor_guid` int unsigned NOT NULL,
  `balance_after` bigint unsigned NOT NULL DEFAULT 0,
  `reserved_after` bigint unsigned NOT NULL DEFAULT 0,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`guid`, `request_id`),
  KEY `idx_portable_hole_receipt_action` (`guid`, `action`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
