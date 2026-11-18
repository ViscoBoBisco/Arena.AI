-- VaultTerminal rollback
-- World DB objects are deleted by pinned IDs. Disable the module and remove the addon first.
-- Do not drop Characters state as part of feature rollback: it contains character progress and
-- durable request receipts needed to make delayed retries idempotent.

DELETE FROM gossip_menu_option WHERE MenuID = 900300;
DELETE FROM gossip_menu        WHERE MenuID = 900300;
DELETE FROM npc_text           WHERE ID     = 900300;
DELETE FROM creature           WHERE guid   = 9003001;
DELETE FROM creature_template_model WHERE CreatureID = 900300;
DELETE FROM creature_template  WHERE entry  = 900300;

-- Characters database: keep `vault_terminal_claims` and `vault_terminal_claims_requests`.
-- If migrated from the old one-row-per-day schema, also retain `vault_terminal_claims_legacy_v1`.
-- Any eventual purge is a separate reviewed operation after backup and a deliberate retention decision.
