-- VaultTerminal (1/3) — the module's own storage (CHARACTERS database)
-- The addon never stores state: this table is the truth the panel renders.
-- One row per successful claim, so "seals" is a COUNT and the day is a DATE.

CREATE TABLE IF NOT EXISTS `vault_terminal_claims` (
  `guid`      int unsigned NOT NULL COMMENT 'character guid',
  `claim_day` date         NOT NULL COMMENT 'server-local day, one claim per day',
  `claim_at`  timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`guid`, `claim_day`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
