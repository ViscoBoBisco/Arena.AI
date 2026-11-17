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

EXAMPLE = helpers.VAULT_TERMINAL
PROTOCOL = EXAMPLE / "PROTOCOL.md"
LUA = EXAMPLE / "addon" / "VaultTerminal" / "VaultTerminal.lua"
TOC = EXAMPLE / "addon" / "VaultTerminal" / "VaultTerminal.toc"
SERVER = EXAMPLE / "server" / "mod-vault-terminal" / "src" / "VaultTerminal.cpp"
SQL = sorted((EXAMPLE / "sql").glob("*.sql"))
MODEL_PATH = EXAMPLE / "harness" / "vt_model.py"

# API that does not exist in 3.3.5a; using any of it means the addon cannot load on this client.
NOT_IN_WOTLK = ("C_Timer", "C_Map", "C_QuestLog", "C_AddOns", "RegisterAddonMessagePrefix",
                "GetSpellInfo2", "UnitAuraBySlot", "C_Container")


def model():
    return helpers.vt_model()


def spec_rank_ladder() -> dict:
    """`| 10–24 | `Adept` |` rows -> {10: 'Adept', 25: 'Keeper', ...} using the lower bound."""
    rows = re.findall(r"^\|\s*(\d+)(?:[–-]\d+)?\+?\s*\|\s*`([A-Za-z]+)`\s*\|", protocol_text(), re.MULTILINE)
    return {int(low): name for low, name in rows}


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




class TheModelAndTheSpecAgree(unittest.TestCase):
    """The four descriptions of the protocol: PROTOCOL.md, the Lua, the C++, the Python model."""

    def test_constants_match_the_spec(self):
        text = protocol_text()
        m = model()
        self.assertIn(f'prefix    := "{m.PREFIX}"', text)
        self.assertIn("**255 bytes**", text)
        self.assertEqual(set(m.CLIENT_OPCODES), client_opcodes())
        self.assertEqual(set(m.SERVER_OPCODES), server_opcodes())
        self.assertLessEqual(set(m.ERRORS), set(re.findall(r"`(BUSY|COOLDOWN|VERSION|DENIED|INTERNAL)`", text)))
        self.assertEqual(m.rank_for(0), "Initiate")
        self.assertEqual(m.rank_for(10), "Adept")
        self.assertEqual(m.rank_for(25), "Keeper")
        self.assertEqual(m.rank_for(50), "Vaultmaster")

    def test_rank_ladder_is_the_documented_one(self):
        self.assertEqual(spec_rank_ladder(), {0: "Initiate", 10: "Adept", 25: "Keeper", 50: "Vaultmaster"})

    def test_cpp_constants_match_the_model(self):
        cpp = SERVER.read_text(encoding="utf-8")
        m = model()
        ranks = {int(seals): name for seals, name in re.findall(r'\{\s*(\d+),\s*"([A-Za-z]+)"\s*\}', cpp)}
        peer = {
            "prefix": re.search(r'VT_PREFIX = "([^"\\]+)', cpp).group(1),
            "version": re.search(r"VT_PROTOCOL_VERSION = (\d+)", cpp).group(1),
            "max_bytes": 255 if "payload.size() > 255" in cpp else None,
            "target_seals": int(re.search(r"VT_TARGET_SEALS = (\d+)", cpp).group(1)),
            "claims_per_day": int(re.search(r"VT_CLAIMS_PER_DAY = (\d+)", cpp).group(1)),
            "ranks": ranks,
        }
        self.assertEqual(helpers.vt_model().constant_mismatches(peer), [])
        self.assertEqual(peer["target_seals"], m.TARGET_SEALS)

    def test_lua_constants_match_the_model(self):
        lua = LUA.read_text(encoding="utf-8")
        m = model()
        self.assertIn(f'local PREFIX          = "{m.PREFIX}"', lua)
        self.assertIn(f'local PROTOCOL_VERSION = "{m.VERSION}"', lua)
        self.assertIn("> 255", lua)
        self.assertIn(f"local POLL_INTERVAL   = {m.POLL_SECONDS}.0", lua)
        self.assertIn(f"local RESPONSE_TIMEOUT = {m.TIMEOUT_SECONDS}.0", lua)

    def test_documented_vectors_replay_against_the_model(self):
        import datetime
        m = model()
        text = protocol_text()
        sample_s = re.search(r"(VT1\\tS\\t12.*?)\s+\(\d+ bytes\)", text).group(1).replace("\\t", "\t")
        session = m.Session(today=datetime.date(2026, 11, 17), seals=12)
        self.assertEqual(session.handle("VT1\tHELLO\t1\tdemo-1.0.0"), [sample_s])
        self.assertEqual(session.handle("VT1\tSTATE"), [sample_s])
        self.assertEqual(session.handle("VT1\tPING"), ["VT1\tOK"])
        replies = session.handle("VT1\tCLAIM")
        self.assertEqual(replies[0], "VT1\tOK\tseal claimed")
        self.assertEqual(replies[1], "VT1\tS\t13\t50\t0\tAdept\t0")
        self.assertEqual(session.handle("VT1\tCLAIM"), ["VT1\tERR\tCOOLDOWN\talready claimed today"])

    def test_the_model_refuses_what_the_grammar_forbids(self):
        m = model()
        with self.assertRaises(m.FrameError):
            m.build("S", "x" * 200, "y" * 60)          # over the cap
        with self.assertRaises(m.FrameError):
            m.build("ok")                              # lower-case opcode
        with self.assertRaises(m.FrameError):
            m.build("OK", "a\tb")                      # separator inside a field
        with self.assertRaises(m.FrameError):
            m.build("OK", "x" * 65)                    # field wider than the grammar allows

    def test_unknown_opcode_is_silent_in_both_implementations(self):
        cpp = SERVER.read_text(encoding="utf-8")
        self.assertNotIn('"INTERNAL", "unknown opcode"', cpp,
                         "rule 2 says unknown opcodes are ignored; refusing them breaks forward compatibility")
        self.assertIn("rule 2", cpp)
        self.assertEqual(model().Session(today=__import__("datetime").date(2026, 1, 1)).handle("VT1\tFUTURE\tx"), [])

    def test_disabled_server_says_bye_in_both_implementations(self):
        import datetime
        cpp = SERVER.read_text(encoding="utf-8")
        self.assertIn('"BYE\\tserver is under maintenance"', cpp)
        reply = model().Session(today=datetime.date(2026, 1, 1), disabled=True).handle("VT1\tSTATE")
        self.assertEqual(reply, ["VT1\tBYE\tserver is under maintenance"])


