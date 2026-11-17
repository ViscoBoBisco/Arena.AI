"""TIER 1 — intern tests: "does it run, does it parse, is anything obviously missing".

The point of this tier is speed and breadth, not depth. If these fail, nothing else matters.
What an intern finds: typos, crashed CLIs, files that do not decode, chapters nobody links to.

Run: python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402


class ToolsStart(unittest.TestCase):
    def test_every_tool_prints_help(self):
        for tool in (helpers.DBC_TOOL, helpers.CATALOG_TOOL, helpers.MANIFEST):
            result = helpers.run_tool(tool, "--help")
            self.assertEqual(result.returncode, 0, f"{tool.name}: {result.stderr}")
            self.assertIn("usage", result.stdout.lower(), tool.name)

    def test_preflight_usage_without_arguments(self):
        result = helpers.run_bash(helpers.PREFLIGHT)
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage", result.stderr.lower())

    def test_selftest_passes(self):
        result = helpers.run_tool(helpers.TOOLS / "selftest.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)


class FilesAreSane(unittest.TestCase):
    def test_docs_decode_and_have_titles(self):
        for path in sorted((helpers.REPO / "docs").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# "), f"{path.name}: no H1 on line 1")
            self.assertTrue(text.endswith("\n"), f"{path.name}: no trailing newline")

    def test_every_json_parses(self):
        for path in helpers.REPO.rglob("*.json"):
            if ".git" in path.parts:
                continue
            with self.subTest(path=path.relative_to(helpers.REPO)):
                json.loads(path.read_text(encoding="utf-8-sig"))

    def test_readme_mentions_top_level_directories(self):
        readme = (helpers.REPO / "README.md").read_text(encoding="utf-8")
        for name in ("docs/", "tools/", "catalog/", "examples/", "tests/"):
            self.assertIn(name, readme, f"README does not mention {name}")

    def test_makefile_exposes_the_documented_targets(self):
        makefile = (helpers.REPO / "Makefile").read_text(encoding="utf-8")
        for target in ("check:", "test:", "selftest:", "preflight:", "manifest:", "catalog:"):
            self.assertIn(target, makefile, f"Makefile has no {target}")


class CatalogBasics(unittest.TestCase):
    def test_catalog_loads_and_is_populated(self):
        data = helpers.load_catalog()
        self.assertGreater(len(data["entries"]), 50)
        for entry in data["entries"]:
            self.assertTrue(entry["title"].strip(), entry["id"])
            self.assertTrue(entry["effect"].strip(), entry["id"])

    def test_cli_commands_run(self):
        for command in ("validate", "list", "stats", "matrix", "surfaces"):
            result = helpers.run_tool(helpers.CATALOG_TOOL, command)
            self.assertEqual(result.returncode, 0, f"{command}: {result.stderr}")
            self.assertTrue(result.stdout.strip(), f"{command} printed nothing")


class FixturesWork(unittest.TestCase):
    def test_a_minimal_valid_dbc_is_accepted(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = helpers.write_dbc(Path(tmp) / "Item.dbc", 8, [(1, 2, 3, 4, 5, 6, 7, 8)])
            result = helpers.run_tool(helpers.DBC_TOOL, "info", path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("records        1", result.stdout)


if __name__ == "__main__":
    unittest.main()
