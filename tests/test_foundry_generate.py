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
NOT_IN_WOTLK = (
    "C_Timer", "C_Map", "C_QuestLog", "C_AddOns", "RegisterAddonMessagePrefix",
    "C_ChatInfo", "UnitAuraBySlot", "C_Container",
)


def assert_legacy_wotlk_addon(files: dict) -> None:
    """Fail on known post-3.3.5 API symbols in generated Lua or TOC output."""
    lua_toc = "\n".join(text for path, text in files.items()
                        if path.endswith((".lua", ".toc")))
    for token in NOT_IN_WOTLK:
        if token in lua_toc:
            raise AssertionError(f"{token} is not available in WoW 3.3.5a")


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
        self.assertIn("DirectCommitTransaction(transaction)", source)
        self.assertIn("SELECT request_id, status, total_claims", source)
        self.assertNotIn("ROW_COUNT()", source)
        self.assertIn("PRIMARY KEY (`guid`)", sql)
        self.assertIn("PRIMARY KEY (`guid`, `request_id`)", sql)
        self.assertIn("last_request_id", sql)
        self.assertIn("MAX(claim_day)", sql)
        self.assertIn("vault_terminal_claims_legacy_v1", sql)
        self.assertIn("RENAME TABLE", sql)
        self.assertNotIn("DROP TABLE", sql)
        templates = generate.characters_claim_transaction_sql(spec)
        self.assertEqual(len(templates), 4)
        self.assertIn("total_claims < 50", templates[2][0])
        self.assertIn("status = 'PENDING'", templates[3][0])
        self.assertIn("OnPlayerBeforeSendChatMessage", source)
        self.assertNotIn("TODO", source)
        self.assertIn('"VT1\\t"', source)
        self.assertNotIn('RegisterAddonMessagePrefix', lua)
        self.assertIn('SLASH_VAULTTERMINAL1 = "/vt"', lua)
        self.assertIn('SLASH_VAULTTERMINAL2 = "/vaultterminal"', lua)
        self.assertIn("## Interface: 30300", toc.splitlines())
        self.assertIn("arena-counter-terminal/2", lua)
        self.assertIn("RCPT", lua)
        self.assertIn("## SavedVariables: VaultTerminalDB", toc)
        self.assertIn('distribution ~= "WHISPER"', lua)
        self.assertIn('sender ~= UnitName("player")', lua)
        self.assertIn("MAX_BYTES = 255", lua)
        self.assertIn("MIN_REQUEST_INTERVAL", lua)
        self.assertIn("RESPONSE_TIMEOUT", lua)
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

    def test_every_supported_generated_addon_stays_within_wotlk_api(self):
        specs = sorted((helpers.REPO / "specs").glob("*.json"))
        found = 0
        for spec_path in specs:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            files = generate.addon_files(spec)
            if files is None:
                continue
            found += 1
            assert_legacy_wotlk_addon(files)
            toc_files = [text for path, text in files.items() if path.endswith(".toc")]
            self.assertEqual(len(toc_files), 1, spec_path.name)
            self.assertIn("## Interface: 30300", toc_files[0], spec_path.name)
        self.assertGreater(found, 0)

    def test_generated_api_guard_catches_mutated_addon(self):
        spec = json.loads((helpers.REPO / "specs" / "vault-terminal.json").read_text())
        files = generate.addon_files(spec)
        assert_legacy_wotlk_addon(files)
        mutated = dict(files)
        lua_path = next(path for path in mutated if path.endswith(".lua"))
        mutated[lua_path] += "\nRegisterAddonMessagePrefix(PREFIX)\n"
        with self.assertRaisesRegex(AssertionError, "RegisterAddonMessagePrefix"):
            assert_legacy_wotlk_addon(mutated)

    def test_vault_terminal_addon_runs_through_wotlk_api_shim(self):
        try:
            from lupa.lua51 import LuaRuntime
        except ImportError:
            self.skipTest("install lupa==2.6 to run the Lua 5.1 compatibility harness")

        spec = json.loads((helpers.REPO / "specs" / "vault-terminal.json").read_text())
        files = generate.addon_files(spec)
        lua_source = files["addon/VaultTerminal/VaultTerminal.lua"]
        # Executing this chunk under the Lua 5.1 runtime proves it parses as Lua 5.1.
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute(r'''
            now = 100
            sent = {}
            buttons = {}
            local function widget()
                local w = {shown = false, enabled = true, scripts = {}, children = {}}
                function w:SetWidth(v) self.width = v end
                function w:SetHeight(v) self.height = v end
                function w:SetPoint(...) end
                function w:SetBackdrop(v) self.backdrop = v end
                function w:SetMovable(v) self.movable = v end
                function w:EnableMouse(v) self.mouse = v end
                function w:RegisterForDrag(v) self.drag = v end
                function w:RegisterEvent(v) self.event = v end
                function w:SetScript(k, v) self.scripts[k] = v end
                function w:Hide() self.shown = false end
                function w:Show() self.shown = true end
                function w:IsShown() return self.shown end
                function w:CreateFontString()
                    local child = widget(); table.insert(self.children, child); return child
                end
                function w:SetText(v) self.text = v end
                function w:SetJustifyH(v) self.justify = v end
                function w:StartMoving() end
                function w:StopMovingOrSizing() end
                function w:Disable() self.enabled = false end
                function w:Enable() self.enabled = true end
                return w
            end
            function CreateFrame(kind, name, parent, template)
                local w = widget(); w.name = name; w.template = template
                if parent then table.insert(parent.children, w) end
                if name == "VaultTerminalFrame" then panel = w end
                if template == "UIPanelButtonTemplate" then table.insert(buttons, w) end
                return w
            end
            UIParent = widget()
            SlashCmdList = {}
            function UnitName(unit) return "TestCharacter" end
            function UnitGUID(unit) return "Player-0-TEST" end
            function time() return 1700000000 end
            VaultTerminalDB = {}
            function GetTime() return now end
            function SendAddonMessage(prefix, message, distribution, target)
                table.insert(sent, {prefix, message, distribution, target})
            end
        ''')
        lua.execute(lua_source)
        lua.execute('SlashCmdList["VAULTTERMINAL"]()')
        self.assertEqual(lua.eval("#sent"), 1)
        self.assertEqual(lua.eval("sent[1][1]"), "VT1")
        self.assertEqual(lua.eval("sent[1][2]"), "VT1\tHELLO\t2\t1.0.0")
        self.assertEqual(lua.eval("sent[1][3]"), "WHISPER")
        self.assertEqual(lua.eval("sent[1][4]"), "TestCharacter")

        lua.execute(r'''
            local event = panel.scripts.OnEvent
            event(panel, "CHAT_MSG_ADDON", "OTHER", "VT1\tS\t0\t50\t1\tInitiate\t1", "WHISPER", "TestCharacter")
            event(panel, "CHAT_MSG_ADDON", "VT1", "VT1\tERR\tVERSION\tprotocol mismatch", "WHISPER", "TestCharacter")
            assert(panel.children[8].text == "VERSION: protocol mismatch")
            event(panel, "CHAT_MSG_ADDON", "VT1", "VT1\tS\t0\t50\t1\tInitiate\t1", "WHISPER", "Other")
            assert(panel.children[3].text == "—", "wrong sender changed the rendered state")
            event(panel, "CHAT_MSG_ADDON", "VT1", "VT1\tS\t0\t50\t1\tInitiate\t1", "PARTY", "TestCharacter")
            assert(panel.children[3].text == "—", "wrong distribution changed the rendered state")
            event(panel, "CHAT_MSG_ADDON", "VT1", "VT1\tS\t0\t50\t1\tInitiate\t1", "WHISPER", "TestCharacter")
            assert(panel.children[3].text == "0 / 50")
            assert(panel.children[5].text == "Initiate")
            assert(buttons[1].enabled == true)
            buttons[1].scripts.OnClick(buttons[1]) -- rate limited: no second send
            assert(#sent == 1)
            now = now + 0.3
            buttons[1].scripts.OnClick(buttons[1])
            local firstRequest = sent[#sent][2]:match("^VT1\tCLAIM\t([A-Za-z0-9_-]+)$")
            assert(firstRequest and #firstRequest > 0)
            assert(#sent[#sent][2] <= 255)
            -- A receipt for another request must not clear this request's pending state.
            event(panel, "CHAT_MSG_ADDON", "VT1", "VT1\tRCPT\tother-id\tGRANTED\t1", "WHISPER", "TestCharacter")
            assert(panel.children[3].text == "0 / 50")
            assert(VaultTerminalDB.characters["Player-0-TEST"].pendingRequestId == firstRequest)
            event(panel, "CHAT_MSG_ADDON", "VT1", "VT1\tRCPT\t" .. firstRequest .. "\tGRANTED\t1", "WHISPER", "TestCharacter")
            assert(panel.children[3].text == "0 / 50")
            assert(VaultTerminalDB.characters["Player-0-TEST"].pendingRequestId == nil)
            event(panel, "CHAT_MSG_ADDON", "VT1", "VT1\tS\t1\t50\t0\tInitiate\t0", "WHISPER", "TestCharacter")
            assert(panel.children[3].text == "1 / 50")
            assert(buttons[1].enabled == false)

            -- Timeout and addon reopen retain the request ID until its matching receipt.
            event(panel, "CHAT_MSG_ADDON", "VT1", "VT1\tS\t1\t50\t1\tInitiate\t1", "WHISPER", "TestCharacter")
            now = now + 0.3
            buttons[1].scripts.OnClick(buttons[1])
            local secondRequest = sent[#sent][2]:match("^VT1\tCLAIM\t([A-Za-z0-9_-]+)$")
            assert(secondRequest and secondRequest ~= firstRequest)
            now = now + 10.1
            panel.scripts.OnUpdate(panel)
            assert(panel.children[8].text == "No server response. Retry uses the same request ID.")
            assert(VaultTerminalDB.characters["Player-0-TEST"].pendingRequestId == secondRequest)
            SlashCmdList["VAULTTERMINAL"]() -- close without discarding the intent
            now = now + 0.3
            SlashCmdList["VAULTTERMINAL"]() -- reopen and handshake
            event(panel, "CHAT_MSG_ADDON", "VT1", "VT1\tS\t1\t50\t1\tInitiate\t1", "WHISPER", "TestCharacter")
            now = now + 0.3
            panel.scripts.OnUpdate(panel)
            assert(sent[#sent][2] == "VT1\tCLAIM\t" .. secondRequest)
            event(panel, "CHAT_MSG_ADDON", "VT1", "VT1\tRCPT\t" .. secondRequest .. "\tGRANTED\t2", "WHISPER", "TestCharacter")
            event(panel, "CHAT_MSG_ADDON", "VT1", "VT1\tS\t2\t50\t0\tInitiate\t0", "WHISPER", "TestCharacter")
            assert(panel.children[3].text == "2 / 50")

            -- The 255-byte boundary is accepted; unknown/malformed input is inert.
            local maximum = "VT1\tNOPE\t" .. string.rep("x", 246)
            assert(#maximum == 255)
            event(panel, "CHAT_MSG_ADDON", "VT1", maximum, "WHISPER", "TestCharacter")
            event(panel, "CHAT_MSG_ADDON", "VT1", string.rep("x", 256), "WHISPER", "TestCharacter")
            event(panel, "CHAT_MSG_ADDON", "VT1", "VT1\tS\t1\t50\t0\tRänk\t0", "WHISPER", "TestCharacter")
            event(panel, "CHAT_MSG_ADDON", "VT1", "VT1\tS\t999999999999999999999999\t50\t0\tInitiate\t0", "WHISPER", "TestCharacter")
            assert(panel.children[3].text == "—", "invalid numeric snapshot must clear stale state")
            assert(buttons[1].enabled == false)
            now = now + 0.3
            buttons[2].scripts.OnClick(buttons[2])
            assert(sent[#sent][2]:find("HELLO", 1, true))
            now = now + 10.1
            panel.scripts.OnUpdate(panel)
            assert(panel.children[8].text == "No server response. Retry uses the same request ID.")
            event(panel, "CHAT_MSG_ADDON", "VT1", "VT1\tBYE\tmaintenance", "WHISPER", "TestCharacter")
            assert(panel.children[3].text == "—")
            now = now + 0.3
            buttons[2].scripts.OnClick(buttons[2]) -- refresh recovers after BYE
            assert(sent[#sent][2]:find("HELLO", 1, true))
            SlashCmdList["VAULTTERMINAL"]() -- close
            assert(panel:IsShown() == false)
            now = now + 0.3
            SlashCmdList["VAULTTERMINAL"]() -- reopen: same frame and handler set
            assert(panel:IsShown() == true)
            assert(sent[#sent][2]:find("HELLO", 1, true))
            assert(panel.event == "CHAT_MSG_ADDON")
        ''')

    def test_config_keys_are_sorted(self):
        files = generate.config_files(SPEC)
        keys = [ln.split("=")[0].strip() for ln in
                files["conf/frost-armory.conf"].splitlines()
                if "=" in ln and not ln.startswith("#")]
        self.assertEqual(keys, sorted(keys))


if __name__ == "__main__":
    unittest.main()
