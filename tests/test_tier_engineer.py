"""TIER 2 — middling-engineer tests: boundaries, exit codes, and cross-tool consistency.

The intern asked "does it run". This tier asks "does it behave at the edges, and do the pieces agree
with each other". What it finds: inconsistent exit codes, off-by-one ranges, silent skips, tools that
disagree about the same fact.

Run: python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402


class DbcBoundaries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def info(self, path):
        return helpers.run_tool(helpers.DBC_TOOL, "info", path)

    def test_zero_records_is_valid(self):
        path = helpers.write_dbc(self.root / "Empty.dbc", 8, [])
        result = self.info(path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("records        0", result.stdout)

    def test_truncated_string_block_is_rejected(self):
        # header promises 64 bytes of strings, file carries 4
        path = self.root / "Short.dbc"
        path.write_bytes(helpers.dbc_bytes(8, [(1, *([0] * 7))], b"\0\0\0\0", string_size=64))
        result = self.info(path)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("size mismatch", result.stderr)

    def test_record_size_inconsistent_with_field_count_is_rejected(self):
        path = self.root / "BadSize.dbc"
        path.write_bytes(helpers.dbc_bytes(8, [(1, *([0] * 7))], record_size=30))
        self.assertEqual(self.info(path).returncode, 1)

    def test_wrong_magic_is_rejected(self):
        path = self.root / "Magic.dbc"
        path.write_bytes(helpers.dbc_bytes(8, [(1, *([0] * 7))], magic=b"WDBC"[:3] + b"X"))
        self.assertEqual(self.info(path).returncode, 1)

    def test_zero_field_count_is_rejected(self):
        path = self.root / "NoFields.dbc"
        path.write_bytes(helpers.dbc_bytes(0, [], record_size=0))
        self.assertEqual(self.info(path).returncode, 1)

    def test_negative_ids_are_legal(self):
        path = helpers.write_dbc(self.root / "Neg.dbc", 8, [(-1, *([0] * 7)), (5, *([0] * 7))])
        self.assertEqual(self.info(path).returncode, 0)

    def test_duplicate_ids_are_reported_but_not_fatal(self):
        path = helpers.write_dbc(self.root / "Dup.dbc", 8, [(7, *([0] * 7)), (7, *([0] * 7))])
        result = self.info(path)
        # duplicate IDs are a check failure, reported on stderr (preflight treats that as a FAIL)
        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate", result.stderr.lower())


class ExitCodes(unittest.TestCase):
    """One taxonomy, applied everywhere: 0 ok, 1 check failed, 2 usage or IO."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_is_an_io_error(self):
        self.assertEqual(helpers.run_tool(helpers.DBC_TOOL, "info", self.root / "nope.dbc").returncode, 2)

    def test_directory_is_an_io_error(self):
        self.assertEqual(helpers.run_tool(helpers.DBC_TOOL, "info", self.root).returncode, 2)

    def test_corrupt_file_is_a_check_failure(self):
        path = self.root / "bad.dbc"
        path.write_bytes(b"garbage")
        self.assertEqual(helpers.run_tool(helpers.DBC_TOOL, "info", path).returncode, 1)

    def test_bad_argument_is_a_usage_error(self):
        path = helpers.write_dbc(self.root / "Ok.dbc", 8, [(1, *([0] * 7))])
        self.assertEqual(helpers.run_tool(helpers.DBC_TOOL, "free", path).returncode, 2)
        self.assertEqual(helpers.run_tool(helpers.DBC_TOOL, "refs", path, "--field", "99",
                                          "--must-exist", path).returncode, 2)


class FreeIdWindows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def free(self, path, lo, hi):
        return helpers.run_tool(helpers.DBC_TOOL, "free", path, "--from", lo, "--to", hi)

    def test_windows_are_the_complement_of_used_ids(self):
        path = helpers.write_dbc(self.root / "I.dbc", 8,
                                 [(800000, *([0] * 7)), (800005, *([0] * 7))])
        out = self.free(path, 800000, 800009).stdout
        self.assertIn("800001-800004", out)
        self.assertIn("800006-800009", out)

    def test_boundaries_are_inclusive(self):
        path = helpers.write_dbc(self.root / "I.dbc", 8, [(800000, *([0] * 7))])
        out = self.free(path, 800001, 800002).stdout
        self.assertIn("800001-800002", out)

    def test_fully_used_range_fails(self):
        path = helpers.write_dbc(self.root / "I.dbc", 8, [(1, *([0] * 7)), (2, *([0] * 7))])
        self.assertEqual(self.free(path, 1, 2).returncode, 1)

    def test_untouched_range_is_all_free(self):
        path = helpers.write_dbc(self.root / "I.dbc", 8, [(1, *([0] * 7))])
        self.assertIn("100-199", self.free(path, 100, 199).stdout)

    def test_inverted_range_is_a_usage_error(self):
        path = helpers.write_dbc(self.root / "I.dbc", 8, [(1, *([0] * 7))])
        self.assertEqual(self.free(path, 100, 10).returncode, 2)


