"""Foundry generators: determinism, rollback symmetry, module/addon shape."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

sys.path.insert(0, str(helpers.REPO))
from foundry import generate  # noqa: E402
from foundry.spec import allocate_ids  # noqa: E402

SPEC = json.loads((helpers.REPO / "specs" / "frost-armory.json").read_text(encoding="utf-8"))
PKG = helpers.REPO / "packages" / "frost-armory"


class Determinism(unittest.TestCase):
    def test_same_spec_same_bytes(self):
        self.assertEqual(generate.world_sql(SPEC), generate.world_sql(SPEC))
        self.assertEqual(generate.rollback_sql(SPEC), generate.rollback_sql(SPEC))

    def test_committed_package_matches_regeneration(self):
        manifest = json.loads((PKG / "manifest.json").read_text(encoding="utf-8"))
        for rel, meta in manifest["files"].items():
            if rel in ("manifest.json", "evidence.json", "compatibility.json"):
                continue
            data = (PKG / rel).read_bytes()
            import hashlib
            self.assertEqual(hashlib.sha256(data).hexdigest(), meta["sha256"], rel)
        for rel, content in generate.module_cpp(SPEC).items():
            if rel.startswith("server/"):
                self.assertEqual((PKG / rel).read_text(encoding="utf-8"), content, rel)


class RollbackSymmetry(unittest.TestCase):
    def test_every_insert_has_a_delete(self):
        world = generate.world_sql(SPEC)
        rollback = generate.rollback_sql(SPEC)
        inserts = re.findall(r"INSERT INTO (\w+)", world)
        deletes = re.findall(r"DELETE FROM (\w+)", rollback)
        for table in set(inserts):
            self.assertIn(table, deletes, f"{table} inserted but never rolled back")

    def test_delete_before_insert(self):
        world = generate.world_sql(SPEC)
        for m in re.finditer(r"DELETE FROM (\w+) WHERE entry = (\d+);\nINSERT INTO \1", world):
            self.assertTrue(m)
        # every INSERT is preceded by a DELETE for the same table
        lines = world.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("INSERT INTO"):
                table = line.split()[2]
                prior = "\n".join(lines[:i])
                self.assertIn(f"DELETE FROM {table}", prior, line)

    def test_rollback_covers_every_allocated_id(self):
        rollback = generate.rollback_sql(SPEC)
        for key, nid in allocate_ids(SPEC).items():
            if key.startswith("recipe") or key.endswith("vendor"):
                continue  # no table row of their own
            self.assertIn(str(nid), rollback, key)


class ModuleAndAddon(unittest.TestCase):
    def test_counter_terminal_outputs_functional_protocol_and_durable_ledger(self):
        spec = json.loads((helpers.REPO / "specs" / "vault-terminal.json").read_text())
        module = generate.module_cpp(spec)
        source = module["server/mod-vault-terminal/src/VaultTerminal.cpp"]
        addon = generate.addon_files(spec)
        lua = addon["addon/VaultTerminal/VaultTerminal.lua"]
        toc = addon["addon/VaultTerminal/VaultTerminal.toc"]
        sql = generate.characters_sql_files(spec)[
            "sql/characters/vault_terminal_claims.sql"]
        protocol = json.loads(generate.protocol_files(spec)[
            "protocol/vault-terminal.json"])

        self.assertIn('constexpr char const* CLAIM_TABLE = "vault_terminal_claims";', source)
        self.assertIn('"INSERT IGNORE INTO `" + std::string(CLAIM_TABLE)', source)
        self.assertIn("PRIMARY KEY (`guid`, `claim_day`)", sql)
        self.assertIn("OnPlayerBeforeSendChatMessage", source)
        self.assertNotIn("TODO", source)
        self.assertIn('"VT1\\t"', source)
        self.assertIn('RegisterAddonMessagePrefix(PREFIX)', lua)
        self.assertIn('SLASH_VAULTTERMINAL1 = "/vt"', lua)
        self.assertIn('SLASH_VAULTTERMINAL2 = "/vaultterminal"', lua)
        self.assertIn("## Interface: 30300", toc.splitlines())
        self.assertIn("arena-counter-terminal/1", lua)
        self.assertEqual(protocol["client_opcodes"], ["HELLO", "STATE", "CLAIM", "PING"])
        self.assertEqual(toc.splitlines()[-1], "VaultTerminal.lua")
        self.assertTrue(toc.endswith("\n"))

    def test_loader_symbol_matches_directory(self):
        files = generate.module_cpp(SPEC)
        loader = files["server/mod-frost-armory/src/mod-frost-armory_loader.cpp"]
        self.assertIn("Addmod_frost_armoryScripts()", loader)
        self.assertIn("AddFrostArmoryScripts();", loader)

    def test_registration_symbol_is_declared_in_shared_header(self):
        files = generate.module_cpp(SPEC)
        header = files["server/mod-frost-armory/src/mod-frost-armory.h"]
        loader = files["server/mod-frost-armory/src/mod-frost-armory_loader.cpp"]
        feature = files["server/mod-frost-armory/src/FrostArmory.cpp"]
        self.assertIn("void AddFrostArmoryScripts();", header)
        self.assertIn('#include "mod-frost-armory.h"', loader)
        self.assertIn('#include "mod-frost-armory.h"', feature)
        self.assertIn("AddFrostArmoryScripts();", loader)

    def test_leading_digit_spec_name_becomes_valid_cpp_identifier(self):
        spec = {"name": "2nd-forge", "module": {"enabled": True}}
        files = generate.module_cpp(spec)
        feature = files["server/mod-2nd-forge/src/_2ndForge.cpp"]
        self.assertIn("class _2ndForgeScript", feature)
        self.assertIn("void Add_2ndForgeScripts()", feature)

    def test_addon_is_30300_and_has_no_retail_api(self):
        files = generate.addon_files(SPEC)
        toc = files["addon/FrostArmory/FrostArmory.toc"]
        lua = files["addon/FrostArmory/FrostArmory.lua"]
        self.assertIn("## Interface: 30300", toc)
        for banned in ("C_Timer", "C_Map", "RegisterAddonMessagePrefix"):
            self.assertNotIn(banned, lua + toc)

    def test_config_keys_are_sorted(self):
        files = generate.config_files(SPEC)
        keys = [ln.split("=")[0].strip() for ln in
                files["conf/frost-armory.conf"].splitlines()
                if "=" in ln and not ln.startswith("#")]
        self.assertEqual(keys, sorted(keys))


if __name__ == "__main__":
    unittest.main()
