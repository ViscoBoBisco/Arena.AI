"""Executable WotLK 3.3.5a addon API smoke tests for the shipped Lua surfaces."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADDONS = {
    "PortableHole": ROOT / "server/mod-portable-hole/addon/PortableHole/PortableHole.lua",
    "RunicCodex": ROOT / "server/mod-runic-codex/addon/RunicCodex/RunicCodex.lua",
    "ArgentCommission": ROOT / "server/mod-argent-commission/addon/ArgentCommission/ArgentCommission.lua",
}

_WOTLK_MOCKS = r"""
UIParent = {}
SlashCmdList = {}
TEST_FRAMES = {}
TEST_SENT = {}
TEST_NOW = 10
function time() return 1700000000 end
function GetTime() TEST_NOW = TEST_NOW + 0.25; return TEST_NOW end
function UnitName(unit) assert(unit == 'player'); return 'Tester' end
function UnitGUID(unit) assert(unit == 'player'); return 'Player-0-00000001' end
function SendAddonMessage(prefix, message, distribution, target)
    TEST_SENT[#TEST_SENT + 1] = { prefix, message, distribution, target }
end

local function noop() end
local objectMethods = {
    SetSize = noop, SetPoint = noop, SetAutoFocus = noop, SetNumeric = noop,
    SetWidth = noop, SetJustifyH = noop, SetBackdrop = noop,
    SetBackdropColor = noop, SetBackdropBorderColor = noop, EnableMouse = noop,
    SetMovable = noop, RegisterForDrag = noop, StartMoving = noop,
    StopMovingOrSizing = noop, SetFrameStrata = noop,
    Enable = noop, Disable = noop,
}
function objectMethods:SetScript(name, fn) self.scripts[name] = fn end
function objectMethods:RegisterEvent(name) self.registeredEvents[name] = true end
function objectMethods:Show() self.visible = true end
function objectMethods:Hide() self.visible = false end
function objectMethods:IsShown() return self.visible end
function objectMethods:SetText(value) self.text = value end
function objectMethods:GetText() return self.text or '' end
function objectMethods:CreateFontString()
    return setmetatable({ scripts = {}, registeredEvents = {} }, { __index = objectMethods })
end
function CreateFrame(kind, name, parent, template)
    assert(template == nil or template == 'InputBoxTemplate' or template == 'UIPanelButtonTemplate',
        'unexpected or non-WotLK frame template: ' .. tostring(template))
    local obj = setmetatable({ kind = kind, name = name, parent = parent, template = template,
        scripts = {}, registeredEvents = {}, visible = false }, { __index = objectMethods })
    TEST_FRAMES[#TEST_FRAMES + 1] = obj
    return obj
end
function TEST_FIRE(event, ...)
    for _, obj in ipairs(TEST_FRAMES) do
        if obj.registeredEvents[event] and obj.scripts.OnEvent then
            obj.scripts.OnEvent(obj, event, ...)
        end
    end
end
"""


class WotlkAddonCompatibility(unittest.TestCase):
    def test_tocs_target_wotlk_and_sources_avoid_known_post_335_apis(self):
        forbidden = (
            "RegisterAddonMessagePrefix", "BasicFrameTemplateWithInset",
            "BackdropTemplate", "CreateFromMixins", "strsplit(",
        )
        for addon, source_path in ADDONS.items():
            toc = source_path.with_suffix(".toc").read_text(encoding="utf-8")
            source = source_path.read_text(encoding="utf-8")
            self.assertIn("## Interface: 30300", toc, addon)
            for symbol in forbidden:
                self.assertNotIn(symbol, source, (addon, symbol))

    def test_login_slash_commands_and_addon_messages_run_without_modern_api_stubs(self):
        try:
            from lupa.lua51 import LuaRuntime
        except ImportError:
            self.skipTest("install lupa==2.6 for WotLK Lua runtime smoke tests")

        for addon, source_path in ADDONS.items():
            with self.subTest(addon=addon):
                lua = LuaRuntime(unpack_returned_tuples=True)
                lua.execute(_WOTLK_MOCKS)
                # Deliberately do not define RegisterAddonMessagePrefix or newer C_ APIs.
                lua.execute(source_path.read_text(encoding="utf-8"))
                lua.globals().TEST_FIRE("PLAYER_LOGIN")
                slash_name = {
                    "PortableHole": "PORTABLEHOLE",
                    "RunicCodex": "RUNICCODEX",
                    "ArgentCommission": "ARGENTCOMMISSION",
                }[addon]
                slash = lua.globals().SlashCmdList[slash_name]
                self.assertIsNotNone(slash, f"{addon} must install a WotLK slash command")
                slash()
                messages = lua.globals().TEST_SENT
                self.assertGreaterEqual(len(messages), 1, f"{addon} should issue its initial state request")
                first = messages[1]
                self.assertEqual(first[1], {"PortableHole": "PH1", "RunicCodex": "RC1",
                                            "ArgentCommission": "AC1"}[addon])
                self.assertEqual(first[3], "WHISPER")
                self.assertEqual(first[4], "Tester")
                if addon == "PortableHole":
                    self.assertIn("\tLIST\t0\tENTRY", first[2])
                elif addon == "RunicCodex":
                    self.assertIn("\tSTATE", first[2])
                else:
                    self.assertIn("\tSTATUS", first[2])


if __name__ == "__main__":
    unittest.main()
