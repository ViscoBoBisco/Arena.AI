"""Consistency tests for the VaultTerminal example: addon ↔ protocol ↔ server ↔ SQL.

The example is documentation that happens to be code, so the tests check that the three descriptions
of the wire format agree with each other, that the addon stays inside the 3.3.5a API, and that the
SQL's pinned IDs are fully rolled back. A protocol that drifts from its own spec is worse than none.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

EXAMPLE = helpers.REPO / "examples" / "vault-terminal"
PROTOCOL = EXAMPLE / "PROTOCOL.md"
LUA = EXAMPLE / "addon" / "VaultTerminal" / "VaultTerminal.lua"
TOC = EXAMPLE / "addon" / "VaultTerminal" / "VaultTerminal.toc"
SERVER = EXAMPLE / "server" / "mod-vault-terminal" / "src" / "VaultTerminal.cpp"
SQL = sorted((EXAMPLE / "sql").glob("*.sql"))

# API that does not exist in 3.3.5a; using any of it means the addon cannot load on this client.
NOT_IN_WOTLK = ("C_Timer", "C_Map", "C_QuestLog", "C_AddOns", "RegisterAddonMessagePrefix",
                "GetSpellInfo2", "UnitAuraBySlot", "C_Container")


def protocol_text() -> str:
    return PROTOCOL.read_text(encoding="utf-8")


def client_opcodes() -> set:
    """Opcodes the spec says the client may send, from the client→server table."""
    text = protocol_text()
    block = text.split("Client → server:")[1].split("Server → client:")[0]
    return set(re.findall(r"^\|\s*`([A-Z]{2,8})`", block, re.MULTILINE))


def server_opcodes() -> set:
    text = protocol_text()
    block = text.split("Server → client:")[1].split("Error codes:")[0]
    return set(re.findall(r"^\|\s*`([A-Z]{1,8})`", block, re.MULTILINE))


class AddonStaysIn2008(unittest.TestCase):
    def test_toc(self):
        toc = TOC.read_text(encoding="utf-8")
        self.assertIn("## Interface: 30300", toc, "3.3.5a is interface 30300")
        self.assertIn("VaultTerminal.lua", toc)
        self.assertIn("## X-Protocol-Version: 1", toc)
        self.assertIn("## SavedVariables: VaultTerminalDB", toc)

    def test_uses_wotlk_api_only(self):
        lua = LUA.read_text(encoding="utf-8")
        for token in NOT_IN_WOTLK:
            self.assertNotIn(token, lua, f"{token} does not exist in 3.3.5a")

    def test_uses_the_channel_correctly(self):
        lua = LUA.read_text(encoding="utf-8")
        self.assertIn('SendAddonMessage(PREFIX', lua)
        self.assertIn('"WHISPER"', lua, "client reaches the server by whispering itself")
        self.assertIn("CHAT_MSG_ADDON", lua)
        self.assertIn("local PREFIX", lua)

    def test_enforces_the_255_byte_cap_before_sending(self):
        lua = LUA.read_text(encoding="utf-8")
        self.assertIn("#payload > 255", lua.replace(" ", " "), "client must refuse oversized payloads")

    def test_degrades_when_the_server_is_silent(self):
        lua = LUA.read_text(encoding="utf-8")
        self.assertIn("RESPONSE_TIMEOUT", lua)
        self.assertIn("no server response", lua)

    def test_ignores_unknown_opcodes(self):
        lua = LUA.read_text(encoding="utf-8")
        self.assertIn("unknown opcodes are ignored", lua)


class SpecAndCodeAgree(unittest.TestCase):
    def test_prefix_matches_everywhere(self):
        lua = LUA.read_text(encoding="utf-8")
        cpp = SERVER.read_text(encoding="utf-8")
        self.assertIn('local PREFIX          = "VT1"', lua)
        self.assertIn('VT_PREFIX = "VT1\\t"', cpp)
        self.assertIn('prefix    := "VT1"', protocol_text())

    def test_protocol_version_agrees(self):
        lua = LUA.read_text(encoding="utf-8")
        cpp = SERVER.read_text(encoding="utf-8")
        self.assertIn('local PROTOCOL_VERSION = "1"', lua)
        self.assertIn("VT_PROTOCOL_VERSION = 1", cpp)
        self.assertIn("## X-Protocol-Version: 1", TOC.read_text(encoding="utf-8"))

    def test_addon_sends_only_documented_opcodes(self):
        lua = LUA.read_text(encoding="utf-8")
        sent = set(re.findall(r'send\("([A-Z]{2,8})"', lua))
        self.assertTrue(sent, "no send() calls found")
        self.assertLessEqual(sent, client_opcodes(), f"undocumented client opcodes: {sent - client_opcodes()}")

    def test_addon_handles_only_documented_opcodes(self):
        lua = LUA.read_text(encoding="utf-8")
        handled = set(re.findall(r'opcode == "([A-Z]{1,8})"', lua))
        self.assertTrue(handled)
        self.assertLessEqual(handled, server_opcodes(), f"undocumented server opcodes: {handled - server_opcodes()}")

    def test_server_emits_only_documented_opcodes(self):
        cpp = SERVER.read_text(encoding="utf-8")
        emitted = set(re.findall(r'VT_PREFIX\) \+ "([A-Z]{1,8})\\t"', cpp))
        # "S" is assembled from pieces, so check the ERR/OK/other literals and the S branch separately
        self.assertLessEqual(emitted, server_opcodes() | {"ERR"}, f"undocumented server opcodes: {emitted}")
        self.assertIn('"S\\t"', cpp)

    def test_error_codes_used_by_server_are_documented(self):
        cpp = SERVER.read_text(encoding="utf-8")
        used = set(re.findall(r'SendError\(player, "([A-Z]+)"', cpp))
        self.assertTrue(used)
        documented = set(re.findall(r"`(BUSY|COOLDOWN|VERSION|DENIED|INTERNAL)`", protocol_text()))
        self.assertLessEqual(used, documented, f"undocumented error codes: {used - documented}")


class ConformanceVectorsAreTrue(unittest.TestCase):
    """Every `(NN bytes)` annotation in PROTOCOL.md must match the string it annotates."""

    def test_byte_counts(self):
        text = protocol_text()
        vector_block = text.split("## Conformance vectors")[1].split("## What this costs")[0]
        checked = 0
        for line in vector_block.splitlines():
            match = re.search(r"`?((?:VT1|)\\t.*?)\s+\((\d+) bytes\)", line)
            if not match:
                continue
            payload, claimed = match.group(1), int(match.group(2))
            actual = len(payload.replace("\\t", "\t"))
            self.assertEqual(actual, claimed, f"{payload!r}: claimed {claimed}, actual {actual}")
            checked += 1
        self.assertGreaterEqual(checked, 5, "expected at least five annotated vectors")

    def test_worst_case_fits_the_cap(self):
        """A full S line with maximum-width fields must stay under 255 bytes."""
        worst = "\t".join(["VT1", "S", "9" * 10, "9" * 10, "9" * 10, "K" * 16, "1"])
        self.assertLess(len(worst), 255)
        self.assertIn("255", protocol_text())


class SqlIsSelfConsistent(unittest.TestCase):
    def test_pinned_ids_are_rolled_back(self):
        create = " ".join(p.read_text(encoding="utf-8") for p in SQL if "rollback" not in p.name)
        rollback = next(p for p in SQL if "rollback" in p.name).read_text(encoding="utf-8")
        entries = set()
        for match in re.finditer(r"@ENTRY\s*:?=\s*(\d+)", create):
            entries.add(match.group(1))
        spawns = set(re.findall(r"VALUES\s*\(\s*(\d{7})\s*,\s*@ENTRY", create))
        self.assertEqual(entries, {"900300"}, entries)
        for entry in entries:
            self.assertIn(entry, rollback, f"entry {entry} is not rolled back")
        for guid in spawns or {"9003001"}:
            self.assertIn(guid, rollback, f"spawn guid {guid} is not rolled back")

    def test_every_insert_targets_a_deletable_table(self):
        create = " ".join(p.read_text(encoding="utf-8") for p in SQL if "rollback" not in p.name)
        inserted = set(re.findall(r"INSERT INTO (\w+)", create))
        self.assertTrue(inserted)
        for table in inserted:
            self.assertIn(table, (EXAMPLE / "sql" / "99_rollback.sql").read_text(encoding="utf-8"),
                          f"{table} rows are inserted but never removed")

    def test_no_unverified_quest_sql(self):
        """quest_template changed shape in recent cores; the example deliberately ships none."""
        for path in SQL:
            self.assertNotIn("quest_template", path.read_text(encoding="utf-8"))


class DocsPointHere(unittest.TestCase):
    def test_readme_and_novel_chapter_link_the_example(self):
        readme = (helpers.REPO / "README.md").read_text(encoding="utf-8")
        novel = (helpers.REPO / "docs" / "10-novel-alterations.md").read_text(encoding="utf-8")
        self.assertIn("examples/vault-terminal", readme)
        self.assertIn("vault-terminal", novel)


if __name__ == "__main__":
    unittest.main()
