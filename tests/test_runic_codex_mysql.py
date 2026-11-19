"""Disposable Characters DB and source-contract tests for the Runic Codex vertical."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

sys.path.insert(0, str(helpers.REPO / "server/mod-portable-hole/harness"))
import portable_hole_mysql as db  # noqa: E402

_REQUIRED = ("ARENA_MYSQL_HOST", "ARENA_MYSQL_DATABASE", "ARENA_MYSQL_USER", "ARENA_MYSQL_PASSWORD")
_ENABLED = all(os.environ.get(key) is not None for key in _REQUIRED)
_MIGRATION = helpers.REPO / "server/mod-runic-codex/data/sql/characters/001_runic_codex.sql"
_MODULE = helpers.REPO / "server/mod-runic-codex/src/RunicCodex.cpp"
_POLICY = helpers.REPO / "server/mod-runic-codex/src/ArcaneEchoPolicy.h"
_POLICY_TEST = helpers.REPO / "tests/native/runic_codex_policy_test.cpp"
_ADDON = helpers.REPO / "server/mod-runic-codex/addon/RunicCodex/RunicCodex.lua"


@unittest.skipUnless(_ENABLED, "requires explicitly configured disposable MySQL")
class RunicCodexMySQL(unittest.TestCase):
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
        cls.next_guid = 971000

    @classmethod
    def tearDownClass(cls):
        conn = db.connect(cls.pymysql)
        try:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM runic_codex_powers WHERE guid BETWEEN 971000 AND %s",
                               (cls.next_guid,))
            conn.commit()
        finally:
            conn.close()

    def setUp(self):
        type(self).next_guid += 1
        self.guid = type(self).next_guid

    def row(self):
        conn = db.connect(self.pymysql)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT unlocked,equipped FROM runic_codex_powers "
                               "WHERE guid=%s AND power_key='arcane_echo'", (self.guid,))
                return cursor.fetchone()
        finally:
            conn.close()

    def test_schema_supports_locked_state_idempotent_unlock_durable_equip_and_character_scope(self):
        conn = db.connect(self.pymysql)
        try:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO runic_codex_powers(guid,power_key,unlocked,equipped) "
                               "VALUES(%s,'arcane_echo',0,0)", (self.guid,))
            conn.commit()
        finally:
            conn.close()
        conn = db.connect(self.pymysql)
        try:
            with self.assertRaises(self.pymysql.err.DatabaseError) as raised:
                with conn.cursor() as cursor:
                    cursor.execute("UPDATE runic_codex_powers SET equipped=1 WHERE guid=%s "
                                   "AND power_key='arcane_echo'", (self.guid,))
            self.assertEqual(raised.exception.args[0], 3819,
                             "MySQL CHECK violation should reject equip while locked")
        finally:
            conn.close()
        self.assertEqual(self.row(), (0, 0), "locked entitlement must remain unequipped")

        conn = db.connect(self.pymysql)
        try:
            conn.begin()
            with conn.cursor() as cursor:
                # Learning a verified spell chain is represented as an idempotent entitlement insert.
                for _ in range(2):
                    cursor.execute("INSERT INTO runic_codex_powers(guid,power_key,unlocked,equipped) "
                                   "VALUES(%s,'arcane_echo',1,0) ON DUPLICATE KEY UPDATE unlocked=1",
                                   (self.guid,))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.row(), (1, 0))

        conn = db.connect(self.pymysql)
        try:
            with conn.cursor() as cursor:
                cursor.execute("UPDATE runic_codex_powers SET equipped=1 WHERE guid=%s "
                               "AND power_key='arcane_echo' AND unlocked=1", (self.guid,))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.row(), (1, 1))
        self.assertIsNone(self._read(self.guid + 999))

    def _read(self, guid):
        conn = db.connect(self.pymysql)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT unlocked,equipped FROM runic_codex_powers WHERE guid=%s "
                               "AND power_key='arcane_echo'", (guid,))
                return cursor.fetchone()
        finally:
            conn.close()


class RunicCodexSourceContract(unittest.TestCase):
    def test_discovery_and_effect_use_verified_server_hooks_and_existing_spell_chain(self):
        source = _MODULE.read_text(encoding="utf-8")
        addon = _ADDON.read_text(encoding="utf-8")
        self.assertIn("PLAYERHOOK_ON_LEARN_SPELL", source)
        self.assertIn("PLAYERHOOK_ON_SPELL_CAST", source)
        self.assertIn("PLAYERHOOK_ON_UPDATE", source)
        self.assertIn("bool /*skipCheck*/", source)
        self.assertNotIn("skipCheck ||", source)
        self.assertIn("player->HasSpell(spell)", source)
        self.assertIn("spell->GetSpellInfo()->PowerType != POWER_MANA", source)
        self.assertIn("spell->GetPowerCost()", source)
        self.assertIn("RunicCodexPolicy::ManaSpent", source)
        self.assertIn("RunicCodexPolicy::CalculateRefund", source)
        self.assertIn("ModifyPower(POWER_MANA, static_cast<int32>(refund))", source)
        self.assertIn("runic_codex_powers", source)
        self.assertIn('"EQUIP"', source)
        self.assertIn('SendAddonMessage(PREFIX', addon)
        self.assertIn('send("EQUIP"', addon)
        self.assertNotIn("GetGUID", addon)
        self.assertIn("5143, 5144, 5145", _POLICY.read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("c++"), "a C++ compiler is required for policy regression")
    def test_native_arcane_echo_policy_boundaries(self):
        with tempfile.TemporaryDirectory(prefix="runic-codex-policy-") as directory:
            executable = Path(directory) / "runic-codex-policy-test"
            subprocess.run([
                shutil.which("c++"), "-std=c++20", "-Wall", "-Wextra", "-Werror",
                "-I", str(_POLICY.parent), str(_POLICY_TEST), "-o", str(executable),
            ], check=True, capture_output=True, text=True)
            subprocess.run([str(executable)], check=True, capture_output=True, text=True)

    def test_addon_parses_under_wotlk_lua_51(self):
        try:
            from lupa.lua51 import LuaRuntime
        except ImportError:
            self.skipTest("install lupa==2.6 for Lua 5.1 addon syntax validation")
        source = _ADDON.read_text(encoding="utf-8")
        self.assertIsNotNone(LuaRuntime().eval("loadstring")(source, "@RunicCodex.lua"))


if __name__ == "__main__":
    unittest.main()