class PairComparison(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_identical_copies_pass(self):
        a = helpers.write_dbc(self.root / "a.dbc", 8, [(1, *([0] * 7))])
        b = self.root / "b.dbc"
        b.write_bytes(a.read_bytes())
        self.assertEqual(helpers.run_tool(helpers.DBC_TOOL, "verify-pair", a, b).returncode, 0)

    def test_value_drift_fails(self):
        a = helpers.write_dbc(self.root / "a.dbc", 8, [(1, 2, *([0] * 6))])
        b = helpers.write_dbc(self.root / "b.dbc", 8, [(1, 9, *([0] * 6))])
        self.assertEqual(helpers.run_tool(helpers.DBC_TOOL, "verify-pair", a, b).returncode, 1)

    def test_shape_drift_fails(self):
        a = helpers.write_dbc(self.root / "a.dbc", 8, [(1, *([0] * 7))])
        b = helpers.write_dbc(self.root / "b.dbc", 9, [(1, *([0] * 8))])
        self.assertEqual(helpers.run_tool(helpers.DBC_TOOL, "verify-pair", a, b).returncode, 1)


class ManifestEdges(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.stage = self.root / "stage"
        (self.stage / "DBFilesClient" / "nested").mkdir(parents=True)
        (self.stage / "DBFilesClient" / "Item.dbc").write_bytes(b"item")
        (self.stage / "DBFilesClient" / "nested" / "Spell.dbc").write_bytes(b"spell")
        (self.stage / ".DS_Store").write_bytes(b"junk")
        (self.stage / "DBFilesClient" / "Sprite ünïcode.dbc").write_bytes(b"unicode")

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, out="m.json", *extra):
        return helpers.run_tool(helpers.MANIFEST, "make", "--staging", self.stage,
                                "--version", "test", "--out", self.root / out, *extra)

    def test_junk_files_are_skipped_and_paths_are_posix(self):
        self.assertEqual(self.make().returncode, 0)
        data = json.loads((self.root / "m.json").read_text())
        self.assertNotIn(".DS_Store", data["files"])
        self.assertIn("DBFilesClient/nested/Spell.dbc", data["files"])
        self.assertIn("DBFilesClient/Sprite ünïcode.dbc", data["files"])

    def test_empty_staging_is_an_error(self):
        empty = self.root / "empty"
        empty.mkdir()
        result = helpers.run_tool(helpers.MANIFEST, "make", "--staging", empty,
                                  "--version", "t", "--out", self.root / "e.json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no files", result.stderr)

    def test_no_temp_file_is_left_behind(self):
        self.assertEqual(self.make().returncode, 0)
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.endswith(".tmp")], [])

    def test_verify_reports_each_kind_of_drift(self):
        self.make()
        (self.stage / "DBFilesClient" / "Item.dbc").write_bytes(b"edited")
        (self.stage / "DBFilesClient" / "New.dbc").write_bytes(b"new")
        (self.stage / "DBFilesClient" / "nested" / "Spell.dbc").unlink()
        result = helpers.run_tool(helpers.MANIFEST, "verify", "--manifest", self.root / "m.json",
                                  "--staging", self.stage)
        self.assertEqual(result.returncode, 1)
        self.assertIn("CHANGED", result.stdout)
        self.assertIn("EXTRA", result.stdout)
        self.assertIn("MISSING", result.stdout)


class PreflightBehaviour(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_tables_is_a_usage_error(self):
        empty = self.root / "empty"
        empty.mkdir()
        self.assertEqual(helpers.run_bash(helpers.PREFLIGHT, empty).returncode, 2)

    def test_missing_server_dir_is_a_usage_error_not_a_silent_skip(self):
        client, _ = helpers.item_pair(self.root)
        self.assertEqual(helpers.run_bash(helpers.PREFLIGHT, client, self.root / "typo").returncode, 2)

    def test_table_missing_on_the_server_only_warns(self):
        client, server = helpers.item_pair(self.root)
        (server / "SpellVisual.dbc").unlink()
        result = helpers.run_bash(helpers.PREFLIGHT, client, server)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("WARN", result.stdout)

    def test_drift_and_dangling_refs_both_fail(self):
        client, server = helpers.item_pair(self.root, drift=True)
        self.assertEqual(helpers.run_bash(helpers.PREFLIGHT, client, server).returncode, 1)

        client2, server2 = helpers.item_pair(self.root / "second", dangling=True)
        result = helpers.run_bash(helpers.PREFLIGHT, client2, server2)
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL  Item.dbc.DisplayInfoID", result.stdout)


class CatalogCliConsistency(unittest.TestCase):
    def test_filters_match_the_json(self):
        data = helpers.load_catalog()
        for layer in ("policy", "content", "novel"):
            with self.subTest(layer=layer):
                result = helpers.run_tool(helpers.CATALOG_TOOL, "query", "--layer", layer, "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                ids = {e["id"] for e in json.loads(result.stdout)}
                expected = {e["id"] for e in data["entries"] if e["layer"] == layer}
                self.assertEqual(ids, expected)

    def test_no_mpq_filter_excludes_patch_kinds(self):
        result = helpers.run_tool(helpers.CATALOG_TOOL, "query", "--no-mpq", "--json")
        entries = json.loads(result.stdout)
        self.assertTrue(entries)
        self.assertEqual({e["patch"] for e in entries} - {"none", "client-files"}, set())

    def test_unknown_id_is_a_usage_error(self):
        self.assertEqual(helpers.run_tool(helpers.CATALOG_TOOL, "show", "nope.nothing").returncode, 2)


if __name__ == "__main__":
    unittest.main()
