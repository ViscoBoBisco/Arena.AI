"""Tests for the pinned-core compile gate's demangled nm symbol matching."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "compile_generated_module", REPO / "tools" / "compile_generated_module.py")
compile_gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compile_gate)


class DemangledSymbols(unittest.TestCase):
    def test_complete_build_keeps_upstream_core_and_script_pchs_enabled(self):
        workflow = (REPO / ".github" / "workflows" / "azerothcore-compile.yml").read_text(
            encoding="utf-8")
        configure = workflow[workflow.index("cmake -S /tmp/azerothcore"):
                             workflow.index("cmake -S /tmp/azerothcore") + 500]
        self.assertIn("-DUSE_COREPCH=1", configure)
        self.assertIn("-DUSE_SCRIPTPCH=1", configure)
        self.assertNotIn("-DUSE_COREPCH=0", configure)
        self.assertNotIn("-DUSE_SCRIPTPCH=0", configure)
        workflow = (REPO / ".github" / "workflows" / "azerothcore-compile.yml").read_text(
            encoding="utf-8")
        self.assertIn("2>&1 | tee /tmp/worldserver-build.log", workflow)
        self.assertIn("- name: Verify linked worldserver executable", workflow)
        self.assertIn("/tmp/azerothcore-build/src/server/apps/worldserver", workflow)
        self.assertNotIn("/tmp/azerothcore-build/bin/worldserver", workflow)

    def test_accepts_source_spelling_against_nm_function_spelling(self):
        output = "0000000000000000 T AddFrostArmoryScripts()\n"
        self.assertTrue(compile_gate._symbol(output, "T", "AddFrostArmoryScripts"))

    def test_accepts_nm_spelling_too(self):
        output = "                 U Addmod_frost_armoryScripts()\n"
        self.assertTrue(compile_gate._symbol(
            output, "U", "Addmod_frost_armoryScripts()"))

    def test_does_not_confuse_function_prefixes(self):
        output = "0000000000000000 T AddFrostArmoryScriptsExtra()\n"
        self.assertFalse(compile_gate._symbol(
            output, "T", "AddFrostArmoryScripts"))


if __name__ == "__main__":
    unittest.main()
