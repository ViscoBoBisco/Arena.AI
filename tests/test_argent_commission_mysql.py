"""Disposable Characters DB and source-contract tests for Argent Commission."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

sys.path.insert(0, str(helpers.REPO / "server/mod-portable-hole/harness"))
import portable_hole_mysql as db  # noqa: E402

_REQUIRED = ("ARENA_MYSQL_HOST", "ARENA_MYSQL_DATABASE", "ARENA_MYSQL_USER", "ARENA_MYSQL_PASSWORD")
_ENABLED = all(os.environ.get(key) is not None for key in _REQUIRED)
_MIGRATION = helpers.REPO / "server/mod-argent-commission/data/sql/characters/001_argent_commission.sql"
_MODULE = helpers.REPO / "server/mod-argent-commission/src/ArgentCommission.cpp"
_ADDON = helpers.REPO / "server/mod-argent-commission/addon/ArgentCommission/ArgentCommission.lua"
_README = helpers.REPO / "server/mod-argent-commission/README.md"


@unittest.skipUnless(_ENABLED, "requires explicitly configured disposable MySQL")
class ArgentCommissionMySQL(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import pymysql
        except ImportError as exc:
            raise unittest.SkipTest("install PyMySQL for disposable MySQL integration") from exc
        cls.pymysql = pymysql
        conn = db.connect(pymysql)
        try:
            with conn.cursor() as cursor:
                for statement in _MIGRATION.read_text(encoding="utf-8").split(";"):
                    statement = "\n".join(line for line in statement.splitlines()
                                           if not line.lstrip().startswith("--")).strip()
                    if statement:
                        cursor.execute(statement)
            conn.commit()
        finally:
            conn.close()
        cls.next_guid = 972000

    @classmethod
    def tearDownClass(cls):
        conn = db.connect(cls.pymysql)
        try:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM argent_commission_entitlements WHERE guid BETWEEN 972000 AND %s",
                               (cls.next_guid,))
            conn.commit()
        finally:
            conn.close()

    def setUp(self):
        type(self).next_guid += 1
        self.guid = type(self).next_guid

    def test_persistent_entitlement_schema_accepts_native_identity_and_is_idempotent(self):
        conn = db.connect(self.pymysql)
        try:
            conn.begin()
            with conn.cursor() as cursor:
                cursor.execute("INSERT IGNORE INTO argent_commission_entitlements "
                               "(guid,faction_id,effect_spell,granted_by) "
                               "VALUES(%s,1106,57819,'REPUTATION_CLAIM')", (self.guid,))
                cursor.execute("INSERT IGNORE INTO argent_commission_entitlements "
                               "(guid,faction_id,effect_spell,granted_by) "
                               "VALUES(%s,1106,57819,'REPUTATION_CLAIM')", (self.guid,))
                cursor.execute("SELECT faction_id,effect_spell,granted_by FROM "
                               "argent_commission_entitlements WHERE guid=%s", (self.guid,))
                rows = cursor.fetchall()
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(rows, ((1106, 57819, "REPUTATION_CLAIM"),))
        self.assertIsNone(self._read(self.guid + 999))

    def _read(self, guid):
        conn = db.connect(self.pymysql)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT guid FROM argent_commission_entitlements WHERE guid=%s", (guid,))
                return cursor.fetchone()
        finally:
            conn.close()


class ArgentCommissionSourceContract(unittest.TestCase):
    def test_claim_uses_native_reputation_and_existing_championing_spell(self):
        source = _MODULE.read_text(encoding="utf-8")
        addon = _ADDON.read_text(encoding="utf-8")
        self.assertIn("ARGENT_CRUSADE_FACTION = 1106", source)
        self.assertIn("ARGENT_CHAMPION_SPELL = 57819", source)
        claim = source[source.index("void Claim(Player* player)"):source.index("class ArgentCommissionPlayerScript")]
        gate = claim.index("GetReputationRank(ARGENT_CRUSADE_FACTION) < REP_HONORED")
        persist = claim.index("INSERT IGNORE INTO `argent_commission_entitlements`")
        self.assertLess(gate, persist, "native Honored rank check must precede entitlement persistence")
        self.assertIn("PLAYERHOOK_ON_LOGIN", source)
        self.assertIn("6ffb62abb867dabb258de6813ff5d06d6b011543", _README.read_text(encoding="utf-8"))
        self.assertIn("argent_commission_entitlements", source)
        self.assertIn("AND faction_id={} AND effect_spell={}", source)
        self.assertNotIn("SetFaction(", source)
        self.assertNotIn("ModifyReputation(", source)
        self.assertIn('send("CLAIM")', addon)
        self.assertNotIn("GetGUID", addon)

    def test_addon_parses_under_wotlk_lua_51(self):
        try:
            from lupa.lua51 import LuaRuntime
        except ImportError:
            self.skipTest("install lupa==2.6 for Lua 5.1 addon syntax validation")
        source = _ADDON.read_text(encoding="utf-8")
        self.assertIsNotNone(LuaRuntime().eval("loadstring")(source, "@ArgentCommission.lua"))


if __name__ == "__main__":
    unittest.main()