class TheModuleWouldActuallyLoad(unittest.TestCase):
    """A module that never loads is the quietest failure in this whole example."""

    def test_loader_symbol_matches_the_directory_name(self):
        module = EXAMPLE / "server" / "mod-vault-terminal"
        expected = "Add" + module.name.replace("-", "_") + "Scripts()"
        loader = (module / "src" / f"{module.name}_loader.cpp").read_text(encoding="utf-8")
        self.assertIn(expected, loader)
        defined = re.findall(r"^void (Add\w+Scripts)\((?:void)?\)\s*\{", loader, re.MULTILINE)
        self.assertEqual(defined, [expected[:-2]], "the loader must define exactly one entry point")

    def test_the_entry_point_reaches_the_scripts(self):
        module = EXAMPLE / "server" / "mod-vault-terminal" / "src"
        loader = (module / "mod-vault-terminal_loader.cpp").read_text(encoding="utf-8")
        body = (module / "VaultTerminal.cpp").read_text(encoding="utf-8")
        inner = re.search(r"void (AddVaultTerminalScripts)\(\)", body).group(1)
        self.assertIn(inner + "();", loader, "the loader must call the function the module defines")

    def test_config_file_is_named_for_the_module(self):
        module = EXAMPLE / "server" / "mod-vault-terminal"
        conf = list((module / "conf").glob("*.conf.dist"))
        self.assertEqual([c.name for c in conf], [f"{module.name}.conf.dist"])

    def test_the_conf_key_is_the_one_the_code_reads(self):
        module = EXAMPLE / "server" / "mod-vault-terminal"
        conf = (module / "conf" / "mod-vault-terminal.conf.dist").read_text(encoding="utf-8")
        cpp = (module / "src" / "VaultTerminal.cpp").read_text(encoding="utf-8")
        keys = set(re.findall(r"^(VaultTerminal\.\w+) = ", conf, re.MULTILINE))
        self.assertTrue(keys)
        for key in keys:
            self.assertIn(f'"{key}"', cpp, f"{key} is configured but never read")


class TheModelRunsStandalone(unittest.TestCase):
    def test_transcript_demo_exits_clean_and_stays_in_the_vocabulary(self):
        import subprocess
        out = subprocess.run([sys.executable, str(MODEL_PATH)], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("seal claimed", out.stdout)
        self.assertIn("COOLDOWN", out.stdout)
        self.assertIn("midnight", out.stdout)
        # the invisible sixth message proves rule 2 on the page, not just in a unit test
        self.assertNotIn("FUTURE", out.stdout.split("server")[-1])
        for line in out.stdout.splitlines():
            if "'VT1" in line:
                frame = line.split("'")[1]
                self.assertLessEqual(len(frame), 255)


class RepoHasNoRetailOnlyApi(unittest.TestCase):
    def test_register_addon_message_prefix_is_not_recommended(self):
        """That function is 5.x+. Naming it as a 3.3.5a step is the kind of error this suite exists for."""
        docs = [helpers.REPO / "README.md", *sorted((helpers.REPO / "docs").glob("*.md")),
                *sorted(EXAMPLE.rglob("*.md"))]
        offenders = []
        for path in docs:
            for line in path.read_text(encoding="utf-8").splitlines():
                if "RegisterAddonMessagePrefix" in line and not re.search(r"not|no |never|5\.x|later", line, re.I):
                    offenders.append(f"{path.relative_to(helpers.REPO)}: {line.strip()[:90]}")
        self.assertEqual(offenders, [], "recommend a 3.3.5a-safe transport instead")

    def test_no_catalog_surface_names_a_missing_function(self):
        catalog = helpers.CATALOG.read_text(encoding="utf-8")
        self.assertNotIn("RegisterAddonMessagePrefix", catalog,
                         "catalog surfaces must name 3.3.5a API only")


class DocsPointHere(unittest.TestCase):
    def test_readme_and_novel_chapter_link_the_example(self):
        readme = (helpers.REPO / "README.md").read_text(encoding="utf-8")
        novel = (helpers.REPO / "docs" / "10-novel-alterations.md").read_text(encoding="utf-8")
        self.assertIn("examples/vault-terminal", readme)
        self.assertIn("vault-terminal", novel)


if __name__ == "__main__":
    unittest.main()
