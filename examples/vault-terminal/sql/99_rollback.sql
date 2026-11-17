-- VaultTerminal — rollback
-- World DB objects are deleted by pinned ids; the characters table is dropped separately because
-- it lives in a different database (run the DROP against acore_characters).

DELETE FROM gossip_menu_option WHERE MenuID = 900300;
DELETE FROM gossip_menu        WHERE MenuID = 900300;
DELETE FROM npc_text           WHERE ID     = 900300;
DELETE FROM creature           WHERE guid   = 9003001;
DELETE FROM creature_template_model WHERE CreatureID = 900300;
DELETE FROM creature_template  WHERE entry  = 900300;

-- characters database:
-- DROP TABLE IF EXISTS `vault_terminal_claims`;
